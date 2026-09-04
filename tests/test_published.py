"""The half of `check` that looks at what was actually published.

Every fault here is one that shipped on a real video. The offline checker passed
all of them, because `out/` was correct in each case and the mistake happened
between `out/` and the YouTube form.

Nothing here touches the network: `check_published` takes the live data as an
argument for exactly that reason. A checker whose tests need YouTube to be up is
a checker that gets skipped, and this one has to run on a spent day.
"""
from __future__ import annotations

import json

import pytest

from vidsmith.published import (Unreachable, attribution, check_published,
                                video_id)


def _live(**over):
    live = {
        "id": "0PkBP0dk4Lw",
        "title": "Why Rome Never Really Fell made with vidsmith",
        "description": (
            "Rome did not fall on a single night.\n\n"
            "CHAPTERS\n0:00 The Final Collapse\n0:18 The Popular Myth\n"
            "1:53 Endless Transformation\n\n"
            "Footage from Pixabay. Thumbnail from Pexels: "
            "https://www.pexels.com photo by Bakr Magrabi"),
        "tags": ["roman empire", "fall of rome"],
        "uploaded_captions": ["en-US"],
        "asr_captions": ["en"],
    }
    live.update(over)
    return live


@pytest.fixture
def out(tmp_path):
    (tmp_path / "credits.txt").write_text(
        "Footage from Pixabay (https://pixabay.com)\n"
        "Engin_Akyurt - https://pixabay.com/videos/id-172455/\n"
        "QuinceCreative - https://pixabay.com/videos/id-13460/\n"
        "Thumbnail: Bakr Magrabi - https://www.pexels.com/photo/a-coin-7272207/\n",
        encoding="utf-8")
    (tmp_path / "youtube.json").write_text(json.dumps({
        "title": "Why Rome Never Really Fell",
        "tags": ["roman empire"],
        "chapters": [{"time": "0:00", "label": "The Final Collapse"},
                     {"time": "0:18", "label": "The Popular Myth"},
                     {"time": "1:53", "label": "Endless Transformation"}],
    }), encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# ids
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("given", [
    "0PkBP0dk4Lw",
    "https://www.youtube.com/watch?v=0PkBP0dk4Lw",
    "https://youtu.be/0PkBP0dk4Lw",
    "https://www.youtube.com/watch?v=0PkBP0dk4Lw&t=42s",
    "https://www.youtube.com/shorts/0PkBP0dk4Lw",
])
def test_it_takes_whatever_shape_of_link_was_pasted(given):
    assert video_id(given) == "0PkBP0dk4Lw"


def test_a_non_id_is_refused_rather_than_guessed(out):
    with pytest.raises(ValueError):
        video_id("my video")


# --------------------------------------------------------------------------- #
# attribution, which is the licence condition
# --------------------------------------------------------------------------- #
def test_a_named_pexels_photographer_passes(out):
    assert attribution(_live()["description"], out) == []


def test_a_missing_pexels_photographer_is_a_finding(out):
    """The fault this found on a real published video the day it was written:
    the thumbnail's photographer was dropped when the description was trimmed."""
    desc = _live()["description"].replace(" photo by Bakr Magrabi", "")
    problems = attribution(desc, out)

    assert len(problems) == 1
    assert "Bakr Magrabi" in problems[0]


def test_pixabay_contributors_are_not_required_by_name(out):
    """Pixabay's terms ask that you do not pass the content off as your own,
    not that every uploader is listed. Demanding it would make the check fire
    on correct videos, and a check that cries wolf stops being read."""
    problems = attribution(_live()["description"], out)
    assert not any("Engin_Akyurt" in p or "QuinceCreative" in p for p in problems)


def test_the_source_still_has_to_be_named(out):
    problems = attribution("A description crediting nobody at all.", out)
    joined = " ".join(problems)
    assert "pexels.com" in joined
    assert "Pixabay" in joined


def test_the_two_licences_are_not_confused_for_each_other(tmp_path):
    """Pexels footage, no Pixabay anywhere: every contributor must be named.

    The published mistake this guards was reading the Pexels *content* licence,
    which says attribution is not required, instead of the API guidelines, which
    govern how vidsmith obtains the clip.
    """
    (tmp_path / "credits.txt").write_text(
        "Footage from Pexels (https://www.pexels.com)\n"
        "Kelly Lacy - https://www.pexels.com/video/1/\n"
        "Pavel Danilyuk - https://www.pexels.com/video/2/\n", encoding="utf-8")

    problems = attribution(
        "Footage from Pexels: https://www.pexels.com", tmp_path)
    assert len(problems) == 2
    assert any("Kelly Lacy" in p for p in problems)
    assert any("Pavel Danilyuk" in p for p in problems)


# --------------------------------------------------------------------------- #
# the rest of the published video
# --------------------------------------------------------------------------- #
def test_a_clean_video_reports_nothing(out):
    assert check_published(out, "0PkBP0dk4Lw", live=_live()) == []


def test_an_empty_description_is_the_only_thing_reported(out):
    """It already happened: the title saved and the description did not, because
    a published video's Details page does not autosave. Everything else would be
    noise on top of that one fact."""
    problems = check_published(out, "0PkBP0dk4Lw", live=_live(description="  "))
    assert problems == ["the published video has no description at all"]


def test_a_dropped_chapter_is_reported(out):
    desc = _live()["description"].replace("0:18 The Popular Myth\n", "")
    problems = check_published(out, "0PkBP0dk4Lw", live=_live(description=desc))
    assert any("The Popular Myth" in p for p in problems)
    assert any("no chapters at all" in p for p in problems)


def test_an_appended_title_is_not_a_finding(out):
    """The real one reads 'Why Rome Never Really Fell made with vidsmith'. People
    edit titles after upload and that is not a fault."""
    problems = check_published(out, "0PkBP0dk4Lw", live=_live())
    assert not any("title" in p for p in problems)


def test_a_replaced_title_is_a_finding(out):
    problems = check_published(out, "0PkBP0dk4Lw",
                               live=_live(title="Ten Facts About Rome"))
    assert any("does not contain the built title" in p for p in problems)


def test_automatic_captions_only_is_a_finding(out):
    """The point of the whole pipeline is exact word timings from edge-tts.
    Shipping YouTube's transcription instead throws that away silently."""
    problems = check_published(out, "0PkBP0dk4Lw",
                               live=_live(uploaded_captions=[]))
    assert any("automatic" in p and "captions.srt" in p for p in problems)


def test_no_captions_at_all_is_a_different_finding(out):
    problems = check_published(out, "0PkBP0dk4Lw",
                               live=_live(uploaded_captions=[], asr_captions=[]))
    assert any("no caption track" in p for p in problems)


def test_tags_that_never_arrived_are_reported(out):
    """Tags were lost once on this exact video, to the same no-autosave trap."""
    problems = check_published(out, "0PkBP0dk4Lw", live=_live(tags=[]))
    assert any("tags" in p for p in problems)


def test_it_never_reaches_the_network_when_given_live_data(out, monkeypatch):
    """The guarantee the offline half of check depends on."""
    import vidsmith.published as pub

    def explode(*a, **k):
        raise AssertionError("fetch() was called despite live data being given")

    monkeypatch.setattr(pub, "fetch", explode)
    assert check_published(out, "0PkBP0dk4Lw", live=_live()) == []


def test_an_unreadable_page_is_not_a_finding_about_the_video(out, monkeypatch):
    """A blocked request must not be reported as 'the video has no description'.
    The CLI prints it as a warning and leaves the offline findings standing."""
    import vidsmith.published as pub

    monkeypatch.setattr(pub, "fetch",
                        lambda *a, **k: (_ for _ in ()).throw(Unreachable("blocked")))
    with pytest.raises(Unreachable):
        check_published(out, "0PkBP0dk4Lw")
