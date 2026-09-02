"""Locating and driving ffmpeg/ffprobe."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

_CACHE: dict[str, str] = {}

# `-progress` writes plain key=value lines. `out_time` is the one worth keeping;
# the rest is stripped out of ordinary failure messages so it cannot drown them.
_OUT_TIME = re.compile(r"^out_time=(\S+)", re.M)
_PROGRESS_KEY = re.compile(
    r"^(frame|fps|stream_\d+_\d+_q|bitrate|total_size|out_time\w*|dup_frames"
    r"|drop_frames|speed|progress)=")

# winget's Gyan.FFmpeg drops binaries here but only updates PATH for new shells,
# so an already-running session has to look for them.
_WINGET_HINTS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
]


REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TIMEOUT = 900.0


def timeout_limit() -> float:
    """The bound on one ffmpeg call, read when it is needed.

    Generous on purpose: a 1080p master of a long script on a slow free
    instance is minutes of honest work, and killing that would be worse than
    the hang it guards against. It is a bound on forever, not a performance
    budget.

    Read at call time rather than kept as a module constant, because a constant
    read at import can only be tested by reloading the module, and
    `importlib.reload` is not something monkeypatch undoes. Two tests here
    reloaded to check the default and the override, and reloaded again to put
    it back - while monkeypatch was still active, so they restored the module
    against the *patched* environment and left it holding 900. Every test file
    sorting after `test_filter_paths` then ran with 900 no matter what the
    environment said. CI sets 45 exactly so this guard fires inside pytest's
    120s limit and prints what ffmpeg said; at 900 pytest always won, and the
    macOS narration hang was reported twice as a stack trace through subprocess
    with nothing from ffmpeg in it. The guard was never broken. It was never
    reached.
    """
    return float(os.environ.get("VIDSMITH_FFMPEG_TIMEOUT", DEFAULT_TIMEOUT))


def _resolve(name: str) -> str:
    if name in _CACHE:
        return _CACHE[name]

    # an explicit path wins: hosts without a package manager fetch a static
    # build at deploy time and point at it
    override = os.environ.get(f"{name.upper()}_BINARY")
    if override and Path(override).exists():
        _CACHE[name] = override
        return override

    local = REPO_ROOT / "bin" / name
    for candidate in (local, local.with_suffix(".exe")):
        if candidate.exists():
            _CACHE[name] = str(candidate)
            return str(candidate)

    found = shutil.which(name)
    if not found:
        for hint in _WINGET_HINTS:
            if not hint.exists():
                continue
            for cand in hint.rglob(f"{name}.exe"):
                found = str(cand)
                break
            if found:
                break
    if not found:
        raise RuntimeError(
            f"{name} not found. Install it with:  winget install Gyan.FFmpeg\n"
            "then open a new terminal so PATH picks it up."
        )
    _CACHE[name] = found
    return found


def ffmpeg_bin() -> str:
    return _resolve("ffmpeg")


def ffprobe_bin() -> str:
    return _resolve("ffprobe")


def _text(raw) -> str:
    """`TimeoutExpired` hands back bytes or str depending on how it was raised."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw


def last_progress(text: str) -> Optional[str]:
    """The last position `-progress` reported, or None if it never reported one.

    This is the entire reason `-progress pipe:1` is on every call. Everything
    here runs at `-loglevel error`, where a healthy ffmpeg prints nothing at
    all, so an empty capture from a killed process was consistent with both a
    process that hung before it started and one that stopped halfway. Three
    macOS hangs were reported as "it said nothing at all before it was killed",
    which read like evidence and was not.

    `out_time` is written regardless of the log level. No lines means it never
    got going; a value that stops means it stopped there.
    """
    found = _OUT_TIME.findall(text or "")
    return found[-1] if found else None


def _without_progress(text: str) -> str:
    """Progress belongs in the hang report, not in an ordinary failure."""
    return "\n".join(line for line in (text or "").splitlines()
                     if not _PROGRESS_KEY.match(line))


