"""The viewer's own photos and videos, held until a render asks for them.

A file arrives on its own request (a raw body, so no multipart dependency) and
is checked before it is kept: an image has to open, a video has to have a
duration. A render that hit a corrupt file would fail at the encode, minutes
in, with an ffmpeg error nobody can act on; refusing at the door is one line.
Files wait under `<jobs>/_uploads/<id>/` and are copied into a job's
`assets/mine` when it is submitted, so a render never depends on a file that a
later sweep could remove.
"""
from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from vidsmith import ffmpeg_util as ff
from vidsmith.visuals import IMAGE_EXT, VIDEO_EXT

DIR = "_uploads"          # leading underscore: the orphan sweep leaves it alone
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 150 * 1024 * 1024
MAX_FILES = 20
MAX_TOTAL_BYTES = 400 * 1024 * 1024


class Refused(ValueError):
    """A file that cannot be used, with a reason a person can act on."""


def safe_name(raw: str) -> str:
    """A file name with no path, no odd characters, and an extension kept."""
    name = re.sub(r"[^\w.\- ]", "_", Path(raw or "").name.strip()).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return ""
    return f"{stem[:60]}.{ext.lower()}"


def limit_for(name: str) -> int:
    return MAX_VIDEO_BYTES if Path(name).suffix.lower() in VIDEO_EXT else MAX_IMAGE_BYTES


class Uploads:
    def __init__(self, workdir: Path, keep_seconds: float):
        self.root = workdir / DIR
        self.keep = keep_seconds

    def begin(self, raw_name: str) -> tuple[str, Path]:
        """Where a file will be written, or Refused for a name that is no use."""
        name = safe_name(raw_name)
        ext = Path(name).suffix.lower()
        if not name or ext not in VIDEO_EXT | IMAGE_EXT:
            allowed = ", ".join(sorted(e.lstrip(".") for e in VIDEO_EXT | IMAGE_EXT))
            raise Refused(f"'{raw_name}' is not a photo or video vidsmith can use "
                          f"({allowed})")
        self.sweep()
        upload_id = uuid.uuid4().hex[:12]
        folder = self.root / upload_id
        folder.mkdir(parents=True)          # the first upload makes the root too
        return upload_id, folder / name

    def finish(self, upload_id: str, path: Path) -> Dict[str, object]:
        """Check what arrived; remove it and refuse if it is not what it says."""
        try:
            if path.suffix.lower() in VIDEO_EXT:
                if not ff.duration(path):
                    raise Refused(f"'{path.name}' is not a video ffmpeg can read")
                kind = "video"
            else:
                try:
                    with Image.open(path) as im:
                        im.verify()
                except Exception:
                    raise Refused(f"'{path.name}' is not an image that opens")
                kind = "image"
        except Refused:
            self.discard(upload_id)
            raise
        except Exception as exc:
            self.discard(upload_id)
            raise Refused(f"'{path.name}' could not be read: {exc}")
        return {"id": upload_id, "name": path.name, "kind": kind,
                "bytes": path.stat().st_size}

    def discard(self, upload_id: str) -> None:
        if re.fullmatch(r"[0-9a-f]{12}", upload_id or ""):
            shutil.rmtree(self.root / upload_id, ignore_errors=True)

    def find(self, upload_id: str) -> Optional[Path]:
        if not re.fullmatch(r"[0-9a-f]{12}", upload_id or ""):
            return None
        files = [p for p in (self.root / upload_id).glob("*") if p.is_file()]
        return files[0] if files else None

    def sweep(self) -> None:
        cutoff = time.time() - self.keep
        for folder in self.root.glob("*"):
            try:
                if folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                continue

    def stage(self, ids: List[str], dest: Path) -> List[str]:
        """Copy the named uploads into `dest`; the names they now carry, in order.

        Two uploads can share a name (every phone calls its photos IMG_0001), so
        a repeat is numbered rather than allowed to overwrite the first.
        """
        if len(ids) > MAX_FILES:
            raise Refused(f"at most {MAX_FILES} files per video")
        dest.mkdir(parents=True, exist_ok=True)
        names: List[str] = []
        total = 0
        for upload_id in ids:
            src = self.find(upload_id)
            if src is None:
                raise Refused("an uploaded file has expired; add it again")
            total += src.stat().st_size
            if total > MAX_TOTAL_BYTES:
                raise Refused("those files add up to more than "
                              f"{MAX_TOTAL_BYTES // 2**20} MB")
            name, n = src.name, 1
            while (dest / name).exists():
                n += 1
                name = f"{src.stem}-{n}{src.suffix}"
            shutil.copy2(src, dest / name)
            names.append(name)
        return names


def spread(script: str, names: List[str]) -> str:
    """Give each scene some of `names`, in order, unless the script already says.

    Someone who uploads photos and writes no `[media:]` line wants to see them,
    not to learn a directive first. A script that names any file is left alone:
    that person is placing them on purpose.
    """
    from vidsmith import script_parser

    if not names:
        return script
    _, scenes, spans = script_parser._parse(script)
    if any(s.media for s in scenes) or not scenes:
        return script
    lines = script.splitlines()
    total, n = len(scenes), len(names)
    for i in range(total - 1, -1, -1):
        lo, hi = i * n // total, (i + 1) * n // total
        chunk = names[lo:hi] or [names[min(lo, n - 1)]]
        first = spans[i][0]
        lines.insert(first, f"[media: {', '.join(chunk)}]")
    return "\n".join(lines) + "\n"
