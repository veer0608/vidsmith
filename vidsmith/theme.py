"""One palette and type scale, shared by cards, captions and overlays.

Every on-screen element reads from a Theme, so a video looks designed rather
than assembled: the accent on the title card, the caption highlight, the
progress bar and the lower-third rule are all the same colour by construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


def hex_rgb(value: str) -> Tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def ass_color(value: str, alpha: int = 0) -> str:
    """#RRGGBB -> ASS &HAABBGGRR. alpha 0 = opaque, 255 = invisible."""
    r, g, b = hex_rgb(value)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = hex_rgb(a)
    rb, gb, bb = hex_rgb(b)
    f = lambda x, y: int(round(x + (y - x) * t))
    return f"#{f(ra, rb):02X}{f(ga, gb):02X}{f(ba, bb):02X}"


@dataclass
class Theme:
    name: str = "midnight"
    bg: str = "#0B1020"          # card base
    bg_alt: str = "#1B2340"      # card gradient target
    accent: str = "#FFC24B"      # rules, highlights, progress bar
    text: str = "#F5F7FA"        # headlines and caption body
    muted: str = "#8C93A8"       # kickers, counters, watermark
    stroke: str = "#000000"      # caption outline
    light: bool = False          # light backgrounds need a dark caption outline

    headline_font: str = "Segoe UI Black"
    kicker_font: str = "Bahnschrift"
    caption_font: str = "Segoe UI Black"

    # PIL needs files, libass needs family names; keep both in step.
    headline_file: str = "seguibl.ttf"
    kicker_file: str = "bahnschrift.ttf"


PRESETS: Dict[str, Theme] = {
    "midnight": Theme(
        name="midnight", bg="#0B1020", bg_alt="#232C52", accent="#FFC24B",
        text="#F5F7FA", muted="#8C93A8",
    ),
    "ink": Theme(
        name="ink", bg="#12100E", bg_alt="#2C2622", accent="#E5533D",
        text="#F2EFEA", muted="#9A938A",
    ),
    "sunset": Theme(
        name="sunset", bg="#1A0F1F", bg_alt="#4A1F3D", accent="#FF7A59",
        text="#FFF3EC", muted="#C79BAE",
    ),
    "forest": Theme(
        name="forest", bg="#0A1512", bg_alt="#16332A", accent="#5EE6A8",
        text="#EDF7F2", muted="#7E9C90",
    ),
    "paper": Theme(
        name="paper", bg="#F4F1EA", bg_alt="#DCD6C8", accent="#1F4E5F",
        text="#17181A", muted="#6B6B6B", stroke="#FFFFFF", light=True,
    ),
    "mono": Theme(
        name="mono", bg="#101114", bg_alt="#26282E", accent="#FFFFFF",
        text="#FFFFFF", muted="#8A8A8A",
    ),
}


def resolve(preset: str = "midnight", accent: str = "", font: str = "") -> Theme:
    base = PRESETS.get(preset, PRESETS["midnight"])
    t = Theme(**{**base.__dict__})
    if accent:
        t.accent = accent if accent.startswith("#") else "#" + accent
    if font:
        t.headline_font = t.caption_font = font
    return t
