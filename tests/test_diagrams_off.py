"""A `[diagram:]` line with drawing switched off stops the build at parse.

dodo-prep wrote eight `[diagram:]` scenes by hand, and `vidsmith new` writes
`visuals.diagrams: false`, so every one became stock footage with one log line
each, and that was found seventeen minutes later at the end of the build. The
web page had the same trap for everyone: its hint teaches `[diagram: three
boxes] draws one`, and web jobs never switched drawing on.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import make_scene
from vidsmith import pipeline as pl
from vidsmith.config import write_default_config

SCRIPT = """# Diagram Test

## Intro
[visual: a person at a desk]
Some words that open the video.

## How it works
[diagram: three boxes joined by arrows]
Some words about how it works.

## Close
[diagram: a tree with two branches]
Some words that close it.
"""


def _scene(index: int, heading: str = "", diagram: str = ""):
    scene = make_scene("Some words here.", index=index)
    scene.heading, scene.diagram = heading, diagram
    return scene


def test_no_diagram_lines_is_never_refused():
    pl.refuse_unwanted_footage([_scene(0), _scene(1)], drawing_on=False)


def test_drawing_on_is_never_refused():
    pl.refuse_unwanted_footage([_scene(0, diagram="boxes")], drawing_on=True)


def test_the_refusal_names_each_scene_and_the_fix():
    with pytest.raises(pl.DiagramsOff) as caught:
        pl.refuse_unwanted_footage(
            [_scene(0, "Intro"), _scene(1, "How it works", "boxes"),
             _scene(2, "Close", "a tree")], drawing_on=False)
    said = str(caught.value)
    # numbered from 0, like the shot sheet and `retake --scene`
    assert "2 diagrams" in said and "1 'How it works'" in said and "2 'Close'" in said
    assert "diagrams: true" in said and "[visual: ...]" in said
    assert isinstance(caught.value, RuntimeError), "the CLI prints RuntimeErrors cleanly"


def test_one_diagram_reads_as_one():
    with pytest.raises(pl.DiagramsOff, match=r"1 diagram \(scene 0"):
        pl.refuse_unwanted_footage([_scene(0, diagram="boxes")], drawing_on=False)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    write_default_config(root / "config.yaml", "Diagram Test")
    return root


def _drawing(root: Path, on: bool) -> None:
    path = root / "config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("visuals", {})["diagrams"] = on
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_a_build_stops_before_any_work(project, monkeypatch):
    """Before the voice, which is the first stage that costs anything."""
    def voice_must_not_run(*a, **k):
        raise AssertionError("the build went on past parse")

    monkeypatch.setattr(pl, "find_keys", lambda *a, **k: {})
    monkeypatch.setattr(pl, "synthesize_all", voice_must_not_run, raising=False)
    _drawing(project, False)
    with pytest.raises(pl.DiagramsOff, match="2 diagrams"):
        pl.build(project, log=lambda *a: None)


def test_a_retake_of_an_existing_render_is_not_refused(project, monkeypatch):
    """A finished render already decided; stranding it over a directive would be worse."""
    _drawing(project, False)
    reached = {}

    def stop_after_parse(*a, **k):
        reached["past parse"] = True
        raise RuntimeError("stub: stop here")

    monkeypatch.setattr(pl, "resolve_title", stop_after_parse)
    with pytest.raises(RuntimeError, match="stub: stop here"):
        pl.build(project, retake=True, log=lambda *a: None)
    assert reached


def test_a_web_job_that_asks_for_a_diagram_gets_drawing(tmp_path):
    from web.jobs import Jobs

    root = tmp_path / "job"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    Jobs(tmp_path / "jobs")._write_config(root, {})
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert raw["visuals"]["diagrams"] is True


def test_a_web_job_without_diagram_lines_keeps_drawing_off(tmp_path):
    """The default stays: model-decided diagrams read as slides."""
    from web.jobs import Jobs

    root = tmp_path / "job"
    root.mkdir()
    (root / "script.md").write_text("# T\n\n## One\n[visual: a desk]\nWords.\n",
                                    encoding="utf-8")
    Jobs(tmp_path / "jobs")._write_config(root, {})
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert raw["visuals"]["diagrams"] is False
