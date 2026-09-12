"""What actually leaves this machine on `vidsmith upload`.

Every request is stubbed. These are not about YouTube being reachable, they are
about the three things that have already gone wrong at the upload form and can
only go wrong here now: the wrong cut's description, a caption track YouTube was
left to guess at, and a receipt that witnesses files nobody published.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vidsmith import upload as up


# --------------------------------------------------------------------------- #
# stubs
# --------------------------------------------------------------------------- #
class Reply:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._body


class Recorder:
    """Stands in for requests, and remembers every call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def _next(self, kind, url, kwargs):
        self.calls.append({"kind": kind, "url": url, **kwargs})
        if not self.replies:
            raise AssertionError(f"unexpected {kind} to {url}")
        return self.replies.pop(0)

    def post(self, url, **kwargs):
        return self._next("post", url, kwargs)

    def put(self, url, **kwargs):
        return self._next("put", url, kwargs)


@pytest.fixture
def out(tmp_path):
    """A delivery holding both cuts, each with its own description."""
    d = tmp_path / "out"
    d.mkdir()
    (d / "a-video.mp4").write_bytes(b"wide" * 10)
    (d / "a-video-9x16.mp4").write_bytes(b"tall" * 10)
    (d / "a-video.jpg").write_bytes(b"wide jpg")
    (d / "a-video-9x16.jpg").write_bytes(b"tall jpg")
    (d / "description.txt").write_text("the wide credits", encoding="utf-8")
    (d / "description-9x16.txt").write_text("the tall credits", encoding="utf-8")
    (d / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n",
                                    encoding="utf-8")
    (d / "youtube.json").write_text(json.dumps(
        {"title": "A Video", "tags": ["one", "two"]}), encoding="utf-8")
    return d


# --------------------------------------------------------------------------- #
# the body
# --------------------------------------------------------------------------- #
def test_the_description_is_passed_in_not_read_from_the_shared_metadata():
    """youtube.json holds one description for every aspect; the file beside the
    cut holds that cut's credits. Publishing the shared one names photographers
    whose clips are not in the video, which has shipped twice."""
    meta = {"title": "A Video", "tags": ["a"], "description": "the shared one"}

    body = up.snippet(meta, "the tall credits", "28", "private")

    assert body["snippet"]["description"] == "the tall credits"


def test_the_body_declares_made_for_kids():
    """YouTube refuses an upload that declares nothing, and the answer is not a
    guess: nothing this tool makes is for children."""
    body = up.snippet({"title": "T"}, "d", "28", "unlisted")

    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["status"]["privacyStatus"] == "unlisted"


def test_an_untitled_video_is_not_uploaded_with_an_empty_title():
    assert up.snippet({}, "d", "28", "private")["snippet"]["title"] == "Untitled"


# --------------------------------------------------------------------------- #
# the bytes
# --------------------------------------------------------------------------- #
def test_the_chunks_are_sent_as_ranges_the_server_can_resume_from(monkeypatch):
    data = b"x" * (up.CHUNK * 2 + 17)
    fake = Recorder([
        Reply(308, headers={"Range": f"bytes=0-{up.CHUNK - 1}"}),
        Reply(308, headers={"Range": f"bytes=0-{2 * up.CHUNK - 1}"}),
        Reply(200, body={"id": "abc123"}),
    ])
    monkeypatch.setattr(up, "requests", fake)

    got = up._put_chunks("https://session", data, log=lambda *a: None)

    assert got["id"] == "abc123"
    ranges = [c["headers"]["Content-Range"] for c in fake.calls]
    assert ranges == [
        f"bytes 0-{up.CHUNK - 1}/{len(data)}",
        f"bytes {up.CHUNK}-{2 * up.CHUNK - 1}/{len(data)}",
        f"bytes {2 * up.CHUNK}-{len(data) - 1}/{len(data)}",
    ]
    assert up.CHUNK % (256 * 1024) == 0, \
        "every chunk but the last must be a multiple of 256 KB or the range is refused"


