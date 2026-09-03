"""The WebVTT cut of the captions, and the guarantee that it matches the SRT.

YouTube takes the SRT, so nothing here is about YouTube. This is for the same
delivery being playable anywhere else - a `<video>` on a landing page, an embed,
any player that is not YouTube - without somebody converting a file by hand.

The test that earns its place is the last one. Two subtitle writers with two
copies of the timing loop is exactly the shape of fault this project keeps
finding after the fact: the shot ceiling lived in two modules and disagreed, the
aspect tag was spelled by hand and picked the wrong cut. A caption half a second
late still looks like a caption, so drift here would not be noticed by looking.
"""
from __future__ import annotations

import re

from vidsmith.captions import cues, write_srt, write_vtt
from vidsmith.config import CaptionConfig

CFG = CaptionConfig()
LEAD_IN = 0.25

_VTT_STAMP = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})$")


def _seconds(stamp: str) -> float:
    h, m, s = stamp.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _vtt_cues(text: str):
    """Every `(start, end, text)` in a WebVTT file, by parsing it back."""
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _VTT_STAMP.match(line)
        if m:
            out.append((_seconds(m.group(1)), _seconds(m.group(2)), lines[i + 1]))
    return out


def _srt_cues(text: str):
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "-->" in line:
            start, end = [p.strip() for p in line.split("-->")]
            out.append((_seconds(start), _seconds(end), lines[i + 1]))
    return out


def test_it_starts_with_the_webvtt_header(scenes, tmp_path):
    """Without this exact first line a browser rejects the whole file, and it
    does it silently: the track just never shows."""
    body = write_vtt(scenes, tmp_path / "c.vtt", CFG, LEAD_IN).read_text(
        encoding="utf-8")
    assert body.startswith("WEBVTT\n\n")


def test_timestamps_use_a_dot_not_a_comma(scenes, tmp_path):
    """The one syntactic difference from SRT, and the usual reason a converted
    file fails to parse."""
    body = write_vtt(scenes, tmp_path / "c.vtt", CFG, LEAD_IN).read_text(
        encoding="utf-8")
    assert "-->" in body
    for line in body.splitlines():
        if "-->" in line:
            assert _VTT_STAMP.match(line), line


def test_cue_text_is_escaped(scenes, tmp_path):
    """WebVTT parses a little markup, so an unescaped `<` opens a tag that never
    closes and eats the rest of the cue. A script comparing two numbers is
    enough to trigger it."""
    scenes[0].words[0]["text"] = "a<b&c"
    body = write_vtt(scenes, tmp_path / "c.vtt", CFG, LEAD_IN).read_text(
        encoding="utf-8")
    assert "a&lt;b&amp;c" in body
    assert "a<b" not in body


def test_no_cue_is_empty_or_backwards(scenes, tmp_path):
    body = write_vtt(scenes, tmp_path / "c.vtt", CFG, LEAD_IN).read_text(
        encoding="utf-8")
    parsed = _vtt_cues(body)
    assert parsed
    for start, end, text in parsed:
        assert end > start, (start, end, text)
        assert text.strip()


def test_cues_are_in_order(scenes, tmp_path):
    body = write_vtt(scenes, tmp_path / "c.vtt", CFG, LEAD_IN).read_text(
        encoding="utf-8")
    starts = [c[0] for c in _vtt_cues(body)]
    assert starts == sorted(starts)


def test_the_two_formats_cannot_drift(scenes, tmp_path):
    """The point of the refactor: both writers read one cue list.

    Parsed back from the files rather than compared in memory, so this fails if
    either writer formats a timestamp wrong, drops a cue, or reorders one -
    not only if the shared function changes.
    """
    srt = _srt_cues(write_srt(scenes, tmp_path / "c.srt", CFG, LEAD_IN)
                    .read_text(encoding="utf-8"))
    vtt = _vtt_cues(write_vtt(scenes, tmp_path / "c.vtt", CFG, LEAD_IN)
                    .read_text(encoding="utf-8"))

    assert len(srt) == len(vtt) and srt
    for (s_start, s_end, s_text), (v_start, v_end, v_text) in zip(srt, vtt):
        assert abs(s_start - v_start) < 0.0011, (s_start, v_start)
        assert abs(s_end - v_end) < 0.0011, (s_end, v_end)
        assert s_text == v_text


def test_both_writers_agree_with_the_cue_list_they_share(scenes, tmp_path):
    """And that the shared list is what actually reached the files, rather than
    the two of them agreeing on something wrong."""
    shared = cues(scenes, CFG, LEAD_IN)
    vtt = _vtt_cues(write_vtt(scenes, tmp_path / "c.vtt", CFG, LEAD_IN)
                    .read_text(encoding="utf-8"))

    assert len(shared) == len(vtt)
    for (a_start, a_end, a_text), (b_start, b_end, b_text) in zip(shared, vtt):
        assert abs(a_start - b_start) < 0.0011
        assert abs(a_end - b_end) < 0.0011
        assert a_text == b_text
