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

import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import yaml

from vidsmith import pipeline
from vidsmith import retake as retakes
from vidsmith.check import delivered
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
# How long a finished render stays downloadable, and how much disk all of them
# may hold between them. It was an hour and the directories did not survive a
# restart, so a deploy deleted the video someone had not downloaded yet, and a
# render finished while nobody was watching was simply gone. Most of a render's
# working files are deleted the moment it finishes - the downloads alone were
# 315 MB of a 428 MB build - so what is kept is the delivery and what a retake
# needs, and a budget measured on that goes a long way.
KEEP_SECONDS = int(float(os.environ.get("VIDSMITH_KEEP_DAYS", "7")) * 86400)
KEEP_BYTES = int(float(os.environ.get("VIDSMITH_KEEP_GB", "4")) * 1024 ** 3)
# A failed or stopped render has nothing to download, only a log to read.
FAILED_KEEP_SECONDS = 60 * 60
# What a finished render's build/ can lose and still have a shot changed later:
# the downloaded footage, the cut and the mixed narration are all remade or
# fetched again by a retake. What stays is the per-shot clips, the timings, the
# ledger and the verdicts, about a tenth of the whole.
DISPOSABLE = ("visuals*/cache", "picture*.mp4", "narration.wav",
              ".thumbframes", ".thumbstock")
