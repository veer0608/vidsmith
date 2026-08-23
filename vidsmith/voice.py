"""Narration via Microsoft Edge neural voices (edge-tts).

Free, no API key, and it returns
WordBoundary events, which is the reason it is the default here: we get
word-level timings for captions without ever running Whisper. Timings come from the same engine that produced the audio,
so they cannot drift.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Sequence

import edge_tts

from .config import VoiceConfig
from .script_parser import Scene
from . import ffmpeg_util as ff

TICKS = 1e7  # edge-tts reports offsets in 100-nanosecond ticks
MAX_CONCURRENCY = 3
RETRIES = 3


async def _synthesize_one(scene: Scene, out: Path, cfg: VoiceConfig) -> List[Dict[str, Any]]:
    last_err: Exception | None = None
    for attempt in range(RETRIES):
        words: List[Dict[str, Any]] = []
        chunks: List[bytes] = []
        try:
            comm = edge_tts.Communicate(
                scene.text,
                cfg.name,
                rate=cfg.rate,
                pitch=cfg.pitch,
                volume=cfg.volume,
                boundary="WordBoundary",
            )
            async for ch in comm.stream():
                if ch["type"] == "audio":
                    chunks.append(ch["data"])
                elif ch["type"] == "WordBoundary":
                    words.append(
                        {
                            "text": ch["text"],
                            "start": ch["offset"] / TICKS,
                            "end": (ch["offset"] + ch["duration"]) / TICKS,
                        }
                    )
            if not chunks:
                raise RuntimeError("edge-tts returned no audio")
            out.write_bytes(b"".join(chunks))
            return words
        except Exception as exc:  # network hiccups against the MS endpoint are common
            last_err = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"TTS failed for scene {scene.index}: {last_err}")


async def _synthesize_all(scenes: Sequence[Scene], audio_dir: Path, cfg: VoiceConfig,
                          force: bool, log) -> None:
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def worker(scene: Scene):
        mp3 = audio_dir / f"scene_{scene.index:03d}.mp3"
        if mp3.exists() and not force and scene.words:
            return
        async with sem:
            words = await _synthesize_one(scene, mp3, cfg)
        scene.audio = str(mp3)
        scene.words = words
        log(f"  voiced scene {scene.index:>3}  {len(scene.text.split()):>4}w")

    await asyncio.gather(*(worker(s) for s in scenes))


def narrate(scenes: List[Scene], audio_dir: Path, cfg: VoiceConfig,
            force: bool = False, log=print) -> List[Scene]:
    """Synthesize every scene and stamp durations + absolute start times."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(_synthesize_all(scenes, audio_dir, cfg, force, log))

    clock = 0.0
    for scene in scenes:
        mp3 = audio_dir / f"scene_{scene.index:03d}.mp3"
        scene.audio = str(mp3)
        # The encoded file is authoritative for length; word timings can end early.
        spoken = ff.duration(mp3)
        if scene.words:
            spoken = max(spoken, scene.words[-1]["end"])
        scene.duration = max(cfg.lead_in + spoken + cfg.gap, scene.hold)
        scene.start = clock
        clock += scene.duration
    return scenes


def total_duration(scenes: Sequence[Scene]) -> float:
    return sum(s.duration for s in scenes)


async def _voices() -> List[Dict[str, str]]:
    return await edge_tts.list_voices()


def list_voices(language: str = "en") -> List[Dict[str, str]]:
    voices = asyncio.run(_voices())
    if language:
        voices = [v for v in voices if v["Locale"].lower().startswith(language.lower())]
    return sorted(voices, key=lambda v: (v["Locale"], v["ShortName"]))
