"""A `[diagram:]` line draws; `visuals.diagrams` gates only the model's own.

One switch used to cover both. dodo-prep's eight hand-written diagrams rendered
as footage with drawing off, and the fix for that (turning the switch on) let
btrees2's build add six comparison slides of its own beside its two drafted
diagrams. A request and the model's own judgement are different things, so they
have different switches now: none for the request, `visuals.diagrams` for the
model.
"""
from __future__ import annotations

import pytest
import yaml

from vidsmith import pipeline as pl
from vidsmith import visuals
from vidsmith.config import Config

SCRIPT = """# Diagram Test

## Intro
[visual: a person at a desk]
Some words that open the video.

## How it works
[diagram: three boxes joined by arrows]
Some words about how it works.

## Close
[visual: a server rack]
Some words that close it.
"""


@pytest.mark.parametrize("model_diagrams", [False, True])
def test_a_diagram_line_always_draws(model_diagrams):
    assert visuals.draws("a node splits", None, False, model_diagrams) is True


def test_the_models_own_decision_needs_the_switch():
    assert visuals.draws("", True, False, model_diagrams=False) is False
    assert visuals.draws("", True, False, model_diagrams=True) is True


def test_no_request_and_no_decision_is_footage():
    assert visuals.draws("", None, False, model_diagrams=True) is False
    assert visuals.draws("", False, False, model_diagrams=True) is False


def test_the_viewers_own_files_beat_any_drawing():
    assert visuals.draws("a node splits", True, True, model_diagrams=True) is False


def test_the_models_own_drawing_stays_off_by_default():
    """The default exists because the model's diagrams read as slides."""
    assert Config().visuals.diagrams is False


def test_a_build_with_drawing_off_no_longer_refuses_a_diagram_line(tmp_path, monkeypatch):
    """#181 stopped this build at parse; the line now simply draws."""
    from vidsmith.config import write_default_config

    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    write_default_config(root / "config.yaml", "Diagram Test")
    reached = {}

    def stop_after_parse(*a, **k):
        reached["past parse"] = True
        raise RuntimeError("stub: stop here")

    monkeypatch.setattr(pl, "resolve_title", stop_after_parse)
    with pytest.raises(RuntimeError, match="stub: stop here"):
        pl.build(root, log=lambda *a: None)
    assert reached
    assert not hasattr(pl, "DiagramsOff")


def test_a_web_job_never_switches_the_models_own_drawing_on(tmp_path):
    """Any web script with one [diagram:] line used to hand the build its own
    slide-making as well, which is how btrees2 got six it never asked for."""
    from web.jobs import Jobs

    root = tmp_path / "job"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    Jobs(tmp_path / "jobs")._write_config(root, {})
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert raw["visuals"]["diagrams"] is False
