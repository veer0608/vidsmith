"""scripts/autodeploy.sh, run for real against a throwaway origin.

The script restarts a live service, so what it must not do matters more than
what it does: move a checkout on another branch, move a diverged one, or restart
in the middle of a render. `curl`, `sudo` and pip are stubs, so the box's API and
systemctl are whatever each test says they are, and every restart is logged.

The stub service behaves like the real one in the way that matters here: the
commit `/healthz` reports changes only when it is restarted, not when the
checkout moves underneath it. A run that pulls and then backs off for a render
leaves exactly that state, and the next run must still restart into it.

Skipped on Windows: `bash` there can resolve to WSL's launcher, which has no
distribution on CI, and the script only ever runs on the Linux box.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"),
    reason="runs the deploy script under a POSIX bash and git")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "autodeploy.sh"
IDLE = '{"busy":false,"waiting":0}'
BUSY = '{"busy":true,"waiting":1,"stage":"render"}'


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class Box:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.origin = tmp_path / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "main")
        git(self.origin, "config", "user.email", "t@example.com")
        git(self.origin, "config", "user.name", "t")
        (self.origin / "scripts").mkdir()
        (self.origin / "scripts" / "fetch-runtime-deps.sh").write_text("exit 0\n")
        (self.origin / "requirements.txt").write_text("a\n")
        (self.origin / "requirements-web.txt").write_text("-r requirements.txt\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", "one")

        self.app = tmp_path / "app"
        git(tmp_path, "clone", "-q", str(self.origin), str(self.app))
        git(self.app, "config", "user.email", "t@example.com")
        git(self.app, "config", "user.name", "t")
        (self.app / ".venv" / "bin").mkdir(parents=True)

        self.pip_log = tmp_path / "pip.log"
        self.pip_fails = tmp_path / "pip.fails"
        pip = self.app / ".venv" / "bin" / "pip"
        pip.write_text(f"#!/bin/sh\necho \"$@\" >> '{self.pip_log}'\n"
                       f"[ -f '{self.pip_fails}' ] && exit 1\nexit 0\n")
        pip.chmod(0o755)

        # the service: what it is running changes only on a restart
        self.running = tmp_path / "running"
        self.running.write_text(self.head()[:7])
        # /api/busy answers one line per call from this file, then repeats the last
        self.busy = tmp_path / "busy"
        self.busy.write_text(IDLE + "\n")
        self.sudo_log = tmp_path / "sudo.log"

        stubs = tmp_path / "stubs"
        stubs.mkdir()
        (stubs / "sudo").write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" >> '{self.sudo_log}'\n"
            "case \"$*\" in *'restart vidsmith'*)\n"
            f"  git -C '{self.app}' rev-parse --short=7 HEAD > '{self.running}' ;;\n"
            "esac\n")
        (stubs / "curl").write_text(
            "#!/bin/sh\n"
            "for a in \"$@\"; do url=\"$a\"; done\n"
            "case \"$url\" in\n"
            f"  */healthz) printf '{{\"ok\":true,\"commit\":\"%s\"}}' \"$(cat '{self.running}')\" ;;\n"
            f"  */api/busy) head -1 '{self.busy}';\n"
            f"    if [ \"$(wc -l < '{self.busy}')\" -gt 1 ]; then\n"
            f"      tail -n +2 '{self.busy}' > '{self.busy}.next'; mv '{self.busy}.next' '{self.busy}'; fi ;;\n"
            "esac\n")
        (stubs / "sleep").write_text("#!/bin/sh\nexit 0\n")
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        self.stubs = stubs
        self.state = tmp_path / "state" / "_autodeploy.json"

    def head(self, where: Path = None) -> str:
        return git(where or self.origin, "rev-parse", "HEAD")

    def push(self, files=None) -> str:
        for path, text in (files or {"change.txt": str(len(os.listdir(self.origin)))}).items():
            (self.origin / path).write_text(text)
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "-m", "next")
        return self.head()

    def answers(self, *busy: str) -> None:
        self.busy.write_text("\n".join(busy) + "\n")

    def run(self):
        env = {**os.environ, "PATH": f"{self.stubs}{os.pathsep}{os.environ['PATH']}",
               "VIDSMITH_APP_DIR": str(self.app), "VIDSMITH_LOCAL_URL": "http://box",
               "VIDSMITH_AUTODEPLOY_STATE": str(self.state)}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)

    def restarts(self) -> int:
        return self.sudo_log.read_text().count("restart vidsmith") if self.sudo_log.exists() else 0

    def recorded(self) -> dict:
        return json.loads(self.state.read_text())


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def test_nothing_new_does_nothing_and_records_nothing(box):
    result = box.run()
    assert result.returncode == 0 and result.stdout == ""
    assert box.restarts() == 0
    assert not box.state.exists()


def test_a_new_commit_is_pulled_restarted_confirmed_and_recorded(box):
    sha = box.push()
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert box.head(box.app) == sha
    assert box.restarts() == 1
    assert box.running.read_text().strip() == sha[:7]
    state = box.recorded()
    assert state["state"] == "deployed" and state["ok"] is True
    assert state["commit"] == sha[:7] and sha[:7] in state["said"]
    assert not box.pip_log.exists()          # requirements did not move


def test_a_running_render_is_never_restarted(box):
    box.answers(BUSY)
    before = box.head(box.app)
    box.push()
    result = box.run()
    assert result.returncode == 0 and "render is running" in result.stdout
    assert box.head(box.app) == before
    assert box.restarts() == 0
    assert box.recorded()["state"] == "waiting"


def test_a_render_that_starts_during_the_pull_is_not_restarted_under(box):
    """Idle before the pull, busy by the restart: back off, owe the restart."""
    sha = box.push()
    box.answers(IDLE, BUSY)
    first = box.run()
    assert first.returncode == 0, first.stderr
    assert box.head(box.app) == sha, "the code was pulled"
    assert box.restarts() == 0, "but the render was not killed"
    assert box.recorded()["state"] == "waiting"

    # the checkout is on main now and the service is not: the next idle run
    # must restart, not call it "nothing new"
    box.answers(IDLE)
    second = box.run()
    assert second.returncode == 0, second.stderr
    assert box.restarts() == 1
    assert box.running.read_text().strip() == sha[:7]
    assert box.recorded()["state"] == "deployed"


def test_a_service_that_does_not_answer_is_still_deployed(box):
    box.answers("")                          # down is not rendering
    box.running.write_text("")
    sha = box.push()
    assert box.run().returncode == 0
    assert box.head(box.app) == sha


def test_a_service_that_is_down_on_current_code_is_left_to_systemd(box):
    box.running.write_text("")
    result = box.run()
    assert result.returncode == 0 and box.restarts() == 0


def test_changed_requirements_are_installed(box):
    box.push(files={"requirements.txt": "a\nb\n"})
    assert box.run().returncode == 0
    assert "requirements-web.txt" in box.pip_log.read_text()


def test_a_failed_install_is_recorded_not_only_logged(box):
    box.pip_fails.write_text("")
    box.push(files={"requirements.txt": "a\nb\n"})
    result = box.run()
    assert result.returncode != 0
    assert box.restarts() == 0
    state = box.recorded()
    assert state["state"] == "failed" and state["ok"] is False
    assert "pip install" in state["said"]


def test_another_branch_is_left_alone(box):
    git(box.app, "checkout", "-q", "-b", "trying-something")
    box.push()
    result = box.run()
    assert result.returncode == 0 and "trying-something" in result.stdout
    assert box.restarts() == 0


def test_a_diverged_main_is_refused_and_recorded(box):
    (box.app / "local.txt").write_text("edited on the box")
    git(box.app, "add", "-A")
    git(box.app, "commit", "-q", "-m", "local")
    box.push()
    result = box.run()
    assert result.returncode == 1 and "diverged" in result.stdout
    assert box.restarts() == 0
    state = box.recorded()
    assert state["state"] == "refused" and "diverged" in state["said"]
