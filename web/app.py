"""HTTP front for vidsmith: paste a script, watch it render, download the mp4."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from vidsmith import llm
from vidsmith import music as music_mod
from vidsmith.config import ASPECTS, env
from vidsmith.pipeline import find_keys
from vidsmith.theme import PRESETS
from web.jobs import Busy, Jobs, stage_sequence

HERE = Path(__file__).resolve().parent
WORKDIR = Path(os.environ.get("VIDSMITH_JOBS", HERE.parent / "jobs"))
MAX_MINUTES = float(os.environ.get("VIDSMITH_MAX_MINUTES", "4"))
_DOTENVS = (HERE.parent / ".env", Path.cwd() / ".env")
# Set this before exposing the app to the internet. Every render spends the
# owner's Pexels and Gemini quota, so an open renderer is an open wallet.
# read from .env as well, so the token sits beside the API keys instead of
# having to be exported into every shell that starts the server
TOKEN = (os.environ.get("VIDSMITH_TOKEN")
         or env("VIDSMITH_TOKEN", *_DOTENVS)).strip()

app = FastAPI(title="vidsmith", docs_url="/api/docs", redoc_url=None)
jobs = Jobs(WORKDIR)


def guard(x_vidsmith_token: str = Header(default=""), t: str = "") -> None:
    """No-op when no token is configured, so local use stays frictionless.

    `t` is accepted as a query parameter because a <video> element and a plain
    download link cannot set a header.
    """
    if not TOKEN:
        return
    supplied = x_vidsmith_token or t
    if not hmac.compare_digest(supplied, TOKEN):
        raise HTTPException(401, "bad or missing token")


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
    from vidsmith import cards
    from vidsmith import ffmpeg_util as ff

    # fonts are reported rather than enforced: a missing face is a cosmetic
    # downgrade, and the build deliberately does not fail over one
    bundled = sorted(p.name for p in cards.FONT_DIR.glob("*.ttf"))         if cards.FONT_DIR.exists() else []
    try:
        ffmpeg = ff.ffmpeg_bin()
    except RuntimeError as exc:
        return {"ok": False, "ffmpeg": str(exc), "fonts": bundled}
    return {"ok": True, "ffmpeg": ffmpeg, "fonts": bundled,
            "busy": jobs.busy(), "max_minutes": MAX_MINUTES,
            "keys": {name: bool(value) for name, value
                     in find_keys(Path.cwd()).items()}}


@app.get("/api/options")
def options() -> Dict[str, Any]:
    return {"aspects": sorted(ASPECTS), "themes": sorted(PRESETS),
            "moods": music_mod.moods(), "max_minutes": MAX_MINUTES,
            "busy": jobs.busy(), "auth": bool(TOKEN),
            "stages": stage_sequence()}


@app.get("/api/busy")
def busy() -> Dict[str, Any]:
    """Whether the one render slot is taken, and by what.

    Polled by the page while it is idle, so a second person sees that the box
    is working before they write a script and get a 429 for their trouble.
    """
    active = jobs.active()
    if active is None:
        return {"busy": False}
    return {"busy": True, "stage": active["stage"],
            "elapsed": active["elapsed"], "progress": active["progress"]}


@app.post("/api/jobs", status_code=202)
def create(req: BuildRequest, _: None = Depends(guard)) -> Dict[str, Any]:
    _validate(req)
    try:
        job = jobs.submit(req.script, req.options())
    except Busy as exc:
        # one x264 encode already has this box; a second would starve both
        raise HTTPException(429, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return job.public()


class DraftRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    minutes: float = 2.0


@app.post("/api/draft")
def draft(req: DraftRequest, _: None = Depends(guard)) -> Dict[str, Any]:
    """Write a script from a topic, so the page is usable without one."""
    key = find_keys(Path.cwd()).get("gemini", "")
    if not key:
        raise HTTPException(503, "drafting needs GEMINI_API_KEY on this instance")
    minutes = max(0.5, min(MAX_MINUTES, req.minutes))
    try:
        return {"script": llm.draft_script(req.topic.strip(), minutes, key),
                "minutes": minutes}
    except llm.LLMUnavailable as exc:
        raise HTTPException(502, f"the model did not answer: {exc}")


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str, _: None = Depends(guard)) -> Dict[str, Any]:
    outcome = jobs.cancel(job_id)
    if outcome is None:
        raise HTTPException(404, "no such job")
    if outcome == "finished":
        raise HTTPException(409, "that render has already finished")
    return {"id": job_id, "status": "stopping"}


@app.get("/api/jobs/{job_id}/description")
def description(job_id: str, _: None = Depends(guard)) -> Dict[str, str]:
    if jobs.get(job_id) is None:
        raise HTTPException(404, "no such job")
    return {"description": jobs.description(job_id)}


@app.get("/api/jobs/{job_id}")
def status(job_id: str, _: None = Depends(guard)) -> Dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job.public()


@app.get("/api/jobs/{job_id}/files/{name}")
def download(job_id: str, name: str,
             _: None = Depends(guard)) -> FileResponse:
    path = jobs.file(job_id, name)
    if path is None:
        raise HTTPException(404, "no such file")
    inline = path.suffix.lower() in (".mp4", ".jpg")
    return FileResponse(
        path, filename=path.name,
        content_disposition_type="inline" if inline else "attachment",
    )
