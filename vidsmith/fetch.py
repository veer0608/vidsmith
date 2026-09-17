"""Bring a finished render down off the live instance.

Three downloads died mid-file in one evening on a home connection that drops,
and the render itself was fine every time: the server had the mp4, the laptop
had half of it. `requests` raises rather than retrying, so finished work sat
there unreachable. This waits for the job, downloads each file with retries,
and only ever writes whole files.

The token is the *instance's*, which is not the one a local `.env` holds: pass
it in, or export `VIDSMITH_TOKEN` for the box being fetched from.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

TIMEOUT = (20, 300)          # connect, read: a 20 MB mp4 on a slow line
TRIES = 5
PAUSE = 6.0
POLL = 15.0
RUNNING = ("queued", "running")

Get = Callable[..., Any]
Sleep = Callable[[float], None]


class FetchFailed(RuntimeError):
    pass


def _retrying(get: Get, url: str, headers: Dict[str, str], sleep: Sleep,
              tries: int = TRIES) -> Any:
    """A GET that survives a connection this house drops every few minutes."""
    last = ""
    for attempt in range(tries):
        try:
            r = get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code == 404:
                raise FetchFailed(f"{url} is not there (404)")
            if r.status_code == 401:
                raise FetchFailed("the instance refused the token; it is the box's "
                                  "own VIDSMITH_TOKEN, not the one in a local .env")
            if r.status_code != 200:
                last = f"status {r.status_code}"
            else:
                return r
        except requests.RequestException as exc:
            last = type(exc).__name__
        if attempt < tries - 1:
            sleep(PAUSE)
    raise FetchFailed(f"gave up on {url} after {tries} tries ({last})")


def fetch(job_id: str, host: str, token: str, out_dir: Path,
          log: Callable[[str], None] = print, get: Get = requests.get,
          sleep: Sleep = time.sleep, wait: bool = True) -> List[Path]:
    """Download a job's files into `out_dir`, waiting for it to finish first."""
    base = f"https://{host}"
    headers = {"X-Vidsmith-Token": token} if token else {}

    while True:
        job = _retrying(get, f"{base}/api/jobs/{job_id}", headers, sleep).json()
        status = job.get("status", "")
        if status not in RUNNING:
            break
        if not wait:
            raise FetchFailed(f"the render is {status}; it has nothing to download yet")
        log(f"  {status}: {job.get('stage') or 'working'}")
        sleep(POLL)

    if status != "done":
        raise FetchFailed(f"the render is {status}: {job.get('error') or 'no reason given'}")

    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for entry in job.get("outputs") or []:
        name = entry.get("name") or ""
        if not name or "/" in name or "\\" in name:
            continue                      # the server names these; never build a path
        body = _retrying(get, f"{base}/api/jobs/{job_id}/files/{name}",
                         headers, sleep).content
        # written whole, then moved, so a dropped connection cannot leave a
        # half file that looks like a finished one
        part = out_dir / f".{name}.part"
        part.write_bytes(body)
        final = out_dir / name
        part.replace(final)
        written.append(final)
        log(f"  {name}  {len(body) / 1e6:.1f} MB")
    if not written:
        raise FetchFailed(f"the render is done but listed no files")
    return written


def job_token(explicit: str, env: Callable[[str], str]) -> str:
    return (explicit or env("VIDSMITH_TOKEN") or "").strip().strip('"')


def default_out(job_id: str) -> Path:
    return Path("jobs") / job_id[:12]
