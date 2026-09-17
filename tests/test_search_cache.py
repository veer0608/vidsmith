"""Repeated stock searches must come off disk, not off the API.

Pixabay's API terms require a result to be cached for 24 hours rather than
re-requested, and Pexels bills a monthly quota that every render spends. Two
customers with similar scenes used to cost two searches each; a paid instance
would burn its quota answering the same question.
"""
from __future__ import annotations

import json
import time

import pytest

from vidsmith import visuals


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    """Never let a test read or write the real cache."""
    monkeypatch.setenv("VIDSMITH_SEARCH_CACHE", str(tmp_path / "searches"))
    return tmp_path / "searches"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


PIXABAY_PAYLOAD = {"hits": [{
    "id": 1, "duration": 8, "user": "Ada", "pageURL": "http://x.test/a",
    "videos": {"large": {"url": "http://x.test/1.mp4", "thumbnail": "http://x.test/1.jpg"}},
}]}

PEXELS_VIDEO_PAYLOAD = {"videos": [{
    "id": 7, "duration": 9, "user": {"name": "Grace"}, "url": "http://x.test/g",
    "image": "http://x.test/g.jpg",
    "video_files": [{"link": "http://x.test/g.mp4", "height": 1080}],
}]}


def _count_calls(monkeypatch, payload):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(payload)

    monkeypatch.setattr(visuals.requests, "get", fake_get)
    return calls


def test_a_repeated_pixabay_search_does_not_hit_the_api(monkeypatch):
    calls = _count_calls(monkeypatch, PIXABAY_PAYLOAD)

    first = visuals.pixabay_search("city at night", "key", 1080)
    second = visuals.pixabay_search("city at night", "key", 1080)

    assert len(calls) == 1, f"the second search went to the network: {calls}"
    assert first == second


def test_a_repeated_pexels_search_does_not_hit_the_api(monkeypatch):
    calls = _count_calls(monkeypatch, PEXELS_VIDEO_PAYLOAD)

    visuals.pexels_search("a quiet office", "key", "landscape", 1080)
    visuals.pexels_search("a quiet office", "key", "landscape", 1080)

    assert len(calls) == 1


def test_a_different_query_is_a_different_search(monkeypatch):
    calls = _count_calls(monkeypatch, PIXABAY_PAYLOAD)

    visuals.pixabay_search("city at night", "key", 1080)
    visuals.pixabay_search("a quiet office", "key", 1080)
    visuals.pixabay_search("city at night", "key", 720)      # size is part of it

    assert len(calls) == 3


def test_the_api_key_never_reaches_the_cache_filename(cache_dir, monkeypatch):
    """The cache is shared across jobs, so a key in a filename would be a
    secret sitting somewhere nobody thinks of as sensitive."""
    _count_calls(monkeypatch, PIXABAY_PAYLOAD)
    visuals.pixabay_search("city at night", "super-secret-key", 1080)

    names = [p.name for p in cache_dir.iterdir()]
    assert names and all("super-secret-key" not in n for n in names), names


def test_a_stale_entry_is_searched_again(cache_dir, monkeypatch):
    calls = _count_calls(monkeypatch, PIXABAY_PAYLOAD)
    visuals.pixabay_search("city at night", "key", 1080)
    assert len(calls) == 1

    for path in cache_dir.iterdir():                 # older than the day allowed
        stale = time.time() - (visuals.SEARCH_TTL + 60)
        import os
        os.utime(path, (stale, stale))

    visuals.pixabay_search("city at night", "key", 1080)
    assert len(calls) == 2, "a day-old entry must be refreshed, not served"


def test_an_unreadable_cache_does_not_stop_a_build(cache_dir, monkeypatch):
    """A search that still works beats a render that stops."""
    calls = _count_calls(monkeypatch, PIXABAY_PAYLOAD)
    visuals.pixabay_search("city at night", "key", 1080)
    for path in cache_dir.iterdir():
        path.write_text("{ this is not json", encoding="utf-8")

    hits = visuals.pixabay_search("city at night", "key", 1080)
    assert len(calls) == 2 and hits, "a corrupt entry should be re-fetched"


def test_the_cached_shape_is_the_parsed_result_not_the_raw_payload(cache_dir,
                                                                   monkeypatch):
    """Caching the raw API body would re-do the parsing, and worse, would store
    fields the parser deliberately drops."""
    _count_calls(monkeypatch, PIXABAY_PAYLOAD)
    visuals.pixabay_search("city at night", "key", 1080)

    stored = json.loads(next(cache_dir.iterdir()).read_text(encoding="utf-8"))
    assert stored[0]["author"] == "Ada"
    assert "hits" not in stored
