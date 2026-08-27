"""Locating and driving ffmpeg/ffprobe."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

_CACHE: dict[str, str] = {}

# winget's Gyan.FFmpeg drops binaries here but only updates PATH for new shells,
# so an already-running session has to look for them.
_WINGET_HINTS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
]


REPO_ROOT = Path(__file__).resolve().parent.parent


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


def run(args: List[str], quiet: bool = True) -> subprocess.CompletedProcess:
    """Run ffmpeg with the given args (binary and -y are prepended)."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-25:]
        raise RuntimeError(
            "ffmpeg failed:\n  " + " ".join(cmd[:14]) + " ...\n" + "\n".join(tail)
        )
    return proc


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            ffprobe_bin(), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True,
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


# A filtergraph path is parsed by two layers whose escaping rules have moved
# between ffmpeg builds, and the apostrophe is where they disagree: the spelling
# that works on the Windows and Debian builds is rejected by Homebrew's newer
# one, which reads the same bytes and reports "No option name near". Rather than
# hunt for a spelling that satisfies every build, hand the parser a path that
# has nothing in it to argue about.
UNSAFE_IN_FILTERGRAPH = set("'\[],;:=")


def filtergraph_safe(path: Path, staging: Path) -> Path:
    """A path ffmpeg's filtergraph can take, copying the file if it must.

    Only the characters the parser fights over matter; a drive colon is fine
    because escape_filter_path handles it, so it is not counted here.
    """
    resolved = path.resolve()
    awkward = set(resolved.parent.as_posix()) & (UNSAFE_IN_FILTERGRAPH - {":"})
    if not awkward:
        return resolved

    staging.mkdir(parents=True, exist_ok=True)
    if set(staging.resolve().as_posix()) & (UNSAFE_IN_FILTERGRAPH - {":"}):
        # nowhere better to put it; the escaping is still the best we have
        return resolved
    dest = staging / resolved.name
    shutil.copy2(resolved, dest)
    return dest.resolve()


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
