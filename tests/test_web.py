"""The HTTP front.

The render itself is covered elsewhere; what matters here is that a caller
cannot start work the box cannot finish, cannot read files outside their own
job, and gets a truthful status while a render is in flight.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

# Skipping is right locally: the CLI does not need fastapi, and someone working
# on the render should not have to install a web stack. On CI it is wrong. The
# web dependencies are installed there on purpose, so a skip would drop 39 tests
# and still report the run green, which is how a suite stops being evidence.
if os.environ.get("CI"):
    import fastapi                              # noqa: F401
else:
    fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient        # noqa: E402

import web.jobs as jobs_mod                      # noqa: E402
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
    # the developer's own .env may carry a token; these tests decide their own
    monkeypatch.setattr(web_app, "TOKEN", "")
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


def test_options_carries_the_stage_order_for_the_stepper(client):
    """The page draws a stepper from this, so it must not keep its own copy:
    a stage added to the worker has to appear here without touching the page."""
    import web.jobs as jobs_mod

    stages = client.get("/api/options").json()["stages"]
    assert [s["key"] for s in stages] == list(jobs_mod.STAGE_LABELS)
    assert stages[0]["label"] == "reading the script"
    assert stages[-1]["label"] == "writing the description"
    assert all(s["label"] for s in stages), "a stage with no label cannot be drawn"


def test_options_carries_the_parser_vocabulary(client):
    """The page counts scenes as you type, and must not hold its own copy of
    the directive set: adding one to the parser has to reach the page."""
    from vidsmith import script_parser

    rules = client.get("/api/options").json()["script"]
    assert rules["wps"] == script_parser.WPS
    assert rules["directives"] == list(script_parser.DIRECTIVE_KINDS)
    assert rules["notes"] == list(script_parser.NOTE_PREFIXES)
    assert "diagram" in rules["directives"] and "hold" in rules["directives"]


def test_the_page_does_not_hardcode_the_directive_set(client):
    """The literal fallback in the page is allowed, a second source is not:
    the page must read the served list, or the two can silently disagree."""
    page = client.get("/").text
    assert "adoptScriptRules(o.script)" in page, "the page ignores the served rules"


def test_options_says_which_footage_sources_this_instance_can_reach(client):
    """A provider without its key does not error, it falls back to cards. The
    page has to know that before offering it, or a render finishes looking
    nothing like what was asked for."""
    providers = client.get("/api/options").json()["providers"]
    assert [p["name"] for p in providers] == list(web_app.PROVIDERS)
    cards = next(p for p in providers if p["name"] == "cards")
    assert cards["ready"] is True, "cards needs no key and must always be offered"


def test_a_provider_is_only_ready_when_its_key_resolves(client, monkeypatch):
    monkeypatch.setattr(web_app, "_keys", lambda: {"pexels": "k", "pixabay": ""})
    ready = {p["name"]: p["ready"] for p in client.get("/api/options").json()["providers"]}
    assert ready == {"pexels": True, "pixabay": False, "cards": True}


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


def test_a_failed_setup_gives_the_render_slot_back(tmp_path):
    """The slot is claimed before the job directory is written.

    Nothing between claiming it and starting the worker thread used to hand it
    back, so an unwritable jobs directory or a full disk wedged the instance:
    every later caller got 429 for a render that was never running, and only a
    restart cleared it.
    """
    jobs = Jobs(tmp_path)
    # scoped, not monkeypatch.undo(): undo would also tear down the autouse
    # pipeline stub and send the recovery render at the real encoder
    with mock.patch.object(Jobs, "_write_config", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            jobs.submit(SCRIPT, {})

    assert not jobs.busy()
    recovered = jobs.submit(SCRIPT, {})
    _settle(jobs)
    assert recovered.status in ("done", "failed")


def test_a_render_that_never_starts_is_recorded_as_failed(tmp_path):
    """A job that could not be set up is finished, not left queued forever."""
    jobs = Jobs(tmp_path)
    with mock.patch.object(Jobs, "_write_config", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            jobs.submit(SCRIPT, {})

    job = next(iter(jobs._jobs.values()))
    assert job.status == "failed"
    assert "disk full" in job.error
    assert job.finished > 0


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


# --------------------------------------------------------------------------- #
# the token gate
# --------------------------------------------------------------------------- #
def test_no_token_configured_means_no_friction(client):
    """Local use must not need a secret."""
    assert client.get("/api/options").json()["auth"] is False
    assert client.post("/api/jobs", json={"script": SCRIPT}).status_code == 202


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "s3cret")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    return TestClient(web_app.app)


def test_a_configured_token_is_advertised(guarded):
    assert guarded.get("/api/options").json()["auth"] is True


def test_starting_a_render_without_the_token_is_refused(guarded):
    assert guarded.post("/api/jobs", json={"script": SCRIPT}).status_code == 401


def test_a_wrong_token_is_refused(guarded):
    r = guarded.post("/api/jobs", json={"script": SCRIPT},
                     headers={"X-Vidsmith-Token": "guess"})
    assert r.status_code == 401


def test_the_right_token_gets_through(guarded):
    r = guarded.post("/api/jobs", json={"script": SCRIPT},
                     headers={"X-Vidsmith-Token": "s3cret"})
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert guarded.get(f"/api/jobs/{job_id}",
                       headers={"X-Vidsmith-Token": "s3cret"}).status_code == 200


def test_media_can_authenticate_by_query(guarded):
    """A <video> element cannot set a header."""
    r = guarded.post("/api/jobs", json={"script": SCRIPT},
                     headers={"X-Vidsmith-Token": "s3cret"})
    job_id = r.json()["id"]
    _settle(web_app.jobs)
    assert guarded.get(f"/api/jobs/{job_id}/files/video.mp4",
                       params={"t": "s3cret"}).status_code == 200
    assert guarded.get(f"/api/jobs/{job_id}/files/video.mp4",
                       params={"t": "wrong"}).status_code == 401


def test_health_stays_open_so_a_deploy_can_be_checked(guarded):
    assert guarded.get("/healthz").status_code == 200


# --------------------------------------------------------------------------- #
# drafting and progress reporting
# --------------------------------------------------------------------------- #
def test_drafting_needs_a_key(client, monkeypatch):
    monkeypatch.setattr(web_app, "find_keys", lambda *a, **k: {"gemini": ""})
    r = client.post("/api/draft", json={"topic": "why indexes slow writes"})
    assert r.status_code == 503


def test_drafting_returns_a_script(client, monkeypatch):
    monkeypatch.setattr(web_app, "find_keys", lambda *a, **k: {"gemini": "k"})
    monkeypatch.setattr(web_app.llm, "draft_script",
                        lambda topic, minutes, key, **kw: f"# {topic}\n")
    r = client.post("/api/draft", json={"topic": "why indexes slow writes"})
    assert r.status_code == 200
    assert r.json()["script"].startswith("# why indexes")


def test_drafting_is_clamped_to_the_instance_limit(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(web_app, "find_keys", lambda *a, **k: {"gemini": "k"})
    monkeypatch.setattr(web_app.llm, "draft_script",
                        lambda topic, minutes, key, **kw: seen.setdefault("m", minutes) and "" or "# x\n")
    client.post("/api/draft", json={"topic": "a topic", "minutes": 99})
    assert seen["m"] <= web_app.MAX_MINUTES


def test_a_short_topic_is_refused(client):
    assert client.post("/api/draft", json={"topic": "x"}).status_code == 422


def test_the_stage_is_reported_in_words_not_pipeline_jargon(client):
    """"visuals" means nothing to someone watching a progress bar."""
    from web.jobs import Job

    job = Job(id="x", stage="visuals")
    assert job.public()["stage"] == "finding footage"
    assert Job(id="x", stage="render").public()["stage"] == "encoding"


def test_status_carries_elapsed_time(client):
    r = client.post("/api/jobs", json={"script": SCRIPT})
    job_id = r.json()["id"]
    _settle(web_app.jobs)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["elapsed"] >= 0
    assert body["stage"]


def test_the_description_is_served_when_present(client, tmp_path):
    r = client.post("/api/jobs", json={"script": SCRIPT})
    job_id = r.json()["id"]
    _settle(web_app.jobs)
    assert client.get(f"/api/jobs/{job_id}/description").json()["description"] == ""

    out = web_app.jobs.get(job_id).root / "out"
    (out / "description.txt").write_text("paste me", encoding="utf-8")
    assert client.get(f"/api/jobs/{job_id}/description").json()["description"] == "paste me"


def test_the_description_of_an_unknown_job_is_a_404(client):
    assert client.get("/api/jobs/nope/description").status_code == 404


# --------------------------------------------------------------------------- #
# stopping a render, and seeing that the box is taken
# --------------------------------------------------------------------------- #
def test_the_idle_page_is_told_the_box_is_free(client):
    assert client.get("/api/busy").json() == {"busy": False}


def test_the_idle_page_is_told_what_is_running(tmp_path, monkeypatch):
    """A second visitor should see the box is taken before writing a script."""
    monkeypatch.setattr(web_app, "TOKEN", "")
    jobs = Jobs(tmp_path / "jobs")
    monkeypatch.setattr(web_app, "jobs", jobs)
    gate = threading.Event()

    def slow_build(root, **kwargs):
        kwargs["log"]("voice    recording")
        gate.wait(5)
        out = Path(root) / "out"
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr(jobs_mod.pipeline, "build", slow_build)
    client = TestClient(web_app.app)
    client.post("/api/jobs", json={"script": SCRIPT})
    for _ in range(200):                       # the worker thread needs a moment
        if client.get("/api/busy").json()["busy"]:
            break
        time.sleep(0.02)

    state = client.get("/api/busy").json()
    assert state["busy"] is True
    assert state["stage"] == "recording narration"
    assert state["elapsed"] >= 0
    gate.set()
    _settle(jobs)


def test_cancelling_an_unknown_job_is_a_404(client):
    assert client.post("/api/jobs/deadbeef/cancel").status_code == 404


def test_a_finished_render_cannot_be_cancelled(client):
    job_id = client.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    _settle(web_app.jobs)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_a_cancelled_render_stops_and_frees_the_queue(tmp_path, monkeypatch):
    """The flag is read in the log callback, so the run ends at a stage edge."""
    monkeypatch.setattr(web_app, "TOKEN", "")
    jobs = Jobs(tmp_path / "jobs")
    monkeypatch.setattr(web_app, "jobs", jobs)
    seen = threading.Event()
    stages = []

    def stoppable_build(root, **kwargs):
        log = kwargs["log"]
        log("voice    recording")
        seen.set()
        for stage in ("visuals", "captions", "render"):
            time.sleep(0.05)
            log(f"{stage}   working")         # one of these raises Cancelled
            stages.append(stage)
        return Path(root)

    monkeypatch.setattr(jobs_mod.pipeline, "build", stoppable_build)
    client = TestClient(web_app.app)
    job_id = client.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    assert seen.wait(5)

    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "stopping"
    _settle(jobs)

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "cancelled"
    assert stages != ["visuals", "captions", "render"]      # it really stopped
    assert not jobs.busy()
    assert any("cancelled during" in line for line in body["log"])


def test_a_cancellation_survives_the_pipeline_catching_exceptions(tmp_path, monkeypatch):
    """`Cancelled` derives from BaseException for exactly this reason."""
    monkeypatch.setattr(web_app, "TOKEN", "")
    jobs = Jobs(tmp_path / "jobs")
    monkeypatch.setattr(web_app, "jobs", jobs)
    started = threading.Event()

    def swallowing_build(root, **kwargs):
        log = kwargs["log"]
        log("voice    recording")
        started.set()
        for _ in range(100):
            try:
                time.sleep(0.05)
                log("visuals  working")
            except Exception:                 # the shape of pipeline's handlers
                pass
        return Path(root)

    monkeypatch.setattr(jobs_mod.pipeline, "build", swallowing_build)
    client = TestClient(web_app.app)
    job_id = client.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    assert started.wait(5)
    client.post(f"/api/jobs/{job_id}/cancel")
    _settle(jobs)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"


def test_a_finished_job_is_not_reported_as_stopping(client):
    job_id = client.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    _settle(web_app.jobs)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["cancelling"] is False       # a finished job is not "stopping"

