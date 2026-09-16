"""HTTP front for vidsmith: paste a script, watch it render, download the mp4."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import hmac

import html

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from vidsmith import llm
from vidsmith import music as music_mod
from vidsmith.config import ASPECTS, VoiceConfig, env
from vidsmith import cover
from vidsmith import retake as retakes
from vidsmith import rewrite
from vidsmith import script_parser
from vidsmith import usage
from vidsmith.pipeline import find_keys
from vidsmith.theme import PRESETS
from web.jobs import KEEP_BYTES, KEEP_SECONDS, MAX_QUEUE, Busy, Jobs, stage_sequence
from web.youtube import CALLBACK, Refused, YouTube

HERE = Path(__file__).resolve().parent
WORKDIR = Path(os.environ.get("VIDSMITH_JOBS", HERE.parent / "jobs"))
MAX_MINUTES = float(os.environ.get("VIDSMITH_MAX_MINUTES", "4"))
# Spoken words, at the rate drafting sizes a script by. One number for the
# check, the drafting and the page's meter: the check used to say 150 words a
# minute over every word in the file while drafting said 155 over narration,
# so a script drafted at the limit came back refused by the same instance.
WORD_CAP = int(MAX_MINUTES * llm.WORDS_PER_MINUTE)
# How far under the limit a draft aims. `llm.lengthen` has landed anywhere from
# 90% to 109% of what it is asked for, so a draft asked for exactly the limit
# would be over it about half the time, on the page that had just written it.
DRAFT_HEADROOM = 1 / llm.LENGTHEN_OVERSHOOT
_DOTENVS = (HERE.parent / ".env", Path.cwd() / ".env")
# Set this before exposing the app to the internet. Every render spends the
# owner's Pexels and Gemini quota, so an open renderer is an open wallet.
# read from .env as well, so the token sits beside the API keys instead of
# having to be exported into every shell that starts the server
TOKEN = (os.environ.get("VIDSMITH_TOKEN")
         or env("VIDSMITH_TOKEN", *_DOTENVS)).strip()
# `cards` needs no key, which is why it is the fallback the whole app leans on.
PROVIDERS = ("pexels", "pixabay", "cards")

# Where Google sends a person back after they allow YouTube access. Worked out
# from the request when unset, which behind Caddy is the public https address;
# set it when a proxy in front does not pass the scheme and host along. Either
# way it has to match a redirect URI listed on the OAuth client exactly.
PUBLIC_URL = (os.environ.get("VIDSMITH_PUBLIC_URL")
              or env("VIDSMITH_PUBLIC_URL", *_DOTENVS)).strip().rstrip("/")

app = FastAPI(title="vidsmith", docs_url="/api/docs", redoc_url=None)
jobs = Jobs(WORKDIR)
# the login lives beside .env at the repo root, where `vidsmith upload` keeps it
youtube = YouTube(HERE.parent, lambda: _keys())


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
    words = script_parser.narration_words(req.script)
    if words > WORD_CAP:
        raise HTTPException(
            400, f"{words} spoken words is over the {MAX_MINUTES:g} minute limit "
                 f"({WORD_CAP} words) for this instance; shorten the script or "
                 "run it locally")


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
    from vidsmith import build_info, cards
    from vidsmith import ffmpeg_util as ff

    # The live box was six commits behind and nothing here said so: both the
    # endpoints deploy/aws.md calls the honest witnesses were green against
    # stale code. Reported unconditionally, above the token gate, because it is
    # what a deploy check asks and it discloses nothing a git remote does not.
    commit = build_info.commit()

    # fonts are reported rather than enforced: a missing face is a cosmetic
    # downgrade, and the build deliberately does not fail over one
    bundled = sorted(p.name for p in cards.FONT_DIR.glob("*.ttf"))         if cards.FONT_DIR.exists() else []
    try:
        ffmpeg = ff.ffmpeg_bin()
    except RuntimeError as exc:
        return {"ok": False, "ffmpeg": str(exc), "fonts": bundled,
                "commit": commit}
    body: Dict[str, Any] = {"ok": True, "ffmpeg": ffmpeg, "fonts": bundled,
                            "commit": commit,
                            "busy": jobs.busy(), "max_minutes": MAX_MINUTES}
    if authorised(x_vidsmith_token, t):
        body["keys"] = {name: bool(value) for name, value in _keys().items()}
    return body


@app.get("/api/options")
def options() -> Dict[str, Any]:
    return {"aspects": sorted(ASPECTS), "themes": sorted(PRESETS),
            # the swatch beside the theme picker, taken from the presets the
            # render draws with, so a changed preset changes its preview
            "theme_colours": {name: [t.bg, t.accent, t.text]
                              for name, t in PRESETS.items()},
            # how long a finished job stays downloadable, for "kept until"
            "keep_seconds": KEEP_SECONDS,
            "moods": music_mod.moods(), "max_minutes": MAX_MINUTES,
            # counted the way the check counts, so the meter turns red at
            # exactly the script that would come back as a 400
            "word_cap": WORD_CAP,
            # the page needs the bound to know whether there is room to join
            # the line. Without it the only safe assumption is that a busy box
            # refuses, which is what it used to do and is no longer true.
            "max_queue": MAX_QUEUE,
            # how long a vertical cut may be and still be a Short
            "shorts_seconds": SHORTS_SECONDS,
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
            # the pauses around each scene are the voice config's own defaults,
            # so the page's runtime estimate adds what the render will add
            "script": {"wps": script_parser.WPS,
                       "lead_in": VoiceConfig.lead_in, "gap": VoiceConfig.gap,
                       "directives": list(script_parser.DIRECTIVE_KINDS),
                       "notes": list(script_parser.NOTE_PREFIXES)}}


@app.get("/api/usage")
def allowance(_: None = Depends(guard)) -> Dict[str, Any]:
    """How much of the free Gemini and stock allowances this instance has used.

    Behind the token: it says which services this box holds keys for and how
    hard they are being used, which a stranger has no business reading.
    """
    keys = _keys()
    body = usage.report(llm.DEFAULT_MODEL)
    body["keys"] = {name: bool(keys.get(name)) for name in ("gemini", "pexels", "pixabay")}
    return body


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


@app.get("/api/jobs")
def renders(_: None = Depends(guard)) -> Dict[str, Any]:
    """Finished renders still held, newest first, with when each will go.

    Behind the token like every job route: a title is the subject of somebody's
    video, and the list is the archive of what this instance has made.
    """
    return {"renders": jobs.renders(), "keep_seconds": KEEP_SECONDS,
            "keep_bytes": KEEP_BYTES}


def _redirect_uri(request: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL + CALLBACK
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host")
            or request.url.netloc)
    return f"{proto}://{host}{CALLBACK}"


@app.get("/api/youtube")
def youtube_status(request: Request, _: None = Depends(guard)) -> Dict[str, Any]:
    """Whether uploading is set up and connected, and the redirect URI to register."""
    return {**youtube.status(), "redirect_uri": _redirect_uri(request)}


@app.post("/api/youtube/connect")
def youtube_connect(request: Request, _: None = Depends(guard)) -> Dict[str, str]:
    try:
        return {"url": youtube.connect_url(_redirect_uri(request))}
    except Refused as exc:
        raise HTTPException(400, str(exc))


@app.get(CALLBACK, response_class=HTMLResponse)
def youtube_callback(request: Request, state: str = "", code: str = "",
                     error: str = "") -> HTMLResponse:
    """Google's redirect after consent.

    Deliberately outside the token gate: the browser arriving here is following
    Google's redirect, and cannot carry the header. The single-use state issued
    by `/api/youtube/connect`, which is gated, is what authorises it.
    """
    try:
        message = youtube.finish_connect({"state": state, "code": code, "error": error},
                                         _redirect_uri(request))
        ok = True
    except Refused as exc:
        message, ok = str(exc), False
    page = ("<!doctype html><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>vidsmith</title>"
            "<body style='font:16px system-ui,sans-serif;background:#000;color:#ededed;"
            "display:grid;place-items:center;min-height:100vh;margin:0;padding:0 16px'>"
            f"<main style='max-width:32rem'><h1 style='font-size:20px'>"
            f"{'Connected' if ok else 'Not connected'}</h1><p>{html.escape(message)}</p>"
            "<p><a href='/' style='color:#a78bfa'>Back to vidsmith</a></p></main>")
    return HTMLResponse(page, status_code=200 if ok else 400)


class UploadRequest(BaseModel):
    privacy: str = "private"


@app.post("/api/jobs/{job_id}/youtube", status_code=202)
def youtube_upload(job_id: str, req: UploadRequest,
                   _: None = Depends(guard)) -> Dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    try:
        return youtube.start(job, req.privacy, jobs.record)
    except Refused as exc:
        raise HTTPException(409, str(exc))


def _finished(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job.status != "done" or job.root is None:
        raise HTTPException(409, "only a finished render has shots to change")
    return job


@app.get("/api/jobs/{job_id}/shots")
def shots(job_id: str, _: None = Depends(guard)) -> Dict[str, Any]:
    """Every shot of a finished render, and whether its clip can be changed.

    Answers rather than refuses when it cannot, because that is the ordinary
    case for a cards build or a render from before shots were kept, and the page
    only needs to know whether to offer anything.
    """
    job = _finished(job_id)
    try:
        return {"available": True, "reason": "", **retakes.shots(job.root)}
    except retakes.RetakeRefused as exc:
        return {"available": False, "reason": str(exc), "shots": []}


@app.get("/api/jobs/{job_id}/shots/{scene}/{shot}/frame")
def shot_frame(job_id: str, scene: int, shot: int,
               _: None = Depends(guard)) -> FileResponse:
    job = _finished(job_id)
    try:
        path = retakes.frame(job.root, scene, shot)
    except (retakes.RetakeRefused, RuntimeError):
        raise HTTPException(404, "no frame for that shot")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache"})


@app.get("/api/jobs/{job_id}/shots/{scene}/{shot}/candidates")
def shot_candidates(job_id: str, scene: int, shot: int, q: str = "",
                    _: None = Depends(guard)) -> Dict[str, Any]:
    """The clips this shot could hold instead, from its own search or `q`."""
    job = _finished(job_id)
    try:
        return retakes.candidates(job.root, scene, shot, keys=_keys(), query=q or None)
    except retakes.RetakeRefused as exc:
        raise HTTPException(409, str(exc))


class SwapRequest(BaseModel):
    clip: str = Field(min_length=1, max_length=40)
    query: str = Field(default="", max_length=retakes.MAX_QUERY)


@app.post("/api/jobs/{job_id}/shots/{scene}/{shot}", status_code=202)
def swap_shot(job_id: str, scene: int, shot: int, req: SwapRequest,
              _: None = Depends(guard)) -> Dict[str, Any]:
    """Put a different clip under one shot and deliver the video again.

    Queued like a render, because the encode is most of one. The job keeps its
    id, so the page follows it the same way.
    """
    try:
        job = jobs.retake(job_id, scene, shot, req.clip, req.query, keys=_keys())
    except retakes.RetakeRefused as exc:
        raise HTTPException(409, str(exc))
    except Busy as exc:
        raise HTTPException(429, str(exc))
    if job is None:
        raise HTTPException(404, "no such job")
    return jobs.snapshot(job.id) or job.public()


# YouTube treats a vertical video up to three minutes long as a Short.
SHORTS_SECONDS = 180


class CutRequest(BaseModel):
    aspect: str = "9:16"


@app.post("/api/jobs/{job_id}/cuts", status_code=202)
def add_cut(job_id: str, req: CutRequest, _: None = Depends(guard)) -> Dict[str, Any]:
    """Another shape of a finished render, from the same narration.

    A vertical cut longer than a Short would be a vertical video nobody asked
    for, so one past the limit is refused rather than built.
    """
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if req.aspect == "9:16" and job.runtime > SHORTS_SECONDS:
        raise HTTPException(409, f"this video is {job.runtime:.0f} seconds, and a Short "
                                 f"is at most {SHORTS_SECONDS}")
    try:
        job = jobs.add_cut(job_id, req.aspect)
    except retakes.RetakeRefused as exc:
        raise HTTPException(409, str(exc))
    except Busy as exc:
        raise HTTPException(429, str(exc))
    return jobs.snapshot(job.id) or job.public()


@app.get("/api/jobs/{job_id}/scenes")
def script_scenes(job_id: str, _: None = Depends(guard)) -> Dict[str, Any]:
    """A finished render's scenes as the script has them, and the word limit."""
    job = _finished(job_id)
    try:
        body = rewrite.scenes(job.root)
    except retakes.RetakeRefused as exc:
        raise HTTPException(409, str(exc))
    # whether a scene can be rebuilt on its own, which needs the kept build
    body["editable"] = (job.root / "build" / "scenes.json").exists()
    return {**body, "word_cap": WORD_CAP}


