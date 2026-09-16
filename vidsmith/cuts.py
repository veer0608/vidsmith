"""Add another shape of a finished video: a Shorts version of a widescreen one.

Everything a second shape needs from the voice is already built - narration,
word timings, the beat searches - and the pipeline has always kept picture,
captions and the delivery per aspect. So a new cut costs vertical footage and
one encode, and it is the same `pipeline.build` with the aspect overridden,
never a second copy of the render stage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List

from . import pipeline, snapshot
from .check import delivered
from .config import ASPECTS, aspect_tag, load_config
from .retake import RetakeRefused


def shapes(root: Path) -> List[str]:
    """The aspects this render has delivered, widescreen first."""
    out = Path(root) / "out"
    return [aspect for aspect, _ in delivered(out)] if out.is_dir() else []


def check(root: Path, aspect: str) -> None:
    """Refuse a cut that cannot be added, before it waits in line."""
    root = Path(root)
    if aspect not in ASPECTS:
        raise RetakeRefused(f"a cut is one of {', '.join(sorted(ASPECTS))}")
    if not (root / "build" / "scenes.json").exists():
        raise RetakeRefused("this render kept no working files, so another shape "
                            "cannot reuse its narration; render the script again")
    if aspect in shapes(root):
        raise RetakeRefused(f"this render already has a {aspect} cut")


def touched(root: Path, aspect: str) -> List[str]:
    """What adding a cut can rewrite: the delivery, the shared timings, its picture."""
    tag = aspect_tag(aspect)
    return ["out", "build/scenes.json", f"build/visuals{tag}"]


def add(root: Path, aspect: str, log: Callable[[str], None] = print) -> Path:
    """Build `aspect` beside the cuts already delivered. Returns the new mp4.

    The main cut's shots are written out first when a render predates the
    per-cut shot record, because building a second shape rewrites the shared
    `scenes.json` with its own, and a later shot swap on the first cut would
    otherwise read the vertical cut's shots.
    """
    root = Path(root)
    check(root, aspect)
    cfg = load_config(root / "config.yaml")
    main_vis = root / "build" / f"visuals{aspect_tag(cfg.render.aspect)}"
    if main_vis.is_dir() and not (main_vis / pipeline.SHOTS).exists():
        pipeline.write_shots(main_vis, pipeline.load_scenes(root / "build" / "scenes.json"))

    log(f"cut      adding a {aspect} version from the same narration")
    snapshot.take(root, touched(root, aspect))
    try:
        final = pipeline.build(root, log=log, overrides={"aspect": aspect}, cut=True)
    except BaseException:
        snapshot.restore(root)
        raise
    snapshot.discard(root)
    return final
