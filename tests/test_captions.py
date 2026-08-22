"""Captions are the one thing a viewer reads every second of the video.

Two of these cover bugs that shipped and had to be found by looking at frames:
overlapping caption lines stacking on screen, and a malformed ASS Format row
spilling a comma into the visible text.
"""
from __future__ import annotations

import re

import pytest

from vidsmith.captions import (attach_punctuation, build_ass, caption_events,
                               group_words, scene_groups, write_srt)
from vidsmith.config import CaptionConfig, ThemeConfig
from vidsmith.theme import resolve

CFG = CaptionConfig()
THEME = resolve("midnight")
SIZE = (1920, 1080)


def _times(line: str):
    parts = line.split(",")
    return _seconds(parts[1]), _seconds(parts[2])


def _seconds(stamp: str) -> float:
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


# --------------------------------------------------------------------------- #
# punctuation
# --------------------------------------------------------------------------- #
def test_punctuation_is_restored_from_the_script(scene):
    assert all("." not in w["text"] for w in scene.words), "fixture should be bare"
    fixed = attach_punctuation(scene.words, scene.text)
    assert any(w["text"].endswith(".") for w in fixed)


def test_punctuation_never_loses_or_reorders_words(scene):
    fixed = attach_punctuation(scene.words, scene.text)
    assert len(fixed) == len(scene.words)
    for before, after in zip(scene.words, fixed):
        assert after["text"].startswith(before["text"])
        assert after["start"] == before["start"]


def test_grouping_breaks_on_a_full_stop(scene):
    groups = scene_groups(scene, CFG)
    ends = [g[-1]["text"] for g in groups]
    assert any(e.endswith(".") for e in ends)


def test_groups_cover_every_word_once(scene):
    groups = scene_groups(scene, CFG)
    flat = [w["text"] for g in groups for w in g]
    assert flat == [w["text"] for w in attach_punctuation(scene.words, scene.text)]


def test_groups_respect_the_word_limit(scene):
    for group in scene_groups(scene, CFG):
        assert len(group) <= CFG.max_words + 1


# --------------------------------------------------------------------------- #
# timing
# --------------------------------------------------------------------------- #
def test_caption_lines_never_overlap(scenes):
    """Two lines on screen at once is the bug that stacked captions."""
    events = caption_events(scenes, CFG, THEME, 0.25)
    spans = [_times(e) for e in events]
    for (a_start, a_end), (b_start, _) in zip(spans, spans[1:]):
        assert a_end <= b_start + 1e-6, f"{a_end} overruns the next line at {b_start}"


def test_captions_stay_inside_their_scene(scenes):
    events = caption_events(scenes, CFG, THEME, 0.25)
    last = scenes[-1]
    assert max(_times(e)[1] for e in events) <= last.start + last.duration + 1e-6


def test_every_event_has_positive_duration(scenes):
    for event in caption_events(scenes, CFG, THEME, 0.25):
        start, end = _times(event)
        assert end > start


# --------------------------------------------------------------------------- #
# file shape
# --------------------------------------------------------------------------- #
def test_ass_format_row_matches_the_dialogue_fields(scenes):
    """A Format row short of one field shifts a comma into the visible text."""
    ass = build_ass(scenes, CFG, SIZE, 0.25, THEME, ThemeConfig(), 30.0)
    fmt = next(l for l in ass.splitlines() if l.startswith("Format: Layer"))
    fields = len(fmt.split(","))
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            assert len(line.split(",", fields - 1)) == fields


def test_ass_dialogue_text_is_not_empty(scenes):
    ass = build_ass(scenes, CFG, SIZE, 0.25, THEME, ThemeConfig(), 30.0)
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            text = line.split(",", 9)[9]
            assert text.strip(), "an empty caption renders as a blank flash"
            assert not text.startswith(","), "field misalignment leaked a comma"


def test_every_style_used_is_declared(scenes):
    cfg = ThemeConfig(watermark="@someone", lower_thirds=True)
    ass = build_ass(scenes, CFG, SIZE, 0.25, THEME, cfg, 30.0)
    declared = {l.split(",")[0].removeprefix("Style: ")
                for l in ass.splitlines() if l.startswith("Style:")}
    used = {l.split(",")[3] for l in ass.splitlines() if l.startswith("Dialogue:")}
    assert used <= declared, f"undeclared styles: {used - declared}"


def test_watermark_spans_the_whole_video(scenes):
    ass = build_ass(scenes, CFG, SIZE, 0.25, THEME,
                    ThemeConfig(watermark="@veer0608"), 42.0)
    marks = [l for l in ass.splitlines() if l.startswith("Dialogue:")
             and l.split(",")[3] == "Mark"]
    assert len(marks) == 1
    start, end = _times(marks[0])
    assert start == pytest.approx(0.0, abs=0.01)
    assert end == pytest.approx(42.0, abs=0.05)


def test_portrait_captions_are_larger_than_landscape(scenes):
    def size_of(dims):
        ass = build_ass(scenes, CFG, dims, 0.25, THEME, ThemeConfig(), 30.0)
        row = next(l for l in ass.splitlines() if l.startswith("Style: Narration"))
        return int(row.split(",")[2])

    # a Short is watched with much bigger captions relative to the frame
    assert size_of((1080, 1920)) > size_of((1920, 1080)) * 0.9


# --------------------------------------------------------------------------- #
# srt
# --------------------------------------------------------------------------- #
def test_srt_blocks_are_sequential_and_ordered(scenes, tmp_path):
    path = write_srt(scenes, tmp_path / "c.srt", CFG, 0.25)
    blocks = [b for b in path.read_text(encoding="utf-8").split("\n\n") if b.strip()]
    assert blocks
    for i, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        assert lines[0] == str(i)
        assert re.match(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", lines[1])
