"""Registering for access: ask, be approved, get a token with a daily cap.

What has to hold is the boundary: a member can render and read their own
renders and nothing else, and no route hands out the owner's power.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

if os.environ.get("CI"):
    import fastapi                              # noqa: F401
else:
    fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient        # noqa: E402

import web.jobs as jobs_mod                      # noqa: E402
from web import accounts as accounting           # noqa: E402
from web import app as web_app                   # noqa: E402
from web.jobs import Jobs                        # noqa: E402

SCRIPT = "# T\n\n## One\nA short line of narration for the test.\n"
OWNER = {"X-Vidsmith-Token": "owner-secret"}


@pytest.fixture(autouse=True)
def stub_pipeline(monkeypatch):
    def fake_build(root, **kwargs):
        out = Path(root) / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "video.mp4").write_bytes(b"stub")
        return out / "video.mp4"

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "owner-secret")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    monkeypatch.setattr(web_app, "accounts", accounting.Accounts(tmp_path / "jobs"))
    monkeypatch.setattr(accounting, "DAILY_RENDERS", 2)
    return TestClient(web_app.app)


def _settle(jobs, limit: int = 200) -> None:
    for _ in range(limit):
        if not jobs.busy():
            return
        time.sleep(0.02)
    raise AssertionError("the render slot was never released")


def _member(client, email="ann@example.com"):
    """Register, get approved, collect the token."""
    secret = client.post("/api/register", json={"name": "Ann", "email": email}).json()["secret"]
    listing = client.get("/api/admin/requests", headers=OWNER).json()["requests"]
    request_id = next(r["id"] for r in listing if r["email"] == email)
    assert client.post(f"/api/admin/requests/{request_id}/approve", headers=OWNER).status_code == 200
    token = client.get(f"/api/register/{secret}").json()["token"]
    return {"X-Vidsmith-Token": token}, secret, request_id


def test_pending_request_has_no_token(client):
    secret = client.post("/api/register", json={"name": "Ann", "email": "ann@example.com"}).json()["secret"]
    body = client.get(f"/api/register/{secret}").json()
    assert body["status"] == "pending" and "token" not in body


def test_token_is_handed_over_once(client):
    member, secret, _ = _member(client)
    again = client.get(f"/api/register/{secret}").json()
    assert "token" not in again and again["collected"] is True
    assert client.get("/api/jobs", headers=member).status_code == 200


def test_member_can_render_but_not_see_others(client):
    ann, _, _ = _member(client, "ann@example.com")
    bob, _, _ = _member(client, "bob@example.com")
    job = client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards"}, headers=ann)
    assert job.status_code == 202
    job_id = job.json()["id"]
    _settle(web_app.jobs)
    assert client.get(f"/api/jobs/{job_id}", headers=ann).status_code == 200
    assert client.get(f"/api/jobs/{job_id}", headers=bob).status_code == 404
    assert [r["id"] for r in client.get("/api/jobs", headers=ann).json()["renders"]] == [job_id]
    assert client.get("/api/jobs", headers=bob).json()["renders"] == []
    assert client.get(f"/api/jobs/{job_id}", headers=OWNER).status_code == 200


def test_member_is_capped_per_day(client):
    ann, _, _ = _member(client)
    for _ in range(2):
        assert client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards"},
                           headers=ann).status_code == 202
        _settle(web_app.jobs)
    over = client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards"}, headers=ann)
    assert over.status_code == 429 and "renders for today" in over.json()["detail"]
    # the owner is not counted
    assert client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards"},
                       headers=OWNER).status_code == 202


def test_a_refused_render_is_given_back(client):
    ann, _, _ = _member(client)
    bad = client.post("/api/jobs", json={"script": "   ", "provider": "cards"}, headers=ann)
    assert bad.status_code == 400
    for _ in range(2):                      # both of today's renders are still there
        assert client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards"},
                           headers=ann).status_code == 202
        _settle(web_app.jobs)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/usage"), ("get", "/api/youtube"), ("post", "/api/youtube/connect"),
    ("get", "/api/admin/requests"), ("post", "/api/admin/requests/x/approve"),
])
def test_member_cannot_reach_owner_routes(client, method, path):
    """403, never 401: the page forgets a token on a 401, and a member has one copy."""
    ann, _, _ = _member(client)
    assert getattr(client, method)(path, headers=ann).status_code == 403
    # and the token still works afterwards
    assert client.get("/api/jobs", headers=ann).status_code == 200


def test_whoami_says_who_and_what_is_left(client):
    client.post("/api/register", json={"name": "Waiting", "email": "w@example.com"})
    assert client.get("/api/whoami", headers=OWNER).json() == {"role": "owner", "pending": 1}
    ann, _, _ = _member(client)
    client.post("/api/jobs", json={"script": SCRIPT, "provider": "cards"}, headers=ann)
    _settle(web_app.jobs)
    body = client.get("/api/whoami", headers=ann).json()
    assert body["role"] == "member" and body["left"]["renders"] == 1
    assert client.get("/api/whoami").status_code == 401


def test_member_drafts_are_capped_and_refunded(client, monkeypatch):
    from vidsmith import llm
    monkeypatch.setattr(accounting, "DAILY_DRAFTS", 2)
    monkeypatch.setattr(web_app, "_keys", lambda: {"gemini": "k"})
    ann, _, _ = _member(client)
    topic = {"topic": "how tides work", "minutes": 1}

    def down(*a, **k):
        raise llm.LLMUnavailable("down")
    monkeypatch.setattr(llm, "draft_script", down)
    assert client.post("/api/draft", json=topic, headers=ann).status_code == 502

    monkeypatch.setattr(llm, "draft_script", lambda *a, **k: SCRIPT)
    for _ in range(2):          # the failed one was given back, so two still remain
        assert client.post("/api/draft", json=topic, headers=ann).status_code == 200
    over = client.post("/api/draft", json=topic, headers=ann)
    assert over.status_code == 429 and "drafts for today" in over.json()["detail"]
    assert client.post("/api/draft", json=topic, headers=OWNER).status_code == 200


def test_reissue_replaces_a_lost_token(client):
    old, secret, request_id = _member(client)
    r = client.post(f"/api/admin/requests/{request_id}/reissue", headers=OWNER)
    new = {"X-Vidsmith-Token": r.json()["token"]}
    assert client.get("/api/jobs", headers=old).status_code == 401
    assert client.get("/api/jobs", headers=new).status_code == 200
    # the status link can collect it too, once
    assert client.get(f"/api/register/{secret}").json()["token"] == new["X-Vidsmith-Token"]
    assert "token" not in client.get(f"/api/register/{secret}").json()


def test_only_an_approved_member_is_reissued(client):
    client.post("/api/register", json={"name": "W", "email": "w@example.com"})
    request_id = client.get("/api/admin/requests", headers=OWNER).json()["requests"][0]["id"]
    assert client.post(f"/api/admin/requests/{request_id}/reissue",
                       headers=OWNER).status_code == 404


def test_owner_only_cards_are_marked_on_the_page(client):
    """The page hides these for a member; a card left unmarked would offer a 403."""
    page = client.get("/").text
    for card in ('id="shots-card"', 'id="script-card"', 'id="thumb-card"',
                 'id="yt-box"', 'id="shorts-box"', 'id="allowance"'):
        start = page.index(card)
        tag = page[page.rindex("<", 0, start):page.index(">", start)]
        assert "owner-only" in tag, card


def test_stranger_and_garbage_tokens_are_refused(client):
    assert client.post("/api/jobs", json={"script": SCRIPT}).status_code == 401
    assert client.post("/api/jobs", json={"script": SCRIPT},
                       headers={"X-Vidsmith-Token": "nope"}).status_code == 401
    assert client.get("/api/jobs?t=%C3%A9").status_code == 401   # non-ASCII is a 401, not a 500


def test_revoking_ends_access_at_once(client):
    ann, _, request_id = _member(client)
    assert client.get("/api/jobs", headers=ann).status_code == 200
    client.post(f"/api/admin/requests/{request_id}/deny", headers=OWNER)
    assert client.get("/api/jobs", headers=ann).status_code == 401


def test_register_validates_and_dedupes(client):
    assert client.post("/api/register", json={"name": "", "email": "a@b.co"}).status_code == 400
    assert client.post("/api/register", json={"name": "A", "email": "nope"}).status_code == 400
    assert client.post("/api/register", json={"name": "A", "email": "a@b.co"}).status_code == 201
    assert client.post("/api/register", json={"name": "A", "email": "A@B.co"}).status_code == 400


def test_pending_list_is_bounded(client, monkeypatch):
    monkeypatch.setattr(accounting, "MAX_PENDING", 2)
    for i in range(2):
        assert client.post("/api/register", json={"name": "A", "email": f"a{i}@b.co"}).status_code == 201
    assert client.post("/api/register", json={"name": "A", "email": "z@b.co"}).status_code == 400


def test_tokens_are_stored_hashed(client, tmp_path):
    member, _, _ = _member(client)
    raw = (tmp_path / "jobs" / "_accounts.json").read_text(encoding="utf-8")
    assert member["X-Vidsmith-Token"] not in raw


def test_open_instance_has_nothing_to_register_for(client, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "")
    assert client.post("/api/register", json={"name": "A", "email": "a@b.co"}).status_code == 400


def test_pages_are_served(client):
    assert "Request" in client.get("/register").text
    assert "Access requests" in client.get("/admin").text
