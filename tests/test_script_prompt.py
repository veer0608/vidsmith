"""The drafting prompt.

Measured against the old one on three topics: length went from 51% of the
requested runtime to 84%, diagram directives from none at all to two a script,
and every scene no longer had an identical sentence count. These guard the
constraints that produced that, since the prompt is prose and easy to erode.
"""
from __future__ import annotations

import pytest

from vidsmith import llm
from vidsmith.llm import undash

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


# --------------------------------------------------------------------------- #
# dashes
# --------------------------------------------------------------------------- #
EM, EN = "—", "–"


@pytest.mark.parametrize("raw,expected", [
    (f"It is a record {EM} of what your bank stored.",
     "It is a record, of what your bank stored."),
    (f"Two vCPU{EM}enough to encode.", "Two vCPU, enough to encode."),
    (f"Two vCPU {EN} enough to encode.", "Two vCPU, enough to encode."),
    (f"The lock {EM} a traffic cop {EM} allows one thread.",
     "The lock, a traffic cop, allows one thread."),
    (f"That is the cost {EM}.", "That is the cost."),
])
def test_dashes_become_commas(raw, expected):
    assert undash(raw) == expected


def test_a_number_range_becomes_a_word():
    """A comma between digits reads as a thousands separator out loud."""
    assert undash(f"Wait 5{EN}10 seconds.") == "Wait 5 to 10 seconds."


@pytest.mark.parametrize("text", [
    "We handled 20,000 requests an hour.",
    "It costs 1,250 rupees a month.",
    "Between 1,000 and 10,000 rows.",
])
def test_a_thousands_separator_is_not_read_as_a_range(text):
    """Only a dash makes a range; a comma the writer typed is already correct.

    The range rule used to fire on any digit-comma-digit, which meant it could
    not tell a comma it had just made from one that was always there, and
    "20,000 requests" shipped into the description as "20 to 000 requests".
    """
    assert undash(text) == text


def test_a_range_still_converts_next_to_a_separated_number():
    assert (undash(f"Between 5{EN}10 of the 20,000 rows.")
            == "Between 5 to 10 of the 20,000 rows.")


def test_hyphens_are_left_alone():
    text = "A delivery-ready mp4 with word-level timings."
    assert undash(text) == text


def test_text_without_dashes_is_untouched():
    text = "Nothing to change here, at all."
    assert undash(text) == text


def test_no_dash_survives():
    for raw in (f"a {EM} b", f"a{EN}b", f"{EM}leading", f"trailing{EM}"):
        assert EM not in undash(raw) and EN not in undash(raw)


def test_the_parsers_still_understand_a_hand_written_dash():
    """undash cleans what a model wrote; a human script may still contain one,
    and the caption and shot splitters rely on it as a clause boundary."""
    from vidsmith.captions import TRAILING
    from vidsmith.visuals import CLAUSE_END

    assert EM in TRAILING
    assert EM in CLAUSE_END
