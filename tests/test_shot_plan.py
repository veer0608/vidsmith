"""The shot plan is what keeps picture and speech in step.

Every one of these guards a real failure: a plan that does not sum to the
narration slot puts the picture out of sync with the voice, and every cut after
it drifts.
"""
from __future__ import annotations

import random

import pytest

from vidsmith.visuals import collapse, plan_shots
from conftest import make_scene

MIN_S, MAX_S = 2.4, 5.5


def test_plan_sums_to_the_narration_slot(scene):
    plan = plan_shots(scene, 0.25, MIN_S, MAX_S)
    assert sum(plan) == pytest.approx(scene.duration, abs=1e-6)


def test_plan_sums_exactly_for_arbitrary_scenes():
    rnd = random.Random(20260822)
    for _ in range(200):
        n_words = rnd.randint(3, 60)
        sentences = []
        while n_words > 0:
            take = min(n_words, rnd.randint(3, 14))
            sentences.append(" ".join(["word"] * take) + ".")
            n_words -= take
        s = make_scene(" ".join(sentences), wps=rnd.uniform(1.8, 3.4))
        plan = plan_shots(s, 0.25, MIN_S, MAX_S)
        assert sum(plan) == pytest.approx(s.duration, abs=1e-6)
        assert all(d > 0 for d in plan)


def test_short_scenes_are_one_shot():
    s = make_scene("Short line here.", duration=3.0)
    assert plan_shots(s, 0.25, MIN_S, MAX_S) == [3.0]


def test_scene_without_words_is_one_shot():
    s = make_scene("Anything at all.", duration=9.0)
    s.words = []
    assert plan_shots(s, 0.25, MIN_S, MAX_S) == [9.0]


def test_cuts_land_on_sentence_ends(scene):
    """A cut should fall where a full stop is spoken, not on a timer."""
    plan = plan_shots(scene, 0.25, MIN_S, MAX_S)
    assert len(plan) >= 2

    ends = []
    consumed = 0
    for word in scene.text.split():
        consumed += 1
        if word.endswith((".", "!", "?")):
            ends.append(0.25 + scene.words[consumed - 1]["end"])

    boundary = plan[0]
    assert any(abs(boundary - e) < 0.02 for e in ends), (
        f"first cut at {boundary:.2f}s matches no sentence end in {ends}"
    )


def test_no_shot_is_shorter_than_the_minimum(scenes):
    for s in scenes:
        plan = plan_shots(s, 0.25, MIN_S, MAX_S)
        if len(plan) > 1:
            assert min(plan) >= MIN_S - 0.05


def test_long_sentences_are_broken_at_a_clause():
    text = ("The merchant name on a transaction is typed by the payment "
            "processor, and not by the shop you actually walked into that day.")
    s = make_scene(text, wps=2.0)
    plan = plan_shots(s, 0.25, MIN_S, MAX_S)
    assert len(plan) > 1, "a single sentence longer than max_shot must be split"
    assert sum(plan) == pytest.approx(s.duration, abs=1e-6)


def test_collapse_preserves_total():
    plan = [2.5, 3.0, 3.5, 4.0]
    for n in range(1, 5):
        merged = collapse(plan, n)
        assert len(merged) == n
        assert sum(merged) == pytest.approx(sum(plan), abs=1e-9)


def test_collapse_never_returns_nothing():
    assert len(collapse([4.0, 2.0], 0)) == 1
