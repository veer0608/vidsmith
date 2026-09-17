"""Bringing a finished render down off the live box.

Three downloads died mid-file in one evening while the server held a perfectly
good mp4 each time, so every case here is a connection that misbehaves rather
than a server that does.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import requests

from vidsmith import fetch as fetch_mod

JOB = "41de60d781d7"


class Reply:
    def __init__(self, body=None, status=200, content=b""):
        self.status_code, self._body, self.content = status, body, content

    def json(self):
        return self._body


class Instance:
    """The live box, and a line that drops the first `drops` reads of a file."""

    def __init__(self, status="done", outputs=("a.mp4", "captions.srt"), drops=0,
                 running_first=0):
        self.status, self.outputs, self.drops = status, list(outputs), drops
        self.running_first = running_first
        self.calls, self.slept = [], []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if url.endswith(f"/api/jobs/{JOB}"):
            if self.running_first:
                self.running_first -= 1
                return Reply({"status": "running", "stage": "visuals"})
            return Reply({"status": self.status, "error": "ffmpeg fell over",
                          "outputs": [{"name": n} for n in self.outputs]})
        if "/files/" in url:
            if self.drops:
                self.drops -= 1
                raise requests.ConnectionError("read timed out")
            return Reply(content=b"x" * 1000)
        raise AssertionError(url)

    def sleep(self, seconds):
        self.slept.append(seconds)

    def go(self, tmp_path, **kwargs):
        return fetch_mod.fetch(JOB, "box.example", "tok", tmp_path,
                               log=lambda *a: None, get=self.get, sleep=self.sleep,
                               **kwargs)


def test_a_finished_render_comes_down_whole(tmp_path):
    box = Instance()

    written = box.go(tmp_path)

    assert [p.name for p in written] == ["a.mp4", "captions.srt"]
    assert (tmp_path / "a.mp4").read_bytes() == b"x" * 1000
    assert not list(tmp_path.glob(".*.part")), "a part file was left behind"


def test_a_dropped_download_is_retried(tmp_path):
    box = Instance(drops=3)

    written = box.go(tmp_path)

    assert (tmp_path / "a.mp4").read_bytes() == b"x" * 1000
    assert box.slept, "it never waited between tries"
    assert len(written) == 2


def test_a_line_that_never_holds_is_a_failure(tmp_path):
    box = Instance(drops=99)

    with pytest.raises(fetch_mod.FetchFailed, match="gave up"):
        box.go(tmp_path)
    assert not list(tmp_path.iterdir()), "half a file is worse than none"


def test_it_waits_for_a_render_still_running(tmp_path):
    box = Instance(running_first=2)

    box.go(tmp_path)

    assert box.slept.count(fetch_mod.POLL) == 2


def test_no_wait_refuses_a_running_render(tmp_path):
    box = Instance(running_first=1)

    with pytest.raises(fetch_mod.FetchFailed, match="nothing to download yet"):
        box.go(tmp_path, wait=False)


def test_a_failed_render_says_why(tmp_path):
    box = Instance(status="failed")

    with pytest.raises(fetch_mod.FetchFailed, match="ffmpeg fell over"):
        box.go(tmp_path)


def test_a_refused_token_names_the_right_token(tmp_path):
    """The instance's token is not the one a local .env holds; that 401 cost a
    detour once already."""
    box = Instance()
    box.get = lambda url, headers=None, timeout=None: Reply(status=401)

    with pytest.raises(fetch_mod.FetchFailed, match="not the one in a local .env"):
        box.go(tmp_path)


def test_a_server_named_path_cannot_escape_the_folder(tmp_path):
    box = Instance(outputs=("../escaped.mp4", "a.mp4"))

    written = box.go(tmp_path)

    assert [p.name for p in written] == ["a.mp4"]
    assert not (tmp_path.parent / "escaped.mp4").exists()


def test_the_token_comes_from_the_environment_when_not_given():
    assert fetch_mod.job_token("", lambda name: '"abc"  ') == "abc"
    assert fetch_mod.job_token("explicit", lambda name: "abc") == "explicit"


def test_the_default_folder_is_named_for_the_job():
    assert fetch_mod.default_out(JOB) == Path("jobs") / JOB