class SceneEdit(BaseModel):
    text: str = Field(min_length=1, max_length=rewrite.MAX_SCENE_CHARS)


@app.post("/api/jobs/{job_id}/scenes/{scene}", status_code=202)
def edit_scene(job_id: str, scene: int, req: SceneEdit,
               _: None = Depends(guard)) -> Dict[str, Any]:
    """New words for one scene: it is voiced and filmed again, the rest is kept.

    Queued like a render, because the whole video is mixed and encoded again.
    """
    try:
        job = jobs.edit_scene(job_id, scene, req.text, word_cap=WORD_CAP)
    except retakes.RetakeRefused as exc:
        raise HTTPException(409, str(exc))
    except Busy as exc:
        raise HTTPException(429, str(exc))
    if job is None:
        raise HTTPException(404, "no such job")
    return jobs.snapshot(job.id) or job.public()


@app.get("/api/jobs/{job_id}/thumbnail/candidates")
def thumbnail_candidates(job_id: str, q: str = "",
                         _: None = Depends(guard)) -> Dict[str, Any]:
    """Photographs the thumbnail could be made from, from the build's search or `q`."""
    job = _finished(job_id)
    try:
        return cover.candidates(job.root, keys=_keys(), query=q or None)
    except retakes.RetakeRefused as exc:
        raise HTTPException(409, str(exc))


