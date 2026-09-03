"""Which commit a running process is on.

The live box had drifted six commits behind `main` and nothing said so: both
endpoints `deploy/aws.md` calls the honest witnesses were green against stale
code, and "did the deploy land" needed an SSH session and `git log`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vidsmith import build_info

SHA = "d457be3a1c0f4e2b9a8d7c6e5f4a3b2c1d0e9f8a"


def _repo(tmp_path: Path, head: str) -> Path:
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git" / "HEAD").write_text(head, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _uncached(monkeypatch):
    """The real answer is resolved once per process, which is the point of it."""
    monkeypatch.setattr(build_info, "_resolved", False)
    monkeypatch.setattr(build_info, "_cached", None)


def test_a_loose_ref_is_followed(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "ref: refs/heads/main\n")
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "refs" / "heads" / "main").write_text(SHA + "\n", encoding="utf-8")
    monkeypatch.setattr(build_info, "_REPO", repo)

    assert build_info.commit() == SHA[:7]
    assert build_info.commit(short=False) == SHA


def test_a_fresh_clone_keeps_its_refs_packed(tmp_path, monkeypatch):
    """The deployed box is a clone, and a clone has no loose ref for main.

    Reading only `.git/refs/heads/<branch>` returns nothing there, which would
    have made the field silently empty on the one machine it exists for.
    """
    repo = _repo(tmp_path, "ref: refs/heads/main\n")
    (repo / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{SHA} refs/heads/main\n"
        "0000000000000000000000000000000000000000 refs/remotes/origin/other\n",
        encoding="utf-8")
    monkeypatch.setattr(build_info, "_REPO", repo)

    assert build_info.commit() == SHA[:7]


def test_a_detached_head_holds_the_sha_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, "_REPO", _repo(tmp_path, SHA + "\n"))
    assert build_info.commit() == SHA[:7]


@pytest.mark.parametrize("head", ["ref: refs/heads/gone\n", "not a sha\n", ""])
def test_anything_unresolvable_is_empty_rather_than_a_sentinel(tmp_path, monkeypatch,
                                                              head):
    """Empty, not "unknown": a caller deciding whether to show the field should
    not have to know which magic string means absent."""
    monkeypatch.setattr(build_info, "_REPO", _repo(tmp_path, head))
    assert build_info.commit() == ""


def test_a_pip_install_is_not_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, "_REPO", tmp_path)     # no .git at all
    assert build_info.commit() == ""


def test_it_resolves_once(tmp_path, monkeypatch):
    """`/healthz` is what an uptime check polls, so this must not read the disk
    per request, and the answer cannot change without a restart."""
    repo = _repo(tmp_path, SHA + "\n")
    monkeypatch.setattr(build_info, "_REPO", repo)

    assert build_info.commit() == SHA[:7]
    (repo / ".git" / "HEAD").unlink()
    assert build_info.commit() == SHA[:7], "it went back to the disk"


def test_healthz_reports_it(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from web import app as web_app
    from web.jobs import Jobs

    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    body = TestClient(web_app.app).get("/healthz").json()

    assert "commit" in body, "the field a deploy check reads must always be there"


def test_the_commit_is_not_behind_the_token(tmp_path, monkeypatch):
    """`keys` is gated because it inventories credentials. A commit sha is not a
    secret - the repo is public - and gating it would defeat the purpose, since
    a deploy check that needs the token is the thing being replaced."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from web import app as web_app
    from web.jobs import Jobs

    monkeypatch.setattr(web_app, "TOKEN", "a-secret")
    monkeypatch.setattr(web_app, "jobs", Jobs(tmp_path / "jobs"))
    body = TestClient(web_app.app).get("/healthz").json()

    assert "commit" in body
    assert "keys" not in body, "the guarded field is still guarded"