def run(args: List[str], quiet: bool = True,
        timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    """Run ffmpeg with the given args (binary and -y are prepended).

    The timeout is not a nicety. ffmpeg can sit forever on a filtergraph that
    never decides it is finished, and with no bound the call never returns: the
    web service holds its single render slot for good, and CI reported nothing
    for twenty-five minutes. A killed encode raises like any other failure, so
    every caller that already handles a broken render handles this too.

    `-progress pipe:1` is what makes a timeout worth reading. It costs a line
    every half second on stdout, which nothing here writes media to, and it is
    the difference between "it said nothing" and "it stopped at 12 seconds".
    """
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    limit = timeout_limit() if timeout is None else timeout
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired as expired:
        # Whatever ffmpeg managed to say before it was killed is the only
        # evidence there is about where it stopped. Discarding it left a macOS
        # hang with nothing but a stack trace through subprocess, twice.
        said = _text(expired.stderr)
        tail = "\n".join(_without_progress(said).strip().splitlines()[-25:])
        reached = last_progress(_text(expired.stdout))
        raise RuntimeError(
            f"ffmpeg did not finish within {limit:g}s and was stopped:\n  "
            + " ".join(cmd[:16]) + " ...\n"
            "This is a hang rather than a slow encode; the filtergraph is the "
            "place to look. Raise VIDSMITH_FFMPEG_TIMEOUT if the encode really "
            "is this long.\n"
            + (f"it reached out_time={reached} before it was killed\n" if reached
               else "it never reported any progress, so it had not begun "
                    "encoding\n")
            + (f"what it said before it was killed:\n{tail}" if tail
               else "it wrote nothing to stderr, which at -loglevel error is "
                    "also what a healthy run does"))
    if proc.returncode != 0:
        tail = _without_progress(
            proc.stderr or proc.stdout or "").strip().splitlines()[-25:]
        raise RuntimeError(
            "ffmpeg failed:\n  " + " ".join(cmd[:16]) + " ...\n" + "\n".join(tail)
        )
    return proc


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            ffprobe_bin(), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        # reading a header should be instant; a minute means the file is a pipe,
        # a dead network mount, or truncated mid-write by another build
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    info = probe(path)
    d = info.get("format", {}).get("duration")
    if d:
        return float(d)
    for s in info.get("streams", []):
        if s.get("duration"):
            return float(s["duration"])
    return 0.0


def video_stream(path: Path) -> Optional[dict]:
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


_FILTERS: Optional[set] = None


def filters() -> set:
    """Every filter this ffmpeg was built with.

    Worth asking rather than assuming. Homebrew's ffmpeg 8.1.2 is built without
    libass, so `subtitles` is simply absent, and the parser's answer to that is
    "No option name near <path>" - which reads like a quoting fault and sent a
    whole afternoon into escaping rules that were never wrong.
    """
    global _FILTERS
    if _FILTERS is None:
        proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"],
                              capture_output=True, text=True)
        _FILTERS = {line.split()[1] for line in proc.stdout.splitlines()
                    if line.startswith(" ") and len(line.split()) > 2}
    return _FILTERS


def require_filter(name: str) -> None:
    if name in filters():
        return
    raise RuntimeError(
        f"this ffmpeg has no '{name}' filter, so captions cannot be burned in. "
        f"It was built without the library that provides it: {ffmpeg_bin()}. "
        "A full build (evermeet.cx on macOS, or a static build) has it."
    )


def escape_filter_path(path: Path) -> str:
    """Escape a path for use inside a single-quoted filtergraph option.

    Callers wrap the result in single quotes, as `subtitles='...'` does. Two
    layers of ffmpeg parsing sit under that, and each eats something different:

      the drive colon  separates options at the filter level, so it needs `\\:`
      an apostrophe    ends the filtergraph's quoted section

    The apostrophe therefore has to satisfy both layers at once: `\\'\\''` leaves
    a literal backslash inside the quotes, closes them, escapes a quote outside
    them, and reopens - so the filter level receives `\\'` and reads one quote.
    Every simpler spelling was tried against real ffmpeg and silently dropped
    the character instead, turning `O'Brien` into `OBrien` and failing to open
    the file. The previous `.replace("'", "\\'")` here was a no-op: in Python
    that string is just an apostrophe, so it replaced the character with itself.
    """
    p = str(path.resolve()).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'\\''")


def escape_concat_path(path: Path) -> str:
    """Escape a path for a `file '...'` line in a concat demuxer list.

    One layer of escaping, not the two `escape_filter_path` needs: the demuxer
    reads the list file itself and no filter-option parser sits underneath it,
    so the drive colon is safe and only the apostrophe has to close the quoted
    section, escape itself and reopen. Left unescaped it ended the quoted
    section early and ffmpeg reported `Impossible to open` against a path with
    the character missing.
    """
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