def test_the_next_chunk_starts_where_the_server_says_it_got_to(monkeypatch):
    """The whole point of a resumable session: the server is the authority on
    what it stored, not our count of what we sent."""
    data = b"y" * (up.CHUNK + 500)
    fake = Recorder([
        Reply(308, headers={"Range": "bytes=0-1023"}),   # it kept far less
        Reply(200, body={"id": "v"}),
    ])
    monkeypatch.setattr(up, "requests", fake)

    up._put_chunks("https://session", data, log=lambda *a: None)

    assert fake.calls[1]["headers"]["Content-Range"] == f"bytes 1024-{len(data) - 1}/{len(data)}"


def test_a_refused_chunk_names_the_byte_it_stopped_at(monkeypatch):
    fake = Recorder([Reply(403, text="quotaExceeded")])
    monkeypatch.setattr(up, "requests", fake)

    with pytest.raises(up.UploadFailed) as exc:
        up._put_chunks("https://session", b"z" * 10, log=lambda *a: None)

    assert "byte 0" in str(exc.value) and "quotaExceeded" in str(exc.value)


# --------------------------------------------------------------------------- #
# the caption track
# --------------------------------------------------------------------------- #
def test_the_captions_are_uploaded_without_asking_youtube_to_re_time_them(
        tmp_path, monkeypatch):
    """`sync=true` would hand the text back to a transcriber. The srt carries
    edge-tts word boundaries, which is the timing the audio was made from, and a
    published video once ended up with YouTube's own ASR track instead."""
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    fake = Recorder([Reply(200, body={"id": "cap"})])
    monkeypatch.setattr(up, "requests", fake)

    up.add_captions("tok", "vid1", srt)

    call = fake.calls[0]
    assert call["params"]["sync"] == "false"
    assert call["params"]["uploadType"] == "multipart"
    assert b"hello" in call["data"], "the srt bytes are in the related body"
    assert call["headers"]["Content-Type"].startswith("multipart/related; boundary=")


def test_a_refused_caption_track_is_an_error_not_a_shrug(tmp_path, monkeypatch):
    """The video is already public at this point, and silently having no track
    is the exact failure this command exists to prevent."""
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n", encoding="utf-8")
    monkeypatch.setattr(up, "requests", Recorder([Reply(403, text="forbidden")]))

    with pytest.raises(up.UploadFailed) as exc:
        up.add_captions("tok", "vid1", srt)

    assert "transcribe" in str(exc.value)


# --------------------------------------------------------------------------- #
# the whole publish
# --------------------------------------------------------------------------- #
def _publish_replies():
    return [
        Reply(200, headers={"Location": "https://session"}),   # open session
        Reply(200, body={"id": "vid42"}),                      # the bytes
        Reply(200),                                            # thumbnail
        Reply(200, body={"id": "cap"}),                        # captions
    ]


def test_publish_sends_the_video_then_the_thumbnail_then_the_captions(
        out, monkeypatch):
    fake = Recorder(_publish_replies())
    monkeypatch.setattr(up, "requests", fake)

    vid = up.publish(out / "a-video.mp4", {"title": "A Video", "tags": []},
                     "the wide credits", "tok",
                     thumbnail=out / "a-video.jpg",
                     captions=out / "captions.srt", log=lambda *a: None)

    assert vid == "vid42"
    urls = [c["url"] for c in fake.calls]
    assert urls[0] == up.UPLOAD_URL
    assert urls[2] == up.THUMBNAIL_URL
    assert urls[3] == up.CAPTION_URL


def test_an_optional_file_left_out_is_guarded_on_none(out, monkeypatch):
    """Path("") is Path("."), which is truthy and exists. Guarding on truth here
    is how ffmpeg was once handed a directory where a subtitle file belonged."""
    fake = Recorder(_publish_replies()[:2])
    monkeypatch.setattr(up, "requests", fake)

    up.publish(out / "a-video.mp4", {"title": "A Video"}, "d", "tok",
               thumbnail=None, captions=None, log=lambda *a: None)

    assert len(fake.calls) == 2, "no thumbnail and no caption request"


def test_a_video_with_no_id_back_is_a_failure(out, monkeypatch):
    fake = Recorder([Reply(200, headers={"Location": "https://s"}),
                     Reply(200, body={})])
    monkeypatch.setattr(up, "requests", fake)

    with pytest.raises(up.UploadFailed):
        up.publish(out / "a-video.mp4", {"title": "T"}, "d", "tok",
                   log=lambda *a: None)


