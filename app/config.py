"""Runtime settings, read from environment variables (see .env.example)."""
import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


APP_NAME = os.getenv("APP_NAME", "Tubeget")

# Where temporary downloads live. Files are deleted after FILE_TTL seconds.
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/ytapp"))
FILE_TTL = _int("FILE_TTL", 900)

# Limits that protect a small Always Free VM.
MAX_DURATION = _int("MAX_DURATION", 3600)       # seconds of video accepted
MAX_CONCURRENT = _int("MAX_CONCURRENT", 2)      # downloads running at once
MAX_QUEUE = _int("MAX_QUEUE", 10)               # jobs waiting + running
RATE_LIMIT = _int("RATE_LIMIT", 15)             # jobs per IP ...
RATE_WINDOW = _int("RATE_WINDOW", 600)          # ... per this many seconds
SHORT_SECONDS = _int("SHORT_SECONDS", 60)

# Optional helpers when YouTube blocks the server IP ("confirm you're not a bot").
COOKIES_FILE = os.getenv("COOKIES_FILE", "")    # Netscape cookies.txt path
PROXY = os.getenv("PROXY", "")                  # e.g. http://user:pass@host:port
# Prefer HLS (m3u8) streams: on blocked/proxied servers YouTube often answers 403 for
# direct streams but still serves HLS. Set PREFER_HLS=0 to disable.
PREFER_HLS = os.getenv("PREFER_HLS", "1") not in ("0", "false", "no", "")
# Optional comma-separated yt-dlp player clients, e.g. "default,web_safari".
YTDLP_CLIENTS = [c.strip() for c in os.getenv("YTDLP_CLIENTS", "").split(",") if c.strip()]

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
}
