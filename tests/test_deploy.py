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

    def __init__(self, live="0000000", busy=None, ssh_code=0, ssh_err="", lands=True):
        self.live, self.busy = live, list(busy or [{"busy": False, "waiting": 0}])
        self.ssh_code, self.ssh_err, self.lands = ssh_code, ssh_err, lands
        self.ssh_calls: List[List[str]] = []
        self.log: List[str] = []
        self.clock = 0.0

    def run(self, args, **kwargs):
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, f"{TARGET}\trefs/heads/main\n", "")
        self.ssh_calls.append(args)
        if self.ssh_code == 0 and self.lands:
            self.live = TARGET[:7]
        return subprocess.CompletedProcess(args, self.ssh_code,
                                           "ip-172-31-12-94\nabc1234 a commit\nactive\n",
                                           self.ssh_err)

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
    assert "security group" in message and "203.0.113.9" in message


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