class ThumbnailRequest(BaseModel):
    photo: Optional[str] = Field(default=None, min_length=1, max_length=40)
    scene: Optional[int] = None
    shot: Optional[int] = None
    query: str = Field(default="", max_length=retakes.MAX_QUERY)


@app.post("/api/jobs/{job_id}/thumbnail")
def set_thumbnail(job_id: str, req: ThumbnailRequest,
                  _: None = Depends(guard)) -> Dict[str, Any]:
    """Make the thumbnail from a photograph or a shot's frame, and re-credit it."""
    try:
        job = jobs.set_thumbnail(job_id, photo=req.photo, scene=req.scene,
                                 shot=req.shot, query=req.query, keys=_keys())
    except retakes.RetakeRefused as exc:
        raise HTTPException(409, str(exc))
    except RuntimeError as exc:                 # ffmpeg could not read the frame
        raise HTTPException(500, f"the thumbnail could not be made: {exc}")
    if job is None:
        raise HTTPException(404, "no such job")
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
    minutes = max(0.5, min(MAX_MINUTES * DRAFT_HEADROOM, req.minutes))
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
    easier than taking six. A finished render is kept for `KEEP_SECONDS`.
    """
    path = jobs.archive(job_id)
    if path is None:
        raise HTTPException(404, "nothing to download yet")
    return FileResponse(path, filename=path.name,
                        media_type="application/zip",
                        content_disposition_type="attachment")
