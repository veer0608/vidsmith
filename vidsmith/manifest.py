"""What a build did: where its time went, and what it spent on other people's APIs.

`out/build.json` says what the delivery *is*, and `check` reads it. This says how
the run *went*, and is written to `build/` whether the run finished, stopped
early or failed - a failure is exactly when you want to know which stage it was
in and what it had already paid for.

It exists because the numbers kept being measured by hand. "78% of a build is
ffmpeg" came from temporarily wrapping `ff.run`, `ff.probe` and `requests`; the
daily Gemini ceiling is 500 requests per model and nothing recorded how many a
build used; Polly bills audio and speech marks as separate requests, so a script
is paid for twice, and the only way to see that was to read the code. Every build
now writes those numbers down.

Recording is a context variable, not module state. Nothing is recorded unless
`recording()` is active, so a test or a library caller pays nothing, and two
builds on different threads - the web worker and a CLI run - cannot write into
each other's figures. `asyncio.run` and `asyncio.to_thread` copy the context, so
Polly's worker threads still report into the build that started them; the lock
is for them.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_current: ContextVar[Optional["Recorder"]] = ContextVar("vidsmith_manifest", default=None)


class Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started = time.time()
        self._clock = time.perf_counter()
        self.stage: Optional[str] = None
        self._stage_clock = 0.0
        # stage -> {"seconds": s, section: {field: amount}}, in the order entered
        self.stages: Dict[str, Dict[str, Any]] = {}
        # section -> key -> field -> amount
        self.totals: Dict[str, Dict[str, Dict[str, float]]] = {}
        self.facts: Dict[str, Any] = {}
        self.target: Optional[Path] = None
        self.status = "running"
        self.error = ""
        self.seconds = 0.0

    # -- recording -------------------------------------------------------- #
    def enter(self, stage: str) -> None:
        """Close the stage in progress and start timing `stage`."""
        with self._lock:
            now = time.perf_counter()
            self._close(now)
            self.stage = stage
            self._stage_clock = now
            self.stages.setdefault(stage, {"seconds": 0.0})

    def _close(self, now: float) -> None:
        if self.stage is not None:
            self.stages[self.stage]["seconds"] += now - self._stage_clock
            self.stage = None

    def add(self, section: str, key: str, **amounts: float) -> None:
        with self._lock:
            bucket = self.totals.setdefault(section, {}).setdefault(key, {})
            here = (self.stages[self.stage].setdefault(section, {})
                    if self.stage is not None else None)
            for field, amount in amounts.items():
                bucket[field] = bucket.get(field, 0) + amount
                if here is not None:
                    here[field] = here.get(field, 0) + amount

    def fact(self, name: str, value: Any) -> None:
        """A value rather than a count, such as the aspect being built."""
        with self._lock:
            self.facts[name] = value

    def collect(self, name: str, value: Any) -> None:
        """Add a distinct value to a list, such as each model a build called."""
        with self._lock:
            values = self.facts.setdefault(name, [])
            if value not in values:
                values.append(value)

    def finish(self, status: str, error: Optional[BaseException] = None) -> None:
        with self._lock:
            self._close(time.perf_counter())
            self.seconds = time.perf_counter() - self._clock
            self.status = status
            self.error = f"{type(error).__name__}: {error}"[:500] if error else ""

    # -- reporting -------------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            total = self.seconds or (time.perf_counter() - self._clock)
            stages: List[Dict[str, Any]] = []
            for name, body in self.stages.items():
                stages.append({"name": name, **_rounded(body)})
            inside = {section: sum(fields.get("seconds", 0) for fields in keys.values())
                      for section, keys in self.totals.items()}
            return {
                "status": self.status,
                "error": self.error,
                "started": datetime.fromtimestamp(self.started, timezone.utc)
                                   .isoformat(timespec="seconds"),
                "seconds": round(total, 2),
                **self.facts,
                "stages": stages,
                # wall time spent inside each kind of call. Concurrent calls can
                # overlap, so a share is "at least this busy", not a slice of a pie.
                "share": {section: round(seconds / total, 3)
                          for section, seconds in inside.items() if total and seconds},
                # Nested, not spread beside the facts: the first real manifest
                # had a "voice" fact silently replaced by the "voice" counts.
                "totals": {section: {key: _rounded(fields) for key, fields in keys.items()}
                           for section, keys in self.totals.items()},
            }

    def write(self) -> Optional[Path]:
        """Write the manifest, never raising: a build is not lost over its report."""
        if self.target is None:
            return None
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            self.target.write_text(json.dumps(self.to_dict(), indent=2) + "\n",
                                   encoding="utf-8")
            return self.target
        except (OSError, TypeError, ValueError):
            return None


def _rounded(fields: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, dict):
            out[key] = _rounded(value)
        elif isinstance(value, float):
            out[key] = round(value, 3)
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
# the calls instrumented code makes, all no-ops outside a build
# --------------------------------------------------------------------------- #
@contextmanager
def recording() -> Iterator[Recorder]:
    recorder = Recorder()
    token = _current.set(recorder)
    try:
        yield recorder
    finally:
        _current.reset(token)


def current() -> Optional[Recorder]:
    return _current.get()


def note(section: str, key: str, **amounts: float) -> None:
    recorder = _current.get()
    if recorder is not None:
        recorder.add(section, key, **amounts)


def fact(name: str, value: Any) -> None:
    recorder = _current.get()
    if recorder is not None:
        recorder.fact(name, value)


def collect(name: str, value: Any) -> None:
    recorder = _current.get()
    if recorder is not None:
        recorder.collect(name, value)


@contextmanager
def timed(section: str, key: str, **amounts: float) -> Iterator[None]:
    """Count one call and the seconds it took, whether it returned or raised."""
    started = time.perf_counter()
    try:
        yield
    finally:
        note(section, key, calls=1, seconds=time.perf_counter() - started, **amounts)
