"""Adding a Shorts version from the page."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient        # noqa: E402

import web.jobs as jobs_mod                      # noqa: E402
from test_retake import finished_build           # noqa: E402
from web import app as web_app                   # noqa: E402
from web.jobs import Jobs                        # noqa: E402

SCRIPT = "# T\n\n## One\nA short line of narration for the test.\n"


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    state = {"cuts": []}

    def fake_build(root, log=print, overrides=None, cut=False, **kwargs):
        root = Path(root)
        if cut:
            state["cuts"].append(overrides)
            (root / "out" / "a-title-9x16.mp4").write_bytes(b"the short")
            (root / "out" / "a-title-9x16.jpg").write_bytes(b"jpg")
        else:
            finished_build(root)
            (root / "out" / "a-title.jpg").write_bytes(b"jpg")
        log("done     a-title.mp4  (12.0s, 1.0 MB, 3s to build)")

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)
    return state


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    return TestClient(web_app.app)


def _settle():
    for _ in range(300):
        if not web_app.jobs.busy():
            with web_app.jobs._lock:
                return
        time.sleep(0.02)
    raise AssertionError("the render slot was never released")


def _finished(api):
    job_id = api.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    _settle()
    return web_app.jobs.get(job_id)


def test_a_shorts_version_is_added_to_the_same_render(api, stubs):
    job = _finished(api)

    r = api.post(f"/api/jobs/{job.id}/cuts", json={"aspect": "9:16"})
    assert r.status_code == 202 and r.json()["id"] == job.id
    _settle()

    assert stubs["cuts"] == [{"aspect": "9:16"}]
    assert job.status == "done" and job.swap == {"kind": "cut", "status": "done", "aspect": "9:16"}
    body = api.get(f"/api/jobs/{job.id}").json()
    assert [c["aspect"] for c in body["cuts"]] == ["16:9", "9:16"], \
        "a-title-9x16.mp4 sorts before a-title.mp4, so the main cut must be named, not sorted"
    assert body["cuts"][0] == {"aspect": "16:9", "video": "a-title.mp4", "thumbnail": "a-title.jpg"}


def test_a_second_shorts_version_is_refused(api):
    job = _finished(api)
    (job.root / "out" / "a-title-9x16.mp4").write_bytes(b"short")

    r = api.post(f"/api/jobs/{job.id}/cuts", json={"aspect": "9:16"})

    assert r.status_code == 409 and "already has a 9:16 cut" in r.json()["detail"]


def test_a_video_longer_than_a_short_is_refused(api):
    job = _finished(api)
    job.runtime = web_app.SHORTS_SECONDS + 1

    r = api.post(f"/api/jobs/{job.id}/cuts", json={"aspect": "9:16"})

    assert r.status_code == 409 and "at most" in r.json()["detail"]
    assert not web_app.jobs.busy()


def test_the_page_names_the_main_cut_rather_than_sorting_files(api):
    page = (Path(web_app.HERE) / "static" / "index.html").read_text(encoding="utf-8")
    assert "Make a Shorts Version" in page and "/cuts`" in page
    assert 'job.outputs.find((f) => f.kind === "mp4")' not in page
    assert api.get("/api/options").json()["shorts_seconds"] == web_app.SHORTS_SECONDS
