"""`vidsmith publish` managing a video already live: words, retirement, history.

Replacing howto on 2026-09-24 needed three things the command could not do.
Setting the old video private was a hand-written call, because `--privacy`
offered no private. Correcting the live title's casing was another, because
nothing could put a rebuilt description on a live video. And the new upload's
receipt overwrote the old one, so `published` forgot a video still sitting on
the channel. Separately, the `check --published` that confirmed the fix failed
on the very drift it had just resolved, and passed when run a second time.
"""
from __future__ import annotations

import argparse
import json

import pytest

import vidsmith.check as check_mod
import vidsmith.cli as cli
import vidsmith.upload as up
from vidsmith.check import publish_drift
from vidsmith.published import RECEIPT, receipts, record
from vidsmith.upload import UploadFailed, set_metadata

OLD, NEW = "eNhDPX7s_Xs", "ta1x6KFVuxU"


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.text = json.dumps(body)

    def json(self):
        return self.body


# --------------------------------------------------------------------------- #
# set_metadata
# --------------------------------------------------------------------------- #
def test_set_metadata_replaces_the_words_and_keeps_the_rest(monkeypatch):
    """videos.update replaces the snippet whole: a field left out is cleared."""
    live = {"title": "How To Use Vidsmith", "description": "old", "tags": ["a"],
            "categoryId": "28", "defaultLanguage": "en",
            "defaultAudioLanguage": "en-US", "channelId": "UCx",
            "publishedAt": "2026-09-24T12:05:00Z", "thumbnails": {}}
    sent = {}

    def get(url, params=None, headers=None, timeout=None):
        assert params == {"part": "snippet", "id": NEW}
        return _Resp({"items": [{"snippet": live}]})

    def put(url, params=None, json=None, headers=None, timeout=None):
        assert params == {"part": "snippet"}
        sent.update(json)
        return _Resp({"snippet": json["snippet"]})

    monkeypatch.setattr(up.requests, "get", get)
    monkeypatch.setattr(up.requests, "put", put)
    saved = set_metadata("tok", NEW, "How To Use vidsmith", "new words", ["b", "c"])
    assert sent == {"id": NEW, "snippet": {
        "categoryId": "28", "defaultLanguage": "en", "defaultAudioLanguage": "en-US",
        "title": "How To Use vidsmith", "description": "new words", "tags": ["b", "c"]}}
    assert saved["title"] == "How To Use vidsmith"


def test_a_refused_word_change_is_an_upload_failure(monkeypatch):
    monkeypatch.setattr(up.requests, "get", lambda *a, **k: _Resp(
        {"items": [{"snippet": {"categoryId": "28"}}]}))
    monkeypatch.setattr(up.requests, "put", lambda *a, **k: _Resp(
        {"error": "forbidden"}, status=403))
    with pytest.raises(UploadFailed, match="403"):
        set_metadata("tok", NEW, "t", "d", [])


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
@pytest.fixture
def delivery(tmp_path, monkeypatch):
    """A cut uploaded as NEW, with its receipt, and every network edge stubbed."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "a-video.mp4").write_bytes(b"the published cut")
    (out / "description.txt").write_text("Use Vidsmith.\n", encoding="utf-8")
    (out / "credits.txt").write_text("Footage from Pexels\n", encoding="utf-8")
    (out / "youtube.json").write_text(json.dumps(
        {"title": "How To Use vidsmith", "tags": ["vidsmith", "tutorial"]}),
        encoding="utf-8")
    record(out, NEW)
    monkeypatch.setattr(cli, "_project_dir", lambda name: tmp_path)
    monkeypatch.setattr(cli, "find_keys", lambda root: {"yt_client": "c",
                                                        "yt_secret": "s"})
    monkeypatch.setattr(up, "access_token", lambda *a, **k: "tok")
    # only the receipt half of the offline check; the rest needs a real build
    monkeypatch.setattr(check_mod, "check", lambda out: publish_drift(out))
    calls = {"privacy": [], "meta": [], "checked": []}

    def privacy(token, vid, level):
        calls["privacy"].append((vid, level))
        return level

    def meta(token, vid, title, description, tags):
        calls["meta"].append((vid, title, description, tags))
        return {"title": title, "tags": tags}

    def live(proj, ref, tag=""):
        calls["checked"].append(ref)
        return [], True

    monkeypatch.setattr(up, "set_privacy", privacy)
    monkeypatch.setattr(up, "set_metadata", meta)
    monkeypatch.setattr(cli, "_check_live", live)
    calls["out"] = out
    return calls


def _publish(**over):
    args = {"name": "demo", "video": None, "privacy": None, "force": False,
            "aspect": None, "meta": False}
    args.update(over)
    return cli.cmd_publish(argparse.Namespace(**args))


def test_private_takes_a_video_down_without_checking_it(delivery, monkeypatch, capsys):
    """The video being retired is usually an old one, and its files are gone."""
    monkeypatch.setattr(check_mod, "check", lambda out: ["everything is wrong"])
    assert _publish(video=OLD, privacy="private") == 0
    assert delivery["privacy"] == [(OLD, "private")]
    assert delivery["checked"] == []
    assert "nothing about it was checked" in capsys.readouterr().out


def test_meta_puts_a_rebuilt_description_on_the_same_cut(delivery, capsys):
    (delivery["out"] / "description.txt").write_text("Use vidsmith.\n", encoding="utf-8")
    assert _publish(meta=True) == 0
    assert delivery["meta"] == [(NEW, "How To Use vidsmith", "Use vidsmith.\n",
                                 ["vidsmith", "tutorial"])]
    assert delivery["privacy"] == [], "--meta alone must not change who can see it"
    assert delivery["checked"] == [NEW]
    assert f"{NEW} is updated and matches" in capsys.readouterr().out


def test_meta_and_privacy_together_do_both(delivery):
    assert _publish(meta=True, privacy="public") == 0
    assert len(delivery["meta"]) == 1
    assert delivery["privacy"] == [(NEW, "public")]


def test_meta_is_refused_over_a_new_cut(delivery, capsys):
    """Pasting a new cut's credits onto the old video breaks attribution."""
    (delivery["out"] / "a-video.mp4").write_bytes(b"a rebuilt cut")
    assert _publish(meta=True) == 1
    assert delivery["meta"] == [] and delivery["privacy"] == []
    assert "credit footage it does not contain" in capsys.readouterr().out


