"""Say a scene ran out of footage during the build, not after the encode.

`vidsmith check` reports a frozen shot, but only once the delivery exists, and
the encode is the expensive half: a 2:25 video is about four minutes of ffmpeg.
The build already knows - it has just logged "rejected 15 of 15 as the wrong
subject" a line earlier. Two real cuts shipped this way, one of them published.
"""
from __future__ import annotations

import pytest

from vidsmith.config import VisualConfig
from vidsmith.visuals import LONG_SHOT_FACTOR, long_shot_warnings


def _scene(scene_factory, durations, index=3, diagram=""):
    scene = scene_factory("Words enough to fill the slot.", index=index)
    scene.diagram = diagram
    scene.shots = [{"path": f"s{i}.mp4", "duration": d}
                   for i, d in enumerate(durations)]
    scene.duration = sum(durations)
    return scene


@pytest.fixture
def scene_factory():
    from conftest import make_scene

    return make_scene


def test_the_shape_that_shipped_is_warned_about(scene_factory):
    """The 9:16 `uses` cut: one shot, 11.6s, after the reranker rejected nearly
    everything."""
    cfg = VisualConfig()
    warnings = long_shot_warnings(_scene(scene_factory, [11.6], index=0), cfg)

    assert len(warnings) == 1
    assert "11.6s" in warnings[0] and "scene 0" in warnings[0]
    assert "[visual:" in warnings[0], "it should say what to do about it"


def test_an_ordinary_edit_says_nothing(scene_factory):
    cfg = VisualConfig()
    assert long_shot_warnings(_scene(scene_factory, [3.3, 4.8, 3.5]), cfg) == []


def test_a_last_shot_running_a_little_long_is_not_worth_a_warning(scene_factory):
    """The plan must sum to the narration slot exactly, so the final shot
    routinely absorbs the remainder and sits over the ceiling. Warning on that
    would fire on most scenes and stop being read."""
    cfg = VisualConfig()
    over = cfg.max_shot_seconds + 1.0
    assert over > cfg.max_shot_seconds
    assert long_shot_warnings(_scene(scene_factory, [3.0, 3.0, over]), cfg) == []


def test_a_drawn_scene_is_allowed_to_hold(scene_factory):
    cfg = VisualConfig()
    scene = _scene(scene_factory, [20.0], diagram="how a b-tree splits")
    assert long_shot_warnings(scene, cfg) == []


def test_it_follows_the_project_ceiling(scene_factory):
    """A project that deliberately cuts slowly should not be nagged at the
    default's threshold."""
    slow = VisualConfig(max_shot_seconds=12.0)
    scene = _scene(scene_factory, [16.0])

    assert long_shot_warnings(scene, slow) == []
    assert long_shot_warnings(scene, VisualConfig()) != []


def test_a_scene_with_no_shots_is_not_a_warning(scene_factory):
    """Nothing was planned yet; that is a different fault and not this one's to
    report."""
    scene = _scene(scene_factory, [])
    scene.shots = []
    assert long_shot_warnings(scene, VisualConfig()) == []


def test_the_build_warns_before_check_would(scene_factory):
    """Two rules about the same fault, and the order between them matters.

    `check.LONG_SHOT_SECONDS` reads the delivered edit; this reads the plan. The
    build must never stay quiet about something the delivery check will fail on,
    or a render passes silently, fails `check` four minutes of ffmpeg later, and
    people learn to ignore one of the two.
    """
    from vidsmith.check import LONG_SHOT_SECONDS

    cfg = VisualConfig()
    build_threshold = cfg.max_shot_seconds * LONG_SHOT_FACTOR

    assert build_threshold <= LONG_SHOT_SECONDS, (
        f"the build warns past {build_threshold:.1f}s but check fails past "
        f"{LONG_SHOT_SECONDS:.1f}s, so a shot between them passes the build and "
        "then fails the delivery")


def test_nothing_check_would_fail_gets_through_the_build(scene_factory):
    """The same invariant, exercised rather than asserted about constants."""
    from vidsmith.check import LONG_SHOT_SECONDS

    cfg = VisualConfig()
    just_over = LONG_SHOT_SECONDS + 0.1
    assert long_shot_warnings(_scene(scene_factory, [just_over]), cfg) != []


def test_build_all_logs_the_warning(monkeypatch, tmp_path, scene_factory):
    """It has to reach the log, not just return a string: the whole point is
    that it interrupts the build while there is still time to act."""
    from vidsmith import visuals

    scene = _scene(scene_factory, [14.4], index=1)
    monkeypatch.setattr(visuals.VisualBuilder, "build",
                        lambda self, sc, force=False: None)

    lines = []
    visuals.build_all([scene], VisualConfig(), (1920, 1080), 30, tmp_path,
                      keys={}, log=lines.append)

    assert any("warning" in line and "14.4s" in line for line in lines), lines
