"""One render at a time, tracked well enough to watch it happen.

A video takes minutes, not milliseconds, so nothing here is request-scoped: the
POST creates a job and returns, the browser polls, and the work happens on a
worker thread. The pipeline already reports each stage through a log callback,
so progress is real rather than a spinner.

Deliberately single-process and in-memory. Two concurrent x264 encodes will
exhaust a small host, so exactly one render runs at a time. That is about the
encode, not about the caller: a second submission waits in line rather than
being refused, because the box could always have taken the work, only not that
minute. Running two would not make either finish sooner - x264 already threads
across the cores, so concurrency here buys nothing and doubles peak memory.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import yaml

from vidsmith import pipeline
from vidsmith.config import Config, write_default_config

# stage -> fraction of the run that is behind you once it starts.
#
# Measured from a real build rather than guessed. The guesses had `render`
# spanning 0.88 to 0.96, eight percent of the run; it is closer to forty, so
# the bar sat at 88% for most of the wait and then jumped. Finding footage and
# encoding are the two stages that cost anything and they are roughly equal.
#
# One sample on one machine, and the split moves with the host: a box with half
# the cores spends proportionally longer encoding, so `render` is wider there
# than this says. Roughly right beats wrong by a factor of five, but do not
# read these as exact.
STAGE_PROGRESS = {
    "script": 0.0, "queries": 0.02, "voice": 0.03, "visuals": 0.10,
    "captions": 0.55, "music": 0.57, "render": 0.58, "credits": 0.97,
    "meta": 0.99, "done": 1.0,
}
KEEP_SECONDS = 60 * 60          # finished jobs are swept after an hour
MAX_SCRIPT_CHARS = 12_000

# How many submissions may wait behind the running one. Bounded because the
# wait is the thing being promised: with an unbounded line the tenth caller is
# told "queued" and waits half an hour, which is a worse answer than 429 and a
# clear reason. Saturated, the service behaves as it did before there was a
# queue at all.
MAX_QUEUE = max(0, int(os.environ.get("VIDSMITH_MAX_QUEUE", "3")))


STAGE_LABELS = {
    "script": "reading the script", "queries": "choosing b-roll",
    "voice": "recording narration", "visuals": "finding footage",
    "captions": "timing captions", "music": "scoring", "render": "encoding",
    "credits": "crediting", "meta": "writing the description",
}


def stage_sequence() -> List[Dict[str, str]]:
    """The stages in the order they happen, for a page drawing a stepper.

    Ordered by how much of the run is behind you, so it stays correct when a
    stage is added: the page must not keep its own copy of this list.
    """
    return [{"key": key, "label": STAGE_LABELS[key]}
            for key, _ in sorted(STAGE_PROGRESS.items(), key=lambda kv: kv[1])
            if key in STAGE_LABELS]


@dataclass
class Job:
    id: str
    status: str = "queued"          # queued | running | done | failed | cancelled
    stage: str = ""
    cancel_requested: bool = False
    progress: float = 0.0
    log: List[str] = field(default_factory=list)
    error: str = ""
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    title: str = ""
    created: float = field(default_factory=time.time)
    finished: float = 0.0
    root: Optional[Path] = None
    # carried so a job that waits can be started later by the worker that
    # finishes ahead of it, rather than by the request that submitted it
    options: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        end = self.finished or time.time()
        return {
            "id": self.id, "status": self.status,
            "stage": STAGE_LABELS.get(self.stage, self.stage),
            "elapsed": round(end - self.created, 1),
            "progress": round(self.progress, 3), "log": self.log[-60:],
            "error": self.error, "outputs": self.outputs, "title": self.title,
            "created": datetime.fromtimestamp(self.created, timezone.utc).isoformat(),
            # the stop was asked for but the current stage has not returned yet,
            # so the page can say "stopping" rather than appearing to ignore it
            "cancelling": self.cancel_requested and self.status == "running",
        }


class Busy(RuntimeError):
    """The render slot is taken and the queue behind it is full."""


class Cancelled(BaseException):
    """Raised inside the log callback to abandon a run the caller gave up on.

    It derives from BaseException, not Exception, so the pipeline's own broad
    `except Exception` handlers cannot swallow a cancellation and carry on
    rendering a video nobody is waiting for.
    """


class Jobs:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: Optional[str] = None
        self._waiting: Deque[str] = deque()
        self.sweep_orphans()

    def sweep_orphans(self) -> int:
        """Remove job directories this process knows nothing about.

        `_sweep` walks `self._jobs`, which is memory, so a restart makes every
        directory left on disk unreachable: nothing holds a reference to it and
        nothing ever deletes it. The registry is deliberately in memory and
        that is not the bug; the bug is that the directories outlive it.

        Found on the live instance holding 2.5 GB across five orphans on an
        18 GB disk, growing by a generation every restart and reported by
        nothing. A render needs room to write, so this fails a build eventually
        and the message will be about disk, not about jobs.

        Runs at construction, when `self._jobs` is empty by definition, so
        every directory present is by definition from a previous process. Age
        is not consulted: a directory here cannot belong to this one.
        """
        removed = 0
        for path in sorted(self.workdir.glob("*")):
            if not path.is_dir():
                continue
            try:
                shutil.rmtree(path)
                removed += 1
            except OSError:
                # a directory that will not go is not worth failing a boot over
                continue
        return removed

    # -- queries ------------------------------------------------------------- #
    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def busy(self) -> bool:
        return self._active is not None

    def waiting(self) -> int:
        """How many submissions are in line behind the running one."""
        with self._lock:
            return len(self._waiting)

    def position(self, job_id: str) -> int:
        """Place in the line, 1 being next up. 0 when it is not waiting."""
        with self._lock:
            try:
                return list(self._waiting).index(job_id) + 1
            except ValueError:
                return 0

    def active(self) -> Optional[Dict[str, Any]]:
        """What the box is doing, for a page deciding whether to offer Render."""
        job = self._jobs.get(self._active or "")
        return None if job is None else job.public()

    def snapshot(self, job_id: str) -> Optional[Dict[str, Any]]:
        """A job's public state plus where it sits in the line.

        The position lives here rather than on the Job because it is a fact
        about the queue, not about the job, and a copy kept on the job would
        go stale the moment anything ahead of it finished or was cancelled.
        """
        job = self.get(job_id)
        if job is None:
            return None
        body = job.public()
        body["position"] = self.position(job_id)
        body["waiting"] = self.waiting()
        return body

    def cancel(self, job_id: str) -> Optional[str]:
        """Stop a job, whether it is running or still waiting.

        A running job is cooperative by necessity: the flag is read in the log
        callback, so the run ends at the next stage boundary rather than
        mid-encode. A *queued* job has no log callback to read anything, so it
        is dropped from the line here and stops existing as work. `_next_locked`
        skips anything that is no longer queued, which covers the case where it
        was popped between these two steps.
        """
        job = self.get(job_id)
        if job is None:
            return None
        if job.status in ("done", "failed", "cancelled"):
            return "finished"
        if job.status == "queued":
            with self._lock:
                if job_id in self._waiting:
                    self._waiting.remove(job_id)
            job.status = "cancelled"
            job.finished = time.time()
            job.log.append("stopped  cancelled before it started")
            return "cancelled"
        job.cancel_requested = True
        return "stopping"

    # -- submission ---------------------------------------------------------- #
    def submit(self, script: str, options: Dict[str, Any]) -> Job:
        script = (script or "").strip()
        if not script:
            raise ValueError("the script is empty")
        if len(script) > MAX_SCRIPT_CHARS:
            raise ValueError(
                f"script is {len(script)} characters; the limit is {MAX_SCRIPT_CHARS}"
            )

        job = Job(id=uuid.uuid4().hex[:12], options=dict(options))
        job.root = self.workdir / job.id

        # Registered, but nothing claimed. Being in `_jobs` is how a setup that
        # fails is still reported as failed instead of vanishing; claiming is a
        # separate step below, and the two used to be one. That is what wedged
        # the instance: the slot was taken first and the writing done after, so
        # an unwritable VIDSMITH_JOBS held the slot for a render that had never
        # started and 429'd every later caller until the process restarted.
        with self._lock:
            self._sweep()
            if self._full_locked():
                raise Busy(self._full_message_locked())
            self._jobs[job.id] = job

        try:
            job.root.mkdir(parents=True, exist_ok=True)
            (job.root / "script.md").write_text(script, encoding="utf-8")
            self._write_config(job.root, options)
        except BaseException as exc:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append(f"error    could not start the render: {job.error}")
            job.finished = time.time()
            raise

        with self._lock:
            # checked again, because the writing above happens off the lock and
            # the last free place may have gone to another caller meanwhile
            if self._full_locked():
                message = self._full_message_locked()
                self._jobs.pop(job.id, None)
                full = True
            else:
                full = False
                start_now = self._active is None
                if start_now:
                    # claimed and marked running under one lock, so a cancel
                    # arriving now cannot mistake it for one still waiting
                    self._active = job.id
                    job.status = "running"
                else:
                    self._waiting.append(job.id)
        if full:
            # never became work, so it leaves nothing behind to sweep
            shutil.rmtree(job.root, ignore_errors=True)
            raise Busy(message)

        if start_now:
            self._spawn(job)
        return job

    def _full_locked(self) -> bool:
        """Whether there is nowhere to put another job. Caller holds the lock."""
        return self._active is not None and len(self._waiting) >= MAX_QUEUE

    def _full_message_locked(self) -> str:
        return (f"a render is running and {len(self._waiting)} more are waiting, "
                f"which is the limit")

    def _spawn(self, job: Job) -> None:
        """Put a claimed job on a thread, handing the slot back if it will not go."""
        try:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        except BaseException as exc:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append(f"error    could not start the render: {job.error}")
            job.finished = time.time()
            self._finish(job.id)
            raise

    def _next_locked(self) -> Optional[Job]:
        """Take the next job still worth running. Caller holds the lock."""
        while self._waiting:
            candidate = self._jobs.get(self._waiting.popleft())
            # cancelled while it waited, so it is not work any more
            if candidate is None or candidate.status != "queued":
                continue
            self._active = candidate.id
            candidate.status = "running"
            return candidate
        return None

    def _finish(self, job_id: str) -> None:
        """Give the slot back and start whatever was waiting on it.

        The one place the slot is released, and it always starts the next job
        in the same breath. Releasing without starting is how a queue stalls
        with work in it and nothing running, which looks exactly like the
        wedged instance this replaced.
        """
        with self._lock:
            if self._active == job_id:
                self._active = None
            nxt = self._next_locked()
        if nxt is not None:
            self._spawn(nxt)

    def _write_config(self, root: Path, options: Dict[str, Any]) -> None:
        cfg = Config()
        cfg.theme.preset = options.get("theme") or cfg.theme.preset
        cfg.theme.watermark = (options.get("watermark") or "")[:40]
        cfg.render.aspect = options.get("aspect") or cfg.render.aspect
        cfg.visuals.provider = options.get("provider") or cfg.visuals.provider
        cfg.visuals.orientation = (
            "portrait" if cfg.render.aspect in ("9:16", "4:5") else "landscape"
        )
        cfg.audio.music = "auto" if options.get("music", True) else ""
        cfg.audio.mood = options.get("mood") or cfg.audio.mood
        if options.get("voice"):
            cfg.voice.name = options["voice"]

        write_default_config(root / "config.yaml", cfg.title)
        raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        raw["theme"], raw["render"] = cfg.to_dict()["theme"], cfg.to_dict()["render"]
        raw["visuals"], raw["audio"] = cfg.to_dict()["visuals"], cfg.to_dict()["audio"]
        raw["voice"] = cfg.to_dict()["voice"]
        (root / "config.yaml").write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

    # -- the worker ---------------------------------------------------------- #
    def _run(self, job: Job) -> None:
        # status is set to running by whoever claimed the slot, under the same
        # lock, so "queued" means waiting and nothing else

        def log(line: str) -> None:
            # the pipeline reports at every stage boundary, which makes the log
            # callback the one place a long render reliably passes through
            if job.cancel_requested:
                raise Cancelled(job.stage or "starting")
            line = str(line).rstrip()
            if not line:
                return
            job.log.append(line)
            stage = line.split(" ", 1)[0].strip()
            if stage in STAGE_PROGRESS:
                job.progress = max(job.progress, STAGE_PROGRESS[stage])
                job.stage = stage
            elif line.lstrip().startswith("visual "):
                # inch forward across the slowest stage so it does not look stuck
                job.progress = min(0.78, job.progress + 0.02)

        try:
            pipeline.build(job.root, log=log)
            job.outputs = self._collect(job)
            job.title = self._title(job)
            job.progress = 1.0
            job.status = "done"
        except Cancelled as stopped:
            job.log.append(f"stopped  cancelled during {stopped}")
            job.status = "cancelled"
        except Exception as exc:                      # a render can fail anywhere
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append(f"error    {job.error}")
            job.log.extend(traceback.format_exc().strip().splitlines()[-4:])
            job.status = "failed"
        finally:
            job.finished = time.time()
            self._finish(job.id)

    def description(self, job_id: str) -> str:
        """The paste-ready description, so the page can offer it directly."""
        job = self.get(job_id)
        if job is None or job.root is None:
            return ""
        path = job.root / "out" / "description.txt"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _collect(self, job: Job) -> List[Dict[str, Any]]:
        out = job.root / "out"
        if not out.exists():
            return []
        wanted = (".mp4", ".jpg", ".srt", ".txt")
        files = [p for p in sorted(out.iterdir())
                 if p.is_file() and p.suffix.lower() in wanted]
        return [{"name": p.name, "size": p.stat().st_size,
                 "kind": p.suffix.lstrip(".").lower()} for p in files]

    def _title(self, job: Job) -> str:
        try:
            raw = yaml.safe_load((job.root / "config.yaml").read_text(encoding="utf-8"))
            return str(raw.get("title") or "")
        except (OSError, ValueError):
            return ""

    def file(self, job_id: str, name: str) -> Optional[Path]:
        """Resolve a download, refusing anything that escapes the job's folder."""
        job = self.get(job_id)
        if job is None or job.root is None:
            return None
        out = (job.root / "out").resolve()
        target = (out / name).resolve()
        if out not in target.parents or not target.is_file():
            return None
        return target

    # -- housekeeping -------------------------------------------------------- #
    def _sweep(self) -> None:
        cutoff = time.time() - KEEP_SECONDS
        for job_id, job in list(self._jobs.items()):
            if not (job.finished and job.finished < cutoff):
                continue
            if job.root and job.root.exists():
                try:
                    shutil.rmtree(job.root)
                except OSError:
                    # something still holds a handle; keep the entry so the next
                    # sweep tries again rather than leaking the directory forever
                    continue
            del self._jobs[job_id]
