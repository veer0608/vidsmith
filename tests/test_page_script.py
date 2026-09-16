"""The page's script has to parse, or none of the page works.

`index.html` carries one inline script, and a second `let wordCap` added for the
Script card was a SyntaxError that took the whole page down with it: no
options, no render button, no past renders. Every page test here reads the
file as text and passed. Node's own parser is the check, and GitHub's runners
all carry it; a machine without it skips rather than failing.
"""
from __future__ import annotations

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
