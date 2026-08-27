"""Paths handed to a filtergraph.

The ASS file and the bundled fonts reach ffmpeg as `subtitles='<path>'`, and a
path is not a string ffmpeg reads literally: two layers of its own parsing sit
between the quotes and the filename. Anything those layers eat is a file it
cannot open, and the failure is a dead render rather than a missing caption.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vidsmith import ffmpeg_util as ff

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narration,Arial,24,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Narration,,0,0,0,,hello
"""


def test_a_drive_colon_is_escaped(tmp_path):
    """An unescaped colon splits the filter's own options."""
    escaped = ff.escape_filter_path(tmp_path / "captions.ass")
    assert ":" not in escaped.replace("\\:", "")


def test_backslashes_become_forward_slashes(tmp_path):
    assert "\\" not in ff.escape_filter_path(tmp_path / "a" / "b.ass").replace("\\:", "")


def test_an_apostrophe_is_actually_escaped(tmp_path):
    """`.replace("'", "\\'")` was a no-op: that string is just an apostrophe.

    The character has to satisfy the filtergraph's quoting and the filter's own
    option parsing at once, so a bare apostrophe is not enough.
    """
    escaped = ff.escape_filter_path(tmp_path / "O'Brien" / "captions.ass")
    assert "'" in escaped
    assert escaped.count("'") > 1, "a lone apostrophe would end the quoted section"
    assert "O\\'\\''Brien" in escaped


@pytest.mark.slow
def test_the_apostrophe_escape_is_not_portable(tmp_path):
    """This used to assert the opposite, and passed on two platforms out of three.

    `escape_filter_path` spells an apostrophe in the form the Windows and Debian
    ffmpeg builds accept. Homebrew's newer build reads the same bytes and answers
    "No option name near", so the escaping alone cannot be relied on and the
    render stages the file out of the way instead. What is still portable is the
    drive colon, which is the other half of the job.

    Kept as a live check rather than a comment: if a future ffmpeg accepts the
    escaping everywhere, this starts failing and the staging can go.
    """
    escaped = ff.escape_filter_path(Path("C:/tmp/O'Brien/captions.ass"))
    assert r"C\:/tmp/" in escaped, "the drive colon must still be escaped"
    assert "'" in escaped


# --------------------------------------------------------------------------- #
# the concat demuxer list
# --------------------------------------------------------------------------- #
def test_a_concat_path_escapes_the_quote_but_not_the_colon():
    """One layer here, unlike a filtergraph path.

    The demuxer reads the list file itself and no filter-option parser sits
    under it, so the drive colon is safe and only the apostrophe needs closing,
    escaping and reopening.
    """
    escaped = ff.escape_concat_path(Path("C:/tmp/O'Brien/clip.mp4"))
    assert "C:/tmp/" in escaped, "the colon must be left alone"
    assert "O'\\''Brien" in escaped


def test_the_two_escapes_do_not_agree(tmp_path):
    """They are different rules for different parsers; keep them apart."""
    subject = tmp_path / "O'Brien" / "clip.mp4"
    assert ff.escape_concat_path(subject) != ff.escape_filter_path(subject)


@pytest.mark.slow
def test_ffmpeg_concatenates_clips_under_an_apostrophe(tmp_path):
    """The default transition stream-copies through the concat demuxer.

    An unescaped apostrophe ended the quoted section early and ffmpeg reported
    "Impossible to open" against a path with the character missing, so the
    common render path died outright on a machine whose user folder has one.
    """
    from vidsmith.render import _concat_copy

    try:
        ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")

    root = tmp_path / "O'Brien"
    root.mkdir()
    clips = []
    for i, colour in enumerate(("red", "green")):
        clip = root / f"clip{i}.mp4"
        ff.run(["-f", "lavfi", "-i", f"color=c={colour}:s=320x240:d=1",
                "-r", "24", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(clip)])
        clips.append(clip)

    out = _concat_copy(clips, root / "joined.mp4", root)
    assert out.exists()
    assert ff.duration(out) == pytest.approx(2.0, abs=0.2)


# --------------------------------------------------------------------------- #
# staging, for paths the parsers disagree about
# --------------------------------------------------------------------------- #
def test_an_ordinary_path_is_left_where_it_is(tmp_path):
    """Staging is a last resort; nothing should be copied without cause."""
    subs = tmp_path / "captions.ass"
    subs.write_text(ASS, encoding="utf-8")
    staging = tmp_path / ".filtergraph"
    assert ff.filtergraph_safe(subs, staging) == subs.resolve()
    assert not staging.exists(), "an ordinary path was copied for no reason"


def test_an_apostrophe_in_the_parent_is_staged_away(tmp_path):
    """The macOS failure: the escaping is version-dependent, the copy is not."""
    root = tmp_path / "O'Brien"
    root.mkdir()
    subs = root / "captions.ass"
    subs.write_text(ASS, encoding="utf-8")

    staged = ff.filtergraph_safe(subs, tmp_path / ".filtergraph")
    assert staged != subs.resolve()
    assert "'" not in staged.as_posix()
    assert staged.read_text(encoding="utf-8") == ASS


def test_ffmpeg_opens_a_staged_subtitle_file(tmp_path):
    """End to end, which is the only claim worth making about escaping."""
    try:
        binary = ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")

    root = tmp_path / "O'Brien"
    root.mkdir()
    subs = root / "captions.ass"
    subs.write_text(ASS, encoding="utf-8")
    out = root / "frame.png"

    staged = ff.filtergraph_safe(subs, tmp_path / ".filtergraph")
    proc = subprocess.run(
        [binary, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
         "-vf", f"subtitles='{ff.escape_filter_path(staged)}'",
         "-frames:v", "1", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr.strip()[:400]
    assert out.exists()