def test_meta_is_refused_when_the_receipt_cannot_vouch_for_the_cut(delivery, capsys):
    receipt = delivery["out"] / RECEIPT
    body = json.loads(receipt.read_text(encoding="utf-8"))
    del body["cut"]
    receipt.write_text(json.dumps(body), encoding="utf-8")
    assert _publish(meta=True) == 1
    assert delivery["meta"] == []
    assert "predates recording the cut" in capsys.readouterr().out


def test_meta_is_refused_on_a_video_this_cut_was_not_uploaded_as(delivery, capsys):
    assert _publish(meta=True, video=OLD) == 1
    assert delivery["meta"] == []
    assert f"{OLD} is not the video this cut was uploaded as" in capsys.readouterr().out


def test_meta_answers_only_its_own_drift(delivery, monkeypatch, capsys):
    """The stale description is what --meta replaces; any other fault still stops it."""
    (delivery["out"] / "description.txt").write_text("Use vidsmith.\n", encoding="utf-8")
    monkeypatch.setattr(check_mod, "check",
                        lambda out: publish_drift(out) + ["a credit is missing"])
    assert _publish(meta=True) == 1
    said = capsys.readouterr().out
    assert "1 problem(s)" in said and "a credit is missing" in said
    assert delivery["meta"] == []


# --------------------------------------------------------------------------- #
# a receipt remembers what it replaced
# --------------------------------------------------------------------------- #
def test_a_new_upload_keeps_the_video_it_replaced(tmp_path):
    (tmp_path / "a-video.mp4").write_bytes(b"cut")
    record(tmp_path, OLD)
    record(tmp_path, NEW)
    body = json.loads((tmp_path / RECEIPT).read_text(encoding="utf-8"))
    assert body["video_id"] == NEW
    assert [r["video_id"] for r in body["replaced"]] == [OLD]
    assert body["replaced"][0]["until"] == body["checked"]

    record(tmp_path, "third_video")
    record(tmp_path, "third_video")          # a re-check is not a replacement
    body = json.loads((tmp_path / RECEIPT).read_text(encoding="utf-8"))
    assert [r["video_id"] for r in body["replaced"]] == [NEW, OLD]


def test_a_replaced_video_is_still_listed(tmp_path):
    (tmp_path / "a-video.mp4").write_bytes(b"cut")
    record(tmp_path, OLD)
    record(tmp_path, NEW)
    rows = receipts(tmp_path)
    assert [(r["video_id"], r.get("replaced_by")) for r in rows] == [(NEW, None),
                                                                     (OLD, NEW)]


def test_the_command_says_what_replaced_it(tmp_path, monkeypatch, capsys):
    proj = tmp_path / "projects" / "howto"
    (proj / "out").mkdir(parents=True)
    (proj / "out" / "a-video.mp4").write_bytes(b"cut")
    record(proj / "out", OLD)
    record(proj / "out", NEW)
    monkeypatch.setattr(cli, "_project_dir", lambda name: proj)
    cli.cmd_published(argparse.Namespace(name="howto", live=False, box=False,
                                         host=None, key=None))
    lines = capsys.readouterr().out.splitlines()
    assert any(OLD in l and f"replaced by {NEW}" in l for l in lines), lines


# --------------------------------------------------------------------------- #
# check --published and the receipt it rewrites
# --------------------------------------------------------------------------- #
def test_a_clean_live_check_answers_the_drift_it_just_resolved(delivery, monkeypatch,
                                                               capsys):
    """It reported "the description published there is stale" in the same run
    that confirmed the re-pasted description and rewrote the receipt."""
    (delivery["out"] / "description.txt").write_text("Use vidsmith.\n", encoding="utf-8")

    def live(proj, ref, tag=""):
        record(proj.out, ref, tag=tag)       # what the real one does when clean
        return [], True

    monkeypatch.setattr(cli, "_check_live", live)
    assert cli.cmd_check(argparse.Namespace(name="demo", published=NEW,
                                            aspect=None)) == 0
    assert "matches what is published" in capsys.readouterr().out


def test_a_failed_live_check_keeps_the_drift(delivery, monkeypatch, capsys):
    (delivery["out"] / "description.txt").write_text("Use vidsmith.\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_check_live", lambda proj, ref, tag="": (
        ["the published video has no caption track"], True))
    assert cli.cmd_check(argparse.Namespace(name="demo", published=NEW,
                                            aspect=None)) == 1
    said = capsys.readouterr().out
    assert "2 problem(s)" in said and "stale" in said
