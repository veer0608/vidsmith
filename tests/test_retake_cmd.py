"""`vidsmith retake`: re-film one scene, keep the narration and the rest.

Scene 5 of machine-statements was re-filmed by hand first: drop its entries
from beats.json, call invalidate(only={5}), build again. The hand version is
what these tests hold the command to. The part worth protecting is the
narrowing - a retake that dropped every scene's searches would spend a vision
call per scene against a budget reported in no header, which is the cost
invalidate(only=...) exists to avoid.
"""
from __future__ import annotations

import argparse
import json

import pytest

import vidsmith.cli as cli
import vidsmith.pipeline as pipeline
from conftest import make_scene
from vidsmith.visuals import forget_beats

TEXT = ("A long merchant name runs out of room and finishes on the line below. "
        "Your eye joins the two halves without noticing.")


def _cache(tmp_path, entries):
    (tmp_path / "beats.json").write_text(json.dumps(entries), encoding="utf-8")
    return tmp_path


def test_forget_beats_drops_this_scene_and_no_other(tmp_path):
    scene = make_scene(TEXT, index=5, heading="Broken Words")
    _cache(tmp_path, {
        "aaa": {"text": "A long merchant name runs out of room and finishes "
                        "on the line below.", "query": "broken text on monitor"},
        "bbb": {"text": "Your eye joins the two halves without noticing.",
                "query": "database error on screen"},
        "ccc": {"text": "Footer notes sit in the transaction columns.",
                "query": "accountant typing on calculator"},
    })

    dropped = forget_beats(tmp_path, scene)

    assert sorted(dropped) == ["broken text on monitor", "database error on screen"]
    left = json.loads((tmp_path / "beats.json").read_text(encoding="utf-8"))
    assert list(left) == ["ccc"], "another scene's search was dropped"


def test_forget_beats_survives_a_project_with_no_cache(tmp_path):
    assert forget_beats(tmp_path, make_scene(TEXT)) == []


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "scenes.json").write_text("[]", encoding="utf-8")
    scenes = [make_scene("One two three.", index=i, heading=f"S{i}")
              for i in range(9)]
    monkeypatch.setattr(cli, "_project_dir", lambda name: tmp_path)
    monkeypatch.setattr("vidsmith.script_parser.load_scenes", lambda p: scenes)
    seen = {"invalidated": None, "built": 0}

    def invalidate(proj, log=print, only=None, respoken=None):
        seen["invalidated"] = only

    def build(root, force=(), stop_after="", overrides=None, log=print, **kw):
        seen["built"] += 1
        seen["overrides"] = overrides
        return tmp_path / "out" / "v.mp4"

    monkeypatch.setattr(pipeline, "invalidate", invalidate)
    monkeypatch.setattr(pipeline, "build", build)
    monkeypatch.setattr("vidsmith.visuals.forget_beats",
                        lambda build_dir, scene: ["broken text on monitor"])
    return seen


def _run(**over):
    args = {"name": "demo", "scene": 5, "provider": None, "genre": None}
    args.update(over)
    return cli.cmd_retake(argparse.Namespace(**args))


def test_a_retake_narrows_to_the_one_scene(project, capsys):
    assert _run() == 0
    assert project["invalidated"] == {5}, "a retake must not re-rank every scene"
    assert project["built"] == 1
    said = capsys.readouterr().out
    assert "forgetting the search: broken text on monitor" in said


def test_a_scene_that_does_not_exist_changes_nothing(project, capsys):
    assert _run(scene=99) == 1
    assert project["invalidated"] is None and project["built"] == 0
    assert "0 to 8" in capsys.readouterr().out


def test_an_unbuilt_project_is_told_to_build(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_project_dir", lambda name: tmp_path)
    assert _run() == 1
    assert "run: vidsmith build" in capsys.readouterr().out


def test_the_provider_and_genre_reach_the_build(project):
    assert _run(provider="pixabay", genre="documentary") == 0
    assert project["overrides"] == {"provider": "pixabay", "genre": "documentary"}