# --------------------------------------------------------------------------- #
# tokens
# --------------------------------------------------------------------------- #
def test_a_refresh_never_erases_the_refresh_token(tmp_path, monkeypatch):
    """Google returns a refresh token on the first authorisation and omits it
    from every refresh after, so saving the response wholesale loses it and the
    next run asks for consent again."""
    path = up.token_path(tmp_path)
    path.write_text(json.dumps({"refresh_token": "keep-me",
                                "access_token": "old"}), encoding="utf-8")
    monkeypatch.setattr(up, "requests", Recorder([
        Reply(200, body={"access_token": "new", "expires_in": 3599})]))

    token = up.access_token(tmp_path, "id", "secret", log=lambda *a: None)

    assert token == "new"
    assert json.loads(path.read_text())["refresh_token"] == "keep-me"


def test_a_dead_refresh_token_falls_through_to_consent(tmp_path, monkeypatch):
    path = up.token_path(tmp_path)
    path.write_text(json.dumps({"refresh_token": "revoked"}), encoding="utf-8")
    monkeypatch.setattr(up, "requests", Recorder([Reply(400, text="invalid_grant")]))
    asked = []

    def _consent(client_id, client_secret, log=print):
        asked.append(client_id)
        return {"access_token": "fresh", "refresh_token": "new-one"}

    monkeypatch.setattr(up, "_consent", _consent)

    token = up.access_token(tmp_path, "id", "secret", log=lambda *a: None)

    assert token == "fresh" and asked == ["id"]
    assert json.loads(path.read_text())["refresh_token"] == "new-one"


def test_no_client_says_which_two_variables_are_missing(tmp_path):
    with pytest.raises(up.UploadFailed) as exc:
        up.access_token(tmp_path, "", "", log=lambda *a: None)

    assert "YOUTUBE_CLIENT_ID" in str(exc.value)
    assert "YOUTUBE_CLIENT_SECRET" in str(exc.value)


def test_a_redirect_with_the_wrong_state_hands_back_no_code():
    """The loopback port is open to anything else running on this machine, so a
    code is exchanged only when the redirect carries the state we generated."""
    code, error, _ = up.redirect_result(
        {"state": ["someone-elses"], "code": ["stolen"]}, "the-real-one")

    assert code == "" and "state" in error


def test_a_redirect_with_no_state_at_all_is_refused_too():
    code, error, _ = up.redirect_result({"code": ["stolen"]}, "the-real-one")

    assert code == "" and error


def test_a_refused_consent_is_reported_rather_than_waited_out():
    code, error, _ = up.redirect_result(
        {"state": ["s"], "error": ["access_denied"]}, "s")

    assert code == "" and error == "access_denied"


def test_the_matching_redirect_yields_the_code():
    code, error, body = up.redirect_result(
        {"state": ["s"], "code": ["4/abc"]}, "s")

    assert (code, error) == ("4/abc", "")
    assert b"close this tab" in body


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def _project(tmp_path, aspect="16:9"):
    """A project whose delivery is consistent enough for `check` to pass."""
    from vidsmith.config import write_default_config

    root = tmp_path / "proj"
    (root / "out").mkdir(parents=True)
    (root / "script.md").write_text("# A Video\n\n## One\nA line.\n", encoding="utf-8")
    write_default_config(root / "config.yaml", "A Video")
    cfg = (root / "config.yaml").read_text(encoding="utf-8")
    (root / "config.yaml").write_text(
        cfg.replace("aspect: '16:9'", f"aspect: '{aspect}'"), encoding="utf-8")
    return root


def _deliver(root, tag):
    out = root / "out"
    (out / f"a-video{tag}.mp4").write_bytes(b"m" * 64)
    (out / f"a-video{tag}.jpg").write_bytes(b"j" * 16)
    (out / f"captions{tag}.srt").write_text("1\n", encoding="utf-8")
    (out / f"description{tag}.txt").write_text(f"credits for {tag or '16x9'}",
                                               encoding="utf-8")
    (out / "youtube.json").write_text(
        json.dumps({"title": "A Video", "tags": ["t"]}), encoding="utf-8")
    return out


