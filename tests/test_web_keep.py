"""A finished render outlives the process that made it.

It was kept for an hour and did not survive a restart, so a deploy deleted
whatever had not been downloaded yet, and a render that finished while nobody
was watching was simply gone. One was saved with minutes to spare by copying it
off the box by hand before a restart. Now a finished render records itself,
drops its working files, is taken back by the next process, and is held to an
age and a disk budget instead.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient        # noqa: E402

import web.jobs as jobs_mod                      # noqa: E402
from web import app as web_app                   # noqa: E402
from web.jobs import Jobs                        # noqa: E402

SCRIPT = "# T\n\n## One\nA short line of narration for the test.\n"


@pytest.fixture(autouse=True)
def stub_pipeline(monkeypatch):
    """A render that writes what a real one leaves behind, working files included."""
    def fake_build(root, **kwargs):
        log = kwargs.get("log") or (lambda *a: None)
        root = Path(root)
        (root / "build" / "visuals" / "cache").mkdir(parents=True, exist_ok=True)
        (root / "build" / "visuals" / "scene_000_00.mp4").write_bytes(b"x" * 5000)
        (root / "build" / "visuals" / "cache" / "pexels_1.mp4").write_bytes(b"x" * 50000)
        (root / "build" / "picture.mp4").write_bytes(b"x" * 8000)
        (root / "build" / "scenes.json").write_text("[]", encoding="utf-8")
        out = root / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "a-title.mp4").write_bytes(b"video")
        (out / "credits.txt").write_text("Footage from Pexels\n", encoding="utf-8")
        log("script   1 scenes, ~4s estimated")
        log(f"done     {out / 'a-title.mp4'}  (128.9s, 47.2 MB, 727s to build)")
        return out / "a-title.mp4"

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)


def _settle(jobs, limit: int = 200) -> None:
    for _ in range(limit):
        if not jobs.busy():
            # The slot is released and the sweep run under one lock, and `busy`
            # reads it without one: waiting for the lock is waiting for the sweep.
            with jobs._lock:
                return
        time.sleep(0.02)
    raise AssertionError("the render slot was never released")


def _finished(jobs):
    job = jobs.submit(SCRIPT, {"aspect": "16:9"})
    _settle(jobs)
    assert job.status == "done", job.log
    return job


# --------------------------------------------------------------------------- #
# finishing
# --------------------------------------------------------------------------- #
def test_a_finished_render_drops_its_working_files_and_records_itself(tmp_path):
    jobs = Jobs(tmp_path)
    job = _finished(jobs)

    build = job.root / "build"
    assert not (build / "visuals" / "cache").exists(), "the downloads are most of the disk"
    assert not (build / "picture.mp4").exists(), "the cut is remade by any retake"
    # what changing one shot later needs, and a small share of the whole
    assert (build / "visuals" / "scene_000_00.mp4").exists()
    assert (build / "scenes.json").exists()
    assert (job.root / "out" / "a-title.mp4").exists()
    record = json.loads((job.root / "job.json").read_text(encoding="utf-8"))
    assert record["id"] == job.id and record["status"] == "done"
    assert record["runtime"] == 128.9


def test_a_render_with_no_stock_footage_keeps_no_working_files(tmp_path):
    """Cards have no other candidates, so there is no shot to change later."""
    jobs = Jobs(tmp_path)
    job = jobs.submit(SCRIPT, {"aspect": "16:9", "provider": "cards"})
    _settle(jobs)

    assert job.status == "done", job.log
    assert not (job.root / "build").exists()


def test_a_failed_render_is_not_recorded(tmp_path, monkeypatch):
    def broken(root, **kwargs):
        raise RuntimeError("ffmpeg fell over")

    monkeypatch.setattr(jobs_mod.pipeline, "build", broken)
    jobs = Jobs(tmp_path)
    job = jobs.submit(SCRIPT, {})
    _settle(jobs)

    assert job.status == "failed"
    assert not (job.root / "job.json").exists()


# --------------------------------------------------------------------------- #
# a restart
# --------------------------------------------------------------------------- #
def test_a_restart_keeps_a_finished_render(tmp_path):
    """The fault that cost videos: every deploy deleted every render."""
    first = Jobs(tmp_path)
    job = _finished(first)

    again = Jobs(tmp_path)

    kept = again.get(job.id)
    assert kept is not None and kept.status == "done"
    assert [f["name"] for f in kept.outputs] == ["a-title.mp4", "credits.txt"]
    assert kept.runtime == 128.9 and kept.options["aspect"] == "16:9"
    assert any(line.startswith("done ") for line in kept.log)
    assert again.file(job.id, "a-title.mp4") is not None
    assert again.archive(job.id) is not None, "Download Everything must still work"


def test_a_restart_still_removes_everything_that_is_not_a_finished_render(tmp_path):
    """The orphan sweep's own job: 2.5 GB of dead directories on an 18 GB disk."""
    first = Jobs(tmp_path)
    kept = _finished(first)

    interrupted = tmp_path / "aaaaaaaaaaaa"
    (interrupted / "build").mkdir(parents=True)
    failed = tmp_path / "bbbbbbbbbbbb"
    (failed / "out").mkdir(parents=True)
    (failed / "job.json").write_text(json.dumps({"id": failed.name, "status": "failed"}))
    moved = tmp_path / "cccccccccccc"
    (moved / "out").mkdir(parents=True)
    (moved / "out" / "v.mp4").write_bytes(b"v")
    (moved / "job.json").write_text(json.dumps(
        {"id": "somewhere-else", "status": "done", "finished": time.time()}))
    no_video = tmp_path / "dddddddddddd"
    (no_video / "out").mkdir(parents=True)
    (no_video / "job.json").write_text(json.dumps(
        {"id": no_video.name, "status": "done", "finished": time.time()}))
    garbled = tmp_path / "eeeeeeeeeeee"
    garbled.mkdir()
    (garbled / "job.json").write_text("{not json")

    again = Jobs(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [kept.id]
    assert again.get(kept.id) is not None


def test_a_render_past_its_keep_is_removed_at_a_restart(tmp_path, monkeypatch):
    job = _finished(Jobs(tmp_path))
    record = json.loads((job.root / "job.json").read_text(encoding="utf-8"))
    record["finished"] = time.time() - jobs_mod.KEEP_SECONDS - 60
    (job.root / "job.json").write_text(json.dumps(record), encoding="utf-8")

    again = Jobs(tmp_path)

    assert again.get(job.id) is None and not job.root.exists()


# --------------------------------------------------------------------------- #
# limits
# --------------------------------------------------------------------------- #
def test_a_finished_render_is_kept_for_days_not_an_hour(tmp_path):
    jobs = Jobs(tmp_path)
    job = _finished(jobs)
    job.finished = time.time() - 2 * 60 * 60

    jobs.submit(SCRIPT, {})
    _settle(jobs)

    assert jobs.get(job.id) is not None
    assert jobs_mod.KEEP_SECONDS >= 24 * 60 * 60


def test_a_failed_render_still_goes_after_an_hour(tmp_path, monkeypatch):
    def broken(root, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(jobs_mod.pipeline, "build", broken)
    jobs = Jobs(tmp_path)
    job = jobs.submit(SCRIPT, {})
    _settle(jobs)
    job.finished = time.time() - jobs_mod.FAILED_KEEP_SECONDS - 60

    jobs.submit(SCRIPT, {})
    _settle(jobs)

    assert jobs.get(job.id) is None


def test_over_the_disk_budget_the_oldest_render_goes_first(tmp_path, monkeypatch):
    jobs = Jobs(tmp_path)
    old, middle, new = (_finished(jobs) for _ in range(3))
    for age, job in zip((300, 200, 100), (old, middle, new)):
        job.finished = time.time() - age
    # exactly the two newest: records differ by a few bytes, so "twice one of
    # them" can land just under what the pair actually holds
    monkeypatch.setattr(jobs_mod, "KEEP_BYTES",
                        jobs_mod._size(middle.root) + jobs_mod._size(new.root))

    with jobs._lock:
        jobs._sweep()

    assert jobs.get(old.id) is None and not old.root.exists()
    assert jobs.get(middle.id) is not None and jobs.get(new.id) is not None


def test_the_newest_render_is_kept_even_alone_over_budget(tmp_path, monkeypatch):
    """Otherwise one long video over a small budget is deleted as it finishes."""
    monkeypatch.setattr(jobs_mod, "KEEP_BYTES", 1)
    jobs = Jobs(tmp_path)
    first = _finished(jobs)
    second = _finished(jobs)

    assert jobs.get(first.id) is None
    assert jobs.get(second.id) is not None and second.root.exists()


def test_a_waiting_or_running_render_is_never_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_mod, "KEEP_BYTES", 0)
    monkeypatch.setattr(jobs_mod, "KEEP_SECONDS", -1)
    jobs = Jobs(tmp_path)
    job = jobs_mod.Job(id="inflight0000", status="running", root=tmp_path / "inflight0000")
    job.root.mkdir()
    jobs._jobs[job.id] = job

    with jobs._lock:
        jobs._sweep()

    assert jobs.get(job.id) is job and job.root.exists()


# --------------------------------------------------------------------------- #
# the API
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    return TestClient(web_app.app)


def test_the_list_carries_finished_renders_newest_first(client):
    first = client.post("/api/jobs", json={"script": SCRIPT, "aspect": "9:16"}).json()
    _settle(web_app.jobs)
    second = client.post("/api/jobs", json={"script": SCRIPT}).json()
    _settle(web_app.jobs)
    web_app.jobs.get(first["id"]).finished -= 60

    body = client.get("/api/jobs").json()

    assert [r["id"] for r in body["renders"]] == [second["id"], first["id"]]
    newest = body["renders"][0]
    assert newest["runtime"] == 128.9 and newest["files"] == 2 and newest["size"] > 0
    assert newest["expires"] > newest["finished"]
    assert body["renders"][1]["aspect"] == "9:16"
    assert body["keep_seconds"] == jobs_mod.KEEP_SECONDS


def test_a_job_says_when_it_will_be_removed(client):
    job = client.post("/api/jobs", json={"script": SCRIPT}).json()
    _settle(web_app.jobs)
    body = client.get(f"/api/jobs/{job['id']}").json()
    assert body["expires"] and body["aspect"] == "16:9"


def test_the_list_is_behind_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "sekrit")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    api = TestClient(web_app.app)
    assert api.get("/api/jobs").status_code == 401
    assert api.get("/api/jobs", headers={"X-Vidsmith-Token": "sekrit"}).status_code == 200


def test_the_page_lists_past_renders_and_reads_the_servers_expiry(client):
    page = client.get("/").text
    assert 'id="past"' in page and 'fetch("/api/jobs"' in page
    assert "keepSeconds" not in page, "the page kept its own copy of the keep again"
