"""Nothing in the narration graph may run forever.

macOS CI has hung inside `build_narration` six times. The fifth occurrence is
the first that reported anything, because `-progress pipe:1` was added for it:
it reached `out_time=00:00:21.342000` of a 22.746s output and then sat for the
whole 45s timeout. Not a failure to start, and not a filtergraph that produces
nothing - it stops near the tail, which is the region a bare `apad` owns.

A bare `apad` pads until something downstream stops asking, and `atrim` drops
frames past the end without propagating EOF upstream, so apad goes on producing
silence for atrim to discard. These tests hold the bound in place; they cannot
prove the hang is gone, because it is intermittent and does not reproduce off
macOS.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from vidsmith import ffmpeg_util as ff
from vidsmith import render

TOTAL = 22.746


def _captured_graph(monkeypatch, scenes) -> str:
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return None

    monkeypatch.setattr(render.ff, "run", fake_run)
    render.build_narration(scenes, Path("out.wav"), 0.25, TOTAL)
    args = seen["args"]
    return args[args.index("-filter_complex") + 1]


def _scene(index: int, start: float, tmp_path: Path, scene_factory):
    scene = scene_factory("Some words for this scene.", index=index)
    scene.start = start
    scene.audio = str(tmp_path / f"a{index}.wav")
    return scene


@pytest.fixture
def scene_factory():
    from conftest import make_scene

    return make_scene


@pytest.mark.parametrize("count", [1, 3])
def test_the_pad_is_bounded(monkeypatch, tmp_path, scene_factory, count):
    """A bare `apad` is the fault; `apad=whole_dur=` is the same guarantee with
    an end on it."""
    scenes = [_scene(i, i * 8.0, tmp_path, scene_factory) for i in range(count)]
    graph = _captured_graph(monkeypatch, scenes)

    assert f"apad=whole_dur={TOTAL:.3f}" in graph
    assert not re.search(r"apad(?![=\w])", graph), \
        "an unbounded apad is back in the graph"


@pytest.mark.parametrize("count", [1, 3])
def test_the_other_two_bounds_are_still_there(monkeypatch, tmp_path,
                                              scene_factory, count):
    """Three bounds, each covering a different way of failing to stop. The `-t`
    was added after an earlier hang and must not be traded away for this one."""
    seen = {}
    monkeypatch.setattr(render.ff, "run", lambda args, **kw: seen.setdefault("args", args))
    scenes = [_scene(i, i * 8.0, tmp_path, scene_factory) for i in range(count)]
    render.build_narration(scenes, Path("out.wav"), 0.25, TOTAL)

    args = seen["args"]
    graph = args[args.index("-filter_complex") + 1]
    assert f"atrim=0:{TOTAL:.3f}" in graph
    assert args[args.index("-t") + 1] == f"{TOTAL:.3f}"


@pytest.mark.slow
def test_the_bounded_graph_still_runs_the_full_length(tmp_path, scene_factory):
    """The pad exists so a short last scene still fills the runtime. Bounding it
    must not shorten the file, which is the way this fix could go wrong quietly.
    """
    try:
        ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")

    scenes = []
    for i, start in enumerate((0.0, 9.4, 20.3)):
        path = tmp_path / f"a{i}.wav"
        ff.run(["-f", "lavfi", "-i", "sine=frequency=400:duration=2.0",
                "-ar", "48000", "-ac", "2", str(path)])
        scene = _scene(i, start, tmp_path, scene_factory)
        scene.audio = str(path)
        scenes.append(scene)

    out = render.build_narration(scenes, tmp_path / "narration.wav", 0.25, TOTAL)

    # the last scene's audio ends around 22.5s; without the pad the file would
    # stop there, and the picture would outlast the sound for the rest of the cut
    assert abs(ff.duration(out) - TOTAL) < 0.05, ff.duration(out)
