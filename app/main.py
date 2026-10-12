"""Tubeget — FastAPI entry point.

Run locally:   uvicorn app.main:app --reload
Production:    see deploy/ytapp.service (behind Nginx on Oracle Cloud)
"""
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config
from .downloader import JobManager, UserError, fetch_info

BASE = Path(__file__).parent
app = FastAPI(title=config.APP_NAME, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
jobs = JobManager()


@app.middleware("http")
async def frame_policy(request: Request, call_next):
    # Only the sites listed in FRAME_ANCESTORS may show the app in an iframe.
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = f"frame-ancestors {config.FRAME_ANCESTORS}"
    return response


@app.exception_handler(UserError)
async def user_error(_: Request, exc: UserError):
    return JSONResponse({"error": str(exc)}, status_code=400)


def client_ip(request: Request) -> str:
    # uvicorn runs with --proxy-headers, so this is the real visitor IP behind Nginx.
    return request.client.host if request.client else "unknown"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "app_name": config.APP_NAME,
        "max_minutes": config.MAX_DURATION // 60,
        "short_seconds": config.SHORT_SECONDS,
    })


@app.get("/api/info")
async def info(url: str):
    return await run_in_threadpool(fetch_info, url)


@app.post("/api/jobs")
async def create_job(request: Request, url: str = Form(...),
                     format: str = Form("mp4"), quality: int = Form(720)):
    job = jobs.submit(url, format, quality, client_ip(request))
    return job.public()


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired.")
    return job.public()


@app.get("/api/jobs/{job_id}/file")
async def job_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job.status != "done" or not job.file or not job.file.exists():
        raise HTTPException(404, "File not found or expired.")
    media = "audio/mpeg" if job.filename.endswith(".mp3") else "video/mp4"
    return FileResponse(job.file, media_type=media, filename=job.filename)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
