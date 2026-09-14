"""How fast the voice actually speaks, read off builds already on disk.

`script_parser.WPS` sizes every draft, the web page's runtime estimate and, via
`llm.WORDS_PER_MINUTE`, the instance's minutes limit. It was 2.6 a second by
assumption for months while the voice spoke at 3.35, so every drafted video
came out about a fifth shorter than asked. This reads the word timings each
build wrote to `build/scenes.json` and prints what they add up to, so the
number is re-measured rather than guessed after changing the voice or its rate.

    .venv\\Scripts\\python.exe scripts\\speaking_rate.py
    .venv\\Scripts\\python.exe scripts\\speaking_rate.py projects\\demo projects\\gil

Two rates per project: speech runs from each scene's first word to its last,
which is what `WPS` means, and slot divides by the whole narration slot,
lead-in and gap included, which is closer to minutes of finished video.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def measure(project: Path):
    """(voice, rate, words, speech seconds, slot seconds), or None without timings."""
    scenes_file = project / "build" / "scenes.json"
    if not scenes_file.is_file():
        return None
    scenes = json.loads(scenes_file.read_text(encoding="utf-8"))
    if isinstance(scenes, dict):
        scenes = scenes.get("scenes", [])
    words = speech = slot = 0.0
    for scene in scenes:
        timed = scene.get("words") or []
        if not timed:
            continue
        words += len(timed)
        speech += timed[-1]["end"] - timed[0]["start"]
        slot += float(scene.get("duration") or 0.0)
    if not words or not speech:
        return None
    try:
        voice = (yaml.safe_load((project / "config.yaml").read_text(encoding="utf-8"))
                 or {}).get("voice") or {}
    except OSError:
        voice = {}
    return voice.get("name", "?"), voice.get("rate", "?"), int(words), speech, slot


def main(argv) -> int:
    projects = [Path(a) for a in argv] or sorted((ROOT / "projects").glob("*"))
    rows = [(p.name, m) for p in projects if (m := measure(p))]
    if not rows:
        print("no build/scenes.json with word timings under", ", ".join(map(str, projects)))
        return 1
    print(f"{'project':20} {'voice':24} {'rate':5} {'words':>6} {'speech':>7} {'slot':>6}")
    for name, (voice, rate, words, speech, slot) in rows:
        print(f"{name:20} {voice[:24]:24} {rate:5} {words:6} "
              f"{words / speech * 60:7.0f} {words / slot * 60 if slot else 0:6.0f}")
    words = sum(m[2] for _, m in rows)
    speech = sum(m[3] for _, m in rows)
    slot = sum(m[4] for _, m in rows)
    print(f"\n{words} words: speech {words / speech * 60:.0f} a minute "
          f"(WPS {words / speech:.2f}), slot {words / slot * 60:.0f} a minute")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
