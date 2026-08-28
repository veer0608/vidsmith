"""Check a finished build before it is published.

Everything here compares one delivered file against another. That is deliberate:
the faults this catches were all found by reading outputs after a run, never by
reading the code that wrote them. A thumbnail was refreshed and the description
beside it went on crediting the photographer that had been dropped; a refresh
wrote `untitled.jpg` next to correctly named files and reported success. Both
looked right in isolation and wrong side by side.

Nothing here calls a model or the network, so it costs nothing and works on a
day the quota is gone.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

from . import ffmpeg_util as ff

_END = re.compile(r"--> (\d+:\d+:\d+[,.]\d+)")


def seconds(stamp: str) -> float:
    """Accept both a chapter's `1:23` and an SRT's `00:01:23,400`."""
    parts = [float(x) for x in stamp.replace(",", ".").split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def check(out_dir: Path) -> List[str]:
    """Everything wrong with this delivery, as plain sentences."""
    out = Path(out_dir)
    problems: List[str] = []

    wide = [p for p in sorted(out.glob("*.mp4")) if "9x16" not in p.name]
    if not wide:
        return ["no widescreen mp4 in out/; nothing has been delivered"]
    wide = wide[0]
    shorts = sorted(out.glob("*9x16.mp4"))
    runtime = ff.duration(wide)

    if shorts and abs(runtime - ff.duration(shorts[0])) > 1.0:
        problems.append(
            f"the two cuts disagree on length: {runtime:.0f}s and "
            f"{ff.duration(shorts[0]):.0f}s")

    meta_path = out / "youtube.json"
    desc_path = out / "description.txt"
    desc = desc_path.read_text(encoding="utf-8") if desc_path.exists() else ""
    if not desc:
        problems.append("description.txt is missing; run vidsmith meta")

    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        chapters = meta.get("chapters") or []
        # YouTube drops the entire list rather than the offending line
        if chapters and chapters[0].get("time") != "0:00":
            problems.append("the first chapter is not at 0:00, so YouTube will "
                            "ignore every chapter")
        for c in chapters:
            if seconds(c.get("time", "0:00")) >= runtime:
                problems.append(f"chapter '{c.get('label')}' is at {c.get('time')}, "
                                f"past the {runtime:.0f}s runtime")
            if desc and c.get("label") and c["label"] not in desc:
                problems.append(f"chapter '{c['label']}' is missing from "
                                "description.txt")

    for srt, cut in ((out / "captions.srt", wide),
                     *[(out / "captions-9x16.srt", s) for s in shorts]):
        if not srt.exists():
            problems.append(f"{srt.name} is missing")
            continue
        ends = _END.findall(srt.read_text(encoding="utf-8"))
        if ends and seconds(ends[-1]) > ff.duration(cut) + 0.5:
            problems.append(f"{srt.name} runs {seconds(ends[-1]):.1f}s, past the "
                            f"{ff.duration(cut):.1f}s of {cut.name}")

    for video, portrait in ((wide, False), *[(s, True) for s in shorts]):
        jpg = out / f"{video.stem}.jpg"
        if not jpg.exists():
            problems.append(f"{jpg.name} is missing, so {video.name} has no thumbnail")
            continue
        try:
            from PIL import Image

            w, h = Image.open(jpg).size
        except Exception as exc:                  # a corrupt jpg is the finding
            problems.append(f"{jpg.name} could not be read ({exc})")
            continue
        if portrait and w >= h:
            problems.append(f"{jpg.name} is {w}x{h}, landscape, but names a "
                            "vertical cut")
        if not portrait and h > w:
            problems.append(f"{jpg.name} is {w}x{h}, portrait, but names the "
                            "widescreen cut")

    # attribution is a licence condition, and description.txt is what gets
    # published: a credit that lives only in credits.txt has not been given
    for cf in sorted(out.glob("credits*.txt")):
        for line in cf.read_text(encoding="utf-8").splitlines():
            if line.startswith("Thumbnail:") and desc and line.strip() not in desc:
                problems.append(f"the thumbnail credit in {cf.name} is not in "
                                "description.txt, so it would not be published")

    # a thumbnail nothing delivers, left by a refresh that resolved the wrong name
    named = {v.stem for v in [wide, *shorts]}
    for jpg in sorted(out.glob("*.jpg")):
        if jpg.stem not in named:
            problems.append(f"{jpg.name} matches no delivered cut; a refresh "
                            "probably wrote it under the wrong title")

    return problems
