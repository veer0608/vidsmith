"""Put a finished build on YouTube, including the parts that get forgotten.

Every fault this project has shipped landed on the upload form rather than in
the render: a description that was never saved, tags lost with it, a caption
track that turned out to be YouTube's own transcription of audio we had exact
timings for. `check --published` was written to *notice* those afterwards. This
is the other half - it fills the form from the files the build already wrote, so
there is no pasting step to get wrong.

Three uploads, not one, because YouTube takes them at three endpoints:

    videos.insert       the mp4, the title, the description, the tags
    thumbnails.set      the jpg
    captions.insert     the srt, as a real track rather than an ASR guess

There is no SDK here for the same reason `llm.py` has none: one dependency that
already ships (`requests`) against documented HTTP, so an auth problem is
readable as a status code instead of through a library's exception hierarchy.

Nothing in here decides *what* to publish. `youtube.json`, `description<tag>.txt`
and `captions<tag>.srt` are written by the build, and the one rule that matters
is that all three belong to the same cut: a 9:16 upload carrying the widescreen
description names photographers whose clips are not in it, which is the fault
that has now shipped twice.
"""
from __future__ import annotations

import json
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Dict, Optional

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CAPTION_URL = "https://www.googleapis.com/upload/youtube/v3/captions"
WATCH = "https://www.youtube.com/watch?v="

# youtube.upload alone cannot write a caption track, and force-ssl alone cannot
# insert a video. Asking for one and discovering the other is missing costs a
# second consent screen, so both are requested the first time.
SCOPES = ("https://www.googleapis.com/auth/youtube.upload "
          "https://www.googleapis.com/auth/youtube.force-ssl")

# A resumable session takes the bytes in pieces, and every piece except the last
# must be a multiple of 256 KB or the server rejects the range. 4 MB is small
# enough to report progress on a 20 MB cut and large enough that the round trips
# do not dominate.
CHUNK = 4 * 1024 * 1024
BLOCK = 256 * 1024

TOKEN_FILE = ".youtube-token.json"


class UploadFailed(RuntimeError):
    """Anything that stops the video reaching YouTube.

    A RuntimeError because `cli.main()` already turns those into a message and
    exit 1. What must never happen is a partial success reported as a success:
    an upload that placed the video and then failed to set the caption track has
    published something wrong, so it raises and names which step it reached.
    """


class NotConnected(UploadFailed):
    """No saved login, or one Google no longer honours, and nobody to ask.

    The web service runs on a box with no browser, so it cannot fall through to
    the consent screen the way the CLI does; the page asks the person to connect
    instead. A distinct class so the route can say that rather than "failed".
    """


