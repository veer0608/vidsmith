"""Deploying main to the live box, with nothing real touched.

Each case is a way a deploy here has already gone wrong: a restart during
someone's render, an ssh timeout read as a dead box, and a deploy that reported
success while the box went on running the old commit.
"""
from __future__ import annotations

import subprocess
from typing import Any, Dict, List

import pytest

from vidsmith import deploy

TARGET = "abc1234def"


class Reply:
    def __init__(self, body: Any = None, status: int = 200, text: str = ""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


class Box:
    """The live instance: what its endpoints say, and what ssh does to it."""

    READY = ('{"configured": true, "connected": true, '
             '"redirect_uri": "https://box.example/api/youtube/callback"}')

    def __init__(self, live="0000000", busy=None, ssh_code=0, ssh_err="", lands=True,
                 youtube=READY):
        self.live, self.busy = live, list(busy or [{"busy": False, "waiting": 0}])
        self.ssh_code, self.ssh_err, self.lands = ssh_code, ssh_err, lands
        self.youtube = youtube
        self.ssh_calls: List[List[str]] = []
        self.log: List[str] = []
        self.clock = 0.0

    def run(self, args, **kwargs):
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, f"{TARGET}\trefs/heads/main\n", "")
        self.ssh_calls.append(args)
        if self.ssh_code == 0 and self.lands:
            self.live = TARGET[:7]
        out = "ip-172-31-12-94\nabc1234 a commit\nactive\n"
        if self.youtube is not None:
            out += f"youtube:{self.youtube}\n"
        return subprocess.CompletedProcess(args, self.ssh_code, out, self.ssh_err)

    def get(self, url, timeout=0):
        if url == deploy.CHECK_IP:
            return Reply(text="203.0.113.9\n")
        if url.endswith("/healthz"):
            return Reply({"ok": True, "commit": self.live,
                          "fonts": ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]})
        if url.endswith("/api/busy"):
            state = self.busy.pop(0) if len(self.busy) > 1 else self.busy[0]
            return Reply(state)
        raise AssertionError(url)

    def sleep(self, seconds):
        self.clock += seconds

    def go(self, **kwargs) -> str:
        return deploy.deploy(host="box.example", key="k.pem", log=self.log.append,
                             run=self.run, get=self.get, sleep=self.sleep,
                             clock=lambda: self.clock, **kwargs)


def test_a_deploy_pulls_restarts_and_waits_for_the_public_url_to_agree():
    box = Box()

    assert box.go() == TARGET[:7]

    assert len(box.ssh_calls) == 1
    command = box.ssh_calls[0][-1]
    assert command.startswith("hostname;"), "the output must say which machine it ran on"
    assert "git pull --ff-only" in command and "systemctl restart vidsmith" in command
    assert "BatchMode=yes" in box.ssh_calls[0], "a password prompt would hang it"
    assert any("ran on ip-172-31-12-94" in line for line in box.log)


def test_nothing_happens_when_main_is_already_live():
    box = Box(live=TARGET[:7])
    box.go()
    assert not box.ssh_calls


def test_a_running_render_is_never_restarted_under():
    box = Box(busy=[{"busy": True, "stage": "encoding", "waiting": 1}])

    with pytest.raises(deploy.DeployFailed, match="a render is running"):
        box.go()
    assert not box.ssh_calls


def test_wait_lets_the_render_finish_first():
    box = Box(busy=[{"busy": True, "stage": "encoding"}, {"busy": True}, {"busy": False, "waiting": 0}])
    box.go(wait_minutes=5)
    assert box.ssh_calls and box.clock >= 30


def test_an_ssh_timeout_names_the_firewall_and_the_current_address():
    box = Box(ssh_code=255, ssh_err="ssh: connect to host box.example port 22: Connection timed out")

    with pytest.raises(deploy.DeployFailed) as caught:
        box.go()

    message = str(caught.value)
    assert "security group" in message and "203.0.113.9/32" in message


def test_an_ssh_timeout_links_the_security_groups_in_the_right_region():
    """The console opened on Global shows no security groups, and a search for
    "security" lands on IAM; both happened while the rule waited."""
    box = Box(ssh_code=255, ssh_err="ssh: connect to host box.example port 22: Connection timed out")

    with pytest.raises(deploy.DeployFailed) as caught:
        box.go()

    message = str(caught.value)
    assert ("https://ap-south-1.console.aws.amazon.com/ec2/home"
            "?region=ap-south-1#SecurityGroups:") in message
    assert "not IAM" in message


