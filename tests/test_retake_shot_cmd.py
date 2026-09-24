"""`vidsmith retake --scene N --shot J`: one shot changed, every other kept.

A scene retake re-films every shot in it. howto's scene 1 had one bad clip
among three good ones, and its second roll kept the bad clip and lost a real
`pip install` log. The page could already swap one shot (`retake.py`); the
command line could not, so this drives the same code rather than a copy.
"""
from __future__ import annotations

import json
from io import BytesIO

import pytest
from PIL import Image

from vidsmith import cli, pipeline, retake, visuals

from test_retake import _hits, finished_build


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (320, 180), "grey").save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def root(tmp_path, monkeypatch):
    job = finished_build(tmp_path / "demo")
    monkeypatch.setattr(cli, "_project_dir", lambda name: job)
    # a test must never find the machine's own keys
    monkeypatch.setattr(pipeline, "find_keys",
                        lambda root: {"pexels": "x", "pixabay": "", "gemini": ""})
    monkeypatch.setattr(visuals, "preview_still", lambda url: _jpeg())
    return job


@pytest.fixture
def searches(monkeypatch):
    asked = []

    def search(query, key, orientation, want_h):
        asked.append(query)
        return _hits("10", "20", "21", "22", "23")

    monkeypatch.setattr(visuals, "pexels_search", search)
    return asked


def _run(*extra):
    return cli.main(["retake", "demo", "--scene", "0", *extra])


def test_the_candidates_are_listed_with_the_command_that_takes_the_first(
        root, searches, capsys):
    assert _run("--shot", "1") == 0
    said = capsys.readouterr().out
    assert "searched 'server rack'; now 11" in said
    for clip in ("20", "21", "22", "23"):
        assert f"https://pexels.com/v/{clip}" in said
    assert "https://pexels.com/v/10\n" not in said, "10 is already shot 0:0"
    assert "next:    vidsmith retake demo --scene 0 --shot 1 --clip 20" in said


def test_the_candidates_stills_are_tiled_into_one_image(root, searches, capsys):
    _run("--shot", "1")
    line = next(l for l in capsys.readouterr().out.splitlines()
                if l.startswith("stills"))
    sheet = Image.open(line.split(None, 1)[1].strip())
    assert sheet.size == (3 * 320, 2 * 180), "four stills, three to a row"


def test_listing_changes_nothing(root, searches):
    vis = root / "build" / "visuals"
    before = {p: p.read_bytes() for p in (vis / "credits.json",
                                          vis / "scene_000_01.mp4",
                                          root / "out" / "a-title.mp4")}
    _run("--shot", "1")
    assert {p: p.read_bytes() for p in before} == before


def test_no_stills_is_not_a_failed_listing(root, searches, monkeypatch, capsys):
    monkeypatch.setattr(visuals, "preview_still", lambda url: None)
    assert _run("--shot", "1") == 0
    assert "stills" not in capsys.readouterr().out


def test_a_typed_search_is_asked_and_kept_in_the_next_command(root, searches, capsys):
    _run("--shot", "1", "--search", "blinking server lights")
    assert searches == ["blinking server lights"]
    assert '--clip 20 --search "blinking server lights"' in capsys.readouterr().out


def test_a_clip_goes_through_the_pages_own_retake(root, monkeypatch, capsys):
    """One writer: a second copy of the swap is how credits go missing."""
    seen = []

    def replace(root_, scene, shot, clip, keys=None, query=None, log=print):
        seen.append((root_, scene, shot, clip, query))
        return root_ / "out" / "a-title.mp4"

    monkeypatch.setattr(retake, "replace", replace)
    assert _run("--shot", "1", "--clip", "20") == 0
    assert seen == [(root, 0, 1, "20", None)]
    assert "next:    vidsmith sheet demo" in capsys.readouterr().out


def test_a_refusal_is_said_and_fails(root, searches, capsys):
    assert _run("--shot", "7") == 1
    assert capsys.readouterr().out.startswith("refused  this render has no shot 7")


def test_nothing_free_to_use_says_what_to_try(root, monkeypatch, capsys):
    monkeypatch.setattr(visuals, "pexels_search",
                        lambda *a: _hits("10", "11", "12"))
    assert _run("--shot", "1") == 1
    assert "try --search" in capsys.readouterr().out


def test_a_shot_keeps_the_projects_library(root, searches, capsys):
    assert _run("--shot", "1", "--provider", "pixabay") == 1
    assert searches == []
    assert "re-film a whole scene" in capsys.readouterr().out


def test_a_clip_without_a_shot_is_refused_rather_than_re_filming_the_scene(
        root, monkeypatch, capsys):
    built = []
    monkeypatch.setattr(pipeline, "build", lambda *a, **k: built.append(1))
    assert _run("--clip", "20") == 1
    assert built == []
    assert "add --shot" in capsys.readouterr().out
    assert json.loads((root / "build" / "visuals" / "credits.json")
                      .read_text(encoding="utf-8"))["0:1"]["id"] == "11"
