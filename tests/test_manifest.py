"""The build manifest: where a build's time went, and what it spent.

Two properties matter more than any single figure. It records nothing outside a
build, so a library call or a test pays nothing and cannot leak into a build it
is not part of. And it is written however the build ends, because a failed or
cancelled run is exactly when you want to know which stage it was in.
"""
from __future__ import annotations

import asyncio
import io
import json
import subprocess
import threading

import pytest
import requests

from test_build_wiring import project, rendered  # noqa: F401 - pytest fixtures
from vidsmith import ffmpeg_util as ff
from vidsmith import llm, manifest, voice_polly
from vidsmith import pipeline as pl
from vidsmith.config import VoiceConfig
from vidsmith.visuals import _cached_search


# --------------------------------------------------------------------------- #
# the recorder
# --------------------------------------------------------------------------- #
def test_nothing_is_recorded_outside_a_build():
    manifest.note("model", "rank_clips", requests=1)          # must not raise
    assert manifest.current() is None


def test_calls_land_in_the_stage_they_happen_in():
    with manifest.recording() as rec:
        rec.enter("voice")
        manifest.note("voice", "edge", requests=2, characters=120)
        rec.enter("visuals")
        manifest.note("ffmpeg", "encode", calls=3, seconds=1.5)
        manifest.note("voice", "edge", requests=1)
        rec.finish("done")
    body = rec.to_dict()
    stages = {s["name"]: s for s in body["stages"]}
    assert stages["voice"]["voice"] == {"requests": 2, "characters": 120}
    assert stages["visuals"]["ffmpeg"] == {"calls": 3, "seconds": 1.5}
    assert body["totals"]["voice"]["edge"] == {"requests": 3, "characters": 120}
    assert [s["name"] for s in body["stages"]] == ["voice", "visuals"]


def test_a_fact_is_never_replaced_by_counts_of_the_same_name():
    """The first real manifest lost its "voice" fact to the "voice" totals."""
    with manifest.recording() as rec:
        rec.fact("voice", "edge en-US-AndrewNeural")
        manifest.note("voice", "edge", requests=4)
        rec.finish("done")
    body = rec.to_dict()
    assert body["voice"] == "edge en-US-AndrewNeural"
    assert body["totals"]["voice"]["edge"]["requests"] == 4


def test_asyncio_and_worker_threads_report_into_the_build_that_started_them():
    """Polly synthesises in asyncio.to_thread workers inside asyncio.run."""
    async def scene():
        await asyncio.to_thread(manifest.note, "voice", "polly neural",
                                requests=1, characters=10)

    async def all_scenes():
        await asyncio.gather(*(scene() for _ in range(5)))

    with manifest.recording() as rec:
        rec.enter("voice")
        asyncio.run(all_scenes())
    assert rec.totals["voice"]["polly neural"] == {"requests": 5, "characters": 50}


def test_two_builds_on_two_threads_keep_their_own_figures():
    """The web worker and a CLI run must not write into each other's manifest."""
    seen = {}

    def build(name, n):
        with manifest.recording() as rec:
            for _ in range(n):
                manifest.note("model", "rank_clips", requests=1)
            seen[name] = rec.totals["model"]["rank_clips"]["requests"]

    threads = [threading.Thread(target=build, args=("a", 3)),
               threading.Thread(target=build, args=("b", 7))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"a": 3, "b": 7}


def test_share_is_time_inside_each_kind_of_call():
    with manifest.recording() as rec:
        manifest.note("ffmpeg", "encode", seconds=6.0)
        rec.seconds = 8.0
        rec.status = "done"
    assert rec.to_dict()["share"]["ffmpeg"] == pytest.approx(0.75)


# --------------------------------------------------------------------------- #
# the instrumented calls
# --------------------------------------------------------------------------- #
def test_every_ffmpeg_and_ffprobe_call_is_timed(monkeypatch):
    done = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
    monkeypatch.setattr(ff.subprocess, "run", lambda *a, **k: done)
    with manifest.recording() as rec:
        ff.run(["-i", "x.mp4", "y.mp4"])
        ff.run(["-i", "y.mp4", "z.mp4"])
        ff.probe("z.mp4")
    assert rec.totals["ffmpeg"]["encode"]["calls"] == 2
    assert rec.totals["ffmpeg"]["probe"]["calls"] == 1


class _Reply:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


OK = _Reply(200, {"candidates": [{"content": {"parts": [{"text": "fine"}]}}]})


def _posting(monkeypatch, *outcomes):
    calls = []

    def post(*a, **k):
        calls.append(1)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def rank_clips_stand_in(key):
    """Named like a helper, so the manifest attributes the call to this name."""
    return llm.generate("prompt", key)