def test_the_region_can_be_moved_with_the_box():
    assert "region=eu-west-1" in deploy.security_groups_url("eu-west-1")


@pytest.mark.parametrize("stderr, says", [
    ("ssh: connect to host box port 22: Connection refused", "sshd is not running"),
    ("ubuntu@box: Permission denied (publickey).", "rejected the key"),
    ("Warning: Identity file k.pem not accessible: No such file or directory.", "is not there"),
])
def test_other_ssh_failures_are_told_apart(stderr, says):
    with pytest.raises(deploy.DeployFailed, match=says):
        Box(ssh_code=255, ssh_err=stderr).go()


def test_a_restart_that_leaves_the_old_commit_running_is_a_failure():
    """The deploy that reports success and changes nothing."""
    box = Box(lands=False)

    with pytest.raises(deploy.DeployFailed, match="not abc1234"):
        box.go()
    assert box.clock >= 120, "it waited for the restart before calling it"


def test_the_command_is_on_the_cli():
    from vidsmith import cli

    assert cli.cmd_deploy.__doc__


def test_a_dropped_connection_after_the_restart_is_retried_not_a_failure():
    """A slow connection timed out on /api/busy seconds after /healthz had
    confirmed the new commit, and a deploy that had worked reported failure."""
    box = Box()
    misses = {"n": 2}
    honest = box.get

    def flaky(url, timeout=0):
        if url.endswith("/api/busy") and misses["n"]:
            misses["n"] -= 1
            raise deploy.requests.ConnectionError("read timed out")
        return honest(url, timeout=timeout)

    box.get = flaky

    assert box.go() == TARGET[:7]
    assert misses["n"] == 0, "the retries never happened"
    assert any("live:" in line for line in box.log)


def test_an_endpoint_that_never_answers_is_still_a_failure():
    box = Box()
    honest = box.get

    def dead(url, timeout=0):
        if url.endswith("/api/busy"):
            raise deploy.requests.ConnectionError("read timed out")
        return honest(url, timeout=timeout)

    box.get = dead

    with pytest.raises(deploy.DeployFailed, match="did not answer with a waiting count"):
        box.go()


# --------------------------------------------------------------------------- #
# uploading from the page
# --------------------------------------------------------------------------- #
def test_a_deploy_says_whether_the_page_can_upload():
    """The box ran for weeks with no YouTube client and nothing said so. The
    fault would have surfaced as redirect_uri_mismatch in front of whoever
    first tried to publish from the page."""
    box = Box()

    box.go()

    assert any("uploading from the page: ready" in line for line in box.log), box.log
    command = box.ssh_calls[0][-1]
    assert "127.0.0.1:8077/api/youtube" in command, "asked over loopback, on the box"
    assert "VIDSMITH_TOKEN" in command, "the token is read there, never sent from here"


def test_a_box_with_no_client_is_reported_not_refused():
    """A box that does no uploading is a legitimate box."""
    box = Box(youtube='{"configured": false, "connected": false, "redirect_uri": ""}')

    assert box.go() == TARGET[:7]
    assert any("no YouTube client on the box" in line for line in box.log), box.log


def test_a_client_that_redirects_somewhere_else_fails_the_deploy():
    """Configured against the wrong host is the silent half: consent is refused
    at Google, after the render is paid for."""
    box = Box(youtube='{"configured": true, "connected": false, '
                      '"redirect_uri": "http://127.0.0.1:53682"}')

    with pytest.raises(deploy.DeployFailed, match="not https://box.example"):
        box.go()


def test_a_configured_box_that_nobody_connected_says_so():
    box = Box(youtube='{"configured": true, "connected": false, '
                      '"redirect_uri": "https://box.example/api/youtube/callback"}')

    box.go()

    assert any("not connected yet" in line for line in box.log), box.log


def test_a_box_that_did_not_answer_is_not_a_failure():
    box = Box(youtube=None)

    assert box.go() == TARGET[:7]
    assert any("did not answer" in line for line in box.log), box.log


def test_the_probe_asks_as_the_public_host():
    """The first real run failed a healthy box: /api/youtube builds the redirect
    from the request, so a bare loopback call answers 127.0.0.1:8077."""
    box = Box()

    box.go()

    command = box.ssh_calls[0][-1]
    assert 'Host: box.example' in command
    assert "X-Forwarded-Proto: https" in command


def test_the_service_line_is_not_the_probe_output():
    """`ran on ...; service active` reads the last line, and the probe now
    prints after it."""
    box = Box()

    box.go()

    [line] = [l for l in box.log if l.startswith("ran on")]
    assert line.endswith("service active"), line
