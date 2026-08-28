"""Reading a delivery's files against each other.

Every fault checked here was found by hand this week, in files that each looked
correct on their own and wrong side by side: a thumbnail refreshed while the
description went on crediting the photographer it had dropped, a refresh writing
`untitled.jpg` beside correctly named cuts and reporting success.

None of it calls a model or the network, so it works on a day the quota is gone,
which is exactly when a hurried refresh gets published.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vidsmith.check import check, seconds

pytest.importorskip("PIL")
from PIL import Image                              # noqa: E402


def _mp4(path: Path, seconds_long: float = 4.0) -> Path:
    from vidsmith import ffmpeg_util as ff

    ff.run(["-f", "lavfi", "-i", f"color=c=black:s=320x180:d={seconds_long}",
            "-r", "12", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(path)])
    return path


def _jpg(path: Path, size) -> Path:
    Image.new("RGB", size, (30, 30, 30)).save(path, "JPEG")
    return path


SRT = "1\n00:00:00,000 --> 00:00:02,000\nhello\n\n"


@pytest.fixture
def delivery(tmp_path):
    """A clean, consistent delivery. Each test spoils one thing about it."""
    pytest.importorskip("PIL")
    from vidsmith import ffmpeg_util as ff

    try:
        ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")

    out = tmp_path / "out"
    out.mkdir()
    _mp4(out / "a-title.mp4")
    _mp4(out / "a-title-9x16.mp4")
    _jpg(out / "a-title.jpg", (1280, 720))
    _jpg(out / "a-title-9x16.jpg", (720, 1280))
    (out / "captions.srt").write_text(SRT, encoding="utf-8")
    (out / "captions-9x16.srt").write_text(SRT, encoding="utf-8")
    (out / "credits.txt").write_text(
        "Footage from Pexels\nThumbnail: Real Name - https://p\n", encoding="utf-8")
    (out / "description.txt").write_text(
        "A description.\n\n0:00 Start\n\nThumbnail: Real Name - https://p\n",
        encoding="utf-8")
    (out / "youtube.json").write_text(json.dumps(
        {"title": "A Title", "chapters": [{"time": "0:00", "label": "Start"}]}),
        encoding="utf-8")
    return out


@pytest.mark.slow
def test_a_consistent_delivery_reports_nothing(delivery):
    assert check(delivery) == []


@pytest.mark.slow
def test_a_credit_that_never_reached_the_description_is_caught(delivery):
    """The bug that shipped: credits.txt was corrected and description.txt,
    which is the file that actually gets published, was not."""
    (delivery / "credits.txt").write_text(
        "Footage from Pexels\nThumbnail: Someone Else - https://q\n", encoding="utf-8")
    found = check(delivery)
    assert any("would not be published" in p for p in found), found


@pytest.mark.slow
def test_a_thumbnail_naming_no_cut_is_caught(delivery):
    """`untitled.jpg` beside correctly named files, from a refresh that
    resolved the title differently to the build."""
    _jpg(delivery / "untitled.jpg", (1280, 720))
    assert any("matches no delivered cut" in p for p in check(delivery))


@pytest.mark.slow
def test_a_portrait_thumbnail_on_the_wide_cut_is_caught(delivery):
    _jpg(delivery / "a-title.jpg", (720, 1280))
    assert any("portrait" in p for p in check(delivery))


@pytest.mark.slow
def test_captions_running_past_the_video_are_caught(delivery):
    (delivery / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:09:59,000\nhello\n\n", encoding="utf-8")
    assert any("past the" in p for p in check(delivery))


@pytest.mark.slow
def test_a_chapter_past_the_end_is_caught(delivery):
    (delivery / "youtube.json").write_text(json.dumps(
        {"chapters": [{"time": "0:00", "label": "Start"},
                      {"time": "9:00", "label": "Late"}]}), encoding="utf-8")
    assert any("past the" in p for p in check(delivery))


@pytest.mark.slow
def test_chapters_not_starting_at_zero_are_caught(delivery):
    """YouTube drops the whole list rather than the offending line."""
    (delivery / "youtube.json").write_text(json.dumps(
        {"chapters": [{"time": "0:04", "label": "Start"}]}), encoding="utf-8")
    assert any("0:00" in p for p in check(delivery))


@pytest.mark.slow
def test_a_missing_thumbnail_is_caught(delivery):
    (delivery / "a-title.jpg").unlink()
    assert any("no thumbnail" in p for p in check(delivery))


def test_an_empty_directory_says_so(tmp_path):
    assert check(tmp_path) == ["no widescreen mp4 in out/; nothing has been delivered"]


@pytest.mark.parametrize("stamp,want", [
    ("0:00", 0.0), ("1:23", 83.0), ("00:01:23,400", 83.4), ("1:00:00", 3600.0),
])
def test_both_stamp_formats_parse(stamp, want):
    """Chapters are written `1:23` and SRT ends `00:01:23,400`."""
    assert seconds(stamp) == want


# --------------------------------------------------------------------------- #
# where it runs from
# --------------------------------------------------------------------------- #
def test_a_finished_build_reads_its_own_delivery():
    """Finding this at upload time means it was already wrong for a while.

    A full build ends by checking what it just wrote, so an inconsistency is
    named while the person who caused it is still watching the log.
    """
    import inspect

    from vidsmith import pipeline

    source = inspect.getsource(pipeline.build)
    assert "check(proj.out)" in source, "a build never reads back what it wrote"


def test_the_check_cannot_fail_a_render():
    """A finished render is not thrown away over something wrong beside it -
    the same rule the thumbnail already follows."""
    import inspect

    from vidsmith import pipeline

    source = inspect.getsource(pipeline.build)
    tail = source[source.index("from .check import check"):]
    assert "except Exception" in tail, "a fault in the check would lose the render"
    assert "raise" not in tail, "the check must report, never raise"


def test_a_passing_check_still_says_something():
    """Silence and never-having-run look identical in a log.

    The first wiring of this only logged problems, so a clean build printed
    nothing and there was no way to tell the check had happened at all.
    """
    import inspect

    from vidsmith import pipeline

    source = inspect.getsource(pipeline.build)
    assert "delivery is consistent" in source, \
        "a clean check is silent, so it cannot be distinguished from a missing one"