def test_model_requests_are_counted_per_helper_with_retries(monkeypatch):
    _posting(monkeypatch, _Reply(503, {"error": "busy"}), OK)
    with manifest.recording() as rec:
        assert rank_clips_stand_in("k") == "fine"
    counted = rec.totals["model"]["rank_clips_stand_in"]
    assert counted["calls"] == 1 and counted["requests"] == 2
    assert counted["retries"] == 1 and counted["waited_seconds"] == 1
    assert "failed" not in counted
    assert rec.facts["models"] == [llm.DEFAULT_MODEL]


def test_a_model_call_that_gives_up_is_counted_as_failed(monkeypatch):
    _posting(monkeypatch, requests.exceptions.ConnectionError("reset"))
    with manifest.recording() as rec:
        with pytest.raises(llm.GaveUp):
            llm.generate_vision("prompt", [b"a", b"b"], "k")
    counted = rec.totals["model"]["test_a_model_call_that_gives_up_is_counted_as_failed"]
    assert counted["requests"] == 3 and counted["failed"] == 1 and counted["gave_up"] == 1


def test_a_cached_stock_search_is_counted_apart_from_a_request(tmp_path, monkeypatch):
    """The Pexels quota is requests an hour; a cached answer spends none."""
    monkeypatch.setenv("VIDSMITH_SEARCH_CACHE", str(tmp_path))
    fetched = []
    with manifest.recording() as rec:
        for _ in range(3):
            _cached_search("pexels_video", ("desk", "landscape", 1080),
                           lambda: fetched.append(1) or [{"id": "1"}])
    assert len(fetched) == 1
    assert rec.totals["stock"]["pexels_video"]["calls"] == 1
    assert rec.totals["stock"]["pexels_video"]["cached"] == 2


def test_polly_characters_are_counted_twice_because_it_bills_twice(monkeypatch):
    class Polly:
        def synthesize_speech(self, **kwargs):
            body = b"ID3audio" if kwargs["OutputFormat"] == "mp3" else b"{}"
            return {"AudioStream": io.BytesIO(body)}

    monkeypatch.setattr(voice_polly, "_client", lambda *a, **k: Polly())
    text = "Twelve chars"
    with manifest.recording() as rec:
        voice_polly._synthesize_blocking(text, None, VoiceConfig(name="Matthew"),
                                         "k", "s", "r", "neural")
    assert rec.totals["voice"]["polly neural"] == {"requests": 2,
                                                   "characters": 2 * len(text)}


# --------------------------------------------------------------------------- #
# the build writes it, however the build ends
# --------------------------------------------------------------------------- #
def _read(project):
    return json.loads((project / "build" / "manifest.json").read_text(encoding="utf-8"))


def test_a_stopped_build_writes_its_stages_and_facts(project, rendered):
    pl.build(project, stop_after="render", log=lambda *a: None)
    body = _read(project)
    assert body["status"] == "stopped after render"
    assert [s["name"] for s in body["stages"]] == [
        "parse", "queries", "voice", "visuals", "captions", "render"]
    assert body["aspect"] == "16:9" and body["footage"] == "cards"
    assert body["error"] == "" and body["seconds"] >= 0


def test_a_failed_build_still_writes_where_it_failed(project, rendered, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("ffmpeg failed: the master pass")

    monkeypatch.setattr(pl.render, "master", broken)
    with pytest.raises(RuntimeError):
        pl.build(project, stop_after="render", log=lambda *a: None)
    body = _read(project)
    assert body["status"] == "failed"
    assert "the master pass" in body["error"]
    assert body["stages"][-1]["name"] == "render"


def test_a_cancelled_build_is_not_recorded_as_a_failure(project, rendered, monkeypatch):
    """web.jobs.Cancelled is a BaseException, so broad handlers cannot swallow it."""
    class Cancelled(BaseException):
        pass

    def cancel(*a, **k):
        raise Cancelled()

    monkeypatch.setattr(pl.visuals, "build_all", cancel)
    with pytest.raises(Cancelled):
        pl.build(project, stop_after="render", log=lambda *a: None)
    body = _read(project)
    assert body["status"] == "cancelled"
    assert body["stages"][-1]["name"] == "visuals"


def test_a_manifest_that_cannot_be_written_never_costs_the_build(project, rendered):
    (project / "build" / "manifest.json").mkdir(parents=True)   # a directory in the way
    result = pl.build(project, stop_after="render", log=lambda *a: None)
    assert result.exists()
