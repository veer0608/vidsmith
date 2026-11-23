"""scripts/autodeploy.sh, run for real against a throwaway origin.

The script restarts a live service, so what it must not do matters more than
what it does: move a checkout on another branch, move a diverged one, or restart
in the middle of a render. `curl` and `sudo` are stubs on PATH, so the box's API
and systemctl are whatever each test says they are, and every restart is logged.

Skipped on Windows: `bash` there can resolve to WSL's launcher, which has no
distribution on CI, and the script only ever runs on the Linux box.
"""
from __future__ import annotations

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


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def box(tmp_path):
    """An origin with one commit, the box's clone of it, and stubbed tools."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "t")
    (origin / "scripts").mkdir()
    (origin / "scripts" / "fetch-runtime-deps.sh").write_text("exit 0\n")
    (origin / "requirements.txt").write_text("a\n")
    (origin / "requirements-web.txt").write_text("-r requirements.txt\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "one")

    app = tmp_path / "app"
    git(tmp_path, "clone", "-q", str(origin), str(app))
    (app / ".venv" / "bin").mkdir(parents=True)
    pip_log = tmp_path / "pip.log"
    pip = app / ".venv" / "bin" / "pip"
    pip.write_text(f"#!/bin/sh\necho \"$@\" >> '{pip_log}'\n")
    pip.chmod(0o755)

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    sudo_log = tmp_path / "sudo.log"
    (stubs / "sudo").write_text(f"#!/bin/sh\necho \"$@\" >> '{sudo_log}'\n")
    # /api/busy answers from a file; /healthz reports whatever app's HEAD is,
    # as the restarted process would
    busy = tmp_path / "busy.json"
    busy.write_text('{"busy":false,"waiting":0}')
    (stubs / "curl").write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do url=\"$a\"; done\n"
        "case \"$url\" in\n"
        f"  */api/busy) cat '{busy}' ;;\n"
        f"  */healthz) printf '{{\"ok\":true,\"commit\":\"%s\"}}' "
        f"\"$(git -C '{app}' rev-parse --short=7 HEAD)\" ;;\n"
        "esac\n")
    (stubs / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for stub in stubs.iterdir():
        stub.chmod(0o755)

    def push(name: str = "two", files=None) -> str:
        for path, text in (files or {"change.txt": name}).items():
            (origin / path).write_text(text)
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", name)
        return git(origin, "rev-parse", "HEAD")

    def run():
        env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
               "VIDSMITH_APP_DIR": str(app), "VIDSMITH_LOCAL_URL": "http://box"}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)

    def restarts() -> int:
        return sudo_log.read_text().count("restart vidsmith") if sudo_log.exists() else 0

    class Box:
        pass

    b = Box()
    b.app, b.busy, b.push, b.run, b.restarts, b.pip_log = app, busy, push, run, restarts, pip_log
    return b


def test_nothing_new_does_nothing(box):
    result = box.run()
    assert result.returncode == 0 and result.stdout == ""
    assert box.restarts() == 0


def test_a_new_commit_is_pulled_restarted_and_confirmed(box):
    sha = box.push()
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert git(box.app, "rev-parse", "HEAD") == sha
    assert box.restarts() == 1
    assert "deployed" in result.stdout and sha[:7] in result.stdout
    assert not box.pip_log.exists()          # requirements did not move


def test_a_running_render_is_never_restarted(box):
    box.busy.write_text('{"busy":true,"waiting":1,"stage":"render"}')
    before = git(box.app, "rev-parse", "HEAD")
    box.push()
    result = box.run()
    assert result.returncode == 0 and "render is running" in result.stdout
    assert git(box.app, "rev-parse", "HEAD") == before
    assert box.restarts() == 0


def test_a_service_that_does_not_answer_is_still_deployed(box):
    box.busy.write_text("")                  # down is not rendering
    sha = box.push()
    assert box.run().returncode == 0
    assert git(box.app, "rev-parse", "HEAD") == sha


def test_changed_requirements_are_installed(box):
    box.push(files={"requirements.txt": "a\nb\n"})
    assert box.run().returncode == 0
    assert "requirements-web.txt" in box.pip_log.read_text()


def test_another_branch_is_left_alone(box):
    git(box.app, "checkout", "-q", "-b", "trying-something")
    box.push()
    result = box.run()
    assert result.returncode == 0 and "trying-something" in result.stdout
    assert box.restarts() == 0


def test_a_diverged_main_is_not_touched(box):
    git(box.app, "config", "user.email", "t@example.com")
    git(box.app, "config", "user.name", "t")
    (box.app / "local.txt").write_text("edited on the box")
    git(box.app, "add", "-A")
    git(box.app, "commit", "-q", "-m", "local")
    box.push()
    result = box.run()
    assert result.returncode == 1 and "diverged" in result.stdout
    assert box.restarts() == 0
