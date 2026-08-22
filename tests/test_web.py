"""The HTTP front.

The render itself is covered elsewhere; what matters here is that a caller
cannot start work the box cannot finish, cannot read files outside their own
job, and gets a truthful status while a render is in flight.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient        # noqa: E402

from web import app as web_app                   # noqa: E402
from web.jobs import Busy, Jobs                  # noqa: E402

SCRIPT = "# T\n\n## One\nA short line of narration for the test.\n"


@pytest.fixture(autouse=True)
def stub_pipeline(monkeypatch):
    """Stand in for the render.

    These tests are about the queue, the guards and the downloads. Letting them
    run a real pipeline would make them minutes long and dependent on two
    network services.
    """
    import web.jobs as jobs_mod

    def fake_build(root, **kwargs):
        log = kwargs.get("log") or (lambda *a: None)
        log("script   1 scenes, ~4s estimated")
        log("done     stub")
        out = Path(root) / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "video.mp4").write_bytes(b"stub")
        return out / "video.mp4"

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    return TestClient(web_app.app)


def _settle(jobs, limit: int = 200) -> None:
    """Wait on the slot, not the status: status is set before it is released."""
    for _ in range(limit):
        if not jobs.busy():
            return
        time.sleep(0.02)
    raise AssertionError("the render slot was never released")


# --------------------------------------------------------------------------- #
# surface
# --------------------------------------------------------------------------- #
def test_health_reports_ffmpeg(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert "ffmpeg" in body["ffmpeg"].lower()


def test_options_lists_what_the_form_needs(client):
    body = client.get("/api/options").json()
    assert "16:9" in body["aspects"] and "9:16" in body["aspects"]
    assert "midnight" in body["themes"]
    assert "calm" in body["moods"]


def test_the_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "vidsmith" in r.text


# --------------------------------------------------------------------------- #
# what a caller may ask for
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field,value", [
    ("aspect", "21:9"), ("theme", "neon"), ("provider", "unsplash"),
    ("mood", "furious"),
])
def test_unknown_options_are_refused(client, field, value):
    r = client.post("/api/jobs", json={"script": SCRIPT, field: value})
    assert r.status_code == 400


def test_an_empty_script_is_refused(client):
    assert client.post("/api/jobs", json={"script": "   "}).status_code == 400
    assert client.post("/api/jobs", json={"script": ""}).status_code == 422


def test_a_script_over_the_limit_is_refused(client):
    long = " ".join(["word"] * 4000)
    r = client.post("/api/jobs", json={"script": long})
    assert r.status_code == 400
    assert "minute limit" in r.json()["detail"]


def test_an_unknown_job_is_a_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404
    assert client.get("/api/jobs/deadbeef/files/out.mp4").status_code == 404


# --------------------------------------------------------------------------- #
# the queue
# --------------------------------------------------------------------------- #
def test_only_one_render_runs_at_a_time(tmp_path):
    """Two x264 encodes on one small box starve each other; say no instead."""
    jobs = Jobs(tmp_path)
    started = jobs.submit(SCRIPT, {})
    assert jobs.busy()
    with pytest.raises(Busy):
        jobs.submit(SCRIPT, {})

    _settle(jobs)
    assert started.status in ("done", "failed")


def test_a_second_caller_gets_429(client, monkeypatch):
    monkeypatch.setattr(web_app.jobs, "submit",
                        lambda *a, **k: (_ for _ in ()).throw(Busy("busy")))
    r = client.post("/api/jobs", json={"script": SCRIPT})
    assert r.status_code == 429


def test_a_submitted_job_is_visible_immediately(client):
    r = client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards"})
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] in ("queued", "running", "done", "failed")
    assert 0.0 <= body["progress"] <= 1.0
    _settle(web_app.jobs)
    final = client.get(f"/api/jobs/{job_id}").json()
    assert final["status"] == "done"
    assert [f["name"] for f in final["outputs"]] == ["video.mp4"]


def test_the_job_writes_the_script_and_a_config(tmp_path):
    jobs = Jobs(tmp_path)
    job = jobs.submit(SCRIPT, {"aspect": "9:16", "theme": "ink",
                               "watermark": "@x", "music": False})
    _settle(jobs)
    written = (job.root / "script.md").read_text(encoding="utf-8")
    assert written == SCRIPT.strip()

    import yaml
    cfg = yaml.safe_load((job.root / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["render"]["aspect"] == "9:16"
    assert cfg["theme"]["preset"] == "ink"
    assert cfg["theme"]["watermark"] == "@x"
    assert cfg["audio"]["music"] == ""
    assert cfg["visuals"]["orientation"] == "portrait"


# --------------------------------------------------------------------------- #
# downloads
# --------------------------------------------------------------------------- #
def test_a_download_cannot_escape_the_job_folder(tmp_path):
    jobs = Jobs(tmp_path)
    job = jobs.submit(SCRIPT, {})
    _settle(jobs)
    (job.root / "out").mkdir(exist_ok=True)
    (job.root / "secret.txt").write_text("private", encoding="utf-8")

    for attempt in ("../secret.txt", "../../secret.txt",
                    "..\\secret.txt", "/etc/passwd"):
        assert jobs.file(job.id, attempt) is None, f"{attempt} escaped"


def test_a_real_output_resolves(tmp_path):
    jobs = Jobs(tmp_path)
    job = jobs.submit(SCRIPT, {})
    _settle(jobs)
    out = job.root / "out"
    out.mkdir(exist_ok=True)
    (out / "video.mp4").write_bytes(b"data")
    assert jobs.file(job.id, "video.mp4") == (out / "video.mp4").resolve()


def test_finished_jobs_are_swept(tmp_path, monkeypatch):
    import web.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "KEEP_SECONDS", -1)
    jobs = Jobs(tmp_path)
    old = jobs.submit(SCRIPT, {})
    _settle(jobs)
    old.finished = time.time() - 10

    jobs.submit(SCRIPT, {})
    assert jobs.get(old.id) is None
    assert not old.root.exists()
