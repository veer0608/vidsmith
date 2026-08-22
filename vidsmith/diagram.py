"""Drawn diagrams, for the scenes stock footage cannot serve.

A script about B-trees asks for "branching tree diagram" and every stock library
returns a photograph of a tree. No amount of reranking fixes that - the footage
does not exist. So those scenes get drawn instead.

Gemini writes a small JSON spec (this is text, not image generation - the free
tier has no image quota) and the drawing happens here, in the project's theme,
so a diagram frame sits beside the cards and the footage without looking pasted
in. Multi-shot scenes reveal the diagram progressively: the picture builds as
the narration explains it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from . import cards
from .theme import Theme, hex_rgb

KINDS = ("flow", "tree", "compare", "stack")
MAX_NODES = 8


@dataclass
class Spec:
    kind: str = "flow"
    title: str = ""
    nodes: List[str] = field(default_factory=list)
    groups: List[Dict[str, Any]] = field(default_factory=list)   # compare only

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Spec":
        kind = str(raw.get("kind", "flow")).lower()
        if kind not in KINDS:
            kind = "flow"
        nodes = [str(n).strip() for n in (raw.get("nodes") or []) if str(n).strip()]
        groups = []
        for group in (raw.get("groups") or [])[:2]:
            groups.append({
                "label": str(group.get("label", "")).strip(),
                "items": [str(i).strip() for i in (group.get("items") or [])
                          if str(i).strip()][:4],
            })
        return cls(kind=kind, title=str(raw.get("title", "")).strip(),
                   nodes=nodes[:MAX_NODES], groups=groups)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "nodes": self.nodes,
                "groups": self.groups}

    @property
    def elements(self) -> int:
        if self.kind == "compare":
            return sum(len(g["items"]) for g in self.groups) or 1
        return max(1, len(self.nodes))

    def is_drawable(self) -> bool:
        if self.kind == "compare":
            return len(self.groups) == 2 and all(g["items"] for g in self.groups)
        return len(self.nodes) >= 2


# --------------------------------------------------------------------------- #
# drawing primitives
# --------------------------------------------------------------------------- #
def _alpha(colour: Tuple[int, int, int], a: float) -> Tuple[int, int, int, int]:
    return (*colour, int(max(0.0, min(1.0, a)) * 255))


def _box(draw: ImageDraw.ImageDraw, rect: Tuple[float, float, float, float],
         theme: Theme, on: float, accent: bool = False) -> None:
    x0, y0, x1, y1 = rect
    radius = max(6, int((y1 - y0) * 0.16))
    fill = _alpha(hex_rgb(theme.bg_alt), 0.72 * on + 0.06)
    edge = _alpha(hex_rgb(theme.accent if accent else theme.muted), 0.95 * on + 0.10)
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=edge,
                           width=max(2, int((y1 - y0) * 0.035)))


def _label(draw: ImageDraw.ImageDraw, rect: Tuple[float, float, float, float],
           text: str, theme: Theme, on: float, size: int) -> None:
    x0, y0, x1, y1 = rect
    font = cards.font(theme.kicker_file, size)
    lines = _wrap_to(draw, text, font, (x1 - x0) * 0.86)
    line_h = int(size * 1.18)
    y = (y0 + y1) / 2 - line_h * len(lines) / 2
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(((x0 + x1 - w) / 2, y), line, font=font,
                  fill=_alpha(hex_rgb(theme.text), 0.97 * on + 0.10))
        y += line_h


def _wrap_to(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> List[str]:
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
    return lines[:3]


def _arrow(draw: ImageDraw.ImageDraw, start: Tuple[float, float],
           end: Tuple[float, float], theme: Theme, on: float, scale: float) -> None:
    colour = _alpha(hex_rgb(theme.accent), 0.85 * on + 0.08)
    draw.line([start, end], fill=colour, width=max(2, int(scale * 0.004)))
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head = scale * 0.014
    draw.polygon([
        end,
        (end[0] - head * math.cos(angle - 0.42), end[1] - head * math.sin(angle - 0.42)),
        (end[0] - head * math.cos(angle + 0.42), end[1] - head * math.sin(angle + 0.42)),
    ], fill=colour)


def _metrics(size: Tuple[int, int]) -> Tuple[int, int, bool, float, float]:
    """Frame geometry every layout shares.

    Sizes are keyed to frame WIDTH, never height: 15% of 1920 is not the same
    kind of quantity as 15% of 1080, and keying box heights to height made
    portrait boxes nearly square with a speck of text in them.

    In portrait the layout then FILLS the band rather than centring a
    landscape-sized block inside it. A 9:16 frame has half again as much room
    per unit of width, and leaving it empty makes the diagram look lost.
    """
    w, h = size
    portrait = h > w
    type_size = w * (0.034 if portrait else 0.020)
    # The captions own the bottom of the frame and the diagram must clear them,
    # not merely stop above their baseline: at 1080p the caption box starts
    # around 0.80h, and portrait captions are larger still.
    usable_bottom = h * (0.66 if portrait else 0.74)
    return w, h, portrait, type_size, usable_bottom


def _fill(avail: float, rows: int, gap_ratio: float) -> Tuple[float, float]:
    """Row height and gap that exactly fill `avail` with `rows` rows."""
    rows = max(1, rows)
    row_h = avail / (rows + gap_ratio * (rows - 1))
    return row_h, row_h * gap_ratio


def _title(draw: ImageDraw.ImageDraw, text: str, size: Tuple[int, int],
           theme: Theme) -> float:
    """Draws the heading and returns the y the layout may start from."""
    w, h = size
    top = h * (0.19 if h > w else 0.16)
    if not text:
        return top
    font = cards.font(theme.kicker_file, int(w * (0.030 if h > w else 0.026)))
    label = cards.trim(text.upper(), 46)
    tracking = w * 0.004
    span = sum(draw.textlength(c, font=font) + tracking for c in label)
    y = h * (0.085 if h > w else 0.10)
    x = (w - span) / 2
    for ch in label:
        draw.text((x, y), ch, font=font, fill=hex_rgb(theme.accent))
        x += draw.textlength(ch, font=font) + tracking
    return max(top, y + font.size * 2.4)


def _centre(top: float, bottom: float, block_h: float) -> float:
    """Top edge that centres a block of block_h in the usable band."""
    return max(top, top + (bottom - top - block_h) / 2)


def _fit_type(box_h: float, box_w: float, base: float) -> int:
    """Type that suits the box it sits in, capped so it cannot outgrow it."""
    return int(max(12.0, min(base * 1.9, box_h * 0.30, box_w * 0.13)))


# --------------------------------------------------------------------------- #
# layouts
# --------------------------------------------------------------------------- #
def _draw_flow(draw, spec, size, theme, shown):
    w, h, portrait, ts, bottom = _metrics(size)
    top = _title(draw, spec.title, size, theme)
    n = len(spec.nodes)

    if portrait:
        box_w = w * 0.86
        box_h, gap = _fill(bottom - top, n, 0.42)
        label_size = _fit_type(box_h, box_w, ts)
        x0 = (w - box_w) / 2
        y = top
        for i, text in enumerate(spec.nodes):
            on = 1.0 if i < shown else 0.0
            rect = (x0, y, x0 + box_w, y + box_h)
            _box(draw, rect, theme, on, accent=(i in (0, n - 1)))
            _label(draw, rect, text, theme, on, label_size)
            if i < n - 1:
                _arrow(draw, (w / 2, y + box_h + gap * 0.18),
                       (w / 2, y + box_h + gap * 0.82), theme,
                       1.0 if i + 1 < shown else 0.0, w)
            y += box_h + gap
    else:
        gap = w * 0.030
        box_w = min(w * 0.24, (w * 0.92 - gap * (n - 1)) / n)
        box_h = min((bottom - top) * 0.62, box_w * 0.78)
        label_size = _fit_type(box_h, box_w, ts)
        x = (w - (box_w * n + gap * (n - 1))) / 2
        y = _centre(top, bottom, box_h)
        for i, text in enumerate(spec.nodes):
            on = 1.0 if i < shown else 0.0
            rect = (x, y, x + box_w, y + box_h)
            _box(draw, rect, theme, on, accent=(i in (0, n - 1)))
            _label(draw, rect, text, theme, on, label_size)
            if i < n - 1:
                _arrow(draw, (x + box_w + gap * 0.18, y + box_h / 2),
                       (x + box_w + gap * 0.82, y + box_h / 2), theme,
                       1.0 if i + 1 < shown else 0.0, w)
            x += box_w + gap


def _draw_tree(draw, spec, size, theme, shown):
    """Root on top, the rest fanned out beneath it - the B-tree case.

    Portrait runs the children down a column instead. Three boxes side by side
    across 1080 pixels leaves each too narrow to hold two words.
    """
    w, h, portrait, ts, bottom = _metrics(size)
    top = _title(draw, spec.title, size, theme)
    root, children = spec.nodes[0], spec.nodes[1:]
    kids = max(1, len(children))

    if portrait:
        # A spine down the left with elbows into each child. Fanning three boxes
        # across 1080 pixels leaves each too narrow for two words, and arrows
        # drawn point-to-point just cross each other in the margin.
        box_h, gap = _fill(bottom - top, kids + 1, 0.46)
        spine_x = w * 0.12
        child_x0, child_w = w * 0.22, w * 0.72
        root_x0, root_w = w * 0.06, w * 0.66
        label_size = _fit_type(box_h, child_w, ts)

        root_rect = (root_x0, top, root_x0 + root_w, top + box_h)
        on_root = 1.0 if shown >= 1 else 0.0
        _box(draw, root_rect, theme, on_root, accent=True)
        _label(draw, root_rect, root, theme, on_root, label_size)

        y = top + box_h + gap
        spine_from = top + box_h
        for i, text in enumerate(children):
            on = 1.0 if i + 1 < shown else 0.0
            mid = y + box_h / 2
            colour = _alpha(hex_rgb(theme.accent), 0.85 * on + 0.08)
            draw.line([(spine_x, spine_from), (spine_x, mid)], fill=colour,
                      width=max(2, int(w * 0.004)))
            _arrow(draw, (spine_x, mid), (child_x0 - w * 0.012, mid), theme, on, w)
            rect = (child_x0, y, child_x0 + child_w, y + box_h)
            _box(draw, rect, theme, on)
            _label(draw, rect, text, theme, on, label_size)
            spine_from = mid
            y += box_h + gap
        return

    gap_x = w * 0.03
    child_w = min(w * 0.26, (w * 0.92 - gap_x * (kids - 1)) / kids)
    root_w = child_w * 1.15
    # two rows of boxes with the fan between them, sized to fill the band
    box_h, drop = _fill(bottom - top, 2, 1.05)
    box_h = min(box_h, child_w * 0.62)
    drop = (bottom - top) - box_h * 2
    label_size = _fit_type(box_h, child_w, ts)
    y = top

    root_rect = ((w - root_w) / 2, y, (w + root_w) / 2, y + box_h)
    on_root = 1.0 if shown >= 1 else 0.0
    _box(draw, root_rect, theme, on_root, accent=True)
    _label(draw, root_rect, root, theme, on_root, label_size)
    if not children:
        return

    row_y = y + box_h + drop
    x = (w - (child_w * kids + gap_x * (kids - 1))) / 2
    for i, text in enumerate(children):
        on = 1.0 if i + 1 < shown else 0.0
        rect = (x, row_y, x + child_w, row_y + box_h)
        _arrow(draw, (w / 2, y + box_h + drop * 0.10),
               (x + child_w / 2, row_y - drop * 0.10), theme, on, w)
        _box(draw, rect, theme, on)
        _label(draw, rect, text, theme, on, label_size)
        x += child_w + gap_x


def _draw_stack(draw, spec, size, theme, shown):
    """Layers, revealed bottom-up so the base reads as the foundation."""
    w, h, portrait, ts, bottom = _metrics(size)
    top = _title(draw, spec.title, size, theme)
    n = len(spec.nodes)
    box_w = w * (0.86 if portrait else 0.56)

    if portrait:
        box_h, gap = _fill(bottom - top, n, 0.14)
        label_size = _fit_type(box_h, box_w, ts)
        y = top
    else:
        box_h, gap = _fill(bottom - top, n, 0.16)
        box_h = min(box_h, box_w * 0.26)
        gap = box_h * 0.16
        label_size = _fit_type(box_h, box_w, ts)
        y = _centre(top, bottom, box_h * n + gap * (n - 1))

    x0 = (w - box_w) / 2
    for i, text in enumerate(spec.nodes):
        on = 1.0 if (n - 1 - i) < shown else 0.0
        rect = (x0, y, x0 + box_w, y + box_h)
        _box(draw, rect, theme, on, accent=(i == n - 1))
        _label(draw, rect, text, theme, on, label_size)
        y += box_h + gap


def _draw_compare(draw, spec, size, theme, shown):
    w, h, portrait, ts, bottom = _metrics(size)
    top = _title(draw, spec.title, size, theme)
    gap_x = w * (0.035 if portrait else 0.06)
    col_w = (w * (0.92 if portrait else 0.82) - gap_x) / 2
    x_positions = [(w - col_w * 2 - gap_x) / 2, (w + gap_x) / 2]
    rows = max(len(g["items"]) for g in spec.groups)

    if portrait:
        head_h = ts * 2.6
        box_h, gap_y = _fill(bottom - top - head_h, rows, 0.20)
        label_size = _fit_type(box_h, col_w, ts)
        y_top = top
    else:
        head_h = ts * 2.4
        box_h, gap_y = _fill(bottom - top - head_h, rows, 0.22)
        box_h = min(box_h, col_w * 0.34)
        gap_y = box_h * 0.22
        label_size = _fit_type(box_h, col_w, ts)
        y_top = _centre(top, bottom, head_h + box_h * rows + gap_y * (rows - 1))

    block = head_h + box_h * rows + gap_y * (rows - 1)
    seen = 0
    for col, group in enumerate(spec.groups):
        x0 = x_positions[col]
        head_font = cards.font(theme.kicker_file, int(ts * 0.95))
        label = cards.trim(group["label"].upper(), 24)
        tw = draw.textlength(label, font=head_font)
        draw.text((x0 + (col_w - tw) / 2, y_top), label, font=head_font,
                  fill=hex_rgb(theme.accent if col else theme.muted))

        y = y_top + head_h
        for item in group["items"]:
            on = 1.0 if seen < shown else 0.0
            rect = (x0, y, x0 + col_w, y + box_h)
            _box(draw, rect, theme, on, accent=bool(col))
            _label(draw, rect, item, theme, on, label_size)
            y += box_h + gap_y
            seen += 1

    draw.line([(w / 2, y_top - ts * 0.6), (w / 2, y_top + block)],
              fill=_alpha(hex_rgb(theme.muted), 0.35), width=max(1, int(w * 0.0012)))


DRAWERS = {"flow": _draw_flow, "tree": _draw_tree, "stack": _draw_stack,
           "compare": _draw_compare}


# --------------------------------------------------------------------------- #
# public
# --------------------------------------------------------------------------- #
def render(spec: Spec, out: Path, size: Tuple[int, int], theme: Theme,
           reveal: float = 1.0) -> Path:
    """Draw the diagram with the first `reveal` fraction of its parts shown."""
    img = cards.background(size, theme, f"diagram|{spec.title}|{spec.kind}")
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    shown = max(1, math.ceil(spec.elements * max(0.0, min(1.0, reveal))))
    DRAWERS.get(spec.kind, _draw_flow)(draw, spec, size, theme, shown)

    out.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB").save(
        out, quality=95)
    return out


def reveal_steps(shots: int) -> List[float]:
    """One reveal fraction per shot, so the diagram builds as it is explained."""
    if shots <= 1:
        return [1.0]
    return [(i + 1) / shots for i in range(shots)]
