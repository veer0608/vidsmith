"""Choosing a finished render's thumbnail by hand.

The build picks the thumbnail alone and it is the first thing a viewer sees. A
thumbnail carries a licence obligation of its own - a stock photograph owes its
photographer a credit, a frame of the footage owes nothing extra - so these
check the credits and the pasted description move with it, not just the jpg.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from test_retake import finished_build

from vidsmith import cover, pipeline, visuals

KEYS = {"pexels": "px", "gemini": ""}
PHOTOS = [{"id": str(i), "url": f"https://images.pexels.com/{i}-large.jpg",
           "preview": f"https://images.pexels.com/{i}-medium.jpg",
           "author": f"Photographer {i}", "page": f"https://www.pexels.com/photo/{i}/",
           "alt": f"photo {i}"} for i in (100, 200, 300)]


def _jpeg(colour=(40, 90, 160), size=(1600, 1000)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def root(tmp_path):
    root = finished_build(tmp_path / "job")
    out = root / "out"
    (out / "a-title.jpg").write_bytes(_jpeg((0, 0, 0)))
    (out / "credits.txt").write_text(
        "Footage from Pexels (https://www.pexels.com)\nAda - https://pexels.com/v/10\n"
        "Thumbnail: Old Photographer - https://www.pexels.com/photo/1/\n", encoding="utf-8")
    (out / "youtube.json").write_text(json.dumps(
        {"title": "A Title", "description": "About racks.", "tags": ["racks"]}), encoding="utf-8")
    pipeline.write_thumbnail_choice(root / "build", "",
                                    {"kind": "photo", "query": "server room lights", "id": "1"})
    return root


@pytest.fixture
def searches(monkeypatch):
    asked = []

    def photos(query, key, orientation="landscape", count=12):
        asked.append((query, orientation))
        return [dict(p) for p in PHOTOS]

    monkeypatch.setattr(visuals, "pexels_photos", photos)
    return asked


@pytest.fixture
def downloads(monkeypatch):
    fetched = []

    class Response:
        content = _jpeg()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(cover.requests, "get",
                        lambda url, timeout=0: fetched.append(url) or Response())
    return fetched


def test_the_photos_start_from_the_search_the_build_used(root, searches):
    body = cover.candidates(root, keys=KEYS)

    assert searches == [("server room lights", "landscape")]
    assert body["query"] == "server room lights" and body["current"]["id"] == "1"
    assert [p["id"] for p in body["photos"]] == ["100", "200", "300"]


def test_a_typed_search_is_used_and_trimmed(root, searches):
    cover.candidates(root, keys=KEYS, query="  a   blinking  rack " + "x" * 300)
    assert searches[0][0].startswith("a blinking rack x") and len(searches[0][0]) == 100


def test_a_vertical_cut_searches_for_vertical_photos(tmp_path, searches):
    import yaml

    root = finished_build(tmp_path / "job")
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    raw["render"]["aspect"] = "9:16"
    (root / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    (root / "out" / "a-title.mp4").rename(root / "out" / "a-title-9x16.mp4")

    cover.candidates(root, keys=KEYS)

    assert searches[0][1] == "portrait"


def test_searching_photos_needs_the_pexels_key(root, searches):
    with pytest.raises(cover.RetakeRefused, match="PEXELS_API_KEY"):
        cover.candidates(root, keys={"pexels": ""})


def test_a_photo_becomes_the_thumbnail_and_its_photographer_the_credit(root, searches, downloads):
    before = (root / "out" / "a-title.jpg").read_bytes()

    cover.use_photo(root, "200", keys=KEYS, log=lambda *a: None)

    jpg = root / "out" / "a-title.jpg"
    assert jpg.read_bytes() != before
    assert Image.open(jpg).size == (1280, 720)
    assert downloads == ["https://images.pexels.com/200-large.jpg"], "the full photo, not the preview"
    credits = (root / "out" / "credits.txt").read_text(encoding="utf-8")
    assert "Thumbnail: Photographer 200 - https://www.pexels.com/photo/200/" in credits
    assert "Old Photographer" not in credits and "Ada - " in credits, "footage credits stay"
    description = (root / "out" / "description.txt").read_text(encoding="utf-8")
    assert "Photographer 200" in description and "Old Photographer" not in description, \
        "the file people paste must credit the photograph that is actually on it"
    assert "About racks." in description
    choice = json.loads((root / "build" / "thumbnail.json").read_text(encoding="utf-8"))
    assert choice == {"kind": "photo", "query": "server room lights", "id": "200"}
    assert not list((root / "out").glob("*.part*")), "a half-made jpg was left in out/"


def test_a_photo_not_in_the_search_is_never_downloaded(root, searches, downloads):
    with pytest.raises(cover.RetakeRefused, match="not in the results"):
        cover.use_photo(root, "999", keys=KEYS)
    assert not downloads


def test_a_frame_of_the_footage_owes_no_thumbnail_credit(root, monkeypatch):
    runs = []

    def grab(args):
        runs.append(args)
        Path(args[-1]).write_bytes(_jpeg((200, 30, 30), (1920, 1080)))

    monkeypatch.setattr(cover.ff, "run", grab)

    cover.use_frame(root, 0, 1, log=lambda *a: None)

    assert runs[0][runs[0].index("-i") + 1].endswith("scene_000_01.mp4"), \
        "the shot's own clip, which has no captions burned in"
    credits = (root / "out" / "credits.txt").read_text(encoding="utf-8")
    assert "Thumbnail:" not in credits and "Ada - " in credits
    assert "Old Photographer" not in (root / "out" / "description.txt").read_text(encoding="utf-8")
    choice = json.loads((root / "build" / "thumbnail.json").read_text(encoding="utf-8"))
    assert choice == {"kind": "frame", "scene": 0, "shot": 1}


def test_a_frame_needs_the_shot_to_exist(root):
    with pytest.raises(cover.RetakeRefused, match="no shot 7"):
        cover.use_frame(root, 0, 7)
