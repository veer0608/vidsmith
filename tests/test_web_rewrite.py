"""Rewriting one scene of a finished render from the page."""
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
    state = {"fail": None, "edits": []}

    def fake_build(root, log=print, edit=False, **kwargs):
        root = Path(root)
        if not edit:
            finished_build(root)
        else:
            state["edits"].append((root / "script.md").read_text(encoding="utf-8"))
            if state["fail"]:
                (root / "out" / "a-title.mp4").write_bytes(b"half")
                raise state["fail"]
            (root / "out" / "a-title.mp4").write_bytes(b"the video after")
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
    job = web_app.jobs.get(job_id)
    assert job.status == "done", job.log
    return job


def test_the_scenes_route_lists_the_scripts_scenes(api):
    job = _finished(api)

    body = api.get(f"/api/jobs/{job.id}/scenes").json()

    assert [s["text"] for s in body["scenes"]] == [
        "Racks hold the servers. Each one hums all night long.", "Then someone types the fix."]
    assert body["editable"] is True and body["word_cap"] == web_app.WORD_CAP


def test_an_edit_rebuilds_the_same_job_with_the_new_words(api, stubs):
    job = _finished(api)

    r = api.post(f"/api/jobs/{job.id}/scenes/1", json={"text": "Then an engineer ships the fix."})
    assert r.status_code == 202 and r.json()["id"] == job.id
    _settle()

    assert job.status == "done", job.log
    assert "Then an engineer ships the fix." in stubs["edits"][0]
    assert job.swap == {"kind": "scene", "status": "done", "scene": 1}
    assert (job.root / "out" / "a-title.mp4").read_bytes() == b"the video after"


def test_a_failed_edit_hands_back_the_script_and_the_video(api, stubs):
    job = _finished(api)
    script = (job.root / "script.md").read_text(encoding="utf-8")
    stubs["fail"] = RuntimeError("the voice service is down")

    assert api.post(f"/api/jobs/{job.id}/scenes/1", json={"text": "New words."}).status_code == 202
    _settle()

    assert job.status == "done"
    assert job.swap["kind"] == "scene" and "voice service" in job.swap["error"]
    assert (job.root / "script.md").read_text(encoding="utf-8") == script
    assert (job.root / "out" / "a-title.mp4").read_bytes() == b"the video before"


def test_an_edit_that_changes_the_structure_is_refused_before_it_waits(api):
    job = _finished(api)

    r = api.post(f"/api/jobs/{job.id}/scenes/1", json={"text": "[visual: a new shot]"})

    assert r.status_code == 409
    assert job.status == "done" and not web_app.jobs.busy()


def test_an_edit_over_the_word_limit_is_refused(api, monkeypatch):
    job = _finished(api)
    monkeypatch.setattr(web_app, "WORD_CAP", 5)

    r = api.post(f"/api/jobs/{job.id}/scenes/1", json={"text": "far too many words for this"})

    assert r.status_code == 409 and "limit" in r.json()["detail"]


def test_the_scene_routes_are_behind_the_token(api, monkeypatch):
    job = _finished(api)
    monkeypatch.setattr(web_app, "TOKEN", "secret")
    assert api.get(f"/api/jobs/{job.id}/scenes").status_code == 401
    assert api.post(f"/api/jobs/{job.id}/scenes/0", json={"text": "x"}).status_code == 401
