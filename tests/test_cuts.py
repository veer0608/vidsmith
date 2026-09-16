"""Adding a Shorts version to a finished render.

The narration, timings and searches are shared between shapes, so a new cut is
the same build with the aspect overridden. What can go wrong is the sharing:
`scenes.json` holds the shots of whichever cut was built last, and the
description, title and thumbnail of the first cut must not be rewritten by the
second.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_retake import finished_build

from vidsmith import cuts, pipeline, retake, rewrite, snapshot
from vidsmith.script_parser import load_scenes, save_scenes


@pytest.fixture
def root(tmp_path):
    return finished_build(tmp_path / "job")


def test_a_cut_must_be_a_known_shape_not_already_delivered(root):
    with pytest.raises(cuts.RetakeRefused, match="a cut is one of"):
        cuts.check(root, "9x16")
    with pytest.raises(cuts.RetakeRefused, match="already has a 16:9 cut"):
        cuts.check(root, "16:9")
    cuts.check(root, "9:16")


def test_a_render_without_its_build_cannot_reuse_its_narration(root):
    (root / "build" / "scenes.json").unlink()
    with pytest.raises(cuts.RetakeRefused, match="kept no working files"):
        cuts.check(root, "9:16")


def test_adding_a_cut_overrides_the_aspect_and_records_the_first_cuts_shots(root, monkeypatch):
    calls = []

    def build(root, log=print, overrides=None, cut=False):
        calls.append((overrides, cut))
        # the second shape rewrites the shared file with its own shots
        scenes = load_scenes(Path(root) / "build" / "scenes.json")
        for scene in scenes:
            scene.shots = [{"path": "vertical.mp4", "duration": scene.duration}]
        save_scenes(scenes, Path(root) / "build" / "scenes.json")
        (Path(root) / "out" / "a-title-9x16.mp4").write_bytes(b"short")
        return Path(root) / "out" / "a-title-9x16.mp4"

    monkeypatch.setattr(cuts.pipeline, "build", build)
    cuts.add(root, "9:16", log=lambda *a: None)

    assert calls == [({"aspect": "9:16"}, True)]
    assert cuts.shapes(root) == ["16:9", "9:16"]
    rows = retake.shots(root)["shots"]
    assert [(r["scene"], r["shot"]) for r in rows] == [(0, 0), (0, 1), (1, 0)], \
        "a shot swap on the first cut read the vertical cut's shots"
    assert not (root / snapshot.BACKUP).exists()


def test_a_failed_cut_leaves_the_render_as_it_was(root, monkeypatch):
    def broken(root, log=print, overrides=None, cut=False):
        root = Path(root)
        (root / "build" / "visuals-9x16").mkdir()
        (root / "out" / "a-title-9x16.mp4").write_bytes(b"half")
        raise RuntimeError("no vertical footage")

    monkeypatch.setattr(cuts.pipeline, "build", broken)
    with pytest.raises(RuntimeError):
        cuts.add(root, "9:16")

    assert cuts.shapes(root) == ["16:9"]
    assert not (root / "build" / "visuals-9x16").exists()


def test_the_shared_shots_are_read_per_cut(tmp_path):
    scenes = load_scenes(finished_build(tmp_path / "job") / "build" / "scenes.json")
    vis = tmp_path / "visuals-9x16"
    for scene in scenes:
        scene.shots = [{"path": f"v{scene.index}.mp4", "duration": 1.0}]
    pipeline.write_shots(vis, scenes)
    fresh = load_scenes(tmp_path / "job" / "build" / "scenes.json")

    pipeline.read_shots(vis, fresh)

    assert [s.shots[0]["path"] for s in fresh] == ["v0.mp4", "v1.mp4"]
    pipeline.read_shots(tmp_path / "nowhere", fresh)          # no file: left alone


def test_an_edit_rebuilds_every_other_cut_with_the_new_words(root, monkeypatch):
    (root / "out" / "a-title-9x16.mp4").write_bytes(b"short")
    calls = []
    monkeypatch.setattr(rewrite.pipeline, "build",
                        lambda root, log=print, **kw: calls.append(kw))

    rewrite.apply(root, 1, "An engineer ships the fix.", log=lambda *a: None)

    assert calls == [{"edit": True}, {"overrides": {"aspect": "9:16"}, "cut": True}]
