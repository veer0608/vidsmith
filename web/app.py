"""HTTP front for vidsmith: paste a script, watch it render, download the mp4."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from vidsmith import music as music_mod
from vidsmith.config import ASPECTS
from vidsmith.theme import PRESETS
from web.jobs import Busy, Jobs

HERE = Path(__file__).resolve().parent
WORKDIR = Path(os.environ.get("VIDSMITH_JOBS", HERE.parent / "jobs"))
MAX_MINUTES = float(os.environ.get("VIDSMITH_MAX_MINUTES", "4"))

app = FastAPI(title="vidsmith", docs_url="/api/docs", redoc_url=None)
jobs = Jobs(WORKDIR)


class BuildRequest(BaseModel):
    script: str = Field(min_length=1)
    aspect: str = "16:9"
    theme: str = "midnight"
    provider: str = "pexels"
    watermark: str = ""
    music: bool = True
    mood: str = "calm"
    voice: Optional[str] = None

    def options(self) -> Dict[str, Any]:
        return self.model_dump(exclude={"script"})


def _validate(req: BuildRequest) -> None:
    if req.aspect not in ASPECTS:
        raise HTTPException(400, f"aspect must be one of {sorted(ASPECTS)}")
    if req.theme not in PRESETS:
        raise HTTPException(400, f"theme must be one of {sorted(PRESETS)}")
    if req.provider not in ("pexels", "pixabay", "cards"):
        raise HTTPException(400, "provider must be pexels, pixabay or cards")
    if req.mood not in music_mod.moods():
        raise HTTPException(400, f"mood must be one of {music_mod.moods()}")
    # narration runs at roughly 150 words a minute
    words = len(req.script.split())
    if words > MAX_MINUTES * 150:
        raise HTTPException(
            400, f"{words} words is over the {MAX_MINUTES:g} minute limit for "
                 "this instance; shorten the script or run it locally")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    from vidsmith import ffmpeg_util as ff

    try:
        ffmpeg = ff.ffmpeg_bin()
    except RuntimeError as exc:
        return {"ok": False, "ffmpeg": str(exc)}
    return {"ok": True, "ffmpeg": ffmpeg, "busy": jobs.busy(),
            "max_minutes": MAX_MINUTES}


@app.get("/api/options")
def options() -> Dict[str, Any]:
    return {"aspects": sorted(ASPECTS), "themes": sorted(PRESETS),
            "moods": music_mod.moods(), "max_minutes": MAX_MINUTES,
            "busy": jobs.busy()}


@app.post("/api/jobs", status_code=202)
def create(req: BuildRequest) -> Dict[str, Any]:
    _validate(req)
    try:
        job = jobs.submit(req.script, req.options())
    except Busy as exc:
        # one x264 encode already has this box; a second would starve both
        raise HTTPException(429, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return job.public()


@app.get("/api/jobs/{job_id}")
def status(job_id: str) -> Dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job.public()


@app.get("/api/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str) -> FileResponse:
    path = jobs.file(job_id, name)
    if path is None:
        raise HTTPException(404, "no such file")
    inline = path.suffix.lower() in (".mp4", ".jpg")
    return FileResponse(
        path, filename=path.name,
        content_disposition_type="inline" if inline else "attachment",
    )