def consent_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Google's consent screen for these scopes, coming back to `redirect_uri`.

    `access_type=offline` with `prompt=consent` is what makes the response carry
    a refresh token; without both, a second authorisation returns only an access
    token and every later upload asks again.
    """
    return f"{AUTH_URL}?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def exchange_code(code: str, client_id: str, client_secret: str,
                  redirect_uri: str) -> Dict[str, Any]:
    """Trade an authorisation code for tokens. `redirect_uri` must match exactly."""
    return _post_token({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })


def save_grant(repo_root: Path, granted: Dict[str, Any]) -> None:
    """Keep a first authorisation, which is the only one carrying a refresh token."""
    if not granted.get("refresh_token"):
        raise UploadFailed(
            "Google returned no refresh token; revoke vidsmith's access at "
            "myaccount.google.com/permissions and authorise again")
    _save_token(token_path(repo_root), granted)


def connected(repo_root: Path) -> bool:
    """Whether a login is saved. Not whether Google still honours it."""
    try:
        saved = json.loads(token_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(saved, dict) and saved.get("refresh_token"))


# --------------------------------------------------------------------------- #
# consent, once
# --------------------------------------------------------------------------- #
def redirect_result(query: Dict[str, Any], expected_state: str) -> tuple:
    """What to do with whatever arrived on the loopback port.

    A function rather than a branch inside the handler because it is the only
    security decision this module makes and it needs a test: the port is open to
    anything else running on this machine, so a code is exchanged only when the
    redirect carries the state we generated for this attempt.

    Returns `(code, error, page_body)`.
    """
    if (query.get("state") or [""])[0] != expected_state:
        return ("", "the redirect did not carry the state we sent",
                b"vidsmith: unexpected redirect, ignored.")
    if query.get("error"):
        return "", query["error"][0], \
            b"vidsmith: consent was refused. You can close this tab."
    return ((query.get("code") or [""])[0], "",
            b"vidsmith: authorised. You can close this tab.")


class _Catcher(BaseHTTPRequestHandler):
    """The loopback redirect Google hands the authorisation code back on."""

    code: Optional[str] = None
    state: str = ""
    error: str = ""

    def do_GET(self) -> None:                      # noqa: N802 - stdlib spelling
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code, error, body = redirect_result(query, type(self).state)
        type(self).code, type(self).error = code, error
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass                                        # the build log is the log


def token_path(repo_root: Path) -> Path:
    return Path(repo_root) / TOKEN_FILE


def _consent(client_id: str, client_secret: str, log=print) -> Dict[str, Any]:
    """The one interactive step, and the only one.

    Google's loopback flow for a desktop client: open the consent screen in the
    user's own browser, catch the redirect on a port we picked, exchange the
    code. `access_type=offline` with `prompt=consent` is what makes the response
    carry a refresh token - without both, a second authorisation returns only an
    access token and every later run asks for consent again.
    """
    _Catcher.code = None
    _Catcher.error = ""
    _Catcher.state = secrets.token_urlsafe(24)

    server = HTTPServer(("127.0.0.1", 0), _Catcher)
    port = server.server_address[1]
    redirect = f"http://127.0.0.1:{port}"
    url = consent_url(client_id, redirect, _Catcher.state)

    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()
    log("auth     opening the Google consent screen in your browser")
    log(f"         if nothing opens, visit:\n         {url}")
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()

    if _Catcher.error:
        raise UploadFailed(f"authorisation failed: {_Catcher.error}")
    if not _Catcher.code:
        raise UploadFailed("no authorisation code came back within five minutes")

    return exchange_code(_Catcher.code, client_id, client_secret, redirect)


def _post_token(form: Dict[str, str]) -> Dict[str, Any]:
    try:
        resp = requests.post(TOKEN_URL, data=form, timeout=30)
    except requests.RequestException as exc:
        raise UploadFailed(f"could not reach Google for a token: {exc}") from exc
    if resp.status_code != 200:
        raise UploadFailed(f"token request refused ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def access_token(repo_root: Path, client_id: str, client_secret: str,
                 log=print, interactive: bool = True) -> str:
    """A usable access token, refreshing or asking for consent as needed.

    The refresh token is the thing worth keeping, so it is written back on every
    exchange: Google returns a refresh token on the *first* authorisation and
    then omits it from refresh responses, and a save that copied the response
    wholesale would erase it on the first refresh.

    `interactive=False` is the web service, on a box with no browser to open:
    with no usable login it raises `NotConnected` rather than waiting five
    minutes on a consent screen nobody can see.
    """
    if not client_id or not client_secret:
        raise UploadFailed(
            "no YouTube client: set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET "
            "from an OAuth client in Google Cloud (see README, Uploading it)")

    path = token_path(repo_root)
    saved: Dict[str, Any] = {}
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}

    refresh = saved.get("refresh_token", "")
    if refresh:
        try:
            fresh = _post_token({
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            })
        except UploadFailed:
            fresh = {}                      # revoked or expired; ask again below
        if fresh.get("access_token"):
            saved.update(fresh)
            saved["refresh_token"] = fresh.get("refresh_token") or refresh
            _save_token(path, saved)
            return saved["access_token"]

    if not interactive:
        raise NotConnected("YouTube is not connected, or Google no longer accepts "
                           "the saved login; connect it again")
    granted = _consent(client_id, client_secret, log=log)
    save_grant(repo_root, granted)
    return granted["access_token"]


def _save_token(path: Path, body: Dict[str, Any]) -> None:
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)                   # a no-op on Windows, honest on unix
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# what to send
# --------------------------------------------------------------------------- #
def snippet(meta: Dict[str, Any], description: str, category: str,
            privacy: str) -> Dict[str, Any]:
    """The body of videos.insert.

    The description is passed in rather than read from `meta`, because the file
    beside the cut is the one carrying that cut's credits and `youtube.json`
    holds a single shared copy. Publishing the shared one is how a vertical cut
    ends up naming the widescreen cut's photographers.
    """
    return {
        "snippet": {
            "title": meta.get("title", "") or "Untitled",
            "description": description,
            "tags": list(meta.get("tags", []) or []),
            "categoryId": str(category),
        },
        "status": {
            "privacyStatus": privacy,
            # YouTube refuses an upload that declares nothing here, and the
            # answer is not a guess: nothing this tool makes is for children.
            "selfDeclaredMadeForKids": False,
        },
    }


def _headers(token: str, **extra: str) -> Dict[str, str]:
    head = {"Authorization": f"Bearer {token}"}
    head.update(extra)
    return head


def _open_session(token: str, body: Dict[str, Any], size: int, log=print) -> str:
    resp = requests.post(
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers=_headers(token,
                         **{"Content-Type": "application/json; charset=UTF-8",
                            "X-Upload-Content-Length": str(size),
                            "X-Upload-Content-Type": "video/mp4"}),
        data=json.dumps(body).encode("utf-8"),
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise UploadFailed(f"YouTube refused the upload ({resp.status_code}): "
                           f"{resp.text[:300]}")
    session = resp.headers.get("Location", "")
    if not session:
        raise UploadFailed("YouTube accepted the request but returned no upload URL")
    return session


def _put_chunks(session: str, data: bytes, log=print) -> Dict[str, Any]:
    """Send the file in ranges, and let the server say where it got to.

    The point of the resumable session is this loop: a 308 carries a `Range`
    header naming the last byte stored, so the next chunk starts from what the
    server has rather than from what we think we sent.
    """
    size = len(data)
    sent = 0
    while sent < size:
        end = min(sent + CHUNK, size) - 1
        resp = requests.put(
            session,
            headers={"Content-Length": str(end - sent + 1),
                     "Content-Range": f"bytes {sent}-{end}/{size}"},
            data=data[sent:end + 1],
            timeout=600,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code != 308:
            raise UploadFailed(f"upload stopped at byte {sent} "
                               f"({resp.status_code}): {resp.text[:300]}")
        stored = resp.headers.get("Range", "")
        sent = int(stored.split("-")[-1]) + 1 if "-" in stored else end + 1
        log(f"         {sent / size:.0%} of {size / 1e6:.1f} MB")
    raise UploadFailed("the upload finished its bytes without a video back")


def set_thumbnail(token: str, video_id: str, jpg: Path) -> None:
    resp = requests.post(
        THUMBNAIL_URL,
        params={"videoId": video_id},
        headers=_headers(token, **{"Content-Type": "image/jpeg"}),
        data=jpg.read_bytes(),
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise UploadFailed(f"the video is up but the thumbnail was refused "
                           f"({resp.status_code}): {resp.text[:300]}")


def add_captions(token: str, video_id: str, srt: Path, name: str = "English",
                 language: str = "en") -> None:
    """Upload the timings the build already knows, as a real track.

    `sync` is false on purpose. It asks YouTube to re-time the text against the
    audio, which is exactly the transcription step this project exists to avoid:
    the srt carries edge-tts word boundaries, so the timings in it are the ones
    the speech was made from.
    """
    meta = {"snippet": {"videoId": video_id, "language": language,
                        "name": name, "isDraft": False}}
    boundary = f"vidsmith-{secrets.token_hex(12)}"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        json.dumps(meta).encode("utf-8"), b"\r\n",
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        srt.read_bytes(), b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    resp = requests.post(
        CAPTION_URL,
        params={"part": "snippet", "uploadType": "multipart", "sync": "false"},
        headers=_headers(token,
                         **{"Content-Type": f"multipart/related; boundary={boundary}"}),
        data=body,
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise UploadFailed(f"the video is up but the caption track was refused "
                           f"({resp.status_code}): {resp.text[:300]}. "
                           "Without it YouTube will transcribe the audio itself")


VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
# The writable status fields. videos.update replaces the whole `status` part,
# so any of these left out of the PUT is reset to YouTube's default - a
# made-for-kids declaration among them. `publishAt` is left out on purpose: it
# schedules a private video, and this is making one visible now.
KEEP_STATUS = ("embeddable", "license", "publicStatsViewable",
               "selfDeclaredMadeForKids")


def set_privacy(token: str, video_id: str, privacy: str) -> str:
    """Change who can see a video and nothing else. Returns the new status."""
    if privacy not in ("private", "unlisted", "public"):
        raise ValueError(f"not a YouTube privacy status: {privacy!r}")
    got = requests.get(VIDEOS_URL, params={"part": "status", "id": video_id},
                       headers=_headers(token), timeout=30)
    if got.status_code != 200:
        raise UploadFailed(f"could not read {video_id} ({got.status_code}): "
                           f"{got.text[:300]}")
    items = got.json().get("items") or []
    if not items:
        raise UploadFailed(f"no video {video_id} on this channel")
    status = items[0].get("status") or {}
    body = {"id": video_id,
            "status": {k: status[k] for k in KEEP_STATUS if k in status}}
    body["status"]["privacyStatus"] = privacy
    put = requests.put(VIDEOS_URL, params={"part": "status"}, json=body,
                       headers=_headers(token), timeout=30)
    if put.status_code != 200:
        raise UploadFailed(f"YouTube refused the change ({put.status_code}): "
                           f"{put.text[:300]}")
    return (put.json().get("status") or {}).get("privacyStatus", "")


def publish(cut: Path, meta: Dict[str, Any], description: str, token: str,
            thumbnail: Optional[Path] = None, captions: Optional[Path] = None,
            category: str = "28", privacy: str = "private",
            log=print) -> str:
    """The whole upload, in the order a failure is cheapest to recover from.

    Video first, because everything else needs its id. The optional files are
    guarded on `is None` rather than truthiness: `Path("")` is `Path(".")`,
    which is truthy and exists, and that spelling has already handed ffmpeg a
    directory where a subtitle file should have been.
    """
    data = cut.read_bytes()
    log(f"upload   {cut.name}  {len(data) / 1e6:.1f} MB as {privacy}")
    session = _open_session(token, snippet(meta, description, category, privacy),
                            len(data), log=log)
    video = _put_chunks(session, data, log=log)
    vid = video.get("id", "")
    if not vid:
        raise UploadFailed("YouTube returned no video id for the upload")
    log(f"         {WATCH}{vid}")

    if thumbnail is not None:
        set_thumbnail(token, vid, thumbnail)
        log(f"thumb    {thumbnail.name}")
    if captions is not None:
        add_captions(token, vid, captions)
        log(f"captions {captions.name} uploaded as a track, not transcribed")
    return vid
