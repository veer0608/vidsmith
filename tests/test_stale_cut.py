"""A delivered cut that is an older edit than the rest of its delivery.

howto's 9:16 cut sat in out/ from 2026-09-03 while the 16:9 was rebuilt twice
around it, the second time to take a subscription the product does not have
out of its pricing diagram. `check` passed the pair: both ran 145.27s,
`description-9x16.txt` had been rewritten from the shared youtube.json so its
prose matched, and its credits matched its own old ledger. What gives it away
is `build/visuals-9x16`, which held clips for one scene of eleven.
"""
from __future__ import annotations

import json
from pathlib import Path

from vidsmith import check as check_mod
from vidsmith.check import stale_cuts


def _build(tmp_path, scenes=4, **clips):
    """A project's build/ with `clips[tag]` = the scene indices that have clips."""
    build = tmp_path / "proj" / "build"
    build.mkdir(parents=True)
    for tag, have in clips.items():
        vis = build / f"visuals{'' if tag == 'wide' else '-' + tag}"
        vis.mkdir()
        (vis / "end.mp4").write_bytes(b"x")          # never a scene's clip
        for i in have:
            (vis / f"scene_{i:03d}_00.mp4").write_bytes(b"x")
    return build, [{"index": i} for i in range(scenes)]


CUTS = [("16:9", Path("a.mp4")), ("9:16", Path("a-9x16.mp4"))]


def test_a_cut_rebuilt_for_only_one_shape_is_found(tmp_path):
    build, scenes = _build(tmp_path, scenes=11, wide=range(11), **{"9x16": [2]})
    found = stale_cuts(build, CUTS, scenes)
    assert found == [
        "a-9x16.mp4 was cut before 10 of its 11 scenes last changed and was not "
        "rebuilt since, so it shows an older edit than the rest of this delivery. "
        "Rebuild it with vidsmith build proj --aspect 9:16"]


def test_one_changed_scene_is_named(tmp_path):
    build, scenes = _build(tmp_path, wide=range(4), **{"9x16": [0, 1, 3]})
    assert "was cut before scene 2 last changed" in stale_cuts(build, CUTS, scenes)[0]


def test_a_pair_built_from_the_same_edit_says_nothing(tmp_path):
    """machine-statements: nine scenes, both shapes, all there."""
    build, scenes = _build(tmp_path, scenes=9, wide=range(9), **{"9x16": range(9)})
    assert stale_cuts(build, CUTS, scenes) == []


def test_the_only_cut_is_checked_too(tmp_path):
    """An edit made and not yet built leaves the one delivered cut behind."""
    build, scenes = _build(tmp_path, wide=[0, 1, 2])
    found = stale_cuts(build, CUTS[:1], scenes)
    assert len(found) == 1 and found[0].startswith("a.mp4 was cut before scene 3")


def test_a_cleared_build_folder_says_nothing(tmp_path):
    """A delivery whose visuals were cleared cannot be judged, so it is not guessed at."""
    build = tmp_path / "proj" / "build"
    build.mkdir(parents=True)
    assert stale_cuts(build, CUTS, [{"index": 0}]) == []


def test_no_scenes_says_nothing(tmp_path):
    build, _ = _build(tmp_path, wide=[], **{"9x16": []})
    assert stale_cuts(build, CUTS, []) == []


def test_check_reports_it(tmp_path, monkeypatch):
    build, scenes = _build(tmp_path, wide=range(4), **{"9x16": []})
    (build / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")
    out = build.parent / "out"
    out.mkdir()
    for name in ("a.mp4", "a-9x16.mp4"):
        (out / name).write_bytes(b"x")
    monkeypatch.setattr(check_mod.ff, "duration", lambda path: 60.0)
    problems = check_mod.check(out)
    assert any(p.startswith("a-9x16.mp4 was cut before 4 of its 4 scenes")
               for p in problems), problems
    assert not any(p.startswith("a.mp4 was cut before") for p in problems)
