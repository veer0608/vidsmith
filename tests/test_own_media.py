"""A viewer's own photos and videos: the directive, the spread, the upload door.

The faults worth guarding are quiet ones. A `[media:]` name that is missing
must not turn the scene into a card without saying so, a name must never reach
outside the folder, and a file that is not what its extension says must be
refused when it arrives, not minutes into the encode.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import make_scene
from PIL import Image

from vidsmith import script_parser
from vidsmith.config import ThemeConfig, VisualConfig
from vidsmith.theme import resolve
from vidsmith.visuals import MINE_DIR, VisualBuilder

SCRIPT = "# T\n\nFirst scene is here.\n\nSecond scene is here.\n\nThird scene is here.\n"


def test_media_directive_names_files_for_the_next_scene():
    _, scenes, _ = script_parser._parse(
        "# T\n\nOne.\n[media: a.jpg, B.mp4]\nTwo.\n\nThree.\n")
    assert [s.media for s in scenes] == [[], ["a.jpg", "B.mp4"], []]


def test_media_changes_the_picture_key_so_a_rebuild_notices():
    _, a, _ = script_parser._parse("# T\n\nOne.\n")
    _, b, _ = script_parser._parse("# T\n\n[media: x.jpg]\nOne.\n")
    assert a[0].source_key() != b[0].source_key()


def test_spread_gives_every_scene_a_file_in_order():
    out = web_uploads().spread(SCRIPT, ["a.jpg", "b.jpg", "c.jpg"])
    _, scenes, _ = script_parser._parse(out)
    assert [s.media for s in scenes] == [["a.jpg"], ["b.jpg"], ["c.jpg"]]
    assert [s.text for s in scenes] == [s.text for s in script_parser._parse(SCRIPT)[1]]


def test_spread_with_fewer_files_than_scenes_uses_each_and_repeats_none_early():
    _, scenes, _ = script_parser._parse(web_uploads().spread(SCRIPT, ["a.jpg", "b.jpg"]))
    assert [s.media for s in scenes] == [["a.jpg"], ["a.jpg"], ["b.jpg"]]


def test_spread_with_more_files_than_scenes_groups_them():
    names = [f"{i}.jpg" for i in range(6)]
    _, scenes, _ = script_parser._parse(web_uploads().spread(SCRIPT, names))
    assert [s.media for s in scenes] == [names[0:2], names[2:4], names[4:6]]


def test_spread_leaves_a_script_that_places_its_own_files():
    placed = SCRIPT.replace("Second", "[media: mine.jpg]\nSecond")
    assert web_uploads().spread(placed, ["a.jpg"]) == placed


def test_spread_keeps_a_visual_directive_on_its_scene():
    text = "# T\n\n[visual: a beach]\nOne scene.\n"
    out = web_uploads().spread(text, ["a.jpg"])
    _, scenes, _ = script_parser._parse(out)
    assert scenes[0].directive == "a beach" and scenes[0].media == ["a.jpg"]


def web_uploads():
    pytest.importorskip("fastapi")
    from web import uploads
    return uploads


def _builder(tmp_path, lines):
    workdir = tmp_path / "build" / "visuals"
    workdir.mkdir(parents=True)
    return VisualBuilder(VisualConfig(provider="cards"), (640, 360), 24, workdir,
                         keys={}, log=lines.append, theme=resolve("midnight"),
                         theme_cfg=ThemeConfig(), total_scenes=1, project_root=tmp_path)


def test_mine_finds_a_file_by_name_ignoring_case(tmp_path):
    folder = tmp_path / MINE_DIR
    folder.mkdir(parents=True)
    (folder / "Beach.JPG").write_bytes(b"")
    lines: list = []
    got = _builder(tmp_path, lines)._mine(make_scene("x", media=["beach.jpg"]))
    assert [Path(s["path"]).name for s in got] == ["Beach.JPG"]
    assert got[0]["author"] == ""            # the viewer's own: nothing to credit


def test_mine_says_when_a_name_is_missing(tmp_path):
    (tmp_path / MINE_DIR).mkdir(parents=True)
    lines: list = []
    assert _builder(tmp_path, lines)._mine(make_scene("x", media=["gone.jpg"])) == []
    assert any("gone.jpg" in line for line in lines)


def test_mine_cannot_reach_outside_its_folder(tmp_path):
    (tmp_path / MINE_DIR).mkdir(parents=True)
    (tmp_path / "secret.png").write_bytes(b"")
    lines: list = []
    assert _builder(tmp_path, lines)._mine(
        make_scene("x", media=["../../secret.png"])) == []


# -- the upload door ------------------------------------------------------- #

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from web import app as web_app
    from web.jobs import Jobs
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path))
    monkeypatch.setattr(web_app, "TOKEN", "")
    return TestClient(web_app.app)


def _png(path: Path) -> bytes:
    Image.new("RGB", (8, 8), "red").save(path)
    return path.read_bytes()


def test_an_image_uploads_and_comes_back(client, tmp_path):
    body = _png(tmp_path / "x.png")
    r = client.post("/api/uploads?name=holiday.png", content=body)
    assert r.status_code == 201, r.text
    got = r.json()
    assert got["name"] == "holiday.png" and got["kind"] == "image"
    assert client.get(f"/api/uploads/{got['id']}").content == body


def test_a_file_that_is_not_an_image_is_refused_at_the_door(client):
    r = client.post("/api/uploads?name=fake.jpg", content=b"not an image at all")
    assert r.status_code == 400 and "fake.jpg" in r.json()["detail"]


def test_an_unsupported_type_is_refused(client):
    r = client.post("/api/uploads?name=run.exe", content=b"MZ")
    assert r.status_code == 400


def test_a_path_in_the_name_is_flattened(client, tmp_path):
    body = _png(tmp_path / "x.png")
    r = client.post("/api/uploads?name=..%2F..%2Fevil.png", content=body)
    assert r.status_code == 201 and r.json()["name"] == "evil.png"


def test_an_oversize_body_is_cut_off(client, monkeypatch, tmp_path):
    from web import uploads
    monkeypatch.setattr(uploads, "MAX_IMAGE_BYTES", 100)
    r = client.post("/api/uploads?name=big.png", content=b"x" * 500)
    assert r.status_code == 400 and "limit" in r.json()["detail"]


def test_a_render_stages_the_files_and_spreads_them(client, tmp_path, monkeypatch):
    from web import app as web_app
    monkeypatch.setattr(web_app.jobs, "_spawn", lambda job: None)
    body = _png(tmp_path / "x.png")
    up = client.post("/api/uploads?name=a.png", content=body).json()
    r = client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards",
                                       "media": [up["id"]]})
    assert r.status_code == 202, r.text
    root = web_app.jobs.get(r.json()["id"]).root
    assert (root / "assets" / "mine" / "a.png").is_file()
    assert "[media: a.png]" in (root / "script.md").read_text(encoding="utf-8")


def test_an_unknown_upload_id_is_a_400_not_a_failed_job(client):
    r = client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards",
                                       "media": ["0123456789ab"]})
    assert r.status_code == 400
    assert client.get("/api/jobs").json()["renders"] == []
