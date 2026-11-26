"""An email to the owner when someone asks for access.

Nothing here touches a real mail server: the SMTP class is a recorder, and the
settings never come from the machine's own `.env`.
"""
from __future__ import annotations

import os

import pytest

if os.environ.get("CI"):
    import fastapi                              # noqa: F401
else:
    fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient        # noqa: E402

from web import accounts as accounting           # noqa: E402
from web import alerts                           # noqa: E402
from web import app as web_app                   # noqa: E402
from web.jobs import Jobs                        # noqa: E402

MAIL = alerts.Mail(to="owner@example.com", host="smtp.example.com", port=587,
                   user="sender@example.com", password="app-password",
                   sender="sender@example.com")


class Recorder:
    """Stands in for smtplib: records what would have been sent."""
    sent: list = []
    calls: list = []

    def __init__(self, host, port, timeout=None, context=None):
        Recorder.calls.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        Recorder.calls.append(("starttls",))

    def login(self, user, password):
        Recorder.calls.append(("login", user))

    def send_message(self, msg):
        Recorder.sent.append(msg)


class FakeSmtp:
    SMTP = Recorder
    SMTP_SSL = Recorder


@pytest.fixture(autouse=True)
def reset():
    Recorder.sent, Recorder.calls = [], []


# --------------------------------------------------------------------------- #
# the message
# --------------------------------------------------------------------------- #
def test_the_alert_names_the_person_and_links_the_admin_page():
    msg = alerts.message(MAIL, "Ann Lee", "ann@example.com", "I make explainers",
                         "https://box.example/admin", 2)
    body = msg.get_content()
    assert msg["Subject"] == "vidsmith: Ann Lee asked for access"
    assert msg["To"] == "owner@example.com"
    assert "ann@example.com" in body and "I make explainers" in body
    assert "https://box.example/admin" in body and "2 requests waiting" in body


def test_a_name_cannot_inject_a_header():
    """The name is a stranger's text and it goes into the Subject."""
    msg = alerts.message(MAIL, "Ann\r\nBcc: victim@example.com", "a@b.co", "",
                         "https://box.example/admin", 1)
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert msg["Bcc"] is None
    assert "1 request waiting" in msg.get_content()


def test_send_uses_starttls_on_587_and_logs_in():
    alerts.send(MAIL, alerts.message(MAIL, "A", "a@b.co", "", "u", 1), smtp=FakeSmtp)
    assert Recorder.calls == [("connect", "smtp.example.com", 587), ("starttls",),
                              ("login", "sender@example.com")]
    assert len(Recorder.sent) == 1


def test_port_465_is_implicit_tls_with_no_starttls():
    mail = alerts.Mail(**{**MAIL.__dict__, "port": 465})
    alerts.send(mail, alerts.message(mail, "A", "a@b.co", "", "u", 1), smtp=FakeSmtp)
    assert ("starttls",) not in Recorder.calls and len(Recorder.sent) == 1


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
def test_alerts_are_off_until_every_required_setting_is_there(tmp_path, monkeypatch):
    for name in alerts.NAMES:
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("VIDSMITH_ALERT_TO=owner@example.com\n"
                      "VIDSMITH_SMTP_HOST=smtp.example.com\n"
                      "VIDSMITH_SMTP_USER=sender@example.com\n", encoding="utf-8")
    assert alerts.settings([dotenv]) is None, "no password, so off"
    dotenv.write_text(dotenv.read_text() + "VIDSMITH_SMTP_PASSWORD=pw\n", encoding="utf-8")
    mail = alerts.settings([dotenv])
    assert mail is not None and mail.port == 587 and mail.sender == "sender@example.com"


# --------------------------------------------------------------------------- #
# through the route
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "owner-secret")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    monkeypatch.setattr(web_app, "accounts", accounting.Accounts(tmp_path / "jobs"))
    monkeypatch.setattr(web_app, "PUBLIC_URL", "https://box.example")
    return TestClient(web_app.app)


