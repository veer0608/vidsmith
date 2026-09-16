"""Changing one shot of a finished render from the page.

The swap is a render as far as the box is concerned, so it waits in the same
line and holds the same slot. What is different is failure: the render it
changes had already finished, so a swap that fails or is stopped must hand back
the video it started from, still finished, and never the one-hour sweep a
failed render gets.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient        # noqa: E402

import web.jobs as jobs_mod                      # noqa: E402
from test_retake import _hits, finished_build    # noqa: E402
from vidsmith import retake, visuals             # noqa: E402
from web import app as web_app                   # noqa: E402
from web.jobs import Jobs                        # noqa: E402

SCRIPT = "# T\n\n## One\nA short line of narration for the test.\n"
KEYS = {"pexels": "px", "pixabay": "", "gemini": ""}


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    """A first build that leaves a real-shaped stock render, and a cheap retake."""
    state = {"fail": None, "gate": None}

    def fake_build(root, log=print, retake=False, **kwargs):
        root = Path(root)
        if state["gate"] is not None:
            state["gate"].wait(5)
        if not retake:
            finished_build(root)
        elif state["fail"]:
            (root / "out" / "a-title.mp4").write_bytes(b"half a vid")
            raise state["fail"]
        else:
            (root / "out" / "a-title.mp4").write_bytes(b"the video after")
        log(f"done     {root / 'out' / 'a-title.mp4'}  (12.0s, 1.0 MB, 3s to build)")
        return root / "out" / "a-title.mp4"

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)
    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: _hits("10", "20", "22"))
    monkeypatch.setattr(visuals, "_download",
                        lambda url, out, headers=None: out.parent.mkdir(parents=True, exist_ok=True)
                        or out.write_bytes(b"dl") or out)
    monkeypatch.setattr(retake.ff, "duration", lambda path: 15.0)
    monkeypatch.setattr(visuals, "normalise_video",
                        lambda src, out, *a, **k: out.write_bytes(b"the new clip") or out)
    return state


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "_keys", lambda: dict(KEYS))
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    return TestClient(web_app.app)


def _settle(jobs):
    for _ in range(300):
        if not jobs.busy():
            with jobs._lock:
                return
        time.sleep(0.02)
    raise AssertionError("the render slot was never released")


def _finished(api):
    job_id = api.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    _settle(web_app.jobs)
    job = web_app.jobs.get(job_id)
    assert job.status == "done", job.log
    return job


def _swap(api, job, clip="22", shot=(0, 1)):
    return api.post(f"/api/jobs/{job.id}/shots/{shot[0]}/{shot[1]}", json={"clip": clip})


# --------------------------------------------------------------------------- #
# looking
# --------------------------------------------------------------------------- #
def test_a_finished_render_lists_its_shots(api):
    job = _finished(api)

    body = api.get(f"/api/jobs/{job.id}/shots").json()

    assert body["available"] is True and len(body["shots"]) == 3
    assert body["shots"][1]["clip"] == "11"


def test_a_render_that_cannot_change_a_shot_says_why_rather_than_failing(api):
    job = _finished(api)
    (job.root / "build" / "scenes.json").unlink()

    r = api.get(f"/api/jobs/{job.id}/shots")

    assert r.status_code == 200
    assert r.json()["available"] is False and "working files" in r.json()["reason"]


def test_the_candidates_route_offers_clips_not_already_in_the_video(api):
    job = _finished(api)

    body = api.get(f"/api/jobs/{job.id}/shots/0/1/candidates").json()

    assert [c["id"] for c in body["candidates"]] == ["20", "22"]
    assert api.get("/api/jobs/nope/shots/0/1/candidates").status_code == 404


def test_the_shot_routes_are_behind_the_token(api, monkeypatch):
    job = _finished(api)
    monkeypatch.setattr(web_app, "TOKEN", "secret")

    for path in (f"/api/jobs/{job.id}/shots", f"/api/jobs/{job.id}/shots/0/1/candidates",
                 f"/api/jobs/{job.id}/shots/0/1/frame"):
        assert api.get(path).status_code == 401, path
    assert _swap(api, job).status_code == 401


# --------------------------------------------------------------------------- #
# changing
# --------------------------------------------------------------------------- #
def test_a_swap_rebuilds_the_same_job_and_finishes_it_again(api):
    job = _finished(api)
    before = job.finished
    time.sleep(0.02)

    r = _swap(api, job)
    assert r.status_code == 202 and r.json()["id"] == job.id
    _settle(web_app.jobs)

    assert job.status == "done", job.log
    assert (job.root / "out" / "a-title.mp4").read_bytes() == b"the video after"
    assert job.swap == {"status": "done", "scene": 0, "shot": 1}
    assert job.finished > before, "the keep runs from the change, not the first build"
    ledger = json.loads((job.root / "build" / "visuals" / "credits.json").read_text())
    assert ledger["0:1"]["id"] == "22"
    record = json.loads((job.root / "job.json").read_text(encoding="utf-8"))
    assert record["finished"] == job.finished


def test_a_clip_that_cannot_be_used_is_refused_before_it_waits(api):
    job = _finished(api)

    r = _swap(api, job, clip="10")

    assert r.status_code == 409 and "already in the video" in r.json()["detail"]
    assert job.status == "done" and not web_app.jobs.busy()


def test_a_render_already_on_youtube_is_not_changed(api):
    job = _finished(api)
    job.youtube = {"status": "done", "video_id": "Ab3dE_fG9hI"}

    r = _swap(api, job)

    assert r.status_code == 409 and "second upload" in r.json()["detail"]


def test_a_failed_swap_hands_back_the_finished_video(api, stubs):
    job = _finished(api)
    before = job.finished
    stubs["fail"] = RuntimeError("ffmpeg fell over")

    assert _swap(api, job).status_code == 202
    _settle(web_app.jobs)

    assert job.status == "done", "a failed change would hand the video to the hour sweep"
    assert job.finished == before
    assert job.swap["status"] == "failed" and "ffmpeg fell over" in job.swap["error"]
    assert (job.root / "out" / "a-title.mp4").read_bytes() == b"the video before"
    assert any("the video is as it was" in line for line in job.log)


def test_a_swap_waiting_in_line_can_be_cancelled_without_losing_the_render(api, stubs):
    job = _finished(api)
    before = job.finished
    stubs["gate"] = threading.Event()
    api.post("/api/jobs", json={"script": SCRIPT})          # holds the slot
    try:
        r = _swap(api, job)
        assert r.status_code == 202 and r.json()["status"] == "queued"
        assert api.post(f"/api/jobs/{job.id}/cancel").json()["status"] == "cancelled"
    finally:
        stubs["gate"].set()
    _settle(web_app.jobs)

    assert job.status == "done" and job.finished == before
    assert job.swap == {}
    assert (job.root / "out" / "a-title.mp4").read_bytes() == b"the video before"


def test_a_stopped_swap_does_not_stop_the_next_one(api, stubs, monkeypatch):
    """The stop flag is read in the log callback, and outlived the swap it stopped."""
    job = _finished(api)
    job.cancel_requested = True             # left by a stop that has already landed

    assert _swap(api, job).status_code == 202
    _settle(web_app.jobs)

    assert job.swap == {"status": "done", "scene": 0, "shot": 1}, job.log
    assert job.cancel_requested is False


def test_a_full_line_refuses_a_swap(api, stubs, monkeypatch):
    job = _finished(api)
    monkeypatch.setattr(jobs_mod, "MAX_QUEUE", 0)
    stubs["gate"] = threading.Event()
    api.post("/api/jobs", json={"script": SCRIPT})
    try:
        r = _swap(api, job)
    finally:
        stubs["gate"].set()
    _settle(web_app.jobs)

    assert r.status_code == 429
    assert job.status == "done"


def test_a_restart_during_a_swap_brings_back_the_video_from_before(tmp_path):
    jobs = Jobs(tmp_path)
    job = jobs.submit(SCRIPT, {"aspect": "16:9"})
    _settle(jobs)
    build = retake.Build(job.root)
    retake._stash(build, build.shot_path(0, 1))
    (job.root / "out" / "a-title.mp4").write_bytes(b"truncated by the restart")

    again = Jobs(tmp_path).get(job.id)

    assert again is not None and again.status == "done"
    assert (again.root / "out" / "a-title.mp4").read_bytes() == b"the video before"
    assert again.swap["status"] == "failed"


def test_the_page_offers_the_shots_and_follows_a_swap():
    page = (Path(web_app.HERE) / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="shots-card"' in page and "Use This Clip" in page
    assert "/shots/${s.scene}/${s.shot}/candidates" in page
    assert "follow(body.id)" in page, "a swap is followed like the render it is"
    # a changed shot and a changed video keep their names, so both are versioned
    assert page.count("v: job.expires") >= 1 and "{v: version}" in page
    assert 'referrerpolicy="no-referrer"' in page
