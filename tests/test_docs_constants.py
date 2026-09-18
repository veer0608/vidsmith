"""Numbers the docs quote from the code must still be the code's numbers.

Same shape as `test_docs_model_id.py`: `docs/claude/` states limits, defaults
and thresholds as facts, a session reads them instead of the source, and a
changed constant leaves the prose confidently wrong with nothing going red.

Each case names the doc, a pattern that captures the number as written, and
where the real value lives. A pattern that no longer matches fails too, so
rewording the sentence means updating its case here rather than silently
dropping the check.

Env-backed settings are read from the default written in the source, never
from the imported module: CI exports `VIDSMITH_FFMPEG_TIMEOUT=45`, and the doc
describes the default, not whatever this process was started with.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from vidsmith import config, ffmpeg_util, script_parser, visuals

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "claude"
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def env_default(source: str, name: str) -> float:
    """The fallback in `os.environ.get("NAME", "X")`, as written in the source."""
    text = (ROOT / source).read_text(encoding="utf-8")
    found = re.findall(rf'os\.environ\.get\("{name}",\s*"([^"]+)"\)', text)
    assert len(found) == 1, f"{source} should read {name} with one default"
    return float(found[0])


def _constant(source: str, name: str) -> float:
    """A plain `NAME = number` from a module that is heavy to import."""
    text = (ROOT / source).read_text(encoding="utf-8")
    found = re.findall(rf"^{name}\s*=\s*([\d.]+)\s*$", text, re.MULTILINE)
    assert len(found) == 1, f"{source} should define {name} once"
    return float(found[0])


# (doc, pattern with one group per number, callable giving the code's values)
CASES = [
    ("incidents.md", r"`VIDSMITH_KEEP_DAYS` \((\d+)\)",
     lambda: [env_default("web/jobs.py", "VIDSMITH_KEEP_DAYS")]),
    ("incidents.md", r"`VIDSMITH_KEEP_GB` \((\d+),",
     lambda: [env_default("web/jobs.py", "VIDSMITH_KEEP_GB")]),
    ("incidents.md", r"`VIDSMITH_FFMPEG_TIMEOUT` \((\d+)s\)",
     lambda: [ffmpeg_util.DEFAULT_TIMEOUT]),
    ("incidents.md", r"cached for (\d+) hours",
     lambda: [visuals.SEARCH_TTL / 3600]),
    ("incidents.md", r"under (\d+)% of pixels above luma (\d+)",
     lambda: [visuals.DARK_LIT * 100, visuals.DARK_LUMA]),
    ("web-service.md", r"`SHORTS_SECONDS` \((\d+)\)",
     lambda: [_constant("web/app.py", "SHORTS_SECONDS")]),
    ("web-service.md", r"`VIDSMITH_MAX_MINUTES` \(default (\d+)\)",
     lambda: [env_default("web/app.py", "VIDSMITH_MAX_MINUTES")]),
    ("web-service.md", r"`VIDSMITH_MAX_QUEUE` bounds the line\s+at (\w+),",
     lambda: [env_default("web/jobs.py", "VIDSMITH_MAX_QUEUE")]),
    ("script.md", r"default voice spoke at ([\d.]+),",
     lambda: [script_parser.WPS]),
    ("configuration.md",
     r"narration normalises to `(-\d+)` LUFS, the bed\s+sits `(-\d+)` dB",
     lambda: [config.AudioConfig().lufs, config.AudioConfig().music_gain_db]),
]


def _number(written: str) -> float:
    return float(WORDS.get(written.lower(), written))


def mismatches(text: str, pattern: str, actual: list[float]) -> list[str]:
    m = re.search(pattern, text)
    if not m:
        return [f"no longer says {pattern!r}; update its case here"]
    return [f"says {w!r}, code has {a:g}"
            for w, a in zip(m.groups(), actual) if _number(w) != a]


@pytest.mark.parametrize("doc,pattern,actual", CASES,
                         ids=[f"{d}:{p[:30]}" for d, p, _ in CASES])
def test_quoted_number_matches_the_code(doc, pattern, actual):
    text = (DOCS / doc).read_text(encoding="utf-8")
    problems = mismatches(text, pattern, actual())
    assert not problems, f"{doc}: {problems}"


def test_quoted_aspects_match_config():
    text = (DOCS / "configuration.md").read_text(encoding="utf-8")
    m = re.search(r"`ASPECTS` \(([^)]*)\)", text)
    assert m, "configuration.md no longer lists ASPECTS; update this test"
    assert set(re.findall(r"`([^`]+)`", m.group(1))) == set(config.ASPECTS)


def test_the_gate_can_still_fail():
    """A changed number and a reworded sentence must both be caught."""
    pattern, actual = r"`SHORTS_SECONDS` \((\d+)\)", [180.0]
    assert mismatches("`SHORTS_SECONDS` (180)", pattern, actual) == []
    assert mismatches("`SHORTS_SECONDS` (240)", pattern, actual)
    assert mismatches("the Shorts limit", pattern, actual)
    assert mismatches("at three,", r"at (\w+),", [4.0])
