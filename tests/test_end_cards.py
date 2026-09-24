"""A stock "subscribe" card is never footage of anything.

howto went public with a "Thank you for watching! SUBSCRIBE" card at 76s, under
a line about crediting photographers, because the rerank judged it close enough
to the search. Across 3,620 distinct results cached by real builds, the
phrases below named exactly the 15 end cards among them and nothing else.
"""
from __future__ import annotations

import pytest

from vidsmith import visuals
from vidsmith.visuals import end_card

from test_clip_fit import _builder

PAGE = "https://www.pexels.com/video/{}/"


@pytest.mark.parametrize("slug, phrase", [
    ("a-white-and-black-thank-you-card-with-the-words-thank-you-for-watching-3822494",
     "thank-you-for-watching"),
    ("like-dislike-and-subscribe-buttons-on-black-screen-10378511", "subscribe"),
    ("youtube-subscribe-4936763", "subscribe"),
    ("subscribe-button-for-social-media-10166810", "subscribe"),
])
def test_a_call_to_action_is_named(slug, phrase):
    assert end_card({"page": PAGE.format(slug)}) == phrase


@pytest.mark.parametrize("page", [
    PAGE.format("person-cancelling-a-subscription-on-a-phone-123"),
    PAGE.format("a-thank-you-card-on-a-desk-456"),
    PAGE.format("close-up-of-code-installing-dependencies-35475096"),
    "https://pixabay.com/videos/id-12345/",
    "",
])
def test_ordinary_footage_is_not(page):
    assert end_card({"page": page}) == ""


def test_an_end_card_never_reaches_the_rerank(tmp_path, monkeypatch, scene):
    lines = []
    builder = _builder(tmp_path, lines)
    hits = [{"id": i, "url": f"https://x/{i}", "author": f"a{i}", "preview": "",
             "page": PAGE.format(slug)}
            for i, slug in (("1", "video-player-on-a-laptop-1"),
                            ("3822494", "a-white-and-black-thank-you-card-with-the-"
                                        "words-thank-you-for-watching-3822494"),
                            ("2", "editing-timeline-on-a-monitor-2"))]
    shown = []

    def rerank(hits, scene, query, **k):
        shown.extend(h["id"] for h in hits)
        return hits

    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: list(hits))
    monkeypatch.setattr(builder, "_rerank", rerank)
    monkeypatch.setattr(visuals, "_download", lambda url, out, *a: out.write_bytes(b"x"))
    monkeypatch.setattr(visuals.ff, "duration", lambda p: 10.0)
    monkeypatch.setattr(builder, "_twin", lambda *a: None)

    picked = builder._stock_batch("video player", 2, scene)

    assert shown == ["1", "2"]
    assert [p["id"] for p in picked] == ["1", "2"]
    assert any("end card: dropped pexels 3822494" in line for line in lines)
