"""Pull thumbnail candidates out of a finished build.

Frames are sampled from the picture track, not the delivery file, because the
delivery file has captions, watermark and progress bar burned into it - none of
which belong on a thumbnail. Candidates are then ranked on how much detail and
colour they carry, and spread across the runtime so six of them are not six
frames of the same shot.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from io import BytesIO

from . import cards
from . import llm
from . import ffmpeg_util as ff
from .theme import Theme, hex_rgb

# skip the generated title and end cards; they are already designed frames
HEAD_SKIP = 0.06
TAIL_SKIP = 0.10


@dataclass
class Candidate:
    path: Path
    time: float
    score: float
    sharpness: float
    colour: float


def _sample(video: Path, workdir: Path, samples: int) -> List[Tuple[Path, float]]:
    """Dump frames at known timestamps, one seek each.

    A single fps-filtered pass is faster but the mapping from output file to
    source time is only an assumption, and it was wrong: ffmpeg emitted more
    frames than the span implied, so labels ran past the end of the sampled
    window and a frame of the closing card came back tagged as mid-scene. The
    timestamp matters here - it decides whether a frame counts as a diagram - so
    each one is asked for explicitly and is exactly what it says it is.
    """
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    duration = ff.duration(video)
    start = duration * HEAD_SKIP
    span = duration * (1 - HEAD_SKIP - TAIL_SKIP)
    if span <= 0 or samples < 1:
        return []
    step = span / max(1, samples - 1) if samples > 1 else 0.0

    out: List[Tuple[Path, float]] = []
    for i in range(samples):
        when = start + i * step
        dest = workdir / f"frame_{i:03d}.jpg"
        try:
            ff.run(["-ss", f"{when:.3f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "2", str(dest)])
        except RuntimeError:
            continue
        if dest.exists():
            out.append((dest, when))
    return out


def _score(path: Path) -> Tuple[float, float, float]:
    """(score, sharpness, colour) for one frame.

    Sharpness is the spread of an edge-detect pass - a blurred or motion-smeared
    frame has almost none. Colour is the average per-channel spread. Exposure
    only ever subtracts: a crushed or blown frame is disqualified, not rewarded.
    """
    img = Image.open(path).convert("RGB")
    small = img.resize((480, int(480 * img.height / img.width)), Image.BILINEAR)

    edges = small.convert("L").filter(ImageFilter.FIND_EDGES)
    sharpness = ImageStat.Stat(edges).stddev[0]

    stat = ImageStat.Stat(small)
    colour = sum(stat.stddev) / 3.0
    brightness = sum(stat.mean) / 3.0

    penalty = 0.0
    if brightness < 40:
        penalty = (40 - brightness) * 1.5
    elif brightness > 220:
        penalty = (brightness - 220) * 1.5

    return sharpness * 1.6 + colour - penalty, sharpness, colour


def rank(video: Path, workdir: Path, count: int = 6,
         samples: int = 40, spread: float = 2.5,
         include: Sequence[Tuple[float, float]] = ()) -> List[Candidate]:
    """Best `count` frames, one from each slice of the runtime.

    Taking the top scores outright does not work: the cleanest, best-lit frames
    cluster in whichever shots are static and well exposed, so a 142 second
    video returned six candidates spanning its last 25 seconds, all of them the
    same desk-and-coffee stock footage. Scoring picks the best frame within a
    slice; the slices guarantee the video is actually covered.
    """
    scored: List[Candidate] = []
    for path, when in _sample(video, workdir, samples):
        score, sharp, colour = _score(path)
        scored.append(Candidate(path, when, score, sharp, colour))
    if not scored:
        return []

    first, last = min(c.time for c in scored), max(c.time for c in scored)
    span = max(0.001, last - first)
    width = span / max(1, count)

    picked: List[Candidate] = []
    for i in range(count):
        lo = first + i * width
        hi = lo + width if i < count - 1 else last + 1
        slice_ = [c for c in scored if lo <= c.time < hi]
        if slice_:
            picked.append(max(slice_, key=lambda c: c.score))

    # Drawn scenes are flat and low contrast, so _score ranks them below stock
    # footage every time and they never survive the slice pick - on one video not
    # a single candidate fell inside a diagram. They are the frames most likely
    # to be about the subject, so each drawn range is guaranteed a candidate.
    for lo, hi in include:
        if any(lo <= c.time < hi for c in picked):
            continue
        inside = [c for c in scored if lo <= c.time < hi]
        if inside:
            picked.append(max(inside, key=lambda c: c.score))

    # a short video may leave slices empty; backfill on score, keeping them apart
    if len(picked) < count:
        for cand in sorted(scored, key=lambda c: -c.score):
            if len(picked) >= count:
                break
            if all(abs(cand.time - p.time) >= spread for p in picked):
                picked.append(cand)

    return sorted(picked, key=lambda c: -c.score)


def _thumb_bytes(path: Path, width: int = 384) -> bytes:
    img = Image.open(path).convert("RGB")
    img.thumbnail((width, width), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=74)
    return buf.getvalue()


def choose(video: Path, workdir: Path, title: str, hook: str = "",
           api_key: str = "", pool: int = 6, log=print,
           include: Sequence[Tuple[float, float]] = ()) -> Candidate:
    """The frame that best represents the video, not merely the sharpest one.

    Scoring narrows forty samples to a handful of technically usable frames;
    the model then picks which of those is actually about the subject. Without a
    key the top-scoring frame is used, which is the old behaviour.
    """
    best = rank(video, workdir, count=pool, include=include)
    if not best:
        raise RuntimeError(f"no frames could be sampled from {video}")
    if not api_key or len(best) == 1:
        return best[0]

    try:
        drawn = [i for i, c in enumerate(best)
                 if any(lo <= c.time < hi for lo, hi in include)]
        pick, why = llm.pick_thumbnail(title, hook,
                                       [_thumb_bytes(c.path) for c in best],
                                       api_key, drawn=drawn)
    except Exception as exc:
        log(f"         thumbnail pick skipped ({exc}); using the sharpest frame")
        return best[0]

    chosen = best[pick]
    if pick != 0:
        log(f"         thumbnail: the {chosen.time:.0f}s frame over the sharpest"
            + (f" ({why})" if why else ""))
    return chosen


def _wrap_lines(draw, text: str, font, max_w: float) -> List[str]:
    lines: List[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def titled(frame: Path, out: Path, title: str, theme: Theme,
           size: Optional[Tuple[int, int]] = None) -> Path:
    """Compose a designed thumbnail: the frame, dimmed, with the title on it."""
    img = Image.open(frame).convert("RGB")
    if size:
        img = img.resize(size, Image.LANCZOS)
    w, h = img.size

    # darken the lower half so type has something to sit on
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    band = int(h * 0.62)
    col = Image.new("RGBA", (1, band))
    for i in range(band):
        t = i / max(1, band - 1)
        col.putpixel((0, i), (0, 0, 0, int(215 * (t ** 1.5))))
    scrim.paste(col.resize((w, band), Image.BICUBIC), (0, h - band))
    img = Image.alpha_composite(img.convert("RGBA"), scrim).convert("RGB")

    draw = ImageDraw.Draw(img)
    margin = int(w * 0.06)
    text = cards.trim(title, 52)
    fsize = int(w * (0.115 if len(text) < 26 else 0.088))
    font = cards.font(theme.headline_file, fsize)

    lines = _wrap_lines(draw, text, font, w - margin * 2)[:3]

    # A two line title at full size reaches a third of the way up the frame and
    # lands on whatever the picture was showing - on a diagram frame the accent
    # bar cut straight through a labelled box. Multi-line titles get smaller so
    # the whole block stays inside the bottom third.
    if len(lines) > 1:
        fsize = int(fsize * (0.80 if len(lines) == 2 else 0.66))
        font = cards.font(theme.headline_file, fsize)
        lines = _wrap_lines(draw, text, font, w - margin * 2)[:3]

    line_h = int(fsize * 1.12)
    y = h - margin - line_h * len(lines)

    bar_h = max(6, int(h * 0.014))
    draw.rectangle([margin, y - int(fsize * 0.42), margin + int(w * 0.16),
                    y - int(fsize * 0.42) + bar_h], fill=hex_rgb(theme.accent))

    for line in lines:
        off = max(3, int(w * 0.003))
        draw.text((margin + off, y + off), line, font=font, fill=(0, 0, 0))
        draw.text((margin, y), line, font=font, fill=hex_rgb(theme.text))
        y += line_h

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)
    return out


def extract(video: Path, out_dir: Path, workdir: Path, title: str, theme: Theme,
            count: int = 6, with_title: bool = True, log=print) -> List[Path]:
    """Write ranked candidates, and one composed thumbnail from the best frame."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.jpg"):
        stale.unlink(missing_ok=True)

    best = rank(video, workdir, count=count)
    if not best:
        raise RuntimeError(f"no frames could be sampled from {video}")

    written: List[Path] = []
    for i, cand in enumerate(best, start=1):
        dest = out_dir / f"{i:02d}_at_{cand.time:0.0f}s.jpg"
        img = Image.open(cand.path).convert("RGB")
        # YouTube wants 1280x720; anything portrait keeps its own shape
        if img.width >= img.height and img.width > 1280:
            img = img.resize((1280, round(1280 * img.height / img.width)), Image.LANCZOS)
        img.save(dest, quality=92)
        written.append(dest)
        log(f"  {i:>2}. {cand.time:6.1f}s  score {cand.score:6.1f}  "
            f"(detail {cand.sharpness:.1f}, colour {cand.colour:.1f})")

    if with_title:
        composed = out_dir / "titled.jpg"
        target = (1280, 720) if Image.open(best[0].path).width >= Image.open(
            best[0].path).height else None
        titled(best[0].path, composed, title, theme, target)
        written.append(composed)
        log(f"  titled  {composed.name}  from the {best[0].time:.1f}s frame")

    shutil.rmtree(workdir, ignore_errors=True)
    return written
