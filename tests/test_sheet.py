"""The shot sheet: a frame per shot beside the words spoken over it.

Every footage fault in this project was found by reading one of these and none
by reading a log, which is why it is a command rather than an ffmpeg line
retyped each time.
"""
from __future__ import annotations

import json

import pytest

from conftest import make_scene

from vidsmith import sheet as sheet_mod
from vidsmith.config import write_default_config
from vidsmith.script_parser import save_scenes

TEXT = ("A search that reads every row gets slower as the table grows. Sorting the "
        "rows lets you open the list in the middle and throw away half of it.")


def _project(tmp_path, shots=(4.0, 4.0), aspect="16:9", title_card=False):
    root = tmp_path / "proj"
    (root / "build" / "visuals").mkdir(parents=True)
    (root / "out").mkdir(parents=True)
    (root / "script.md").write_text(f"# A Video\n\n{TEXT}\n", encoding="utf-8")
    write_default_config(root / "config.yaml", "A Video")
    if aspect != "16:9" or title_card:
        import yaml

        raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        raw["render"]["aspect"] = aspect
        raw["theme"]["title_card"] = title_card
        (root / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    scene = make_scene(TEXT, heading="Halving it", query="index cards in a drawer")
    scene.shots = list(shots)
    save_scenes([scene], root / "build" / "scenes.json")
    (root / "build" / "picture.mp4").write_bytes(b"mp4")
    return root, scene


def _runs(calls):
    def run(args, **kwargs):
        calls.append(args)
        out = args[-1]
        open(out, "wb").write(b"jpg")
        return None

    return run


def test_every_shot_gets_a_frame_and_its_own_words(tmp_path):
    root, scene = _project(tmp_path)
    calls = []

    page = sheet_mod.build_sheet(root, log=lambda *a: None, run=_runs(calls))

    assert len(calls) == 2, "one ffmpeg call per shot"
    body = page.read_text(encoding="utf-8")
    assert body.count("<figure>") == 2
    assert "index cards in a drawer" in body
    first, second = [" ".join(c) for c in calls]
    assert "-ss 2.000" in first and "-ss 6.000" in second, "sampled mid-shot"


def test_the_words_follow_the_shot_they_are_spoken_over(tmp_path):
    scene = make_scene(TEXT, heading="Halving it")
    # the shots of a scene always sum to its duration; a plan that stops short
    # would drop the tail of the narration off the sheet
    scene.shots = [scene.duration / 2, scene.duration / 2]
    rows = sheet_mod.shot_times([scene], lead_in=0.25)

    assert len(rows) == 2
    assert rows[0]["words"] and rows[1]["words"]
    assert rows[0]["words"] != rows[1]["words"]
    spoken = (rows[0]["words"] + " " + rows[1]["words"]).split()
    assert spoken == [w["text"] for w in scene.words], "a word was lost or doubled"


def test_a_title_card_shifts_every_frame(tmp_path):
    scene = make_scene(TEXT)
    scene.shots = [4.0, 4.0]
    plain = sheet_mod.shot_times([scene])
    carded = sheet_mod.shot_times([scene], offset=2.4)

    assert [r["start"] + 2.4 for r in plain] == [r["start"] for r in carded]


def test_the_credit_for_each_shot_is_shown(tmp_path):
    root, _ = _project(tmp_path)
    (root / "build" / "visuals" / "credits.json").write_text(json.dumps({
        "0:0": {"credit": "Ada", "url": "http://x.test/a"}}), encoding="utf-8")

    page = sheet_mod.build_sheet(root, log=lambda *a: None, run=_runs([]))

    assert "Ada - http://x.test/a" in page.read_text(encoding="utf-8")


def test_the_delivered_cut_is_used_when_the_picture_is_gone(tmp_path):
    root, _ = _project(tmp_path)
    (root / "build" / "picture.mp4").unlink()
    (root / "out" / "a-video.mp4").write_bytes(b"mp4")
    calls = []

    sheet_mod.build_sheet(root, log=lambda *a: None, run=_runs(calls))

    assert all("a-video.mp4" in " ".join(c) for c in calls)


def test_a_short_cut_is_not_read_as_the_widescreen_one(tmp_path):
    """The empty-tag family: `*.mp4` matches every other cut, and `a-9x16.mp4`
    sorts before `a.mp4`."""
    root, _ = _project(tmp_path)
    (root / "build" / "picture.mp4").unlink()
    (root / "out" / "a-video-9x16.mp4").write_bytes(b"mp4")
    (root / "out" / "a-video.mp4").write_bytes(b"mp4")
    calls = []

    sheet_mod.build_sheet(root, log=lambda *a: None, run=_runs(calls))

    assert all("9x16" not in " ".join(c) for c in calls)


def test_a_project_with_no_build_says_so(tmp_path):
    root, _ = _project(tmp_path)
    (root / "build" / "scenes.json").unlink()

    with pytest.raises(sheet_mod.SheetFailed, match="run vidsmith build first"):
        sheet_mod.build_sheet(root, log=lambda *a: None, run=_runs([]))


def test_a_missing_video_says_which_shape_is_missing(tmp_path):
    root, _ = _project(tmp_path)
    (root / "build" / "picture.mp4").unlink()

    with pytest.raises(sheet_mod.SheetFailed, match="no 16:9 video"):
        sheet_mod.build_sheet(root, log=lambda *a: None, run=_runs([]))


def test_a_build_with_no_per_cut_shots_says_the_times_may_be_another_cuts(tmp_path):
    """`scenes.json` holds whichever cut was built last, and a real project
    from before per-cut shots sampled a 16:9 picture on 9:16 timings."""
    root, _ = _project(tmp_path)
    lines = []

    sheet_mod.build_sheet(root, log=lines.append, run=_runs([]))

    assert any("built last" in line for line in lines)


def test_a_build_that_records_its_own_shots_warns_about_nothing(tmp_path):
    root, scene = _project(tmp_path)
    (root / "build" / "visuals" / "shots.json").write_text(
        json.dumps({"0": [{"duration": d} for d in scene.shots]}), encoding="utf-8")
    lines = []

    sheet_mod.build_sheet(root, log=lines.append, run=_runs([]))

    assert not any("warning" in line for line in lines)


# --------------------------------------------------------------------------- #
# a published project keeps out/ and loses build/
# --------------------------------------------------------------------------- #
SRT = """1
00:00:00,354 --> 00:00:01,677
You likely picture the fall of

2
00:00:01,697 --> 00:00:03,381
the Roman Empire as a sudden,

3
00:00:09,000 --> 00:00:11,200
Rome did not vanish in a single afternoon.
"""


def test_captions_become_blocks_of_a_few_seconds():
    rows = sheet_mod.caption_rows(SRT, block_seconds=6.0)

    assert len(rows) == 2, "two cues close together are one block"
    assert rows[0]["words"] == "You likely picture the fall of the Roman Empire as a sudden,"
    assert rows[0]["start"] == pytest.approx(0.354)
    assert rows[0]["seconds"] == pytest.approx(3.381 - 0.354)
    assert rows[1]["start"] == pytest.approx(9.0)
    assert rows[0]["middle"] < rows[1]["middle"]


def test_a_delivered_project_with_no_build_uses_its_captions(tmp_path):
    """rome kept out/ and lost build/, so there are no shots to cut on."""
    root, _ = _project(tmp_path)
    for path in (root / "build").rglob("*"):
        if path.is_file():
            path.unlink()
    (root / "out" / "rome.mp4").write_bytes(b"mp4")
    (root / "out" / "captions.srt").write_text(SRT, encoding="utf-8")
    lines, calls = [], []

    page = sheet_mod.build_sheet(root, log=lines.append, run=_runs(calls))

    body = page.read_text(encoding="utf-8")
    assert "caption block 1" in body and "scene 0.0" not in body
    assert "cut by caption rather than by shot" in " ".join(lines)
    assert len(calls) == 2 and all("rome.mp4" in " ".join(c) for c in calls)


def test_a_project_with_neither_a_build_nor_captions_says_both(tmp_path):
    root, _ = _project(tmp_path)
    (root / "build" / "scenes.json").unlink()
    (root / "build" / "picture.mp4").unlink()
    (root / "out" / "rome.mp4").write_bytes(b"mp4")

    with pytest.raises(sheet_mod.SheetFailed, match="no captions.srt beside"):
        sheet_mod.build_sheet(root, log=lambda *a: None, run=_runs([]))