# Written beside a finished render's files, so the next process can list it.
RECORD = "job.json"
# A bound on the payload, not on length; the minutes limit in app.py is the
# length limit. Real scripts run about 7.9 characters per spoken word once
# headings and [visual:] lines count, so 12,000 refused a 9.5 minute script
# (about 1,800 words, 14,500 characters) the minutes limit had just allowed.
# 20,000 is about thirteen minutes, past any limit a small box should be set to.
MAX_SCRIPT_CHARS = 20_000

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
    runtime: float = 0.0              # seconds of finished video, off the build log
    # the upload to YouTube, once asked for: status, privacy, video id, error
    youtube: Dict[str, Any] = field(default_factory=dict)
    # a shot change waiting or running, and what to put back if it fails
    retake: Dict[str, Any] = field(default_factory=dict)
    # how the last shot change ended, for the page: status, scene, shot, error
    swap: Dict[str, Any] = field(default_factory=dict)

    def expires(self) -> float:
        """When the sweep will take it; 0 while it is still queued or running."""
        if not self.finished:
            return 0.0
        return self.finished + (KEEP_SECONDS if self.status == "done"
                                else FAILED_KEEP_SECONDS)

    def public(self) -> Dict[str, Any]:
        end = self.finished or time.time()
        return {
            "id": self.id, "status": self.status,
            "stage": STAGE_LABELS.get(self.stage, self.stage),
            "elapsed": round(end - self.created, 1),
            "progress": round(self.progress, 3), "log": self.log[-60:],
            "error": self.error, "outputs": self.outputs, "title": self.title,
            "created": _iso(self.created),
            # served rather than worked out by the page, which used to add an
            # hour it kept its own copy of to the start time
            "expires": _iso(self.expires()) if self.finished else "",
            "aspect": self.options.get("aspect", ""),
            "runtime": self.runtime,
            "youtube": self.youtube,
            "swap": self.swap,
            # the stop was asked for but the current stage has not returned yet,
            # so the page can say "stopping" rather than appearing to ignore it
            "cancelling": self.cancel_requested and self.status == "running",
        }


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


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
        """Take back finished renders, and remove every other job directory.

        `_sweep` walks `self._jobs`, which is memory, so a restart makes every
        directory left on disk unreachable: nothing holds a reference to it and
        nothing ever deletes it. Found on the live instance holding 2.5 GB
        across five orphans on an 18 GB disk, growing by a generation every
        restart and reported by nothing.

        Deleting all of them fixed the disk and cost the videos: every deploy
        removed a render someone had not downloaded yet. A finished render now
        leaves `job.json` beside its files, and one that does is registered
        again as done, then held to the same age and disk limits as any other.
        Everything else - a render the restart interrupted, one that failed, a
        directory from before records existed - is removed as before.

        Runs at construction, when `self._jobs` is empty by definition, so
        every directory present is from a previous process and none of them
        can be a render in flight.
        """
        removed = 0
        for path in sorted(self.workdir.glob("*")):
            if not path.is_dir():
                continue
            job = self._adopt(path)
            if job is not None:
                self._jobs[job.id] = job
                continue
            try:
                shutil.rmtree(path)
                removed += 1
            except OSError:
                # a directory that will not go is not worth failing a boot over
                continue
        with self._lock:
            self._sweep()
        return removed

    def _adopt(self, path: Path) -> Optional[Job]:
        """A finished render from a previous process, or None."""
        try:
            record = json.loads((path / RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        # A restart during a shot change leaves the delivery half rewritten and
        # the copy it was taken from beside it; the copy is the good one.
        try:
            recovered = retakes.recover(path)
        except OSError:
            recovered = False
        if not isinstance(record, dict) or record.get("status") != "done" \
                or record.get("id") != path.name:
            return None
        job = Job(id=path.name, status="done", stage="done", progress=1.0,
                  log=[str(line) for line in record.get("log") or []],
                  title=str(record.get("title") or ""),
                  created=float(record.get("created") or 0.0),
                  finished=float(record.get("finished") or 0.0),
                  root=path, options=dict(record.get("options") or {}),
                  runtime=float(record.get("runtime") or 0.0),
                  youtube=dict(record.get("youtube") or {}))
        if recovered:
            job.swap = {"status": "failed",
                        "error": "the server restarted during the change"}
        if job.youtube.get("status") == "uploading":
            # the restart stopped it partway; YouTube holds no finished video
            job.youtube.update(status="failed",
                               error="the server restarted during the upload")
        # read off the disk rather than the record, which only says what was there
        job.outputs = self._collect(job)
        if not job.finished or not any(f["kind"] == "mp4" for f in job.outputs):
            return None
        return job

    def _keep(self, job: Job) -> None:
        """Make a finished render cheap to hold and able to outlive the process.

        The bulk of the working files go: downloaded footage and the picture cut
        were 330 MB of a 428 MB build whose delivery is 46 MB. The per-shot
        clips, timings and credit ledger stay, because they are what lets one
        shot be changed later without building the video again. A build that
        used no stock footage has no shot to change, so it keeps nothing. Then
        the record, last, so a directory that has one is always a finished
        render.
        """
        build = job.root / "build"
        if job.options.get("provider", "pexels") not in retakes.SWAPPABLE:
            shutil.rmtree(build, ignore_errors=True)
        for pattern in DISPOSABLE:
            for path in list(build.glob(pattern)):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
        self.record(job)

    def record(self, job: Job) -> None:
        """Write what the next process needs to take a finished render back.

        Written again whenever something about it changes, such as an upload,
        and replaced in one move so a restart never reads half a record.
        """
        body = {"id": job.id, "status": job.status, "title": job.title,
                "created": job.created, "finished": job.finished,
                "options": job.options, "runtime": job.runtime,
                "youtube": job.youtube, "log": job.log[-60:]}
        path = job.root / RECORD
        try:
            partial = path.with_suffix(".json.part")
            partial.write_text(json.dumps(body, indent=2), encoding="utf-8")
            partial.replace(path)
        except OSError as exc:
            job.log.append(f"warn     could not record this render to keep it "
                           f"across a restart: {exc}")

    def renders(self) -> List[Dict[str, Any]]:
        """Every finished render still held, newest first, for the page's list."""
        done = [j for j in self._jobs.values() if j.status == "done"]
        return [{"id": j.id, "title": j.title, "created": _iso(j.created),
                 "finished": _iso(j.finished), "expires": _iso(j.expires()),
                 "aspect": j.options.get("aspect", ""), "runtime": j.runtime,
                 "size": sum(f["size"] for f in j.outputs),
                 "files": len(j.outputs)}
                for j in sorted(done, key=lambda j: j.finished, reverse=True)]

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
            if job.retake:
                # the render itself finished long ago; only the change is off
                self._undo(job, "stopped  the change was cancelled before it "
                                "started; the video is as it was")
                return "cancelled"
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

    def retake(self, job_id: str, scene: int, shot: int, clip: str,
               query: str = "", keys: Optional[Dict[str, str]] = None) -> Optional[Job]:
        """Queue a change to one shot of a finished render.

        It is a render as far as the box is concerned - the master pass is most
        of the encode - so it takes the same slot and waits in the same line.
        The job keeps its id, and the page follows it the way it followed the
        first build. Everything that can be refused is refused here, before it
        waits: a clip that is not in the search, a render that is on YouTube.
        """
        job = self.get(job_id)
        if job is None:
            return None
        if job.status != "done" or job.root is None:
            raise retakes.RetakeRefused("only a finished render can have a shot changed")
        state = job.youtube.get("status")
        if state == "uploading":
            raise retakes.RetakeRefused("this render is uploading to YouTube; "
                                        "wait for it to finish")
        if state == "done":
            raise retakes.RetakeRefused("this render is on YouTube already, so a "
                                        "changed video would be a second upload")
        retakes.check(job.root, scene, shot, clip, query or None, keys)

        with self._lock:
            if job.status != "done":
                raise retakes.RetakeRefused("this render is already being changed")
            if self._full_locked():
                raise Busy(self._full_message_locked())
            job.retake = {"scene": scene, "shot": shot, "clip": str(clip),
                          "query": query, "keys": keys,
                          "finished": job.finished, "created": job.created}
            job.swap = {}
            # a render stopped once must not stop the next change at its first line
            job.cancel_requested = False
            job.status, job.stage, job.error = "queued", "", ""
            job.progress, job.finished, job.created = 0.0, 0.0, time.time()
            job.log = []
            start_now = self._active is None
            if start_now:
                self._active = job.id
                job.status = "running"
            else:
                self._waiting.append(job.id)
        if start_now:
            self._spawn(job)
        return job

    def _undo(self, job: Job, line: str, error: str = "") -> None:
        """A shot change that did not happen: the render is as it was."""
        before = job.retake
        job.retake = {}
        job.cancel_requested = False
        job.status, job.stage, job.progress = "done", "done", 1.0
        job.finished = before.get("finished") or time.time()
        job.created = before.get("created") or job.created
        job.log.append(line)
        job.swap = ({"status": "failed", "scene": before.get("scene"),
                     "shot": before.get("shot"), "error": error} if error else {})
        job.outputs = self._collect(job)
        self.record(job)

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
            # a render that just finished may have taken the budget over; it is
            # the newest, so it is never the one that goes
            self._sweep()
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

        retaking = dict(job.retake)
        try:
            if retaking:
                retakes.replace(job.root, retaking["scene"], retaking["shot"],
                                retaking["clip"], keys=retaking.get("keys"),
                                query=retaking["query"] or None, log=log)
                job.swap = {"status": "done", "scene": retaking["scene"],
                            "shot": retaking["shot"]}
                job.retake = {}
            else:
                pipeline.build(job.root, log=log)
            job.outputs = self._collect(job)
            job.title = self._title(job)
            job.runtime = _runtime(job.log)
            job.progress = 1.0
            job.status = "done"
            job.finished = time.time()
            self._keep(job)
        except Cancelled as stopped:
            if retaking:
                self._undo(job, f"stopped  cancelled during {stopped}; the video "
                                f"is as it was")
            else:
                job.log.append(f"stopped  cancelled during {stopped}")
                job.status = "cancelled"
        except Exception as exc:                      # a render can fail anywhere
            error = f"{type(exc).__name__}: {exc}"
            job.log.append(f"error    {error}")
            if not isinstance(exc, retakes.RetakeRefused):
                job.log.extend(traceback.format_exc().strip().splitlines()[-4:])
            if retaking:
                # A failed change is not a failed render. Marking it failed
                # would hand a finished video to the one-hour sweep.
                self._undo(job, "retake   the video is as it was before the change",
                           error=str(exc))
            else:
                job.error = error
                job.status = "failed"
        finally:
            job.finished = job.finished or time.time()
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

    def archive(self, job_id: str) -> Optional[Path]:
        """Every delivered file in one zip.

        The page has always listed the srt, the credits and the description
        beside the mp4, and people take the mp4 and leave. That is not a UI
        quibble: attribution is a licence condition carried in `credits*.txt`
        and folded into `description.txt`, and jobs are swept an hour after they
        finish. A real download went out as the video alone and the credits were
        recovered by hand off the box, with about an hour to spare.

        Built beside the job rather than inside `out/`, so it is not collected as
        an output, not offered as a download of itself, and not seen by
        `vidsmith check` as a file matching no delivered cut.
        """
        job = self.get(job_id)
        if job is None or job.root is None:
            return None
        out = job.root / "out"
        if not out.is_dir() or not any(p.is_file() for p in out.iterdir()):
            return None

        # the name the build already chose, rather than slugging the title a
        # second time: a second slugger is how `thumbs --refresh` came to write
        # `untitled.jpg` beside correctly named cuts and report success
        cuts = delivered(out)
        wide = next((p for aspect, p in cuts if aspect == "16:9"), None)
        stem = (wide or cuts[0][1]).stem if cuts else "vidsmith"
        target = job.root / f"{stem}.zip"
        newest = max(p.stat().st_mtime for p in out.iterdir() if p.is_file())
        if target.exists() and target.stat().st_mtime >= newest:
            return target                      # nothing has been rewritten since

        # write beside it and move, so a half-written zip is never served
        partial = target.with_suffix(".zip.part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(out.iterdir()):
                if path.is_file():
                    bundle.write(path, arcname=f"{stem}/{path.name}")
        partial.replace(target)
        return target

    # -- housekeeping -------------------------------------------------------- #
    def _sweep(self) -> None:
        """Drop what has outlived its keep, then the oldest renders over budget.

        Caller holds the lock. The newest finished render is never dropped for
        the budget, however large: it is the one somebody is most likely still
        waiting to download, and a single long video over a small budget would
        otherwise be deleted the moment it finished.
        """
        now = time.time()
        for job_id, job in list(self._jobs.items()):
            if job.finished and job.expires() < now:
                self._forget(job_id)

        done = sorted((j for j in self._jobs.values() if j.status == "done"),
                      key=lambda j: j.finished)
        sizes = {j.id: _size(j.root) for j in done}
        total = sum(sizes.values())
        for job in done[:-1]:
            if total <= KEEP_BYTES:
                break
            if self._forget(job.id):
                total -= sizes[job.id]

    def _forget(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.root and job.root.exists():
            try:
                shutil.rmtree(job.root)
            except OSError:
                # something still holds a handle; keep the entry so the next
                # sweep tries again rather than leaking the directory forever
                return False
        del self._jobs[job_id]
        return True


def _size(root: Optional[Path]) -> int:
    if root is None or not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def _runtime(log: List[str]) -> float:
    """The finished video's length, as the build's last line reported it."""
    for line in reversed(log):
        if line.startswith("done "):
            found = re.search(r"\((\d+(?:\.\d+)?)s, ", line)
            return float(found.group(1)) if found else 0.0
    return 0.0
