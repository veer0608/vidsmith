"""The line behind the one render slot.

One encode at a time is deliberate: two x264 runs on a small host starve each
other and neither finishes sooner. What was not deliberate is refusing the
second caller outright, so a submission now waits its turn.

The invariant every test here defends is the one that has already taken this
service down once: the slot is claimed in one place and handed back in one
place, and it is handed back on *every* path out. A slot released without
starting the next job looks exactly like the wedged instance it replaced,
except the work is sitting right there.
"""
from __future__ import annotations

import threading
import time

import pytest

from web import jobs as jobs_mod
from web.jobs import Jobs


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """A Jobs whose renders are a blocking stub rather than a real pipeline."""
    gate = threading.Event()
    started: list = []

    def fake_build(root, log=None):
        started.append(root.name)
        # hold the slot until a test lets go, so ordering is observable
        if not gate.wait(timeout=10):
            raise RuntimeError("the test never released the render")

    monkeypatch.setattr(jobs_mod.pipeline, "build", fake_build)
    q = Jobs(tmp_path / "jobs")
    q.gate, q.started = gate, started
    return q


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_the_first_submission_runs_immediately(queue):
    job = queue.submit("# t\n\nhello there", {})

    assert job.status == "running"
    assert queue.busy()
    assert queue.waiting() == 0
    queue.gate.set()


def test_a_second_submission_waits_instead_of_being_refused(queue):
    """The whole point. This used to raise Busy and cost the caller the work."""
    first = queue.submit("# t\n\nfirst script", {})
    second = queue.submit("# t\n\nsecond script", {})

    assert first.status == "running"
    assert second.status == "queued"
    assert queue.waiting() == 1
    assert queue.position(second.id) == 1
    queue.gate.set()


def test_the_queue_is_bounded(queue, monkeypatch):
    """Saturated, it answers as it did before there was a queue.

    An unbounded line would tell the tenth caller "queued" and leave them
    waiting half an hour, which is a worse answer than a refusal that says so.
    """
    monkeypatch.setattr(jobs_mod, "MAX_QUEUE", 1)
    queue.submit("# t\n\nrunning", {})
    queue.submit("# t\n\nwaiting", {})

    with pytest.raises(jobs_mod.Busy):
        queue.submit("# t\n\nrefused", {})
    queue.gate.set()


def test_a_waiting_job_starts_when_the_one_ahead_finishes(queue):
    first = queue.submit("# t\n\nfirst script", {})
    second = queue.submit("# t\n\nsecond script", {})

    queue.gate.set()
    # on the list rather than on the status: the slot is claimed and the job
    # marked running before its thread reaches the pipeline, so status flips
    # first and asserting on it would pass before the render had begun
    assert _wait_until(lambda: len(queue.started) == 2), queue.started
    assert _wait_until(lambda: first.status == "done"), first.status
    assert queue.started == [first.id, second.id], "the line ran out of order"


def test_cancelling_a_queued_job_stops_it_without_running_it(queue):
    """A job that has not started has no log callback to read a flag.

    The cooperative cancel path reads `cancel_requested` inside the pipeline's
    log callback. A queued job never reaches one, so asking it to stop that way
    would render it in full and then notice.
    """
    first = queue.submit("# t\n\nfirst script", {})
    second = queue.submit("# t\n\nsecond script", {})

    assert queue.cancel(second.id) == "cancelled"
    assert second.status == "cancelled"
    assert queue.waiting() == 0

    queue.gate.set()
    assert _wait_until(lambda: first.status == "done")
    assert second.id not in queue.started, "a cancelled job was rendered anyway"


def test_a_cancelled_job_does_not_hold_up_the_one_behind_it(queue):
    first = queue.submit("# t\n\nfirst", {})
    second = queue.submit("# t\n\nsecond", {})
    third = queue.submit("# t\n\nthird", {})

    queue.cancel(second.id)
    queue.gate.set()

    assert _wait_until(lambda: len(queue.started) == 2), queue.started
    assert _wait_until(lambda: third.status == "done"), third.status
    assert queue.started == [first.id, third.id]


def test_the_slot_comes_back_when_a_render_fails(queue, monkeypatch):
    """Failure is a path out of the run, so it owes the slot back like any other."""
    def explode(root, log=None):
        raise RuntimeError("the encode died")

    monkeypatch.setattr(jobs_mod.pipeline, "build", explode)
    first = queue.submit("# t\n\nfirst", {})

    assert _wait_until(lambda: first.status == "failed"), first.status
    assert not queue.busy(), "the slot was never handed back"

    # Not `== "running"`: this stub fails instantly, so on a quick runner the
    # thread is done before the next line reads the status, and the assertion
    # is a footrace rather than a test. What must hold is that the job was
    # claimed rather than left waiting, and that it actually ran.
    second = queue.submit("# t\n\nsecond", {})
    assert second.status != "queued", "the slot was not free for the next job"
    assert _wait_until(lambda: second.status == "failed"), second.status


def test_a_failed_setup_claims_nothing(queue, monkeypatch):
    """The wedge that actually happened, from the other side.

    Writing the job directory used to come *after* the slot was claimed, so an
    unwritable VIDSMITH_JOBS held the one slot for the life of the process and
    429'd every later caller for a render that had never started. Nothing is
    claimed until the writing is done, so a failure here owes nothing back.
    """
    def unwritable(self, root, options):
        raise OSError("read-only file system")

    monkeypatch.setattr(Jobs, "_write_config", unwritable)
    with pytest.raises(OSError):
        queue.submit("# t\n\nfirst", {})

    assert not queue.busy()
    assert queue.waiting() == 0


def test_position_counts_down_as_the_line_moves(queue):
    queue.submit("# t\n\nfirst", {})
    second = queue.submit("# t\n\nsecond", {})
    third = queue.submit("# t\n\nthird", {})

    assert queue.position(second.id) == 1
    assert queue.position(third.id) == 2
    # a running job is not in the line, and neither is one that never joined it
    assert queue.position(queue._active) == 0
    queue.gate.set()


def test_a_snapshot_carries_the_position(queue):
    queue.submit("# t\n\nfirst", {})
    second = queue.submit("# t\n\nsecond", {})

    body = queue.snapshot(second.id)
    assert body["status"] == "queued"
    assert body["position"] == 1
    assert body["waiting"] == 1
    assert queue.snapshot("no such job") is None
    queue.gate.set()
