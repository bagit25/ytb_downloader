"""yt-dlp wrapper and an in-memory job queue with progress tracking.

The app runs as a single uvicorn worker, so an in-process job table is enough.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from . import config

FORMATS = {"mp3", "mp4", "short"}
QUALITIES = {360, 480, 720, 1080}


class UserError(Exception):
    """An error whose message can be shown to the visitor as-is."""


# ---------------------------------------------------------------- helpers

def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise UserError("Collez un lien YouTube.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if host not in config.ALLOWED_HOSTS:
        raise UserError("Ce lien n'est pas un lien YouTube.")
    return url


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "restrictfilenames": True,
    }
    if config.COOKIES_FILE and Path(config.COOKIES_FILE).is_file():
        opts["cookiefile"] = config.COOKIES_FILE
    if config.PROXY:
        opts["proxy"] = config.PROXY
    if config.YTDLP_CLIENTS:
        opts["extractor_args"] = {"youtube": {"player_client": config.YTDLP_CLIENTS}}
    return opts


def _friendly(err: Exception) -> str:
    msg = str(err)
    low = msg.lower()
    if "not a bot" in low or "sign in to confirm" in low:
        return ("YouTube bloque temporairement ce serveur. Réessayez plus tard "
                "ou configurez des cookies (voir README).")
    if "unable to connect" in low or "timed out" in low or "proxy" in low:
        return "Le serveur n'arrive pas à joindre YouTube. Réessayez dans un instant."
    if "private" in low:
        return "Cette vidéo est privée."
    if "unavailable" in low or "removed" in low:
        return "Cette vidéo n'est pas disponible."
    if "age" in low and "restrict" in low:
        return "Vidéo soumise à une limite d'âge : des cookies sont nécessaires."
    if "live" in low:
        return "Les directs ne peuvent pas être téléchargés."
    return "Échec du téléchargement : " + re.sub(r"^ERROR:\s*", "", msg)[:200]


def fetch_info(url: str) -> dict:
    url = validate_url(url)
    try:
        with yt_dlp.YoutubeDL(_base_opts() | {"skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise UserError(_friendly(e)) from e
    if info.get("_type") == "playlist":
        raise UserError("Les playlists ne sont pas prises en charge : collez le lien d'une vidéo.")
    heights = sorted({f.get("height") for f in info.get("formats", [])
                      if f.get("height") and f.get("vcodec") != "none"})
    qualities = [q for q in sorted(QUALITIES) if any(h >= q for h in heights)] or [360]
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "is_live": bool(info.get("is_live")),
        "qualities": qualities,
        "max_duration": config.MAX_DURATION,
    }


def _safe_name(title: str, ext: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", title or "video").strip()
    name = re.sub(r"\s+", " ", name)[:120] or "video"
    return f"{name}.{ext}"


# ---------------------------------------------------------------- jobs

@dataclass
class Job:
    id: str
    url: str
    fmt: str
    quality: int
    ip: str
    status: str = "queued"          # queued | downloading | processing | done | error
    progress: float = 0.0
    message: str = ""
    title: str = ""
    file: Path | None = None
    filename: str = ""
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def public(self) -> dict:
        return {
            "id": self.id, "status": self.status, "progress": round(self.progress, 1),
            "message": self.message, "title": self.title, "filename": self.filename,
        }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT)
        self.hits: dict[str, list[float]] = {}
        config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._janitor, daemon=True).start()

    # -- public API
    def submit(self, url: str, fmt: str, quality: int, ip: str) -> Job:
        url = validate_url(url)
        if fmt not in FORMATS:
            raise UserError("Format inconnu.")
        if quality not in QUALITIES:
            quality = 720
        with self.lock:
            now = time.time()
            recent = [t for t in self.hits.get(ip, []) if now - t < config.RATE_WINDOW]
            if len(recent) >= config.RATE_LIMIT:
                raise UserError("Trop de demandes. Patientez quelques minutes.")
            active = sum(j.status in ("queued", "downloading", "processing")
                         for j in self.jobs.values())
            if active >= config.MAX_QUEUE:
                raise UserError("Le serveur est occupé. Réessayez dans un instant.")
            recent.append(now)
            self.hits[ip] = recent
            job = Job(id=uuid.uuid4().hex, url=url, fmt=fmt, quality=quality, ip=ip)
            self.jobs[job.id] = job
        self.pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    # -- worker
    def _run(self, job: Job) -> None:
        workdir = config.DOWNLOAD_DIR / job.id
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            job.status = "downloading"
            info = fetch_info(job.url)
            job.title = info["title"] or "video"
            if info["is_live"]:
                raise UserError("Les directs ne peuvent pas être téléchargés.")
            # A Short only needs the first seconds, unless HLS forces a full download.
            short_exempt = job.fmt == "short" and not config.PREFER_HLS
            if info["duration"] and info["duration"] > config.MAX_DURATION and not short_exempt:
                raise UserError(f"Vidéo trop longue (limite : {config.MAX_DURATION // 60} min).")

            out = self._download(job, workdir)
            if job.fmt == "short":
                job.status, job.message = "processing", "Recadrage en 9:16…"
                out = self._make_short(out, workdir)

            ext = {"mp3": "mp3", "mp4": "mp4", "short": "mp4"}[job.fmt]
            suffix = " (short)" if job.fmt == "short" else ""
            job.filename = _safe_name(job.title + suffix, ext)
            job.file = out
            job.progress, job.status, job.message = 100.0, "done", "Prêt."
        except UserError as e:
            job.status, job.message = "error", str(e)
        except yt_dlp.utils.DownloadError as e:
            job.status, job.message = "error", _friendly(e)
        except Exception as e:  # noqa: BLE001 - last-resort guard
            job.status, job.message = "error", f"Erreur inattendue : {type(e).__name__}"
        finally:
            job.finished = time.time()
            if job.status == "error":
                shutil.rmtree(workdir, ignore_errors=True)

    def _download(self, job: Job, workdir: Path) -> Path:
        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                frags, idx = d.get("fragment_count"), d.get("fragment_index")
                if frags and idx is not None:          # HLS: count fragments
                    job.progress = max(job.progress, min(idx / frags * 95, 95))
                elif total:
                    # Two streams (video + audio) are merged; keep the bar monotonic.
                    pct = d.get("downloaded_bytes", 0) / total * 95
                    job.progress = max(job.progress, min(pct, 95))
                job.message = "Téléchargement…"
            elif d.get("status") == "finished":
                job.message = "Conversion…"

        opts = _base_opts() | {
            "outtmpl": str(workdir / "media.%(ext)s"),
            "progress_hooks": [hook],
        }
        if job.fmt == "mp3":
            hls = "ba[protocol^=m3u8]/b[protocol^=m3u8]/" if config.PREFER_HLS else ""
            opts |= {
                "format": hls + "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio",
                                    "preferredcodec": "mp3", "preferredquality": "192"}],
            }
        else:
            q = 1080 if job.fmt == "short" else job.quality
            hls = (f"b[protocol^=m3u8][height<={q}]/"
                   f"bv*[protocol^=m3u8][height<={q}]+ba[protocol^=m3u8]/"
                   if config.PREFER_HLS else "")
            opts |= {
                "format": (hls + f"bv*[height<={q}][ext=mp4]+ba[ext=m4a]/"
                           f"bv*[height<={q}]+ba/b[height<={q}]/b"),
                "merge_output_format": "mp4",
            }
            if job.fmt == "short" and not config.PREFER_HLS:
                # Only fetch what the Short needs.
                opts["download_ranges"] = yt_dlp.utils.download_range_func(
                    None, [(0, config.SHORT_SECONDS)])
                opts["force_keyframes_at_cuts"] = True

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([job.url])
        files = [p for p in workdir.iterdir() if p.is_file() and p.suffix != ".part"]
        if not files:
            raise UserError("Aucun fichier produit.")
        return max(files, key=lambda p: p.stat().st_size)

    @staticmethod
    def _make_short(src: Path, workdir: Path) -> Path:
        """Centre-crop to 9:16, trim to SHORT_SECONDS, scale to 1080x1920."""
        dst = workdir / "short.mp4"
        vf = ("crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',"
              "scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
               "-t", str(config.SHORT_SECONDS), "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if res.returncode != 0 or not dst.exists():
            raise UserError("La conversion en Short a échoué.")
        src.unlink(missing_ok=True)
        return dst

    # -- cleanup
    def _janitor(self) -> None:
        while True:
            time.sleep(60)
            now = time.time()
            with self.lock:
                for jid, job in list(self.jobs.items()):
                    if job.finished and now - job.finished > config.FILE_TTL:
                        shutil.rmtree(config.DOWNLOAD_DIR / jid, ignore_errors=True)
                        del self.jobs[jid]
                for ip, ts in list(self.hits.items()):
                    if not any(now - t < config.RATE_WINDOW for t in ts):
                        del self.hits[ip]
            # Orphan folders (e.g. after a restart).
            for d in config.DOWNLOAD_DIR.glob("*"):
                if d.name not in self.jobs and now - d.stat().st_mtime > config.FILE_TTL:
                    shutil.rmtree(d, ignore_errors=True)
