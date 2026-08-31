"""Frame design: the cards vidsmith draws when there is no footage to show.

All three variants (title, scene, end) share one grid so a video cuts between
them without the layout jumping: same left margin, same accent rule, same
type scale derived from frame width.
"""
from __future__ import annotations

import colorsys
import hashlib
import random
import re
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .theme import Theme, hex_rgb, mix

FONT_FALLBACKS = {
    "seguibl.ttf": ("seguibl.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    "bahnschrift.ttf": ("bahnschrift.ttf", "framd.ttf", "arial.ttf", "DejaVuSans.ttf"),
}
# Fonts fetched at deploy time live here; a Linux host has none of the Windows
# families the themes name, and libass needs to be pointed at them too.
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def font(file: str, size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_FALLBACKS.get(file, (file, "arialbd.ttf")):
        bundled = FONT_DIR / name
        if bundled.exists():
            try:
                return ImageFont.truetype(str(bundled), size)
            except OSError:
                pass
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


# --------------------------------------------------------------------------- #
# surfaces
# --------------------------------------------------------------------------- #
def _gradient(size: Tuple[int, int], c1: str, c2: str) -> Image.Image:
    """Diagonal two-stop gradient, built small and scaled up."""
    w, h = size
    small = Image.new("RGB", (2, 2))
    r1, g1, b1 = hex_rgb(c1)
    r2, g2, b2 = hex_rgb(c2)
    mid = hex_rgb(mix(c1, c2, 0.5))
    small.putpixel((0, 0), (r1, g1, b1))
    small.putpixel((1, 0), mid)
    small.putpixel((0, 1), mid)
    small.putpixel((1, 1), (r2, g2, b2))
    return small.resize((w, h), Image.BICUBIC)


def _bloom(img: Image.Image, theme: Theme, rnd: random.Random) -> Image.Image:
    w, h = img.size
    glow = Image.new("RGB", (w // 4, h // 4), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    accent = hex_rgb(theme.accent)
    for _ in range(2):
        r = rnd.randint(int(h * 0.10), int(h * 0.22))
        cx, cy = rnd.randint(0, w // 4), rnd.randint(0, h // 4)
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=h * 0.05)).resize(
        (w, h), Image.BICUBIC
    )
    return Image.blend(img, glow, 0.10 if not theme.light else 0.06)


def _dot_grid(img: Image.Image, theme: Theme) -> Image.Image:
    """A faint printed-paper texture so flat gradients do not band."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    step = max(28, int(w / 56))
    r = max(1, int(w / 1400))
    tint = (255, 255, 255, 16) if not theme.light else (0, 0, 0, 14)
    for y in range(step, h, step):
        for x in range(step, w, step):
            d.ellipse([x - r, y - r, x + r, y + r], fill=tint)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _vignette(img: Image.Image, strength: float = 0.42) -> Image.Image:
    w, h = img.size
    mask = Image.radial_gradient("L").resize((w, h), Image.BICUBIC)
    mask = mask.point(lambda v: int(v * strength))
    shade = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(shade, img, mask)


def background(size: Tuple[int, int], theme: Theme, seed: str) -> Image.Image:
    rnd = random.Random(int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12], 16))
    # Each card nudges the gradient target so a run of cards is not four
    # identical frames. Keep the nudge small - a wide hue drift turns a navy
    # theme green two scenes in and the video stops looking like one thing.
    drift = rnd.uniform(-0.05, 0.05)
    r, g, b = hex_rgb(theme.bg_alt)
    hh, ss, vv = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    rr, gg, bb = colorsys.hsv_to_rgb((hh + drift) % 1.0, ss, vv)
    alt = f"#{int(rr * 255):02X}{int(gg * 255):02X}{int(bb * 255):02X}"

    img = _gradient(size, theme.bg, alt)
    img = _bloom(img, theme, rnd)
    img = _dot_grid(img, theme)
    return _vignette(img, 0.34)


# --------------------------------------------------------------------------- #
# type
# --------------------------------------------------------------------------- #
def _wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> List[str]:
    lines: List[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _tracked(draw: ImageDraw.ImageDraw, xy: Tuple[float, float], text: str, fnt,
             fill, tracking: float) -> float:
    """Letterspaced text - PIL has no tracking, so step glyph by glyph."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + tracking
    return x


def trim(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "..."


def first_clause(text: str, limit: int = 76) -> str:
    first = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    return trim(first, limit)


def headline_phrase(text: str, limit: int = 48) -> str:
    """A short label for a card.

    The caption already carries the full sentence, so the card must not repeat
    it - break at the first natural clause instead of wrapping four lines of the
    same words the viewer is reading at the bottom of the frame.
    """
    first = first_clause(text, 200)
    if len(first) <= limit:
        return first.rstrip(".")
    for sep in (" - ", " — ", ", ", ": ", "; "):
        head = first.split(sep)[0]
        if 12 <= len(head) <= limit:
            return head.rstrip(".")
    return trim(first, limit)


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #
def scene_card(out: Path, size: Tuple[int, int], theme: Theme, headline: str,
               kicker: str = "", counter: str = "") -> Path:
    """Left-aligned editorial card: accent rule, kicker, headline, counter."""
    w, h = size
    img = background(size, theme, f"{kicker}|{headline}")
    draw = ImageDraw.Draw(img)

    margin = int(w * 0.085)
    text_w = int(w * (0.74 if w >= h else 0.80))
    head_size = int(w * (0.070 if len(headline) < 48 else 0.056))
    if w < h:
        head_size = int(head_size * 1.15)
    head_font = font(theme.headline_file, head_size)
    kick_font = font(theme.kicker_file, int(w * 0.021))

    lines = _wrap(draw, headline, head_font, text_w)
    # Three lines of card headline reach down into the caption zone, so shrink
    # once and then hard-truncate rather than letting the two collide.
    if len(lines) > 2:
        head_size = int(head_size * 0.82)
        head_font = font(theme.headline_file, head_size)
        lines = _wrap(draw, headline, head_font, text_w)
    if len(lines) > 2:
        lines = lines[:2]
        lines[-1] = lines[-1].rstrip(" ,.;:-") + "..."
    line_h = int(head_size * 1.14)
    block_h = line_h * len(lines)
    kicker_h = int(head_size * 0.85) if kicker else 0
    # sit the block above centre: the caption owns the bottom third of the frame
    top = int(h * 0.42 - (block_h + kicker_h) / 2)

    bar_x = margin - int(w * 0.022)
    draw.rectangle(
        [bar_x, top - int(head_size * 0.12), bar_x + max(4, int(w * 0.0045)),
         top + block_h + kicker_h - int(head_size * 0.25)],
        fill=hex_rgb(theme.accent),
    )

    y = top
    if kicker:
        _tracked(draw, (margin, y), kicker.upper()[:38], kick_font,
                 hex_rgb(theme.accent), w * 0.0035)
        y += kicker_h

    shadow = (0, 0, 0) if not theme.light else (255, 255, 255)
    for line in lines:
        off = max(2, int(w * 0.0016))
        draw.text((margin + off, y + off), line, font=head_font, fill=shadow)
        draw.text((margin, y), line, font=head_font, fill=hex_rgb(theme.text))
        y += line_h

    if counter:
        # bottom-left; the ASS layer owns bottom-right for the channel mark
        cf = font(theme.kicker_file, int(w * 0.019))
        draw.text((margin, h - margin - int(w * 0.019)), counter, font=cf,
                  fill=hex_rgb(theme.muted))

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return out


def title_card(out: Path, size: Tuple[int, int], theme: Theme, title: str,
               subtitle: str = "") -> Path:
    """Centred opening frame. Deliberately quieter than the scene cards."""
    w, h = size
    img = background(size, theme, f"title|{title}")
    draw = ImageDraw.Draw(img)

    text_w = int(w * 0.80)
    size_px = int(w * (0.088 if len(title) < 34 else 0.066))
    tf = font(theme.headline_file, size_px)
    lines = _wrap(draw, title, tf, text_w)
    line_h = int(size_px * 1.12)
    block = line_h * len(lines)
    top = int(h * 0.46 - block / 2)

    rule_w = int(w * 0.07)
    rule_h = max(4, int(h * 0.006))
    draw.rectangle(
        [(w - rule_w) // 2, top - int(size_px * 0.62),
         (w + rule_w) // 2, top - int(size_px * 0.62) + rule_h],
        fill=hex_rgb(theme.accent),
    )

    shadow = (0, 0, 0) if not theme.light else (255, 255, 255)
    y = top
    for line in lines:
        tw = draw.textlength(line, font=tf)
        x = (w - tw) / 2
        off = max(2, int(w * 0.0018))
        draw.text((x + off, y + off), line, font=tf, fill=shadow)
        draw.text((x, y), line, font=tf, fill=hex_rgb(theme.text))
        y += line_h

    if subtitle:
        sf = font(theme.kicker_file, int(w * 0.023))
        sub = trim(subtitle, 70).upper()
        span = sum(draw.textlength(c, font=sf) + w * 0.004 for c in sub)
        _tracked(draw, ((w - span) / 2, y + int(size_px * 0.30)), sub, sf,
                 hex_rgb(theme.muted), w * 0.004)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return out


def end_card(out: Path, size: Tuple[int, int], theme: Theme, line: str) -> Path:
    return title_card(out, size, theme, trim(line, 60))


# --------------------------------------------------------------------------- #
# scrim: the reason captions stay readable over real footage
# --------------------------------------------------------------------------- #
def scrim(out: Path, size: Tuple[int, int], theme: Theme,
          coverage: float = 0.42) -> Path:
    """A transparent PNG that darkens the bottom of the frame behind captions."""
    w, h = size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    band = int(h * coverage)
    base = (0, 0, 0) if not theme.light else (255, 255, 255)
    peak = 190 if not theme.light else 205
    col = Image.new("RGBA", (1, band))
    for i in range(band):
        t = i / max(1, band - 1)
        alpha = int(peak * (t ** 1.7))
        col.putpixel((0, i), (*base, alpha))
    layer.paste(col.resize((w, band), Image.BICUBIC), (0, h - band))
    out.parent.mkdir(parents=True, exist_ok=True)
    layer.save(out)
    return out