def test_a_new_request_emails_the_owner(client, monkeypatch):
    monkeypatch.setattr(web_app, "_alert_settings", lambda: MAIL)
    sent = []
    monkeypatch.setattr(alerts, "send", lambda mail, msg: sent.append(msg))
    monkeypatch.setattr(alerts.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": staticmethod(target)}))
    r = client.post("/api/register", json={"name": "Ann", "email": "ann@example.com",
                                           "note": "hello"})
    assert r.status_code == 201
    assert len(sent) == 1 and "https://box.example/admin" in sent[0].get_content()


def test_no_settings_means_no_email_and_registration_still_works(client, monkeypatch):
    monkeypatch.setattr(web_app, "_alert_settings", lambda: None)
    sent = []
    monkeypatch.setattr(alerts, "send", lambda mail, msg: sent.append(msg))
    assert client.post("/api/register",
                       json={"name": "Ann", "email": "ann@example.com"}).status_code == 201
    assert sent == []


def test_a_mail_server_that_fails_never_fails_the_request(client, monkeypatch):
    monkeypatch.setattr(web_app, "_alert_settings", lambda: MAIL)

    def down(mail, msg):
        raise OSError("connection refused")

    monkeypatch.setattr(alerts, "send", down)
    monkeypatch.setattr(alerts.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": staticmethod(target)}))
    assert client.post("/api/register",
                       json={"name": "Ann", "email": "ann@example.com"}).status_code == 201


def test_a_refused_request_sends_nothing(client, monkeypatch):
    monkeypatch.setattr(web_app, "_alert_settings", lambda: MAIL)
    sent = []
    monkeypatch.setattr(alerts, "send", lambda mail, msg: sent.append(msg))
    assert client.post("/api/register", json={"name": "", "email": "x"}).status_code == 400
    assert sent == []


def test_admin_says_whether_alerts_are_on(client, monkeypatch):
    owner = {"X-Vidsmith-Token": "owner-secret"}
    monkeypatch.setattr(web_app, "_alert_settings", lambda: None)
    assert client.get("/api/admin/requests", headers=owner).json()["email_alerts"] is False
    monkeypatch.setattr(web_app, "_alert_settings", lambda: MAIL)
    body = client.get("/api/admin/requests", headers=owner).json()
    assert body["email_alerts"] is True
    assert "password" not in str(body), "the settings themselves never leave the box"


# --------------------------------------------------------------------------- #
# a push to the owner's phone, through ntfy
# --------------------------------------------------------------------------- #
PUSH = alerts.Push(topic="vidsmith-abc123", server="https://ntfy.sh")


def test_the_push_carries_nothing_about_who_asked():
    """A topic is readable by anyone who knows it, so the person stays on the box."""
    req = alerts.push_request(PUSH, "https://box.example/admin", 2)
    assert req["url"] == "https://ntfy.sh/vidsmith-abc123"
    assert req["headers"]["Click"] == "https://box.example/admin"
    assert req["data"] == b"2 requests waiting. Tap to review."
    flat = str(req)
    assert "ann" not in flat.lower() and "@" not in flat


def test_push_is_off_without_a_topic(tmp_path, monkeypatch):
    for name in alerts.NAMES:
        monkeypatch.delenv(name, raising=False)
    assert alerts.push_settings([tmp_path / ".env"]) is None
    (tmp_path / ".env").write_text("VIDSMITH_NTFY_TOPIC=vidsmith-abc123\n", encoding="utf-8")
    push = alerts.push_settings([tmp_path / ".env"])
    assert push == alerts.Push(topic="vidsmith-abc123", server="https://ntfy.sh")


def test_a_new_request_pushes_to_the_phone(client, monkeypatch):
    monkeypatch.setattr(web_app, "_push_settings", lambda: PUSH)
    pushed = []
    monkeypatch.setattr(alerts, "post", lambda push, url, waiting: pushed.append((url, waiting)))
    monkeypatch.setattr(alerts.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": staticmethod(target)}))
    assert client.post("/api/register",
                       json={"name": "Ann", "email": "ann@example.com"}).status_code == 201
    assert pushed == [("https://box.example/admin", 1)]


def test_a_push_server_that_fails_never_fails_the_request(client, monkeypatch):
    monkeypatch.setattr(web_app, "_push_settings", lambda: PUSH)

    def down(push, url, waiting):
        raise OSError("ntfy unreachable")

    monkeypatch.setattr(alerts, "post", down)
    monkeypatch.setattr(alerts.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": staticmethod(target)}))
    assert client.post("/api/register",
                       json={"name": "Ann", "email": "ann@example.com"}).status_code == 201


def test_admin_says_whether_phone_alerts_are_on(client, monkeypatch):
    owner = {"X-Vidsmith-Token": "owner-secret"}
    monkeypatch.setattr(web_app, "_push_settings", lambda: None)
    assert client.get("/api/admin/requests", headers=owner).json()["phone_alerts"] is False
    monkeypatch.setattr(web_app, "_push_settings", lambda: PUSH)
    body = client.get("/api/admin/requests", headers=owner).json()
    assert body["phone_alerts"] is True
    assert "vidsmith-abc123" not in str(body), "the topic never leaves the box"
