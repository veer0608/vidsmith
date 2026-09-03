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
from vidsmith import script_parser
from vidsmith.pipeline import find_keys
from vidsmith.theme import PRESETS
from web.jobs import MAX_QUEUE, Busy, Jobs, stage_sequence

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
# `cards` needs no key, which is why it is the fallback the whole app leans on.
PROVIDERS = ("pexels", "pixabay", "cards")

app = FastAPI(title="vidsmith", docs_url="/api/docs", redoc_url=None)
jobs = Jobs(WORKDIR)


def guard(x_vidsmith_token: str = Header(default=""), t: str = "") -> None:
    """No-op when no token is configured, so local use stays frictionless.

    `t` is accepted as a query parameter because a <video> element and a plain
    download link cannot set a header.
    """
    if not TOKEN:
        return
    if not authorised(x_vidsmith_token, t):
        raise HTTPException(401, "bad or missing token")


def authorised(header: str, query: str) -> bool:
    """Whether this caller may see more than a stranger.

    Separate from `guard` because one route wants the answer without refusing
    the request: `/healthz` has to stay reachable for an uptime check that holds
    no token, while the part of it that inventories credentials does not belong
    to anonymous callers.
    """
    if not TOKEN:
        return True                        # nothing configured, nothing to hide
    return hmac.compare_digest(header or query, TOKEN)


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
    if req.provider not in PROVIDERS:
        raise HTTPException(400, f"provider must be one of {list(PROVIDERS)}")
    if req.mood not in music_mod.moods():
        raise HTTPException(400, f"mood must be one of {music_mod.moods()}")
    # narration runs at roughly 150 words a minute
    words = len(req.script.split())
    if words > MAX_MINUTES * 150:
        raise HTTPException(
            400, f"{words} words is over the {MAX_MINUTES:g} minute limit for "
                 "this instance; shorten the script or run it locally")


def _keys() -> Dict[str, str]:
    """Which stock and model keys this instance actually resolves.

    Every route goes through here rather than calling find_keys directly, so
    there is one place to look and one place to stub. Two routes used to call it
    themselves, which meant a test that stubbed this still hit the real lookup:
    it passed on a machine with a key and failed on CI, having never exercised
    the branch it named.
    """
    return find_keys(Path.cwd())


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/healthz")
def healthz(x_vidsmith_token: str = Header(default=""),
            t: str = "") -> Dict[str, Any]:
    """Whether this instance can work, and for the owner, what it resolved.

    Stays reachable without a token, because an uptime check should not need a
    secret and a deploy that cannot answer at all is indistinguishable from one
    that is merely unhealthy.

    `keys` is the exception. It is an inventory of which credentials this box
    holds, AWS included once the polly voice is configured, and a stranger who
    has found the URL has no business reading it. When a token is configured it
    is required for that field and for nothing else, so the deploy check in
    deploy/aws.md still works by passing it.
    """
    from vidsmith import cards
    from vidsmith import ffmpeg_util as ff

    # fonts are reported rather than enforced: a missing face is a cosmetic
    # downgrade, and the build deliberately does not fail over one
    bundled = sorted(p.name for p in cards.FONT_DIR.glob("*.ttf"))         if cards.FONT_DIR.exists() else []
    try:
        ffmpeg = ff.ffmpeg_bin()
    except RuntimeError as exc:
        return {"ok": False, "ffmpeg": str(exc), "fonts": bundled}
    body: Dict[str, Any] = {"ok": True, "ffmpeg": ffmpeg, "fonts": bundled,
                            "busy": jobs.busy(), "max_minutes": MAX_MINUTES}
    if authorised(x_vidsmith_token, t):
        body["keys"] = {name: bool(value) for name, value in _keys().items()}
    return body


