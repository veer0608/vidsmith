"""Two cuts of one video have to agree, and credit only their own footage.

A widescreen video and its Short are one edit at two shapes. On a real build
they share no footage at all - 27 credits against 28, zero lines in common - so
the only honest difference between their descriptions is the credit block.

`credits_published()` already reads each ledger into its description, which
catches a credit that was never published. These are the other direction and
the pair, both of which shipped: a description naming photographers whose clips
are not in that cut, and chapters checked against `description.txt` alone while
a Short published a list YouTube would ignore.
"""
from __future__ import annotations

import pytest

from vidsmith.check import cuts_agree

PROSE = "Why computers hate bank statements.\n\nCHAPTERS\n0:00 The page\n"
WIDE_CREDITS = ("Footage from Pexels (https://pexels.com)\n"
                "Ada - https://www.pexels.com/video/1/\n")
SHORT_CREDITS = ("Footage from Pexels (https://pexels.com)\n"
                 "Grace - https://www.pexels.com/video/2/\n")


@pytest.fixture
def out(tmp_path):
    (tmp_path / "description.txt").write_text(PROSE + WIDE_CREDITS, encoding="utf-8")
    (tmp_path / "credits.txt").write_text(WIDE_CREDITS, encoding="utf-8")
    (tmp_path / "description-9x16.txt").write_text(PROSE + SHORT_CREDITS, encoding="utf-8")
    (tmp_path / "credits-9x16.txt").write_text(SHORT_CREDITS, encoding="utf-8")
    return tmp_path


def test_a_correct_pair_says_nothing(out):
    """Measured on the real pair before this was written: MgD7QwCozms and
    zAv-sAArB5Y, 27 and 28 credits, no overlap, and all three rules quiet."""
    assert cuts_agree(out, [{"time": "0:00", "label": "The page"}]) == []


def test_a_cut_may_not_credit_another_cuts_footage(out):
    """The widescreen description pasted under a Short names photographers whose
    clips are not in it. That shipped twice, and a wrong --aspect repeats it."""
    (out / "description-9x16.txt").write_text(PROSE + WIDE_CREDITS, encoding="utf-8")

    [problem] = cuts_agree(out)

    assert "description-9x16.txt credits someone" in problem
    assert "Ada" in problem


def test_every_cut_is_checked_for_its_chapters(out):
    """Chapters used to be read against description.txt alone, so a Short could
    publish a list with a missing label, which YouTube drops whole."""
    (out / "description-9x16.txt").write_text(
        "Why computers hate bank statements.\n\n" + SHORT_CREDITS, encoding="utf-8")

    problems = cuts_agree(out, [{"time": "0:00", "label": "The page"}])

    assert any("The page" in p and "description-9x16.txt" in p for p in problems), problems


def test_the_cuts_must_describe_the_same_video(out):
    """One cut rebuilt without the other, or a description written again for a
    single cut: the two videos then tell viewers different things."""
    (out / "description-9x16.txt").write_text(
        "An entirely different summary.\n" + SHORT_CREDITS, encoding="utf-8")

    problems = cuts_agree(out)

    assert any("does not describe the same video" in p for p in problems), problems


def test_a_shorts_only_project_has_nothing_to_compare(tmp_path):
    """`projects/promo-short` is real: a vertical cut with no widescreen one.
    Demanding a reference description is the shape assumption this project keeps
    tripping over."""
    (tmp_path / "description-9x16.txt").write_text(PROSE + SHORT_CREDITS, encoding="utf-8")
    (tmp_path / "credits-9x16.txt").write_text(SHORT_CREDITS, encoding="utf-8")

    assert cuts_agree(tmp_path, [{"time": "0:00", "label": "The page"}]) == []
