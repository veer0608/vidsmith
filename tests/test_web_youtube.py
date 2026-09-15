"""Uploading a finished render from the page.

The render made on the live instance had to be downloaded and its description
typed into YouTube Studio by hand, and the upload form there asked questions
the build already had answers to. `vidsmith upload` fills the whole form, but
only from a machine with a browser for its loopback consent screen. These cover
the web half: a consent that comes back to a route, guarded by a single-use
state, and an upload that refuses before anything leaves the box.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient        # noqa: E402

import web.jobs as jobs_mod                      # noqa: E402
import web.youtube as yt_mod                     # noqa: E402
from vidsmith import upload                      # noqa: E402
from web import app as web_app                   # noqa: E402
from web.jobs import Jobs                        # noqa: E402
from web.youtube import YouTube                  # noqa: E402

SCRIPT = "# T\n\n## One\nA short line of narration for the test.\n"
KEYS = {"yt_client": "client-123", "yt_secret": "secret-456"}


@pytest.fixture(autouse=True)
def stub_pipeline(monkeypatch):
    def fake_build(root, **kwargs):
        out = Path(root) / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "a-title.mp4").write_bytes(b"video")
        (out / "a-title.jpg").write_bytes(b"jpg")
        (out / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n\n")
        (out / "credits.txt").write_text("Footage from Pexels\n")
        (out / "description.txt").write_text("A description.\n\nFootage from Pexels\n")
        (out / "youtube.json").write_text(json.dumps({"title": "A Title", "tags": ["a"]}))
        (kwargs.get("log") or print)("done     a-title.mp4  (12.0s, 1.0 MB, 3s to build)")

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)
    monkeypatch.setattr(yt_mod, "check", lambda out: [])


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "PUBLIC_URL", "")
    monkeypatch.setattr(web_app, "_keys", lambda: dict(KEYS))
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    monkeypatch.setattr(web_app, "youtube", YouTube(tmp_path, lambda: web_app._keys()))
    return TestClient(web_app.app)


def _settle(jobs):
    for _ in range(300):
        if not jobs.busy():
            return
        time.sleep(0.02)
    raise AssertionError("the render slot was never released")


def _wait_upload(job):
    for _ in range(300):
        if job.youtube.get("status") != "uploading":
            return
        time.sleep(0.02)
    raise AssertionError("the upload never finished")


def _finished(api):
    job_id = api.post("/api/jobs", json={"script": SCRIPT}).json()["id"]
    _settle(web_app.jobs)
    return web_app.jobs.get(job_id)


def _connect(tmp_path):
    upload.save_grant(tmp_path, {"access_token": "a", "refresh_token": "r"})


# --------------------------------------------------------------------------- #
# status and connecting
# --------------------------------------------------------------------------- #
def test_an_instance_without_a_client_says_so(api, monkeypatch):
    monkeypatch.setattr(web_app, "_keys", lambda: {})
    assert api.get("/api/youtube").json()["configured"] is False
    assert api.post("/api/youtube/connect").status_code == 400


def test_the_redirect_uri_is_the_public_address_behind_the_proxy(api):
    """It has to match the OAuth client exactly, and uvicorn sits behind Caddy."""
    body = api.get("/api/youtube", headers={"x-forwarded-proto": "https",
                                            "host": "vidsmith.duckdns.org"}).json()
    assert body["redirect_uri"] == "https://vidsmith.duckdns.org/api/youtube/callback"
    assert body == {**body, "configured": True, "connected": False}


def test_a_configured_public_url_wins(api, monkeypatch):
    monkeypatch.setattr(web_app, "PUBLIC_URL", "https://example.test")
    assert api.get("/api/youtube").json()["redirect_uri"] == \
        "https://example.test/api/youtube/callback"


def test_connect_hands_back_googles_consent_screen(api):
    url = api.post("/api/youtube/connect").json()["url"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith(upload.AUTH_URL)
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"][0].endswith("/api/youtube/callback")
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert len(query["state"][0]) > 20


def _state(api):
    url = api.post("/api/youtube/connect").json()["url"]
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]


def test_the_callback_saves_the_login_for_a_state_it_issued(api, tmp_path, monkeypatch):
    seen = {}

    def exchange(code, client_id, secret, redirect_uri):
        seen.update(code=code, redirect_uri=redirect_uri)
        return {"access_token": "a", "refresh_token": "r"}

    monkeypatch.setattr(upload, "exchange_code", exchange)
    state = _state(api)

    page = api.get("/api/youtube/callback", params={"state": state, "code": "c0de"})

    assert page.status_code == 200 and "Connected" in page.text
    assert seen["code"] == "c0de" and seen["redirect_uri"].endswith("/api/youtube/callback")
    assert upload.connected(tmp_path)
    assert api.get("/api/youtube").json()["connected"] is True


def test_a_callback_with_a_state_nobody_issued_is_refused(api, tmp_path, monkeypatch):
    """The route is outside the token gate; the state is the only thing guarding it."""
    monkeypatch.setattr(upload, "exchange_code",
                        lambda *a: pytest.fail("exchanged a code for an unknown state"))
    page = api.get("/api/youtube/callback", params={"state": "made-up", "code": "c"})
    assert page.status_code == 400 and not upload.connected(tmp_path)


def test_a_state_is_good_for_one_redirect(api, monkeypatch):
    monkeypatch.setattr(upload, "exchange_code",
                        lambda *a: {"access_token": "a", "refresh_token": "r"})
    state = _state(api)
    assert api.get("/api/youtube/callback", params={"state": state, "code": "c"}).status_code == 200
    assert api.get("/api/youtube/callback", params={"state": state, "code": "c"}).status_code == 400


def test_an_expired_state_is_refused(api, monkeypatch):
    monkeypatch.setattr(yt_mod, "STATE_SECONDS", -1)
    monkeypatch.setattr(upload, "exchange_code",
                        lambda *a: pytest.fail("exchanged a code for an expired state"))
    state = _state(api)
    assert api.get("/api/youtube/callback", params={"state": state, "code": "c"}).status_code == 400


def test_consent_refused_at_google_is_reported(api):
    state = _state(api)
    page = api.get("/api/youtube/callback", params={"state": state, "error": "access_denied"})
    assert page.status_code == 400 and "access_denied" in page.text


def test_the_callback_escapes_what_it_repeats(api):
    state = _state(api)
    page = api.get("/api/youtube/callback", params={"state": state, "error": "<script>x</script>"})
    assert "<script>x" not in page.text


def test_every_youtube_route_but_the_callback_needs_the_token(api, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "sekrit")
    assert api.get("/api/youtube").status_code == 401
    assert api.post("/api/youtube/connect").status_code == 401
    assert api.post("/api/jobs/abc/youtube", json={}).status_code == 401
    assert api.get("/api/youtube/callback", params={"state": "x"}).status_code == 400


# --------------------------------------------------------------------------- #
# uploading
# --------------------------------------------------------------------------- #
def _publish(monkeypatch, calls, fail_after_video=False):
    monkeypatch.setattr(upload, "access_token", lambda *a, **k: "tok")

    def fake(cut, meta, description, token, thumbnail=None, captions=None,
             category="28", privacy="private", log=print):
        calls.append({"cut": cut.name, "title": meta["title"], "privacy": privacy,
                      "description": description, "thumbnail": thumbnail,
                      "captions": captions})
        log("         50% of 1.0 MB")
        log(f"         {upload.WATCH}Ab3dE_fG9hI")
        if fail_after_video:
            raise upload.UploadFailed("the video is up but the caption track was refused")
        return "Ab3dE_fG9hI"

    monkeypatch.setattr(upload, "publish", fake)


def test_a_finished_render_uploads_with_everything_the_build_wrote(api, tmp_path, monkeypatch):
    calls = []
    _publish(monkeypatch, calls)
    _connect(tmp_path)
    job = _finished(api)

    started = api.post(f"/api/jobs/{job.id}/youtube", json={"privacy": "unlisted"})
    _wait_upload(job)

    assert started.status_code == 202 and started.json()["status"] == "uploading"
    [call] = calls
    assert call["cut"] == "a-title.mp4" and call["title"] == "A Title"
    assert call["privacy"] == "unlisted" and "Footage from Pexels" in call["description"]
    assert call["thumbnail"].name == "a-title.jpg" and call["captions"].name == "captions.srt"
    body = api.get(f"/api/jobs/{job.id}").json()["youtube"]
    assert body["status"] == "done" and body["url"] == "https://youtu.be/Ab3dE_fG9hI"
    assert (job.root / "out" / "published.json").exists(), "no receipt for check --published"
    assert json.loads((job.root / "job.json").read_text())["youtube"]["video_id"] == "Ab3dE_fG9hI"


def test_a_render_is_never_uploaded_twice(api, tmp_path, monkeypatch):
    calls = []
    _publish(monkeypatch, calls)
    _connect(tmp_path)
    job = _finished(api)
    api.post(f"/api/jobs/{job.id}/youtube", json={})
    _wait_upload(job)

    again = api.post(f"/api/jobs/{job.id}/youtube", json={})

    assert again.status_code == 409 and len(calls) == 1


def test_a_failure_after_the_video_is_placed_is_not_offered_again(api, tmp_path, monkeypatch):
    """Retrying would make a second video; say what is missing instead."""
    calls = []
    _publish(monkeypatch, calls, fail_after_video=True)
    _connect(tmp_path)
    job = _finished(api)

    api.post(f"/api/jobs/{job.id}/youtube", json={})
    _wait_upload(job)

    assert job.youtube["status"] == "done" and job.youtube["video_id"] == "Ab3dE_fG9hI"
    assert "caption track" in job.youtube["warning"]
    assert api.post(f"/api/jobs/{job.id}/youtube", json={}).status_code == 409


def test_a_login_google_stopped_honouring_asks_to_connect_again(api, tmp_path, monkeypatch):
    def gone(*a, **k):
        raise upload.NotConnected("connect it again")

    monkeypatch.setattr(upload, "access_token", gone)
    _connect(tmp_path)
    job = _finished(api)

    api.post(f"/api/jobs/{job.id}/youtube", json={})
    _wait_upload(job)

    assert job.youtube["status"] == "failed" and job.youtube["reconnect"] is True


def test_a_delivery_with_problems_is_refused_before_anything_leaves(api, tmp_path, monkeypatch):
    calls = []
    _publish(monkeypatch, calls)
    _connect(tmp_path)
    monkeypatch.setattr(yt_mod, "check", lambda out: ["a credit is not in description.txt"])
    job = _finished(api)

    refused = api.post(f"/api/jobs/{job.id}/youtube", json={})

    assert refused.status_code == 409 and "credit" in refused.json()["detail"]
    assert calls == [] and job.youtube == {}


@pytest.mark.parametrize("setup,expect", [
    ("not-connected", "connect YouTube first"),
    ("bad-privacy", "visibility"),
])
def test_an_upload_that_cannot_start_says_why(api, tmp_path, setup, expect):
    if setup != "not-connected":
        _connect(tmp_path)
    job = _finished(api)
    privacy = "everyone" if setup == "bad-privacy" else "private"
    refused = api.post(f"/api/jobs/{job.id}/youtube", json={"privacy": privacy})
    assert refused.status_code == 409 and expect in refused.json()["detail"]


def test_an_unknown_job_is_a_404(api):
    assert api.post("/api/jobs/nope/youtube", json={}).status_code == 404


def test_an_upload_a_restart_interrupted_comes_back_as_failed(tmp_path):
    first = Jobs(tmp_path)
    job = first.submit(SCRIPT, {})
    _settle(first)
    job.youtube = {"status": "uploading", "privacy": "private"}
    first.record(job)

    again = Jobs(tmp_path).get(job.id)

    assert again.youtube["status"] == "failed" and "restarted" in again.youtube["error"]


# --------------------------------------------------------------------------- #
# the login helpers
# --------------------------------------------------------------------------- #
def test_the_server_never_waits_on_a_consent_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "_consent",
                        lambda *a, **k: pytest.fail("opened a consent screen on a server"))
    with pytest.raises(upload.NotConnected):
        upload.access_token(tmp_path, "c", "s", interactive=False)


def test_a_grant_without_a_refresh_token_is_not_kept(tmp_path):
    with pytest.raises(upload.UploadFailed):
        upload.save_grant(tmp_path, {"access_token": "only"})
    assert not upload.connected(tmp_path)


def test_the_page_offers_the_upload_and_guards_public(api):
    page = api.get("/").text
    assert 'id="yt-upload"' in page and 'fetch("/api/youtube"' in page
    assert "Press Again to Publish Publicly" in page
