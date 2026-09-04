"""Generated frames should be visible in the delivery, and re-decidable.

Both halves come from one incident. A promo was built, and a scene the script
asked to be footage came out as a generated card, so the video opened on fifteen
seconds of a static diagram. Two things were wrong with how that went:

* nothing in `out/` recorded it. The build log said so, and the build log was a
  terminal that had been closed, so `check` passed the delivery without comment;
* `--force visuals,render` could not undo it. Whether a scene is drawn is cached
  in `diagram_scenes.json` and only decided when the file has no entry for it, so
  the rebuild redrew the same card with new footage around it. The only way back
  was editing that JSON by hand.

The threshold below is the interesting part. The first version of this rule
counted *every* generated frame and fired above half, and on the build that
prompted it - three of seven - it stayed silent. Counting only the model's
substitutions, that build is two of seven and is reported.
"""
from __future__ import annotations

import json

from vidsmith.check import substituted_scenes


def _cfg(provider="pexels", substituted=(), scenes=0):
    return {"provider": provider, "substituted": list(substituted),
            "scenes": scenes}


# --------------------------------------------------------------------------- #
# the rule
# --------------------------------------------------------------------------- #
def test_the_build_that_prompted_this_is_reported():
    """`projects/exact-captions`, first build: seven scenes, of which the script
    asked for one diagram and the model substituted two more. The first version
    of this rule counted all three against a "more than half" threshold and said
    nothing."""
    problems = substituted_scenes(_cfg(substituted=[0, 6], scenes=7))

    assert len(problems) == 1
    assert "2 of 7" in problems[0]
    assert "scenes 0, 6" in problems[0]
    assert "--force diagrams" in problems[0], "it should say how to undo it"


def test_the_rebuild_is_not_reported():
    """The same project after the fix: one substitution left, which is ordinary."""
    assert substituted_scenes(_cfg(substituted=[6], scenes=7)) == []


def test_one_substitution_is_never_a_finding():
    """One unfilmable idea in a video is normal. Reporting it is how a check
    stops being read."""
    assert substituted_scenes(_cfg(substituted=[2], scenes=3)) == []


def test_a_long_video_is_not_judged_by_a_short_one_is_count():
    """Two substitutions in twenty scenes is a tenth of the video and not a
    pattern; two in seven is."""
    assert substituted_scenes(_cfg(substituted=[3, 11], scenes=20)) == []
    assert substituted_scenes(_cfg(substituted=[3, 5], scenes=7)) != []


def test_a_script_asked_diagram_is_never_counted():
    """It reaches `build.json` as `drawn` but not as `substituted`, so a video
    written around diagrams is not nagged about them."""
    cfg = _cfg(substituted=[], scenes=4)
    cfg["drawn"] = [0, 1, 2, 3]
    assert substituted_scenes(cfg) == []


def test_a_cards_build_is_exempt():
    """`cards` and `local` draw everything by design. Same exemption the
    frozen-shot rule needed, for the same reason."""
    for provider in ("cards", "local"):
        assert substituted_scenes(
            _cfg(provider=provider, substituted=[0, 1, 2], scenes=3)) == []


def test_a_delivery_without_build_info_is_not_judged():
    """Everything built before this existed. It has to stay silent rather than
    report every old delivery."""
    assert substituted_scenes({}) == []
    assert substituted_scenes(_cfg(scenes=0)) == []


# --------------------------------------------------------------------------- #
# recording it
# --------------------------------------------------------------------------- #
def test_build_info_records_both_routes_separately(tmp_path):
    from vidsmith.config import Config
    from vidsmith.pipeline import write_build_info

    write_build_info(tmp_path, Config(),
                     drawn={"drawn": [0, 2, 6], "substituted": [0, 6]},
                     scene_count=7)
    body = json.loads((tmp_path / "build.json").read_text(encoding="utf-8"))

    assert body["drawn"] == [0, 2, 6]
    assert body["substituted"] == [0, 6]
    assert body["scenes"] == 7
    assert substituted_scenes(body), "and check reads it back"


def test_build_info_still_writes_without_them(tmp_path):
    """The arguments are optional, so a caller that has not been updated still
    produces a readable file rather than raising."""
    from vidsmith.config import Config
    from vidsmith.pipeline import write_build_info

    write_build_info(tmp_path, Config())
    body = json.loads((tmp_path / "build.json").read_text(encoding="utf-8"))

    assert body["drawn"] == [] and body["substituted"] == [] and body["scenes"] == 0
    assert substituted_scenes(body) == []


def test_drawn_scenes_keeps_the_two_routes_apart(tmp_path, scenes):
    """Only the model's decisions live in `diagram_scenes.json`; an explicit
    `[diagram: ...]` is re-read from the script every build."""
    from vidsmith.pipeline import Project, drawn_scenes

    proj = Project(tmp_path)
    proj.build.mkdir(parents=True, exist_ok=True)
    (proj.build / "diagram_scenes.json").write_text(
        json.dumps({"1": True, "2": False}), encoding="utf-8")

    for n, s in enumerate(scenes):
        s.index = n
        s.diagram = ""
    scenes[0].diagram = "how a b-tree splits"      # the script asked

    found = drawn_scenes(proj, scenes)

    assert found["substituted"] == [1], "only what the model overruled"
    assert 0 in found["drawn"] and 1 in found["drawn"]
    assert 2 not in found["drawn"], "a scene decided against is not drawn"


def test_force_diagrams_clears_the_decisions(tmp_path):
    """The bug: `--force visuals,render` redrew the same card, because the
    verdict is only made when the file has no entry for that scene."""
    from vidsmith.pipeline import Project, clear_diagram_decisions

    proj = Project(tmp_path)
    proj.build.mkdir(parents=True, exist_ok=True)
    (proj.build / "diagram_scenes.json").write_text('{"0": true}', encoding="utf-8")
    (proj.build / "diagrams.json").write_text("{}", encoding="utf-8")
    keep = proj.build / "scenes.json"
    keep.write_text("[]", encoding="utf-8")

    lines = []
    assert clear_diagram_decisions(proj, lines.append) == 2
    assert not (proj.build / "diagram_scenes.json").exists()
    assert not (proj.build / "diagrams.json").exists()
    assert keep.exists(), "narration timings are not the decision's to drop"
    assert any("judged again" in line for line in lines)


def test_clearing_nothing_says_nothing(tmp_path):
    from vidsmith.pipeline import Project, clear_diagram_decisions

    proj = Project(tmp_path)
    proj.build.mkdir(parents=True, exist_ok=True)
    lines = []
    assert clear_diagram_decisions(proj, lines.append) == 0
    assert lines == []


def test_drawn_scenes_survives_a_corrupt_decision_file(tmp_path, scenes):
    from vidsmith.pipeline import Project, drawn_scenes

    proj = Project(tmp_path)
    proj.build.mkdir(parents=True, exist_ok=True)
    (proj.build / "diagram_scenes.json").write_text("{not json", encoding="utf-8")

    assert drawn_scenes(proj, scenes) == {"drawn": [], "substituted": []}
