"""Which commit this process is actually running.

The live box had drifted six commits behind `main` and nothing said so.
`/healthz` reported ffmpeg and the bundled fonts, `/api/busy` reported the
queue, and both were happily green against stale code. "Did the deploy land" was
answerable only by SSHing in and running `git log`, which is the same shape as
the fault `deploy/aws.md` already warns about: a remote fix reports success and
the symptom does not move, because the change never reached the machine.

Read from `.git` directly rather than by shelling out to `git`. `/healthz` is
what an uptime check polls, so it must not spawn a process per request, and a
deployed box is not guaranteed to have git on PATH even when it was cloned with
it. The answer cannot change without a restart, so it is resolved once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parent.parent
_cached: Optional[str] = None
_resolved = False


def _read_head(repo: Path) -> str:
    head = repo / ".git" / "HEAD"
    if not head.is_file():
        return ""
    try:
        raw = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    if not raw.startswith("ref:"):
        return raw                      # a detached HEAD holds the sha itself

    ref = raw.split(" ", 1)[1].strip()
    loose = repo / ".git" / ref
    if loose.is_file():
        try:
            return loose.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # a freshly cloned checkout keeps its refs packed and has no loose file
    packed = repo / ".git" / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name.strip() == ref:
                    return sha.strip()
        except OSError:
            return ""
    return ""


def commit(short: bool = True) -> str:
    """The checked-out sha, or "" when this is not a git checkout.

    Empty rather than "unknown": a caller deciding whether to show the field at
    all should not have to know which sentinel string means absent.
    """
    global _cached, _resolved
    if not _resolved:
        sha = _read_head(_REPO)
        _cached = sha if all(c in "0123456789abcdef" for c in sha) and sha else ""
        _resolved = True
    if not _cached:
        return ""
    return _cached[:7] if short else _cached
