"""Upload a finished render to YouTube from the page.

`vidsmith upload` already fills the whole form - title, description with its
credits, tags, the thumbnail and a real caption track - but only from the
machine that holds the login, through a consent screen it opens on a loopback
port. A render made on the live instance had to be downloaded and its
description typed into YouTube Studio by hand, which is the pasting step
`upload.py` exists to remove.

The web half differs in one way only: consent. The server has no browser, so
Google's redirect comes back to a route on this app instead of a local port, and
the client has to be a *Web application* OAuth client that lists that route as a
redirect URI. The same `.youtube-token.json` then holds the login, and everything
after consent is `upload.publish()`.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from vidsmith import upload
from vidsmith.check import check, delivered
from vidsmith.config import aspect_tag
from vidsmith.published import record as write_receipt

PRIVACY = ("private", "unlisted", "public")
CALLBACK = "/api/youtube/callback"
# A consent screen left open longer than this is abandoned, and its state goes.
STATE_SECONDS = 15 * 60


class Refused(RuntimeError):
    """An upload that must not start, with a reason a person can act on."""


class YouTube:
    def __init__(self, repo_root: Path, keys: Callable[[], Dict[str, str]]):
        self.repo_root = Path(repo_root)
        self._keys = keys
        self._states: Dict[str, float] = {}
        self._lock = threading.Lock()

    # -- the login ----------------------------------------------------------- #
    def _client(self) -> Tuple[str, str]:
        keys = self._keys()
        return keys.get("yt_client", ""), keys.get("yt_secret", "")

    def status(self) -> Dict[str, bool]:
        client_id, secret = self._client()
        return {"configured": bool(client_id and secret),
                "connected": upload.connected(self.repo_root)}

    def connect_url(self, redirect_uri: str) -> str:
        """A consent screen whose redirect this process will recognise, once."""
        client_id, secret = self._client()
        if not (client_id and secret):
            raise Refused("YouTube is not set up on this instance: it needs "
                          "YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET")
        state = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._states = {s: t for s, t in self._states.items() if t > now}
            self._states[state] = now + STATE_SECONDS
        return upload.consent_url(client_id, redirect_uri, state)

    def finish_connect(self, query: Dict[str, str], redirect_uri: str) -> str:
        """Handle Google's redirect. Returns what to tell the person.

        The route cannot sit behind the token, because it is Google's redirect
        that arrives rather than the page. The state is what stands in for it: a
        code is exchanged only when it carries a state this process issued in
        the last quarter hour, and each state is good for one redirect.
        """
        state = query.get("state", "")
        with self._lock:
            expiry = self._states.pop(state, 0.0)
        if not state or expiry < time.time():
            raise Refused("this sign-in link is not one this server started, or it "
                          "has expired; press Connect YouTube again")
        if query.get("error"):
            raise Refused(f"Google did not grant access ({query['error']})")
        code = query.get("code", "")
        if not code:
            raise Refused("Google sent no authorisation code back")
        client_id, secret = self._client()
        try:
            granted = upload.exchange_code(code, client_id, secret, redirect_uri)
            upload.save_grant(self.repo_root, granted)
        except upload.UploadFailed as exc:
            raise Refused(str(exc)) from exc
        return "YouTube is connected. You can close this tab and upload from vidsmith."

    # -- the upload ---------------------------------------------------------- #
    def start(self, job: Any, privacy: str, save: Callable[[Any], None]) -> Dict[str, Any]:
        """Check a finished render and put its upload on a thread.

        Refused before anything leaves the box when the delivery has problems,
        because everything `check` looks for is worse once published, and taking
        a video down does not unpublish it. A render already on YouTube is not
        sent twice.
        """
        if privacy not in PRIVACY:
            raise Refused(f"visibility must be one of {', '.join(PRIVACY)}")
        if job.status != "done" or job.root is None:
            raise Refused("only a finished render can be uploaded")
        state = job.youtube.get("status")
        if state == "uploading":
            raise Refused("this render is already uploading")
        if state == "done":
            raise Refused("this render is already on YouTube")
        if not self.status()["configured"]:
            raise Refused("YouTube is not set up on this instance")
        if not upload.connected(self.repo_root):
            raise Refused("connect YouTube first")

        out = job.root / "out"
        problems = check(out)
        if problems:
            raise Refused("the delivery has problems, so nothing was uploaded: "
                          + "; ".join(problems[:3]))
        files = self._files(out, job.options.get("aspect", ""))

        job.youtube = {"status": "uploading", "privacy": privacy, "progress": 0.0,
                       "started": time.time()}
        save(job)
        # a copy taken before the thread starts: the worker updates job.youtube
        # in place, and the response is serialised after this returns
        started = dict(job.youtube)
        threading.Thread(target=self._run, args=(job, files, privacy, save),
                         daemon=True).start()
        return started

    @staticmethod
    def _files(out: Path, aspect: str) -> Dict[str, Optional[Path]]:
        """The cut and the files that belong to it, all by the same tag."""
        cuts = delivered(out)
        cut = next((p for a, p in cuts if a == aspect), None) or (cuts[0][1] if cuts else None)
        if cut is None:
            raise Refused("this render delivered no video")
        shape = next(a for a, p in cuts if p == cut)
        tag = aspect_tag(shape)
        description = out / f"description{tag}.txt"
        meta = out / "youtube.json"
        if not description.exists() or not meta.exists():
            raise Refused("this render has no description to upload with; it was "
                          "built without a Gemini key")
        srt, jpg = out / f"captions{tag}.srt", cut.with_suffix(".jpg")
        return {"cut": cut, "description": description, "meta": meta,
                "captions": srt if srt.exists() else None,
                "thumbnail": jpg if jpg.exists() else None, "tag": tag}

    def _run(self, job: Any, files: Dict[str, Any], privacy: str,
             save: Callable[[Any], None]) -> None:
        import json

        def log(line: str) -> None:
            found = re.search(r"(\d+)% of", str(line))
            if found:
                job.youtube["progress"] = int(found.group(1)) / 100
            # The id is known the moment the video is placed, before the
            # thumbnail and the captions. Kept here, so a failure after it is
            # not mistaken for a video that never went up and sent twice.
            placed = re.search(r"watch\?v=([\w-]+)", str(line))
            if placed:
                job.youtube["video_id"] = placed.group(1)

        try:
            client_id, secret = self._client()
            token = upload.access_token(self.repo_root, client_id, secret,
                                        log=log, interactive=False)
            meta = json.loads(files["meta"].read_text(encoding="utf-8"))
            vid = upload.publish(files["cut"], meta,
                                 files["description"].read_text(encoding="utf-8"),
                                 token, thumbnail=files["thumbnail"],
                                 captions=files["captions"], privacy=privacy, log=log)
            write_receipt(job.root / "out", vid, tag=files["tag"])
            job.youtube.update(status="done", video_id=vid, progress=1.0,
                               url=f"https://youtu.be/{vid}",
                               studio=f"https://studio.youtube.com/video/{vid}/edit",
                               finished=time.time())
        except upload.NotConnected as exc:
            job.youtube.update(status="failed", error=str(exc), reconnect=True)
        except Exception as exc:              # the network or YouTube, anywhere
            vid = job.youtube.get("video_id", "")
            if vid:
                # on YouTube, but without its thumbnail or its caption track:
                # uploading again would make a second video, so say what is left
                job.youtube.update(status="done", progress=1.0, warning=str(exc)[:500],
                                   url=f"https://youtu.be/{vid}",
                                   studio=f"https://studio.youtube.com/video/{vid}/edit",
                                   finished=time.time())
            else:
                job.youtube.update(status="failed", error=str(exc)[:500])
        finally:
            save(job)
