"""The page's script has to parse, or none of the page works.

`index.html` carries one inline script, and a second `let wordCap` added for the
Script card was a SyntaxError that took the whole page down with it: no
options, no render button, no past renders. Every page test here reads the
file as text and passed. Node's own parser is the check, and GitHub's runners
all carry it; a machine without it skips rather than failing.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"


def test_the_inline_script_parses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed, so the page script cannot be parsed here")
    scripts = re.findall(r"<script>(.*?)</script>", PAGE.read_text(encoding="utf-8"), re.S)
    assert scripts, "the page has no inline script to check"
    source = tmp_path / "page.js"
    source.write_text("\n".join(scripts), encoding="utf-8")

    result = subprocess.run([node, "--check", str(source)], capture_output=True,
                            text=True, timeout=60)

    assert result.returncode == 0, result.stderr


def _function(source: str, name: str) -> str:
    """One top-level function's source, by counting braces from its signature."""
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        i += 1
        if depth == 0:
            return source[start:i]


def test_the_finished_notification_says_what_actually_finished(tmp_path):
    """A change that did not happen is not "ready", and a Short is not the video."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    page = PAGE.read_text(encoding="utf-8")
    cases = {
        "render": {"title": "Racks", "status": "done", "runtime": 75, "swap": {}},
        "short": {"title": "Racks", "status": "done", "swap": {"kind": "cut", "status": "done"}},
        "scene": {"title": "Racks", "status": "done", "swap": {"kind": "scene", "status": "done", "scene": 1}},
        "undone": {"title": "Racks", "status": "done",
                   "swap": {"kind": "shot", "status": "failed", "error": "no clip"}},
        "failed": {"title": "Racks", "status": "failed", "swap": {}},
    }
    harness = tmp_path / "notify.js"
    harness.write_text(
        _function(page, "fmt") + "\n" + _function(page, "finishedMessage") + "\n"
        + f"const cases = {json.dumps(cases)};\n"
        + "console.log(JSON.stringify(Object.fromEntries(Object.entries(cases)"
          ".map(([k, job]) => [k, finishedMessage(job)]))));\n", encoding="utf-8")

    result = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    said = json.loads(result.stdout)

    assert said["render"] == ["Racks is ready", "1m 15s of video, ready to watch and download."]
    assert "Shorts version ready" in said["short"][0]
    assert "scene 2 rebuilt" in said["scene"][0]
    assert "did not finish" in said["undone"][0] and "ready" not in said["undone"][0]
    assert "no clip" in said["undone"][1]
    assert said["failed"][0] == "Racks failed"


def test_the_check_can_fail(tmp_path):
    """A gate nobody has seen fail is not known to be a gate."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = tmp_path / "bad.js"
    source.write_text("let wordCap = 0;\nlet wordCap = 1;\n", encoding="utf-8")
    result = subprocess.run([node, "--check", str(source)], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode != 0 and "already been declared" in result.stderr
