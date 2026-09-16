"""Changing a finished render's thumbnail from the page."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient        # noqa: E402

import web.jobs as jobs_mod                      # noqa: E402
from test_cover import PHOTOS, _jpeg             # noqa: E402
from test_retake import _hits, finished_build    # noqa: E402
from vidsmith import cover, visuals              # noqa: E402
from web import app as web_app                   # noqa: E402
from web.jobs import Jobs                        # noqa: E402

SCRIPT = "# T\n\n## One\nA short line of narration for the test.\n"


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    def fake_build(root, log=print, retake=False, **kwargs):
        root = finished_build(Path(root))
        (root / "out" / "a-title.jpg").write_bytes(_jpeg((0, 0, 0)))
        log("done     a-title.mp4  (12.0s, 1.0 MB, 3s to build)")

    class Response:
        content = _jpeg()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)
    monkeypatch.setattr(visuals, "pexels_photos", lambda *a, **k: [dict(p) for p in PHOTOS])
    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: _hits("20", "22"))
    monkeypatch.setattr(cover.requests, "get", lambda url, timeout=0: Response())


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "_keys", lambda: {"pexels": "px", "gemini": ""})
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    return TestClient(web_app.app)


def _finished(api):
    job_id = api.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    for _ in range(300):
        if not web_app.jobs.busy():
            with web_app.jobs._lock:
                break
        time.sleep(0.02)
    job = web_app.jobs.get(job_id)
    assert job.status == "done", job.log
    return job


def test_the_candidates_route_lists_photos(api):
    job = _finished(api)

    body = api.get(f"/api/jobs/{job.id}/thumbnail/candidates?q=racks").json()

    assert body["query"] == "racks" and len(body["photos"]) == 3


def test_a_photo_is_applied_in_the_request_and_credited(api):
    job = _finished(api)
    before = (job.root / "out" / "a-title.jpg").read_bytes()

    r = api.post(f"/api/jobs/{job.id}/thumbnail", json={"photo": "300", "query": "racks"})

    assert r.status_code == 200 and r.json()["status"] == "done"
    assert (job.root / "out" / "a-title.jpg").read_bytes() != before
    assert "Photographer 300" in (job.root / "out" / "credits.txt").read_text(encoding="utf-8")
    assert not job.retitling and not web_app.jobs.busy(), "no render slot is taken for this"


def test_a_shots_frame_can_be_the_thumbnail(api, monkeypatch):
    job = _finished(api)
    monkeypatch.setattr(cover.ff, "run", lambda args: Path(args[-1]).write_bytes(_jpeg()))

    r = api.post(f"/api/jobs/{job.id}/thumbnail", json={"scene": 1, "shot": 0})

    assert r.status_code == 200
    choice = json.loads((job.root / "build" / "thumbnail.json").read_text(encoding="utf-8"))
    assert choice == {"kind": "frame", "scene": 1, "shot": 0}


@pytest.mark.parametrize("body", [{}, {"photo": "300", "scene": 0, "shot": 0}])
def test_exactly_one_source_is_required(api, body):
    job = _finished(api)
    r = api.post(f"/api/jobs/{job.id}/thumbnail", json=body)
    assert r.status_code == 409 and "either a photograph or a shot" in r.json()["detail"]


def test_a_render_on_youtube_is_sent_to_youtube_studio(api):
    job = _finished(api)
    job.youtube = {"status": "done", "video_id": "Ab3dE_fG9hI"}

    r = api.post(f"/api/jobs/{job.id}/thumbnail", json={"photo": "300"})

    assert r.status_code == 409 and "YouTube Studio" in r.json()["detail"]


def test_a_render_being_changed_is_not_retitled_under_it(api):
    job = _finished(api)
    job.status = "running"                      # a shot change holds it

    r = api.post(f"/api/jobs/{job.id}/thumbnail", json={"photo": "300"})

    assert r.status_code == 409
    job.status = "done"


def test_a_shot_change_waits_for_a_thumbnail_being_made(api):
    job = _finished(api)
    job.retitling = True
    try:
        r = api.post(f"/api/jobs/{job.id}/shots/0/1", json={"clip": "22"})
    finally:
        job.retitling = False
    assert r.status_code == 409 and "already being changed" in r.json()["detail"]


def test_the_routes_are_behind_the_token(api, monkeypatch):
    job = _finished(api)
    monkeypatch.setattr(web_app, "TOKEN", "secret")
    assert api.get(f"/api/jobs/{job.id}/thumbnail/candidates").status_code == 401
    assert api.post(f"/api/jobs/{job.id}/thumbnail", json={"photo": "1"}).status_code == 401


def test_the_page_offers_a_thumbnail_change():
    page = (Path(web_app.HERE) / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="thumb-card"' in page and "Use This Thumbnail" in page
    assert "/thumbnail/candidates" in page
    assert "thumbVersion" in page, "a changed thumbnail keeps its name, so it is versioned"
