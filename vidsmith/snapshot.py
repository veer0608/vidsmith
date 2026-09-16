"""Copy part of a finished build aside, and put it back if a change fails.

A change to a finished render - a new clip under one shot, new words in one
scene - rewrites the delivery in place, and the master pass writes the mp4 as
it goes. Stopped or failed halfway, it would leave half of a new video where a
finished one was. So the paths a change touches are copied to `.backup/` in the
job first, restored on any way out that is not success, and removed on success.
A backup still present at startup means the process died during a change, and
`restore()` is what finishes it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Sequence

BACKUP = ".backup"
LIST = "paths.json"


def take(root: Path, paths: Sequence[str]) -> Path:
    """Copy each path under `root` aside. Missing paths are recorded as missing."""
    root = Path(root)
    backup = root / BACKUP
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True)
    held = []
    for rel in paths:
        src = root / rel
        dest = backup / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        elif src.exists():
            shutil.copy2(src, dest)
        held.append({"path": rel, "existed": src.exists()})
    # written last, so a backup without its list is one that never finished
    (backup / LIST).write_text(json.dumps(held, indent=2), encoding="utf-8")
    return backup


def restore(root: Path) -> bool:
    """Put back what `take` copied, and remove the copy. True when there was one."""
    root = Path(root)
    backup = root / BACKUP
    if not backup.is_dir():
        return False
    try:
        held = json.loads((backup / LIST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # the copy was never finished, so nothing was changed under it yet
        held = []
    for item in held:
        rel = item["path"]
        target, saved = root / rel, backup / "files" / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
        if not item.get("existed"):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if saved.is_dir():
            shutil.copytree(saved, target)
        elif saved.exists():
            shutil.copy2(saved, target)
    shutil.rmtree(backup, ignore_errors=True)
    return True


def discard(root: Path) -> None:
    shutil.rmtree(Path(root) / BACKUP, ignore_errors=True)
