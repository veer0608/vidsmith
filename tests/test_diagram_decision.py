"""Whether a scene is drawn must be decided once, for every cut.

The model's filmability verdict is not stable between runs: on the same script
the landscape pass called three scenes unfilmable and the portrait pass called
none of them unfilmable. Left per-aspect, that produced a 16:9 cut and a Shorts
cut of the same video that showed different things.
"""
from __future__ import annotations

import json

import pytest

from vidsmith.config import ThemeConfig, VisualConfig
from vidsmith.diagram import Spec
from vidsmith.theme import resolve
from vidsmith.visuals import VisualBuilder

TREE = {"kind": "tree", "title": "How it branches",
        "nodes": ["Root", "Leaf A", "Leaf B"]}


def _builder(tmp_path, provider="pexels", **cfg_kwargs):
    workdir = tmp_path / "visuals"
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = VisualConfig(provider=provider, **cfg_kwargs)
    return VisualBuilder(cfg, (640, 360), 24, workdir, keys={"gemini": "x"},
                         log=lambda *a: None, theme=resolve("midnight"),
                         theme_cfg=ThemeConfig(), total_scenes=1)


def test_the_decision_file_sits_beside_the_narration(tmp_path):
    """Per-aspect would let the two cuts disagree; the build root is shared."""
    builder = _builder(tmp_path)
    assert builder._decision_path().parent == tmp_path
    assert builder._decision_path().name == "diagram_scenes.json"


def test_a_decision_round_trips(tmp_path, scene):
    builder = _builder(tmp_path)
    assert builder._decisions() == {}
    builder._decide(scene, True)
    assert _builder(tmp_path)._decisions() == {"0": True}


def test_decisions_accumulate_per_scene(tmp_path, scenes):
    builder = _builder(tmp_path)
    builder._decide(scenes[0], True)
    builder._decide(scenes[1], False)
    builder._decide(scenes[2], True)
    assert builder._decisions() == {"0": True, "1": False, "2": True}


def test_a_second_cut_reuses_the_first_cuts_decision(tmp_path, scene, monkeypatch):
    """A decided scene must not go looking for footage it will throw away."""
    (tmp_path / "diagram_scenes.json").write_text(json.dumps({"0": True}),
                                                  encoding="utf-8")
    builder = _builder(tmp_path)

    searched = []
    monkeypatch.setattr(builder, "_stock_batch",
                        lambda *a, **k: searched.append(1) or [])
    monkeypatch.setattr(builder, "_diagram_spec",
                        lambda *a, **k: Spec.from_dict(TREE))
    monkeypatch.setattr("vidsmith.visuals.diagram.render",
                        lambda *a, **k: tmp_path / "frame.png")
    monkeypatch.setattr("vidsmith.visuals.normalise_still",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)

    builder.build(scene)
    assert not searched, "a decided diagram scene still searched for footage"
    assert scene.shots and all(s["credit"] == "" for s in scene.shots)


def test_a_scene_decided_against_is_not_redrawn(tmp_path, scene, monkeypatch):
    (tmp_path / "diagram_scenes.json").write_text(json.dumps({"0": False}),
                                                  encoding="utf-8")
    builder = _builder(tmp_path)
    builder._filmable = False           # the signal that would otherwise fire

    drawn = []
    monkeypatch.setattr(builder, "_stock_batch", lambda *a, **k: [])
    monkeypatch.setattr(builder, "_diagram_spec",
                        lambda *a, **k: drawn.append(1) or Spec.from_dict(TREE))
    monkeypatch.setattr("vidsmith.visuals.normalise_still",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)
    monkeypatch.setattr("vidsmith.visuals.cards.scene_card",
                        lambda out, *a, **k: out)

    builder.build(scene)
    assert not drawn, "a scene already decided against was re-evaluated"


def test_an_explicit_directive_beats_an_absent_decision(tmp_path, scene, monkeypatch):
    scene.diagram = "a root branching to leaves"
    builder = _builder(tmp_path)

    searched = []
    monkeypatch.setattr(builder, "_stock_batch",
                        lambda *a, **k: searched.append(1) or [])
    monkeypatch.setattr(builder, "_diagram_spec",
                        lambda *a, **k: Spec.from_dict(TREE))
    monkeypatch.setattr("vidsmith.visuals.diagram.render",
                        lambda *a, **k: tmp_path / "frame.png")
    monkeypatch.setattr("vidsmith.visuals.normalise_still",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)

    builder.build(scene)
    assert not searched


def test_diagrams_off_means_footage_even_when_decided(tmp_path, scene, monkeypatch):
    (tmp_path / "diagram_scenes.json").write_text(json.dumps({"0": True}),
                                                  encoding="utf-8")
    builder = _builder(tmp_path, diagrams=False)

    drawn = []
    monkeypatch.setattr(builder, "_stock_batch", lambda *a, **k: [])
    monkeypatch.setattr(builder, "_diagram_spec",
                        lambda *a, **k: drawn.append(1) or Spec.from_dict(TREE))
    monkeypatch.setattr("vidsmith.visuals.normalise_still",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)
    monkeypatch.setattr("vidsmith.visuals.cards.scene_card",
                        lambda out, *a, **k: out)

    builder.build(scene)
    assert not drawn


