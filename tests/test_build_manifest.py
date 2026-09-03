"""The build tells `check` how it was configured, instead of being guessed at.

`check` reads `out/` and nothing else, which is the property that makes it worth
trusting. The cost was that it had to infer things it could have been told, and
inferring the footage provider from the credits ledger produced two false
positives in a row: every scene of a cards build reported as a frozen shot, then
a mixed scene under-counted when that was fixed by reading the ledger
differently. It also left the frozen-shot threshold a constant rather than the
project's own `max_shot_seconds`.
"""
from __future__ import annotations

import json
from pathlib import Path

from vidsmith import pipeline
from vidsmith.check import frozen_shots, settings
from vidsmith.config import LONG_SHOT_FACTOR, Config


def _ledger(tmp_path: Path, entries: dict) -> Path:
    build = tmp_path / "build"
    (build / "visuals").mkdir(parents=True, exist_ok=True)
    (build / "visuals" / "credits.json").write_text(json.dumps(entries),
                                                    encoding="utf-8")
    return build


def test_the_build_records_what_check_needs(tmp_path):
    cfg = Config()
    cfg.visuals.provider = "pixabay"
    cfg.visuals.max_shot_seconds = 7.5

    path = pipeline.write_build_info(tmp_path / "out", cfg)
    body = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "build.json"
    assert body["provider"] == "pixabay"
    assert body["max_shot_seconds"] == 7.5
    assert "commit" in body, "which vidsmith made this delivery"


def test_a_stated_cards_build_needs_no_inference(tmp_path):
    """The fault that started this: `projects/gil` reported every scene frozen.

    The ledger-reading fallback gets there too, but only because a cards ledger
    happens to name nobody. Being told is not the same as guessing right.
    """
    build = _ledger(tmp_path, {"0:0": {"credit": "Someone", "url": "https://x"}})
    scenes = [{"index": 0, "heading": "One", "duration": 20.0}]

    assert frozen_shots(build, "16:9", scenes, {"provider": "cards"}) == []
    assert frozen_shots(build, "16:9", scenes, {"provider": "local"}) == []
    assert frozen_shots(build, "16:9", scenes, {"provider": "pexels"}) != []


def test_the_threshold_follows_the_project(tmp_path):
    """A project that deliberately cuts slowly was being failed at the default's
    threshold, because the number was a constant here."""
    build = _ledger(tmp_path, {"0:0": {"credit": "Someone", "url": "https://x"}})
    scenes = [{"index": 0, "heading": "One", "duration": 16.0}]

    slow = {"provider": "pexels", "max_shot_seconds": 12.0}
    assert frozen_shots(build, "16:9", scenes, slow) == []

    default = {"provider": "pexels", "max_shot_seconds": 5.5}
    assert frozen_shots(build, "16:9", scenes, default) != []
    assert 5.5 * LONG_SHOT_FACTOR < 16.0


def test_a_delivery_without_the_file_is_still_checked(tmp_path):
    """Every project built before this existed, and anything assembled by hand.
    The manifest is derived and optional; it must never become required."""
    build = _ledger(tmp_path, {"0:0": {"credit": "Someone", "url": "https://x"}})
    scenes = [{"index": 0, "heading": "One", "duration": 20.0}]

    assert settings(tmp_path / "out") == {}
    assert frozen_shots(build, "16:9", scenes, {}) != [], \
        "the inference that predates the manifest still runs"


def test_an_unreadable_manifest_is_not_a_crash(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "build.json").write_text("{not json", encoding="utf-8")

    assert settings(out) == {}


def test_a_manifest_that_is_not_an_object_is_ignored(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "build.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert settings(out) == {}
