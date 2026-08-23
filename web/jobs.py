"""One render at a time, tracked well enough to watch it happen.

A video takes minutes, not milliseconds, so nothing here is request-scoped: the
POST creates a job and returns, the browser polls, and the work happens on a
worker thread. The pipeline already reports each stage through a log callback,
so progress is real rather than a spinner.

Deliberately single-process and in-memory. Two concurrent x264 encodes will
exhaust a small host, so the queue depth is one and a second caller is told to
wait rather than being silently starved.
"""
from __future__ import annotations

import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from vidsmith import pipeline
from vidsmith.config import Config, write_default_config

# stage -> fraction of the run that is behind you once it starts
STAGE_PROGRESS = {
    "script": 0.02, "queries": 0.05, "voice": 0.10, "visuals": 0.20,
    "captions": 0.80, "music": 0.84, "render": 0.88, "credits": 0.96,
    "meta": 0.98, "done": 1.0,
}
KEEP_SECONDS = 60 * 60          # finished jobs are swept after an hour
MAX_SCRIPT_CHARS = 12_000


STAGE_LABELS = {
    "script": "reading the script", "queries": "choosing b-roll",
    "voice": "recording narration", "visuals": "finding footage",
    "captions": "timing captions", "music": "scoring", "render": "encoding",
    "credits": "crediting", "meta": "writing the description",
}


@dataclass
class Job:
    id: str
    status: str = "queued"          # queued | running | done | failed
    stage: str = ""
    progress: float = 0.0
    log: List[str] = field(default_factory=list)
    error: str = ""
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    title: str = ""
    created: float = field(default_factory=time.time)
    finished: float = 0.0
    root: Optional[Path] = None

    def public(self) -> Dict[str, Any]:
        end = self.finished or time.time()
        return {
            "id": self.id, "status": self.status,
            "stage": STAGE_LABELS.get(self.stage, self.stage),
            "elapsed": round(end - self.created, 1),
            "progress": round(self.progress, 3), "log": self.log[-60:],
            "error": self.error, "outputs": self.outputs, "title": self.title,
            "created": datetime.fromtimestamp(self.created, timezone.utc).isoformat(),
        }


class Busy(RuntimeError):
    """Another render is already running."""


class Jobs:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: Optional[str] = None

    # -- queries ------------------------------------------------------------- #
    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def busy(self) -> bool:
        return self._active is not None

    # -- submission ---------------------------------------------------------- #
    def submit(self, script: str, options: Dict[str, Any]) -> Job:
        script = (script or "").strip()
        if not script:
            raise ValueError("the script is empty")
        if len(script) > MAX_SCRIPT_CHARS:
            raise ValueError(
                f"script is {len(script)} characters; the limit is {MAX_SCRIPT_CHARS}"
            )

        with self._lock:
            if self._active is not None:
                raise Busy("a render is already running")
            self._sweep()
            job = Job(id=uuid.uuid4().hex[:12])
            self._jobs[job.id] = job
            self._active = job.id

        job.root = self.workdir / job.id
        job.root.mkdir(parents=True, exist_ok=True)
        (job.root / "script.md").write_text(script, encoding="utf-8")
        self._write_config(job.root, options)

        threading.Thread(target=self._run, args=(job, options), daemon=True).start()
        return job

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
    def _run(self, job: Job, options: Dict[str, Any]) -> None:
        job.status = "running"

        def log(line: str) -> None:
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
        except Exception as exc:                      # a render can fail anywhere
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append(f"error    {job.error}")
            job.log.extend(traceback.format_exc().strip().splitlines()[-4:])
            job.status = "failed"
        finally:
            job.finished = time.time()
            with self._lock:
                self._active = None

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
