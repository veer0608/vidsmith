"""`vidsmith publish`: make an uploaded video visible, then check what is visible.

Going public was done by hand the first time, on 3NuA_RVbO10: read the status,
send it back with only the privacy changed, then run `check --published`
separately. These tests hold the two properties that made the hand version
safe. The status write keeps every other field, because videos.update replaces
the whole part and a dropped made-for-kids flag is reset without a word. And
nothing is made visible over a delivery the offline check already faults.
"""
from __future__ import annotations

import argparse
import json

import pytest

import vidsmith.check as check_mod
import vidsmith.cli as cli
import vidsmith.published as pub
import vidsmith.upload as up
from vidsmith.upload import UploadFailed, set_privacy

VID = "3NuA_RVbO10"


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.text = json.dumps(body)

    def json(self):
        return self.body


def test_set_privacy_changes_privacy_and_nothing_else(monkeypatch):
    status = {"privacyStatus": "private", "embeddable": True,
              "license": "youtube", "publicStatsViewable": True,
              "selfDeclaredMadeForKids": False,
              "publishAt": "2026-10-01T00:00:00Z", "uploadStatus": "processed"}
    sent = {}

    def get(url, params=None, headers=None, timeout=None):
        assert params == {"part": "status", "id": VID}
        return _Resp({"items": [{"status": status}]})

    def put(url, params=None, json=None, headers=None, timeout=None):
        sent.update(json)
        return _Resp({"status": {"privacyStatus": json["status"]["privacyStatus"]}})

    monkeypatch.setattr(up.requests, "get", get)
    monkeypatch.setattr(up.requests, "put", put)
    assert set_privacy("tok", VID, "public") == "public"
    assert sent["status"] == {"privacyStatus": "public", "embeddable": True,
                              "license": "youtube", "publicStatsViewable": True,
                              "selfDeclaredMadeForKids": False}


def test_a_refused_change_is_an_upload_failure(monkeypatch):
    monkeypatch.setattr(up.requests, "get", lambda *a, **k: _Resp(
        {"items": [{"status": {"privacyStatus": "private"}}]}))
    monkeypatch.setattr(up.requests, "put", lambda *a, **k: _Resp(
        {"error": "forbidden"}, status=403))
    with pytest.raises(UploadFailed, match="403"):
        set_privacy("tok", VID, "public")


def test_private_is_not_a_publish_target():
    with pytest.raises(ValueError):
        set_privacy("tok", VID, "hidden")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A delivery with an upload receipt, and every network edge stubbed."""
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "published.json").write_text(
        json.dumps({"video_id": VID}), encoding="utf-8")
    monkeypatch.setattr(cli, "_project_dir", lambda name: tmp_path)
    monkeypatch.setattr(cli, "find_keys", lambda root: {"yt_client": "c",
                                                        "yt_secret": "s"})
    monkeypatch.setattr(check_mod, "check", lambda out: [])
    monkeypatch.setattr(up, "access_token", lambda *a, **k: "tok")
    calls = {"privacy": [], "checked": []}

    def privacy(token, vid, level):
        calls["privacy"].append((vid, level))
        return level

    def live(proj, ref):
        calls["checked"].append(ref)
        return [], True

    monkeypatch.setattr(up, "set_privacy", privacy)
    monkeypatch.setattr(cli, "_check_live", live)
    return calls


def _run(**over):
    args = {"name": "demo", "video": None, "privacy": "public", "force": False}
    args.update(over)
    return cli.cmd_publish(argparse.Namespace(**args))


def test_publish_reads_the_receipt_flips_then_checks(project, capsys):
    assert _run() == 0
    assert project["privacy"] == [(VID, "public")]
    assert project["checked"] == [VID], "the visible copy was never checked"
    assert f"{VID} is public and matches" in capsys.readouterr().out


def test_nothing_goes_public_over_a_faulted_delivery(project, monkeypatch, capsys):
    monkeypatch.setattr(check_mod, "check", lambda out: ["a credit is missing"])
    assert _run() == 1
    assert project["privacy"] == [], "made public over a failing check"
    assert "left as it is" in capsys.readouterr().out


def test_force_publishes_over_a_faulted_delivery(project, monkeypatch):
    monkeypatch.setattr(check_mod, "check", lambda out: ["a credit is missing"])
    assert _run(force=True) == 0
    assert project["privacy"] == [(VID, "public")]


def test_no_receipt_and_no_video_touches_nothing(project, tmp_path, capsys):
    (tmp_path / "out" / "published.json").unlink()
    assert _run() == 1
    assert project["privacy"] == []
    assert "pass --video" in capsys.readouterr().out


def test_a_fault_in_the_visible_copy_fails_the_command(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_check_live",
                        lambda proj, ref: (["the published video has no caption track"], True))
    assert _run(video=f"https://youtu.be/{VID}") == 1
    said = capsys.readouterr().out
    assert f"{VID} is public and has 1 problem(s)" in said


def test_an_unreadable_visible_copy_is_not_called_a_match(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_check_live", lambda proj, ref: ([], False))
    assert _run() == 0
    said = capsys.readouterr().out
    assert "was not checked" in said and "matches" not in said