def _stub_upload(monkeypatch, sent, problems=()):
    """Nothing reaches the network, and `check` is asked nothing of ffprobe.

    `check` itself has its own tests against real files; what these need is a
    controlled verdict, so the command's decision is what is under test.
    """
    from vidsmith import check as check_mod
    from vidsmith import upload as real

    monkeypatch.setattr(check_mod, "check", lambda out: list(problems))
    monkeypatch.setattr(real, "access_token", lambda *a, **k: "tok")

    def _publish(cut, meta, description, token, **kw):
        sent.update(cut=cut, meta=meta, description=description, **kw)
        return "abcdefghijk"

    monkeypatch.setattr(real, "publish", _publish)


def test_the_command_refuses_to_upload_over_a_failing_check(tmp_path, monkeypatch, capsys):
    """Every fault `check` looks for is worse once published, and taking a video
    down does not unpublish it."""
    from vidsmith import cli

    root = _project(tmp_path)
    _deliver(root, "")
    sent = {}
    # a credit that never reached the description: a licence problem the moment
    # anyone can see the video
    _stub_upload(monkeypatch, sent,
                 problems=["credits.txt names Someone, description.txt does not"])

    code = cli.main(["upload", str(root)])

    assert code == 1 and not sent, "nothing left the machine"
    assert "nothing was uploaded" in capsys.readouterr().out


def test_force_uploads_over_the_problems_it_just_printed(tmp_path, monkeypatch):
    from vidsmith import cli

    root = _project(tmp_path)
    _deliver(root, "")
    sent = {}
    _stub_upload(monkeypatch, sent, problems=["a credit is missing"])

    assert cli.main(["upload", str(root), "--force"]) == 0
    assert sent["cut"].name == "a-video.mp4"


def test_the_vertical_upload_carries_the_vertical_description(tmp_path, monkeypatch):
    """The one rule of this command: the description, the thumbnail and the
    captions belong to the cut being uploaded. Reading them by their unsuffixed
    names is how a 9:16 upload names the widescreen cut's photographers."""
    from vidsmith import cli

    root = _project(tmp_path, aspect="9:16")
    _deliver(root, "")            # the widescreen cut is also sitting there
    _deliver(root, "-9x16")
    sent = {}
    _stub_upload(monkeypatch, sent)

    assert cli.main(["upload", str(root), "--aspect", "9:16"]) == 0

    assert sent["cut"].name == "a-video-9x16.mp4"
    assert sent["description"] == "credits for -9x16"
    assert sent["thumbnail"].name == "a-video-9x16.jpg"
    assert sent["captions"].name == "captions-9x16.srt"


def test_the_receipt_names_the_cut_the_command_uploaded(tmp_path, monkeypatch):
    from vidsmith import cli

    root = _project(tmp_path, aspect="9:16")
    out = _deliver(root, "-9x16")
    (out / "credits-9x16.txt").write_text("", encoding="utf-8")
    _stub_upload(monkeypatch, {})


    assert cli.main(["upload", str(root), "--aspect", "9:16"]) == 0

    body = json.loads((out / "published.json").read_text(encoding="utf-8"))
    assert set(body["files"]) == {"description-9x16.txt", "credits-9x16.txt"}


def test_private_is_the_default_privacy(tmp_path, monkeypatch):
    """The listing should be readable by its owner before it is readable by
    anyone else - that is the whole point of checking a published video."""
    from vidsmith import cli

    root = _project(tmp_path)
    _deliver(root, "")
    sent = {}
    _stub_upload(monkeypatch, sent)

    cli.main(["upload", str(root)])

    assert sent["privacy"] == "private"


# --------------------------------------------------------------------------- #
# the receipt
# --------------------------------------------------------------------------- #
def test_the_receipt_witnesses_the_cut_that_was_published(out):
    """A 9:16 upload writing down `description.txt` witnesses a file nobody
    published, and `check` would then report drift against the wrong one. Same
    empty-tag family as `check.delivered()` and `thumbs`."""
    from vidsmith.published import record

    record(out, "abcdefghijk", tag="-9x16")

    body = json.loads((out / "published.json").read_text(encoding="utf-8"))
    assert set(body["files"]) == {"description-9x16.txt", "credits-9x16.txt"}


def test_the_widescreen_receipt_keeps_its_unsuffixed_names(out):
    from vidsmith.published import record

    record(out, "abcdefghijk")

    body = json.loads((out / "published.json").read_text(encoding="utf-8"))
    assert set(body["files"]) == {"description.txt", "credits.txt"}