@app.get("/api/options")
def options() -> Dict[str, Any]:
    return {"aspects": sorted(ASPECTS), "themes": sorted(PRESETS),
            "moods": music_mod.moods(), "max_minutes": MAX_MINUTES,
            # the page needs the bound to know whether there is room to join
            # the line. Without it the only safe assumption is that a busy box
            # refuses, which is what it used to do and is no longer true.
            "max_queue": MAX_QUEUE,
            "busy": jobs.busy(), "auth": bool(TOKEN),
            "stages": stage_sequence(),
            # `ready` says whether this instance holds the key that provider
            # needs, so the page can offer it truthfully instead of letting
            # someone pick a source that will silently fall back to cards
            "providers": [{"name": name,
                           "ready": name == "cards" or bool(_keys().get(name))}
                          for name in PROVIDERS],
            # the vocabulary the page needs to count scenes as you type. Served
            # rather than duplicated, so changing the parser changes the page.
            "script": {"wps": script_parser.WPS,
                       "directives": list(script_parser.DIRECTIVE_KINDS),
                       "notes": list(script_parser.NOTE_PREFIXES)}}


@app.get("/api/busy")
def busy() -> Dict[str, Any]:
    """Whether the one render slot is taken, and by what.

    Polled by the page while it is idle, so a second person sees that the box
    is working before they write a script and get a 429 for their trouble.
    """
    active = jobs.active()
    if active is None:
        return {"busy": False, "waiting": 0}
    return {"busy": True, "waiting": jobs.waiting(), "stage": active["stage"],
            "elapsed": active["elapsed"], "progress": active["progress"]}


@app.post("/api/jobs", status_code=202)
def create(req: BuildRequest, _: None = Depends(guard)) -> Dict[str, Any]:
    _validate(req)
    try:
        job = jobs.submit(req.script, req.options())
    except Busy as exc:
        # one x264 encode already has this box and the line behind it is full;
        # a second encode would starve both rather than finishing either sooner
        raise HTTPException(429, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # snapshot rather than public(), so a caller that landed in the queue is
    # told where it landed in the same response
    return jobs.snapshot(job.id) or job.public()


class DraftRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    minutes: float = 2.0


@app.post("/api/draft")
def draft(req: DraftRequest, _: None = Depends(guard)) -> Dict[str, Any]:
    """Write a script from a topic, so the page is usable without one."""
    key = _keys().get("gemini", "")
    if not key:
        raise HTTPException(503, "drafting needs GEMINI_API_KEY on this instance")
    minutes = max(0.5, min(MAX_MINUTES, req.minutes))
    try:
        return {"script": llm.draft_script(req.topic.strip(), minutes, key),
                "minutes": minutes}
    except llm.QuotaExhausted as exc:
        # 429, not 502: a spent quota is not a broken gateway, and a 5xx invites
        # a proxy to substitute its own HTML page, which the page cannot parse
        raise HTTPException(429, str(exc))
    except llm.LLMUnavailable as exc:
        raise HTTPException(502, f"the model did not answer: {exc}")


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str, _: None = Depends(guard)) -> Dict[str, Any]:
    outcome = jobs.cancel(job_id)
    if outcome is None:
        raise HTTPException(404, "no such job")
    if outcome == "finished":
        raise HTTPException(409, "that render has already finished")
    # "stopping" for a running job, which ends at the next stage boundary, and
    # "cancelled" for one that had not started: reporting the second as the
    # first would have the page wait for a stage that is never going to run
    return {"id": job_id, "status": outcome}


@app.get("/api/jobs/{job_id}/description")
def description(job_id: str, _: None = Depends(guard)) -> Dict[str, str]:
    if jobs.get(job_id) is None:
        raise HTTPException(404, "no such job")
    return {"description": jobs.description(job_id)}


@app.get("/api/jobs/{job_id}")
def status(job_id: str, _: None = Depends(guard)) -> Dict[str, Any]:
    body = jobs.snapshot(job_id)
    if body is None:
        raise HTTPException(404, "no such job")
    return body


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


@app.get("/api/jobs/{job_id}/archive")
def archive(job_id: str, _: None = Depends(guard)) -> FileResponse:
    """Everything the render produced, in one file.

    The mp4 alone is not the deliverable. `credits*.txt` carries attribution the
    stock licence requires and `description.txt` is the file that gets pasted
    into YouTube, and both were being left behind because taking one link is
    easier than taking six. Jobs are swept an hour after they finish.
    """
    path = jobs.archive(job_id)
    if path is None:
        raise HTTPException(404, "nothing to download yet")
    return FileResponse(path, filename=path.name,
                        media_type="application/zip",
                        content_disposition_type="attachment")
