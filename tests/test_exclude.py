"""`visuals.exclude`: clips a project never uses.

A retake writes a near-identical search, the stock results overlap, and the
rerank approved the same clip on every roll: howto's scene 1 came back twice
with `matrix-console-hacking-code-852292` in the same slot, and the second roll
lost the good shots around it. There was no way to say "not this one".
"""
from __future__ import annotations

import json

import pytest
import yaml

from vidsmith import pipeline as pl
from vidsmith import visuals
from vidsmith.config import clip_exclusion, load_config, write_default_config

from test_build_wiring import _build, _configure, project, rendered  # noqa: F401
from test_clip_fit import _builder

PAGE = "https://www.pexels.com/video/matrix-console-hacking-code-852292/"


# --------------------------------------------------------------------------- #
# what an entry may be
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry, expected", [
    (PAGE, ("pexels", "852292")),
    (PAGE.rstrip("/"), ("pexels", "852292")),
    ("https://pixabay.com/videos/id-12345/", ("pixabay", "12345")),
    ("852292", ("", "852292")),
    (852292, ("", "852292")),
])
def test_an_entry_is_what_the_sheet_prints(entry, expected):
    """The sheet prints the page beside every frame; that is what gets copied."""
    assert clip_exclusion(entry) == expected


def test_a_digit_in_the_slug_is_not_the_id():
    page = "https://www.pexels.com/video/2-people-typing-on-a-laptop-7504970/"
    assert clip_exclusion(page) == ("pexels", "7504970")


@pytest.mark.parametrize("entry", ["matrix", "https://example.com/video/852292/",
                                   "https://www.pexels.com/video/", ""])
def test_an_entry_that_names_no_clip_is_refused(entry):
    with pytest.raises(ValueError):
        clip_exclusion(entry)


def _config(tmp_path, exclude):
    path = tmp_path / "config.yaml"
    write_default_config(path, "A Test Video")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["visuals"]["exclude"] = exclude
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_a_bad_entry_is_refused_on_load(tmp_path):
    """It would exclude nothing, and the clip would be back with nothing said."""
    with pytest.raises(ValueError, match=r"config\.yaml: visuals\.exclude"):
        load_config(_config(tmp_path, [PAGE, "the matrix one"]))


def test_one_clip_without_brackets_is_still_a_list(tmp_path):
    """YAML hands `exclude: 852292` over as a bare int."""
    assert load_config(_config(tmp_path, 852292)).visuals.exclude == ["852292"]


def test_a_new_project_excludes_nothing(tmp_path):
    assert load_config(_config(tmp_path, [])).visuals.exclude == []


# --------------------------------------------------------------------------- #
# the search
# --------------------------------------------------------------------------- #
def test_an_excluded_clip_never_reaches_the_rerank(tmp_path, monkeypatch, scene):
    lines = []
    builder = _builder(tmp_path, lines, exclude=[PAGE])
    hits = [{"id": i, "url": f"https://x/{i}", "author": f"a{i}", "page": "",
             "preview": ""} for i in ("852292", "1", "2")]
    shown = []

    def rerank(hits, scene, query, **k):
        shown.extend(h["id"] for h in hits)
        return hits

    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: list(hits))
    monkeypatch.setattr(builder, "_rerank", rerank)
    monkeypatch.setattr(visuals, "_download", lambda url, out, *a: out.write_bytes(b"x"))
    monkeypatch.setattr(visuals.ff, "duration", lambda p: 10.0)
    monkeypatch.setattr(builder, "_twin", lambda *a: None)

    picked = builder._stock_batch("developer typing in terminal", 2, scene)

    assert "852292" not in shown, "it must not take a place in the stills judged"
    assert [p["id"] for p in picked] == ["1", "2"]
    assert any("exclude: dropped pexels 852292" in line for line in lines)


def test_a_rule_for_one_library_leaves_the_other_alone(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path, exclude=["https://pixabay.com/videos/id-852292/"])
    assert not visuals.is_excluded(builder._excluded, "pexels", "852292")
    assert visuals.is_excluded(builder._excluded, "pixabay", "852292")
    assert visuals.is_excluded([("", "852292")], "pexels", "852292")


# --------------------------------------------------------------------------- #
# a clip already on screen
# --------------------------------------------------------------------------- #
def _ledger(build, name, entries):
    path = build / name / "credits.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


def test_the_scenes_showing_an_excluded_clip_are_found_in_every_cut(tmp_path):
    build = tmp_path / "build"
    _ledger(build, "visuals", {
        "0:0": {"id": "1", "url": "https://www.pexels.com/video/a-1/"},
        "1:2": {"id": "852292", "url": PAGE},
    })
    _ledger(build, "visuals-9x16", {"3:0": {"id": "852292", "url": PAGE}})

    assert visuals.excluded_scenes(build, ["852292"]) == {1: ["852292"], 3: ["852292"]}
    assert visuals.excluded_scenes(build, []) == {}
    assert visuals.excluded_scenes(build, ["https://pixabay.com/videos/x-852292/"]) == {}


def test_adding_the_clip_on_screen_re_films_its_scene(project, rendered, monkeypatch):
    """A built scene reuses its clips and never searches again, so filtering the
    results alone would leave the excluded clip exactly where it was."""
    _ledger(project / "build", "visuals", {"1:0": {"id": "852292", "url": PAGE}})
    _configure(project, visuals={"exclude": [PAGE]})
    seen = []
    monkeypatch.setattr(pl, "invalidate",
                        lambda proj, log=print, only=None, respoken=None: seen.append(only))

    _build(project)

    assert seen == [{1}]


def test_force_visuals_re_films_everything_anyway(project, rendered, monkeypatch):
    _ledger(project / "build", "visuals", {"1:0": {"id": "852292", "url": PAGE}})
    _configure(project, visuals={"exclude": [PAGE]})
    seen = []
    monkeypatch.setattr(pl, "invalidate",
                        lambda proj, log=print, only=None, respoken=None: seen.append(only))

    pl.build(project, force=["visuals"], stop_after="render", log=lambda *a: None)

    assert seen == []
