"""Chapters YouTube will actually render.

YouTube enforces its rules by ignoring the whole list rather than the offending
line: the first must be at 0:00, there must be at least three, and none may be
shorter than ten seconds. Break one and the video has no chapters, with nothing
anywhere saying why.

Which makes this the same shape as every other bug in this project's history:
it fails at the destination, silently, and you only find it by looking at the
published result. The build was emitting a six-second chapter and would have
shipped a description whose entire chapter list YouTube discards.
"""
from __future__ import annotations

import pytest

from vidsmith.llm import MIN_CHAPTERS, MIN_CHAPTER_SECONDS, usable_chapters


def _ch(*stamps):
    return [{"time": s, "label": f"scene {i}"} for i, s in enumerate(stamps)]


def _times(chapters):
    return [c["time"] for c in chapters]


# --------------------------------------------------------------------------- #
# the case that was actually shipping
# --------------------------------------------------------------------------- #
def test_the_real_build_that_would_have_lost_its_chapters():
    """Six chapters off a 60.8s render, the last two under ten seconds.

    0:47 to 0:53 is six seconds and 0:53 to the end is under eight, so YouTube
    would have shown none of the six.
    """
    kept = usable_chapters(_ch("0:00", "0:15", "0:26", "0:36", "0:47", "0:53"), 60.8)
    assert _times(kept) == ["0:00", "0:15", "0:26", "0:36", "0:47"]


def test_every_surviving_chapter_clears_the_minimum():
    """The invariant, stated directly rather than by example."""
    kept = usable_chapters(_ch("0:00", "0:15", "0:26", "0:36", "0:47", "0:53"), 60.8)
    starts = [float(m) * 60 + float(s) for m, s in (t.split(":") for t in _times(kept))]
    for a, b in zip(starts, starts[1:]):
        assert b - a >= MIN_CHAPTER_SECONDS
    assert 60.8 - starts[-1] >= MIN_CHAPTER_SECONDS, "the last one runs to the end"


# --------------------------------------------------------------------------- #
# the rules, one at a time
# --------------------------------------------------------------------------- #
def test_a_short_chapter_folds_into_the_one_before_it():
    """Dropping it outright would leave a gap; the earlier label still fits."""
    kept = usable_chapters(_ch("0:00", "0:04", "0:20", "0:40"), 90.0)
    assert _times(kept) == ["0:00", "0:20", "0:40"]


def test_a_short_final_chapter_is_measured_against_the_runtime():
    """A last chapter near the end breaks the list exactly as an interior one."""
    kept = usable_chapters(_ch("0:00", "0:20", "0:40", "0:58"), 60.0)
    assert _times(kept) == ["0:00", "0:20", "0:40"]


def test_a_list_not_starting_at_zero_is_refused_whole():
    assert usable_chapters(_ch("0:05", "0:20", "0:40"), 90.0) == []


def test_too_few_survivors_means_none_at_all():
    """YouTube discards the list anyway.

    A youtube.txt promising chapters that will never appear is worse than one
    admitting the video has none.
    """
    assert usable_chapters(_ch("0:00", "0:03", "0:06", "0:09"), 30.0) == []


def test_exactly_the_minimum_is_kept():
    kept = usable_chapters(_ch("0:00", "0:10", "0:20"), 30.0)
    assert len(kept) == MIN_CHAPTERS
    assert _times(kept) == ["0:00", "0:10", "0:20"]


# --------------------------------------------------------------------------- #
# input a model wrote
# --------------------------------------------------------------------------- #
def test_out_of_order_chapters_are_sorted():
    """Nothing guarantees a model returns them in order."""
    kept = usable_chapters(_ch("0:20", "0:00", "0:40"), 90.0)
    assert _times(kept) == ["0:00", "0:20", "0:40"]


def test_an_unparseable_stamp_is_dropped_not_fatal():
    kept = usable_chapters(
        [{"time": "0:00"}, {"time": "soon"}, {"time": "0:20"}, {"time": "0:40"}], 90.0)
    assert _times(kept) == ["0:00", "0:20", "0:40"]


def test_an_hour_long_stamp_parses():
    kept = usable_chapters(_ch("0:00", "0:30", "1:00:00"), 4000.0)
    assert _times(kept) == ["0:00", "0:30", "1:00:00"]


def test_labels_survive_the_filter():
    kept = usable_chapters(
        [{"time": "0:00", "label": "The hook"},
         {"time": "0:20", "label": "The mechanism"},
         {"time": "0:40", "label": "The takeaway"}], 90.0)
    assert [c["label"] for c in kept] == ["The hook", "The mechanism", "The takeaway"]


def test_no_internal_bookkeeping_leaks_into_the_output():
    """The sort key must not reach youtube.json."""
    for chapter in usable_chapters(_ch("0:00", "0:20", "0:40"), 90.0):
        assert set(chapter) <= {"time", "label"}


@pytest.mark.parametrize("chapters", [None, [], [{"label": "no time"}]])
def test_nothing_usable_is_an_empty_list(chapters):
    assert usable_chapters(chapters, 90.0) == []


# --------------------------------------------------------------------------- #
# the upload form's hard caps
# --------------------------------------------------------------------------- #
from vidsmith.llm import (MAX_TAGS_TOTAL, MAX_TITLE,           # noqa: E402
                          within_youtube_limits)


def test_a_long_title_is_cut_to_the_limit():
    """100 characters is a refusal at upload, after the render is paid for."""
    meta = within_youtube_limits({"title": "word " * 40})
    assert len(meta["title"]) <= MAX_TITLE


def test_a_title_is_cut_at_a_word():
    meta = within_youtube_limits({"title": "supercalifragilistic " * 8})
    assert not meta["title"].endswith("supercalifragilis")
    assert " " in meta["title"]


def test_a_short_title_is_untouched():
    """The common case, and it must not acquire an ellipsis or lose a full stop."""
    assert within_youtube_limits({"title": "Why Your First PR Gets Rejected"}) \
        ["title"] == "Why Your First PR Gets Rejected"


def test_tags_are_dropped_whole_not_truncated():
    """Half a tag is not a tag, and the model writes them most relevant first."""
    meta = within_youtube_limits({"tags": ["x" * 200, "y" * 200, "z" * 200]})
    assert meta["tags"] == ["x" * 200, "y" * 200]
    assert all(len(t) == 200 for t in meta["tags"])


def test_the_tag_budget_counts_the_separators():
    """500 characters is the total across all tags, commas included."""
    meta = within_youtube_limits({"tags": [f"tag{i:03d}" for i in range(200)]})
    rendered = ", ".join(meta["tags"])
    assert len(rendered) <= MAX_TAGS_TOTAL


def test_ordinary_tags_all_survive():
    real = ["pull request", "open source", "coding", "git", "github",
            "software engineering", "programming", "code review",
            "developer tips", "junior developer", "tech careers",
            "coding advice"]
    assert within_youtube_limits({"tags": real})["tags"] == real


def test_blank_tags_are_dropped():
    assert within_youtube_limits({"tags": ["real", "", "  ", "also real"]}) \
        ["tags"] == ["real", "also real"]


def test_a_metadata_block_without_the_keys_survives():
    """upload_metadata runs this on whatever the model returned."""
    assert within_youtube_limits({})["tags"] == []
    assert "title" not in within_youtube_limits({})
