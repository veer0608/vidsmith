"""The whole delivery in one download.

The page has always listed the srt, the credits and the description beside the
mp4. People take the mp4 and leave, which is not a UI quibble: attribution is a
licence condition carried in `credits*.txt` and folded into `description.txt`,
and jobs are swept an hour after they finish. One real download went out as the
video alone and the credits were recovered off the box by hand.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from web.jobs import Jobs                            # noqa: E402


def _delivered(root: Path) -> None:
    """A finished job's out/, in the shape the pipeline leaves it."""
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "a-video.mp4").write_bytes(b"wide")
    (out / "a-video-9x16.mp4").write_bytes(b"tall")
    (out / "a-video.jpg").write_bytes(b"thumb")
    (out / "captions.srt").write_text("1\n", encoding="utf-8")
    (out / "credits.txt").write_text("Someone - https://example.com\n", encoding="utf-8")
    (out / "description.txt").write_text("body\n", encoding="utf-8")


def _job(tmp_path: Path):
    from web.jobs import Job

    jobs = Jobs(tmp_path / "jobs")
    root = tmp_path / "jobs" / "j1"
    _delivered(root)
    jobs._jobs["j1"] = Job(id="j1", status="done", root=root, title="A Video")
    return jobs


def test_the_archive_carries_every_delivered_file(tmp_path):
    jobs = _job(tmp_path)
    zip_path = jobs.archive("j1")

    assert zip_path is not None
    with zipfile.ZipFile(zip_path) as bundle:
        names = sorted(Path(n).name for n in bundle.namelist())

    assert names == ["a-video-9x16.mp4", "a-video.jpg", "a-video.mp4",
                     "captions.srt", "credits.txt", "description.txt"]


def test_the_credits_are_actually_in_it(tmp_path):
    """The file this whole change exists for."""
    jobs = _job(tmp_path)
    with zipfile.ZipFile(jobs.archive("j1")) as bundle:
        member = next(n for n in bundle.namelist() if n.endswith("credits.txt"))
        assert b"example.com" in bundle.read(member)


def test_it_is_named_after_the_cut_rather_than_the_title(tmp_path):
    """A second slugger is how `thumbs --refresh` wrote `untitled.jpg`.

    The build already chose a name and it is on disk; the title is resolved
    separately and has been out of step before.
    """
    jobs = _job(tmp_path)
    assert jobs.archive("j1").name == "a-video.zip"


def test_the_zip_does_not_land_in_out(tmp_path):
    """Inside out/ it would be collected as an output, offered as a download of
    itself, and reported by `vidsmith check` as matching no delivered cut."""
    jobs = _job(tmp_path)
    zip_path = jobs.archive("j1")

    assert zip_path.parent.name == "j1"
    assert not list((tmp_path / "jobs" / "j1" / "out").glob("*.zip"))
    assert not list(zip_path.parent.glob("*.part"))


def test_it_is_rebuilt_when_a_file_changes_under_it(tmp_path):
    """`thumbs --refresh` rewrites files after the render; a cached zip would
    hand back the credits it had just corrected."""
    import os
    import time

    jobs = _job(tmp_path)
    first = jobs.archive("j1")
    before = first.stat().st_mtime

    out = tmp_path / "jobs" / "j1" / "out"
    time.sleep(0.01)
    (out / "credits.txt").write_text("Someone Else - https://other.test\n",
                                     encoding="utf-8")
    os.utime(out / "credits.txt", (time.time() + 5, time.time() + 5))

    with zipfile.ZipFile(jobs.archive("j1")) as bundle:
        member = next(n for n in bundle.namelist() if n.endswith("credits.txt"))
        assert b"other.test" in bundle.read(member)
    assert jobs.archive("j1").stat().st_mtime >= before


def test_an_empty_job_has_nothing_to_download(tmp_path):
    from web.jobs import Job

    jobs = Jobs(tmp_path / "jobs")
    root = tmp_path / "jobs" / "empty"
    (root / "out").mkdir(parents=True)
    jobs._jobs["empty"] = Job(id="empty", status="running", root=root)

    assert jobs.archive("empty") is None
    assert jobs.archive("no-such-job") is None


def test_the_route_serves_it_as_an_attachment(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from web import app as web_app

    jobs = _job(tmp_path)
    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "jobs", jobs)
    client = TestClient(web_app.app)

    r = client.get("/api/jobs/j1/archive")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(r.content)) as bundle:
        assert any(n.endswith("credits.txt") for n in bundle.namelist())

    assert client.get("/api/jobs/nope/archive").status_code == 404
