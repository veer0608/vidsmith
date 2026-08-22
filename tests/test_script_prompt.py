"""The drafting prompt.

Measured against the old one on three topics: length went from 51% of the
requested runtime to 84%, diagram directives from none at all to two a script,
and every scene no longer had an identical sentence count. These guard the
constraints that produced that, since the prompt is prose and easy to erode.
"""
from __future__ import annotations

import pytest

from vidsmith import llm

PROMPT = llm.SCRIPT_PROMPT


def _rendered(minutes: float = 3.0) -> str:
    words = int(minutes * llm.WORDS_PER_MINUTE)
    scenes = max(5, min(18, round(words / llm.WORDS_PER_SCENE)))
    return PROMPT.format(topic="a topic", words=words, scenes=scenes,
                         lo=int(words / scenes * 0.8), hi=int(words / scenes * 1.25))


def test_the_prompt_formats_with_the_fields_draft_script_supplies():
    assert "a topic" in _rendered()


@pytest.mark.parametrize("minutes,expected_scenes", [
    (1.0, 5), (2.0, 7), (3.0, 11), (5.0, 18), (10.0, 18),
])
def test_scene_count_scales_with_runtime(minutes, expected_scenes):
    words = int(minutes * llm.WORDS_PER_MINUTE)
    assert max(5, min(18, round(words / llm.WORDS_PER_SCENE))) == expected_scenes


def test_the_word_budget_matches_the_speaking_rate():
    """155 wpm is what edge-tts actually delivers at the default +8% rate."""
    assert 145 <= llm.WORDS_PER_MINUTE <= 165


def test_the_budget_is_stated_per_scene_not_only_in_total():
    """A lone total was undershot by half; the per-scene figure is the fix."""
    body = _rendered()
    assert "465" in body, "total word budget missing"
    assert "hard" in body.lower() and "budget" in body.lower()
    assert "33" in body and "52" in body, "per-scene range missing"


def test_both_directives_are_taught():
    assert "[visual:" in PROMPT and "[diagram:" in PROMPT


def test_diagrams_are_described_as_diagrams_not_pictures():
    """The first draft filed 'person closing a laptop' as a diagram."""
    low = PROMPT.lower()
    assert "camera" in low, "no test for what belongs in [visual:]"
    assert "bad" in low and "good" in low, "no worked examples of the distinction"
    assert "most scenes are" in low, "nothing discourages over-tagging diagrams"


def test_invented_facts_are_forbidden():
    low = PROMPT.lower()
    assert "do not invent" in low
    for trap in ("version numbers", "percentages", "named studies"):
        assert trap in low, f"{trap} not called out"


def test_rhythm_variation_is_demanded():
    """Every scene was two sentences, eleven times in a row."""
    low = PROMPT.lower()
    assert "vary the rhythm" in low
    assert "single short sentence" in low


def test_headings_must_be_distinct():
    """Four scenes came back all headed 'The Mechanism'."""
    assert "never repeat a heading" in PROMPT.lower()


def test_the_shape_covers_hook_through_takeaway():
    low = PROMPT.lower()
    for beat in ("hook", "mechanism", "what to do", "takeaway"):
        assert beat in low, f"the {beat} beat is missing"
