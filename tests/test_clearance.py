"""Diagrams must not be drawn where the captions are going to land.

The clearance used to be a hardcoded fraction of frame height, which was right
only for the default caption settings. Change `size` or `margin_v` in a project
and a caption would land straight across the bottom of a diagram. It is now
computed from the same numbers that build the ASS styles.
"""
from __future__ import annotations

import pytest
from PIL import Image, ImageChops

from vidsmith import cards
from vidsmith.captions import caption_top
from vidsmith.config import CaptionConfig
from vidsmith.diagram import Spec, render
from vidsmith.theme import resolve

THEME = resolve("midnight")
LANDSCAPE = (960, 540)
PORTRAIT = (540, 960)
SPECS = [
    {"kind": "flow", "title": "What a write costs",
     "nodes": ["Insert row", "Update table", "Rewrite tree"]},
    {"kind": "tree", "title": "How it branches",
     "nodes": ["Root node", "Leaf A", "Leaf B", "Leaf C"]},
    {"kind": "stack", "title": "Where time goes",
     "nodes": ["Disk", "Index pages", "Planner"]},
    {"kind": "compare", "title": "Reads against writes",
     "groups": [{"label": "Faster", "items": ["Lookups", "Scans"]},
                {"label": "Slower", "items": ["Inserts", "Updates"]}]},
]


# --------------------------------------------------------------------------- #
# the figure itself
# --------------------------------------------------------------------------- #
def test_bigger_captions_reach_higher():
    small = caption_top(LANDSCAPE, CaptionConfig(size=48))
    large = caption_top(LANDSCAPE, CaptionConfig(size=110))
    assert large < small


def test_a_higher_margin_reaches_higher():
    low = caption_top(LANDSCAPE, CaptionConfig(margin_v=80))
    high = caption_top(LANDSCAPE, CaptionConfig(margin_v=300))
    assert high < low


def test_a_plate_reaches_higher_than_an_outline():
    assert caption_top(LANDSCAPE, CaptionConfig(box=True)) < \
        caption_top(LANDSCAPE, CaptionConfig(box=False))


@pytest.mark.parametrize("cfg", [CaptionConfig(enabled=False),
                                 CaptionConfig(style="none")])
def test_no_captions_frees_almost_the_whole_frame(cfg):
    assert caption_top(LANDSCAPE, cfg) > LANDSCAPE[1] * 0.9


def test_portrait_captions_reach_higher_in_their_frame():
    """Shorts captions are larger and sit further from the bottom edge."""
    land = caption_top(LANDSCAPE, CaptionConfig()) / LANDSCAPE[1]
    port = caption_top(PORTRAIT, CaptionConfig()) / PORTRAIT[1]
    assert port < land


def test_clearance_is_always_inside_the_frame():
    for size in (LANDSCAPE, PORTRAIT):
        for cfg in (CaptionConfig(), CaptionConfig(size=200),
                    CaptionConfig(margin_v=600)):
            top = caption_top(size, cfg)
            assert 0 < top < size[1]


# --------------------------------------------------------------------------- #
# what actually gets drawn
# --------------------------------------------------------------------------- #
def _drawn_below(path, size, theme, seed, cutoff) -> bool:
    """True if the render put anything under `cutoff` that the background did not."""
    drawn = Image.open(path).convert("RGB")
    plain = cards.background(size, theme, seed).convert("RGB")
    band = (0, int(cutoff), size[0], size[1])
    diff = ImageChops.difference(drawn.crop(band), plain.crop(band))
    return diff.getbbox() is not None


@pytest.mark.parametrize("raw", SPECS, ids=lambda r: r["kind"])
@pytest.mark.parametrize("size", [LANDSCAPE, PORTRAIT], ids=["16:9", "9:16"])
def test_nothing_is_drawn_in_the_caption_zone(tmp_path, raw, size):
    spec = Spec.from_dict(raw)
    clear = caption_top(size, CaptionConfig())
    out = render(spec, tmp_path / "d.png", size, THEME, 1.0, clear_below=clear)
    seed = f"diagram|{spec.title}|{spec.kind}"
    assert not _drawn_below(out, size, THEME, seed, clear), \
        f"{spec.kind} at {size} drew into the caption zone"


@pytest.mark.parametrize("raw", SPECS, ids=lambda r: r["kind"])
def test_huge_captions_push_the_diagram_up(tmp_path, raw):
    """The case the hardcoded fraction got wrong."""
    spec = Spec.from_dict(raw)
    clear = caption_top(LANDSCAPE, CaptionConfig(size=140, margin_v=260))
    out = render(spec, tmp_path / "d.png", LANDSCAPE, THEME, 1.0, clear_below=clear)
    seed = f"diagram|{spec.title}|{spec.kind}"
    assert not _drawn_below(out, LANDSCAPE, THEME, seed, clear)


def test_switching_captions_off_lets_the_diagram_grow(tmp_path):
    spec = Spec.from_dict(SPECS[2])
    with_caps = render(spec, tmp_path / "a.png", LANDSCAPE, THEME, 1.0,
                       clear_below=caption_top(LANDSCAPE, CaptionConfig()))
    without = render(spec, tmp_path / "b.png", LANDSCAPE, THEME, 1.0,
                     clear_below=caption_top(LANDSCAPE,
                                             CaptionConfig(enabled=False)))
    seed = f"diagram|{spec.title}|{spec.kind}"
    cut = caption_top(LANDSCAPE, CaptionConfig())
    assert not _drawn_below(with_caps, LANDSCAPE, THEME, seed, cut)
    assert _drawn_below(without, LANDSCAPE, THEME, seed, cut), \
        "with no captions the diagram should use the room they freed"


def test_the_builder_takes_its_clearance_from_the_project(tmp_path):
    from vidsmith.config import ThemeConfig, VisualConfig
    from vidsmith.visuals import VisualBuilder

    def build(caption_cfg):
        return VisualBuilder(VisualConfig(), LANDSCAPE, 24, tmp_path, keys={},
                             log=lambda *a: None, theme=THEME,
                             theme_cfg=ThemeConfig(),
                             caption_cfg=caption_cfg).caption_clear

    assert build(CaptionConfig(size=140)) < build(CaptionConfig(size=40))
