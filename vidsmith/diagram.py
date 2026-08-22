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


def _title(draw: ImageDraw.ImageDraw, text: str, size: Tuple[int, int],
           theme: Theme) -> float:
    """Draws the caption-style heading and returns the top of the canvas below it."""
    w, h = size
    if not text:
        return h * 0.16
    font = cards.font(theme.kicker_file, int(w * 0.026))
    label = cards.trim(text.upper(), 46)
    span = sum(draw.textlength(c, font=font) + w * 0.004 for c in label)
    y = h * 0.10
    x = (w - span) / 2
    for ch in label:
        draw.text((x, y), ch, font=font, fill=hex_rgb(theme.accent))
        x += draw.textlength(ch, font=font) + w * 0.004
    return h * 0.20


# --------------------------------------------------------------------------- #
# layouts
# --------------------------------------------------------------------------- #
def _draw_flow(draw, spec, size, theme, shown):
    w, h = size
    top = _title(draw, spec.title, size, theme)
    n = len(spec.nodes)
    vertical = h > w

    if vertical:
        gap = h * 0.035
        box_h = min(h * 0.11, (h * 0.66 - gap * (n - 1)) / n)
        box_w = w * 0.72
        x0 = (w - box_w) / 2
        y = top + (h * 0.72 - (box_h * n + gap * (n - 1))) / 2
        for i, text in enumerate(spec.nodes):
            on = 1.0 if i < shown else 0.0
            rect = (x0, y, x0 + box_w, y + box_h)
            _box(draw, rect, theme, on, accent=(i == 0 or i == n - 1))
            _label(draw, rect, text, theme, on, int(w * 0.033))
            if i < n - 1:
                _arrow(draw, (w / 2, y + box_h + gap * 0.15),
                       (w / 2, y + box_h + gap * 0.85), theme,
                       1.0 if i + 1 < shown else 0.0, w)
            y += box_h + gap
    else:
        gap = w * 0.035
        box_w = min(w * 0.20, (w * 0.86 - gap * (n - 1)) / n)
        box_h = h * 0.22
        total = box_w * n + gap * (n - 1)
        x = (w - total) / 2
        y = top + (h * 0.62 - box_h) / 2
        for i, text in enumerate(spec.nodes):
            on = 1.0 if i < shown else 0.0
            rect = (x, y, x + box_w, y + box_h)
            _box(draw, rect, theme, on, accent=(i == 0 or i == n - 1))
            _label(draw, rect, text, theme, on, int(w * 0.020))
            if i < n - 1:
                _arrow(draw, (x + box_w + gap * 0.18, y + box_h / 2),
                       (x + box_w + gap * 0.82, y + box_h / 2), theme,
                       1.0 if i + 1 < shown else 0.0, w)
            x += box_w + gap


def _draw_tree(draw, spec, size, theme, shown):
    """Root on top, the rest fanned out beneath it - the B-tree case."""
    w, h = size
    top = _title(draw, spec.title, size, theme)
    nodes = spec.nodes
    root, children = nodes[0], nodes[1:]

    box_w = min(w * 0.26, w * 0.86 / max(1, len(children)))
    box_h = h * 0.15
    root_rect = ((w - box_w) / 2, top, (w + box_w) / 2, top + box_h)
    _box(draw, root_rect, theme, 1.0 if shown >= 1 else 0.0, accent=True)
    _label(draw, root_rect, root, theme, 1.0 if shown >= 1 else 0.0, int(w * 0.021))

    if not children:
        return
    row_y = top + box_h + h * 0.20
    gap = (w * 0.88 - box_w * len(children)) / max(1, len(children) - 1) \
        if len(children) > 1 else 0
    x = (w - (box_w * len(children) + gap * (len(children) - 1))) / 2
    for i, text in enumerate(children):
        on = 1.0 if i + 1 < shown else 0.0
        rect = (x, row_y, x + box_w, row_y + box_h)
        _arrow(draw, (w / 2, top + box_h + h * 0.015),
               (x + box_w / 2, row_y - h * 0.015), theme, on, w)
        _box(draw, rect, theme, on)
        _label(draw, rect, text, theme, on, int(w * 0.019))
        x += box_w + gap


def _draw_stack(draw, spec, size, theme, shown):
    """Layers, drawn bottom-up so the base reads as the foundation."""
    w, h = size
    top = _title(draw, spec.title, size, theme)
    n = len(spec.nodes)
    gap = h * 0.018
    box_h = min(h * 0.13, (h * 0.66 - gap * (n - 1)) / n)
    box_w = w * 0.62
    x0 = (w - box_w) / 2
    y = top + (h * 0.70 - (box_h * n + gap * (n - 1))) / 2
    for i, text in enumerate(spec.nodes):
        on = 1.0 if (n - 1 - i) < shown else 0.0
        rect = (x0, y, x0 + box_w, y + box_h)
        _box(draw, rect, theme, on, accent=(i == n - 1))
        _label(draw, rect, text, theme, on, int(w * 0.024))
        y += box_h + gap


def _draw_compare(draw, spec, size, theme, shown):
    w, h = size
    top = _title(draw, spec.title, size, theme)
    col_w = w * 0.38
    gap = w * 0.06
    x_positions = [(w - col_w * 2 - gap) / 2, (w + gap) / 2]
    seen = 0

    for col, group in enumerate(spec.groups):
        x0 = x_positions[col]
        head_font = cards.font(theme.kicker_file, int(w * 0.024))
        label = cards.trim(group["label"].upper(), 26)
        tw = draw.textlength(label, font=head_font)
        draw.text((x0 + (col_w - tw) / 2, top), label, font=head_font,
                  fill=hex_rgb(theme.accent if col else theme.muted))

        y = top + h * 0.09
        box_h = h * 0.11
        for item in group["items"]:
            on = 1.0 if seen < shown else 0.0
            rect = (x0, y, x0 + col_w, y + box_h)
            _box(draw, rect, theme, on, accent=bool(col))
            _label(draw, rect, item, theme, on, int(w * 0.018))
            y += box_h + h * 0.025
            seen += 1

    divider_x = w / 2
    draw.line([(divider_x, top - h * 0.02), (divider_x, top + h * 0.60)],
              fill=_alpha(hex_rgb(theme.muted), 0.35), width=max(1, int(w * 0.001)))


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
