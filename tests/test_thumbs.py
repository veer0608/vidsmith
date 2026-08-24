"""Ranking the stock thumbnails.

This path went quiet rather than red: the ranking call referenced a name that
did not exist in its scope, the bare `except Exception` around it caught the
NameError, and every video shipped with whatever Pexels happened to return
first. The model was never asked. These tests are here so that failure mode
cannot come back silently.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vidsmith import llm, thumbs

PHOTOS = [
    {"id": "1", "alt": "a laptop open on a desk", "preview": "http://x.test/1s",
     "url": "http://x.test/1", "author": "Ada", "page": "http://x.test/a"},
    {"id": "2", "alt": "a person holding a paper receipt", "preview": "http://x.test/2s",
     "url": "http://x.test/2", "author": "Grace", "page": "http://x.test/g"},
    {"id": "3", "alt": "a bank statement on a table", "preview": "http://x.test/3s",
     "url": "http://x.test/3", "author": "Kay", "page": "http://x.test/k"},
]


@pytest.fixture
def stock(monkeypatch):
    """A working search and a downloadable image, so only the ranking is under test."""
    from PIL import Image
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGB", (64, 64), (30, 40, 60)).save(buf, "JPEG")
    body = buf.getvalue()

    class Response:
        content = body
        def raise_for_status(self): return None

    monkeypatch.setattr(thumbs.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr("vidsmith.visuals.pexels_photos",
                        lambda *a, **k: list(PHOTOS))
    monkeypatch.setattr(llm, "thumbnail_query", lambda *a, **k: "bank statement")
    return body


def test_the_model_is_actually_asked_which_photo_to_use(tmp_path, stock, monkeypatch):
    calls = []

    def fake_pick(title, hook, images, api_key, **kwargs):
        calls.append({"title": title, "hook": hook, "images": len(images),
                      "notes": kwargs.get("notes", "")})
        return 2, "it shows the statement itself"

    monkeypatch.setattr(llm, "pick_thumbnail", fake_pick)
    out = thumbs.from_stock("Why Your Bank Statement Lies",
                            "a bank statement on a desk", (1920, 1080),
                            {"pexels": "k", "gemini": "g"}, tmp_path,
                            log=lambda *a: None)

    assert calls, "the ranking never ran"
    assert out["author"] == PHOTOS[2]["author"], "the model's pick was not used"


def test_the_ranking_is_told_what_the_video_shows_not_its_hook(tmp_path, stock,
                                                               monkeypatch):
    """A hook is a frustration; ranking against one returns a stressed face."""
    seen = {}
    monkeypatch.setattr(llm, "pick_thumbnail",
                        lambda title, hook, images, key, **kw:
                        (seen.update(hook=hook, notes=kw.get("notes", "")), (0, ""))[1])
    thumbs.from_stock("Why Your Bank Statement Lies", "a bank statement on a desk",
                      (1920, 1080), {"pexels": "k", "gemini": "g"}, tmp_path,
                      log=lambda *a: None)
    assert seen["hook"] == "a bank statement on a desk"


def test_every_candidate_is_described_to_the_model(tmp_path, stock, monkeypatch):
    """The alt text is the only way it can tell a receipt from a statement."""
    seen = {}
    monkeypatch.setattr(llm, "pick_thumbnail",
                        lambda title, hook, images, key, **kw:
                        (seen.update(notes=kw.get("notes", "")), (0, ""))[1])
    thumbs.from_stock("T", "a bank statement", (1920, 1080),
                      {"pexels": "k", "gemini": "g"}, tmp_path, log=lambda *a: None)
    for photo in PHOTOS:
        assert photo["alt"] in seen["notes"]


def test_a_failed_ranking_still_returns_a_thumbnail(tmp_path, stock, monkeypatch):
    """The fallback is correct behaviour; it was the silence that was wrong."""
    lines = []
    monkeypatch.setattr(llm, "pick_thumbnail",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("no pick")))
    out = thumbs.from_stock("T", "a bank statement", (1920, 1080),
                            {"pexels": "k", "gemini": "g"}, tmp_path,
                            log=lines.append)
    assert out["author"] == PHOTOS[0]["author"]
    assert any("thumbnail pick skipped" in line for line in lines)


def test_stock_photos_are_not_described_to_the_model_as_video_frames(tmp_path, stock,
                                                                     monkeypatch):
    """Ranking a stock photo against "prefer a frame from the video" is a trap:
    none of the candidates is one, so the advice can only be satisfied badly."""
    sent = {}
    monkeypatch.setattr(llm, "generate_vision",
                        lambda prompt, *a, **k: (sent.update(prompt=prompt),
                                                 '{"pick": 1, "why": "x"}')[1])
    thumbs.from_stock("T", "a bank statement", (1920, 1080),
                      {"pexels": "k", "gemini": "g"}, tmp_path, log=lambda *a: None)
    assert "stock photographs found for this video" in sent["prompt"]
    assert "frames taken from the finished video" not in sent["prompt"]
    assert "beats a stock shot" not in sent["prompt"]
    assert '{"pick"' in sent["prompt"], "the JSON example must survive formatting"


def test_the_frame_prompt_is_unchanged_for_frames(monkeypatch):
    sent = {}
    monkeypatch.setattr(llm, "generate_vision",
                        lambda prompt, *a, **k: (sent.update(prompt=prompt),
                                                 '{"pick": 0}')[1])
    llm.pick_thumbnail("T", "a hook", [b"a", b"b"], "key", drawn=(1,))
    assert "frames taken from the finished video" in sent["prompt"]
    assert "beats a stock shot" in sent["prompt"]
    assert "DRAWN FOR THIS VIDEO: images 1" in sent["prompt"]
    assert '{"pick"' in sent["prompt"]


def test_the_notes_reach_the_prompt_whole(monkeypatch):
    """A line per candidate must not be clipped the way a hook is."""
    sent = {}
    monkeypatch.setattr(llm, "generate_vision",
                        lambda prompt, *a, **k: (sent.update(prompt=prompt),
                                                 '{"pick": 0}')[1])
    notes = "WHAT EACH PHOTO SHOWS:\n" + "\n".join(
        f"{i}: a photograph described at some considerable length" for i in range(8))
    llm.pick_thumbnail("T", "h" * 400, [b"a", b"b"], "key", notes=notes)
    assert "7: a photograph" in sent["prompt"], "the last candidate was truncated away"
    assert "h" * 221 not in sent["prompt"], "the hook should still be clamped"
