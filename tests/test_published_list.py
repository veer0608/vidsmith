"""`vidsmith published`: what this repo has put on the channel.

Test uploads accumulate quietly. Two private ones were on the channel within a
day of the upload path working, and nothing in the tool could say so. The
receipts already know - one per published cut - so this reads them, offline,
and only asks YouTube when told to.
"""
from __future__ import annotations

import argparse
import json

import pytest

import vidsmith.cli as cli
import vidsmith.published as pub
from vidsmith.published import privacy_of, receipts, record

WIDE = "MgD7QwCozms"
SHORT = "zAv-sAArB5Y"


@pytest.fixture
def out(tmp_path):
    (tmp_path / "out").mkdir()
    out = tmp_path / "out"
    for name, text in (("description.txt", "wide prose"), ("credits.txt", "Ada - x"),
                       ("description-9x16.txt", "short prose"),
                       ("credits-9x16.txt", "Grace - y")):
        (out / name).write_text(text, encoding="utf-8")
    (out / "a-video.mp4").write_bytes(b"wide")
    (out / "a-video-9x16.mp4").write_bytes(b"short")
    record(out, WIDE)
    record(out, SHORT, tag="-9x16")
    return out


def test_every_published_cut_is_listed(out):
    rows = {r["tag"]: r for r in receipts(out)}

    assert set(rows) == {"", "-9x16"}
    assert rows[""]["video_id"] == WIDE and rows["-9x16"]["video_id"] == SHORT
    assert rows[""]["cut"] == "a-video.mp4"
    assert not rows[""]["moved"] and not rows["-9x16"]["moved"]


def test_a_changed_file_is_named_against_its_own_video(out):
    (out / "description-9x16.txt").write_text("rewritten", encoding="utf-8")

    rows = {r["tag"]: r for r in receipts(out)}

    assert rows["-9x16"]["moved"] == ["description-9x16.txt"]
    assert not rows[""]["moved"], "the widescreen video was not touched"


def test_a_rebuilt_cut_counts_as_drift_too(out):
    (out / "a-video.mp4").write_bytes(b"a rebuilt cut")

    rows = {r["tag"]: r for r in receipts(out)}

    assert rows[""]["moved"] == ["a-video.mp4"]


def test_junk_and_missing_receipts_are_skipped(tmp_path):
    (tmp_path / "published.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "published-1x1.json").write_text('{"files": {}}', encoding="utf-8")

    assert receipts(tmp_path) == []


def test_privacy_is_asked_in_one_request_per_fifty(monkeypatch):
    """videos.list costs a unit per call, not per video, so a channel's worth
    of receipts is one unit."""
    calls = []

    class Reply:
        def __init__(self, ids):
            self.ids = ids

        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"id": i, "snippet": {"title": i},
                               "status": {"privacyStatus": "private"}}
                              for i in self.ids]}

    def get(url, params=None, headers=None, timeout=None):
        calls.append(params["id"].split(","))
        return Reply(params["id"].split(","))

    import requests

    monkeypatch.setattr(requests, "get", get)
    live = privacy_of([f"video{n:03d}" for n in range(60)], "tok")

    assert len(calls) == 2 and len(calls[0]) == 50
    assert len(live) == 60 and live["video000"]["privacy"] == "private"


def test_a_refused_read_is_unreachable_not_an_empty_channel(monkeypatch):
    import requests

    def boom(*a, **k):
        raise RuntimeError("403 quota")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(pub.Unreachable, match="could not read the channel"):
        privacy_of([WIDE], "tok")


def test_the_command_lists_projects_offline(out, monkeypatch, capsys):
    """Offline by default, like `check`: it works on a spent day."""
    monkeypatch.setattr(cli, "_project_dir", lambda name: out.parent)
    monkeypatch.setattr(pub, "privacy_of",
                        lambda *a, **k: pytest.fail("--live was not asked for"))

    code = cli.cmd_published(argparse.Namespace(name="demo", live=False, box=False,
                                            host=None, key=None))

    said = capsys.readouterr().out
    assert code == 0
    assert WIDE in said and SHORT in said
    assert "16:9" in said and "-9x16" in said


def test_the_command_says_when_a_video_is_gone(out, monkeypatch, capsys):
    """A receipt outlives the video: deleting one in Studio leaves the claim."""
    monkeypatch.setattr(cli, "_project_dir", lambda name: out.parent)
    monkeypatch.setattr(cli, "find_keys", lambda root: {"yt_client": "c", "yt_secret": "s"})
    monkeypatch.setattr("vidsmith.upload.access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(pub, "privacy_of",
                        lambda ids, token, **k: {WIDE: {"title": "t", "privacy": "public"}})

    cli.cmd_published(argparse.Namespace(name="demo", live=True, box=False,
                                     host=None, key=None))

    said = capsys.readouterr().out
    assert "public" in said and "gone from the channel" in said


# --------------------------------------------------------------------------- #
# what the live instance published
# --------------------------------------------------------------------------- #
def test_the_box_is_read_over_loopback_on_the_box(monkeypatch):
    """Every job route is behind the token and the token lives on the box, so
    the question goes there rather than the credential coming here."""
    import subprocess

    from vidsmith import deploy

    seen = {}

    def run(args, **kwargs):
        seen["command"] = args[-1]
        return subprocess.CompletedProcess(args, 0, '{"renders": []}', "")

    body = deploy.remote_api("/api/jobs", host="box.example", key="k.pem", run=run)

    assert body == {"renders": []}
    assert "127.0.0.1:8077/api/jobs" in seen["command"]
    assert "VIDSMITH_TOKEN" in seen["command"] and "Host: box.example" in seen["command"]


def test_a_box_that_answers_nonsense_is_a_clean_failure(monkeypatch):
    import subprocess

    from vidsmith import deploy

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "<html>bad gateway</html>", "")

    with pytest.raises(deploy.DeployFailed, match="not JSON"):
        deploy.remote_api("/api/jobs", host="box.example", key="k.pem", run=run)


def test_the_command_lists_what_the_instance_uploaded(out, monkeypatch, capsys):
    from vidsmith import deploy

    monkeypatch.setattr(cli, "_project_dir", lambda name: out.parent)
    monkeypatch.setattr(deploy, "remote_api", lambda path, **k: {"renders": [
        {"id": "1e8bb07f1f04", "aspect": "16:9",
         "youtube": {"status": "done", "video_id": "m7HFMi6m7AU"}},
        {"id": "23a21a8f62fa", "aspect": "16:9", "youtube": None},
    ]})

    cli.cmd_published(argparse.Namespace(name="demo", live=False, box=True,
                                         host=None, key=None))

    said = capsys.readouterr().out
    assert "live box" in said and "m7HFMi6m7AU" in said
    assert "23a21a8f62fa" not in said, "a render nobody uploaded is not published"


def test_an_unreachable_box_does_not_lose_the_local_list(out, monkeypatch, capsys):
    from vidsmith import deploy

    monkeypatch.setattr(cli, "_project_dir", lambda name: out.parent)

    def refuse(path, **kwargs):
        raise deploy.DeployFailed("ssh: connect to host box.example port 22")

    monkeypatch.setattr(deploy, "remote_api", refuse)

    cli.cmd_published(argparse.Namespace(name="demo", live=False, box=True,
                                         host=None, key=None))

    said = capsys.readouterr().out
    assert "could not read the live box" in said
    assert WIDE in said, "the local receipts are still worth printing"