def test_the_spec_cache_is_shared_too(tmp_path):
    builder = _builder(tmp_path)
    assert builder._diagram_cache_path().parent == tmp_path


# --------------------------------------------------------------------------- #
# staleness
# --------------------------------------------------------------------------- #
def test_a_redraft_drops_decisions_made_for_the_old_scenes(tmp_path):
    """Everything per-scene is keyed by index, with nothing tying it to words.

    Redraft a script and scene five is a different scene, so a stale "draw this
    one" lands on the wrong scene and the credits name a clip that is no longer
    in the video.
    """
    from vidsmith.pipeline import Project, invalidate

    proj = Project(tmp_path)
    build = proj.build
    vis = build / "visuals"
    vis.mkdir(parents=True)
    (build / "diagram_scenes.json").write_text('{"5": true}', encoding="utf-8")
    (build / "diagrams.json").write_text("{}", encoding="utf-8")
    (build / "picture.mp4").write_bytes(b"x")
    (build / "narration.wav").write_bytes(b"x")
    (vis / "rerank.json").write_text("{}", encoding="utf-8")
    (vis / "credits.json").write_text("{}", encoding="utf-8")
    (vis / "scene_005_00.mp4").write_bytes(b"x")
    (vis / "intro.mp4").write_bytes(b"x")
    cache = vis / "cache"
    cache.mkdir()
    (cache / "pexels_123.mp4").write_bytes(b"keep me")

    invalidate(proj, log=lambda *a: None)

    for gone in ("diagram_scenes.json", "diagrams.json", "picture.mp4",
                 "narration.wav"):
        assert not (build / gone).exists(), gone
    for gone in ("rerank.json", "credits.json", "scene_005_00.mp4", "intro.mp4"):
        assert not (vis / gone).exists(), gone
    # downloads are keyed by provider id, so they survive a redraft
    assert (cache / "pexels_123.mp4").read_bytes() == b"keep me"


def test_invalidating_a_fresh_project_is_harmless(tmp_path):
    from vidsmith.pipeline import Project, invalidate

    proj = Project(tmp_path)
    proj.dirs()
    invalidate(proj, log=lambda *a: None)


def test_the_mixed_narration_is_dropped_too(tmp_path):
    """The one that reached the viewer.

    narration.wav is only rebuilt when it is missing, so a redraft left the
    previous script's voice mixed under the new picture and truncated to the
    shorter runtime. The per-scene mp3s were correct; the mix was not.
    """
    from vidsmith.pipeline import Project, invalidate

    proj = Project(tmp_path)
    proj.dirs()
    (proj.build / "narration.wav").write_bytes(b"old voice")
    invalidate(proj, log=lambda *a: None)
    assert not (proj.build / "narration.wav").exists()


# --------------------------------------------------------------------------- #
# saying so
# --------------------------------------------------------------------------- #
def _log_for(tmp_path, monkeypatch, scene, spec):
    """Build one scene and return what it wrote to the log."""
    lines = []
    builder = _builder(tmp_path)
    builder.log = lines.append
    monkeypatch.setattr(builder, "_stock_batch", lambda *a, **k: [])
    monkeypatch.setattr(builder, "_diagram_spec", lambda *a, **k: spec)
    monkeypatch.setattr("vidsmith.visuals.diagram.render",
                        lambda *a, **k: tmp_path / "frame.png")
    monkeypatch.setattr("vidsmith.visuals.normalise_still",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)
    monkeypatch.setattr("vidsmith.visuals.cards.scene_card", lambda out, *a, **k: out)
    builder.build(scene)
    return "\n".join(lines)


def test_a_scene_the_script_asked_to_draw_says_it_was_drawn(tmp_path, scene, monkeypatch):
    """It used to draw in silence, so a build log could not tell you whether the
    directive had worked."""
    scene.diagram = "a root branching to leaves"
    out = _log_for(tmp_path, monkeypatch, scene, Spec.from_dict(TREE))
    assert "drawing a tree diagram" in out
    assert "the script asked" in out


def test_a_directive_that_cannot_be_drawn_says_so(tmp_path, scene, monkeypatch):
    """The silent version of this is what made a working feature and a broken
    one produce identical logs."""
    scene.diagram = "a root branching to leaves"
    out = _log_for(tmp_path, monkeypatch, scene, None)
    assert "none could be drawn" in out
    assert "the script" in out


def test_a_model_decided_diagram_names_the_model_not_the_script(tmp_path, scene,
                                                                monkeypatch):
    (tmp_path / "diagram_scenes.json").write_text(json.dumps({"0": True}),
                                                  encoding="utf-8")
    out = _log_for(tmp_path, monkeypatch, scene, Spec.from_dict(TREE))
    assert "the model asked" in out
