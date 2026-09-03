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
    # the whole credits block, as description_box() actually writes it: the
    # "Footage from ..." line is the prominent link the API terms ask for, not
    # a heading, so a description missing it is under-crediting
    for tag in ("", "-9x16"):
        (out / f"description{tag}.txt").write_text(
            "A description.\n\n0:00 Start\n\n"
            "Footage from Pexels\nThumbnail: Real Name - https://p\n",
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


# --------------------------------------------------------------------------- #
# the empty tag, again, this time in the checker itself
# --------------------------------------------------------------------------- #
def test_every_aspect_resolves_to_its_own_shape(tmp_path):
    """`a-1x1.mp4` sorts before `a.mp4`, because `-` is 0x2D and `.` is 0x2E.

    Taking the first name that was not a short therefore handed back the square
    cut as the widescreen one. Nothing here needs a real encode: the fault was
    entirely in the names, which is why it survived a suite full of real ones.
    """
    from vidsmith.check import delivered

    for name in ("a-title.mp4", "a-title-9x16.mp4", "a-title-1x1.mp4",
                 "a-title-4x5.mp4"):
        (tmp_path / name).touch()

    cuts = delivered(tmp_path)
    assert {a: p.name for a, p in cuts} == {
        "16:9": "a-title.mp4",
        "9:16": "a-title-9x16.mp4",
        "1:1": "a-title-1x1.mp4",
        "4:5": "a-title-4x5.mp4",
    }
    assert cuts[0][0] == "16:9", "the widescreen cut must come first"


@pytest.mark.slow
def test_a_square_cut_beside_the_wide_one_is_not_a_fault(delivery):
    """All four shapes are one edit at four sizes; delivering them is normal.

    Before `delivered()`, adding a 1:1 cut made the checker report both the
    16:9 thumbnail and the 4:5 one as matching no delivered cut, because the
    set of known names had been built from the square cut and the shorts.
    """
    _mp4(delivery / "a-title-1x1.mp4")
    _jpg(delivery / "a-title-1x1.jpg", (1280, 720))
    (delivery / "captions-1x1.srt").write_text(SRT, encoding="utf-8")
    assert check(delivery) == []


@pytest.mark.slow
def test_the_widescreen_cut_is_still_checked_beside_a_square_one(delivery):
    """The quiet half of the same bug, and the half that would have shipped.

    The real 16:9 cut was neither the resolved widescreen one nor a short, so
    it fell out of every loop: its runtime, its captions and its thumbnail went
    unexamined the moment a 1:1 cut existed beside it.
    """
    _mp4(delivery / "a-title-1x1.mp4")
    _jpg(delivery / "a-title-1x1.jpg", (1280, 720))
    (delivery / "captions-1x1.srt").write_text(SRT, encoding="utf-8")

    (delivery / "a-title.jpg").unlink()
    found = check(delivery)
    assert any("a-title.jpg" in p and "no thumbnail" in p for p in found), found


@pytest.mark.slow
def test_a_portrait_thumbnail_on_the_four_five_cut_is_caught(delivery):
    """4:5 is vertical and was not a short, so it was checked as landscape."""
    _mp4(delivery / "a-title-4x5.mp4")
    _jpg(delivery / "a-title-4x5.jpg", (1280, 720))
    (delivery / "captions-4x5.srt").write_text(SRT, encoding="utf-8")
    found = check(delivery)
    assert any("a-title-4x5.jpg" in p and "vertical cut" in p for p in found), found


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


def test_the_readme_documents_every_command_that_exists():
    """A command nobody can find is a command nobody runs.

    `check` and `thumbs --refresh` both shipped without reaching the README, so
    the only place they were written down was a file aimed at Claude rather than
    at whoever clones this.
    """
    import re
    from pathlib import Path

    from vidsmith import cli

    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    if parser is None:
        import argparse

        # the parser is assembled inside main(); read the subcommand names off
        # the same source rather than duplicating the list here
        source = __import__("inspect").getsource(cli)
        commands = set(re.findall(r'sub\.add_parser\("([a-z]+)"', source))
    else:
        commands = {a for a in parser._subparsers._actions[-1].choices}

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8")
    missing = sorted(c for c in commands if f"vidsmith {c}" not in readme)
    assert not missing, f"undocumented commands: {missing}"


# ---- a scene that ran out of footage ---------------------------------- #

def _edit(tmp_path: Path, tag: str, shots_per_scene, durations, diagrams=()):
    """A build/ holding one aspect's shot ledger and the shared scene timings."""
    build = tmp_path / "build"
    (build / f"visuals{tag}").mkdir(parents=True, exist_ok=True)
    credits = {}
    scenes = []
    for index, (shots, duration) in enumerate(zip(shots_per_scene, durations)):
        for shot in range(shots):
            credits[f"{index}:{shot}"] = {"credit": "A Creator", "url": "http://x"}
        scenes.append({
            "index": index,
            "heading": f"Scene {index}",
            "duration": duration,
            "diagram": diagrams[index] if index < len(diagrams) else "",
        })
    (build / f"visuals{tag}" / "credits.json").write_text(
        json.dumps(credits), encoding="utf-8")
    return build, scenes


def test_a_scene_that_sat_on_one_clip_is_a_fault(tmp_path):
    """The 9:16 `uses` cut shipped 11.6s of one clip and check called it fine.

    `plan_shots` asks for shots between 2.4s and 5.5s, so a scene holding one
    clip for triple that did not choose it: reranking left too few usable
    candidates and `collapse()` merged the plan to keep the total exact. The
    build log said so - "rejected 15 of 15 as the wrong subject" - and nothing
    carried that as far as the delivery.
    """
    from vidsmith.check import frozen_shots

    build, scenes = _edit(tmp_path, "-9x16", shots_per_scene=[1, 3],
                          durations=[16.8, 12.0])
    problems = frozen_shots(build, "9:16", scenes)

    assert len(problems) == 1
    assert "16.8s" in problems[0]
    assert "9:16" in problems[0] and "Scene 0" in problems[0]


def test_an_ordinary_edit_is_not_a_fault(tmp_path):
    from vidsmith.check import frozen_shots

    build, scenes = _edit(tmp_path, "", shots_per_scene=[4, 3, 2],
                          durations=[16.0, 12.0, 9.0])

    assert frozen_shots(build, "16:9", scenes) == []


def test_a_drawn_scene_is_allowed_to_hold(tmp_path):
    """`[diagram: ...]` is one frame by design, however long the scene runs."""
    from vidsmith.check import frozen_shots

    build, scenes = _edit(tmp_path, "", shots_per_scene=[1], durations=[20.0],
                          diagrams=["how a b-tree splits"])

    assert frozen_shots(build, "16:9", scenes) == []


def test_each_aspect_is_read_from_its_own_ledger(tmp_path):
    """`scenes.json` is shared across cuts; the shot list is not.

    Reading shot counts from the shared file would report whichever aspect was
    built last, which is the same empty-tag family of fault that had `thumbs`
    and `check` both sampling the wrong cut.
    """
    from vidsmith.check import frozen_shots

    build, scenes = _edit(tmp_path, "", shots_per_scene=[4], durations=[16.0])
    _edit(tmp_path, "-9x16", shots_per_scene=[1], durations=[16.0])

    assert frozen_shots(build, "16:9", scenes) == []
    assert len(frozen_shots(build, "9:16", scenes)) == 1


def test_a_build_with_no_ledger_is_not_a_fault(tmp_path):
    """A cards or local build owes no credits, and a job pulled down from the
    web service arrives as out/ with no build/ beside it at all."""
    from vidsmith.check import frozen_shots

    (tmp_path / "build").mkdir()
    assert frozen_shots(tmp_path / "build", "16:9", [{"index": 0, "duration": 20.0}]) == []
