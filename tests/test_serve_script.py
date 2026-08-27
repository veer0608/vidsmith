"""The tunnel script's token parsing, run as PowerShell actually runs it.

This exists because of a bug that no amount of reading caught. The script had

    $live = if (Test-Path $envFile) { @(...) } else { @() }

and PowerShell unrolls a single-element array on its way out of a statement, so
$live came back a String rather than an array. `$live[-1]` then indexed the
string and produced its last character, and the tunnel printed its access token
as "c". `$live.Count` is 1 for both a one-element array and a bare string, so
nothing looked wrong, and testing the same lines with a direct assignment - the
form that keeps the array - passed every time.

So the lines are lifted out of the real script and run, rather than retyped.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="PowerShell semantics, Windows only")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "serve-public.ps1"
TOKEN = "-o4ohkTw9Bx6RZ4y3Os5I1Rc"      # leading dash on purpose: it has bitten


def _token_block() -> str:
    """The script's own parsing lines, lifted rather than retyped.

    Two brace-free slices: how $live is built, and how $token is read out of it.
    Cutting anything in between would take a closing brace with it and leave the
    fragment unparseable, and the minting branch is deliberately excluded - it
    needs a venv and would write a token into the file under test.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    # both anchors match either form, so reverting the script fails these tests
    # on the token they read back rather than on the extraction
    build_start = re.search(r"^\$live = ", text, re.M).start()
    build = text[build_start:text.index("if ($NoToken)")]
    # anchored on the assignment, not on how it is written: reverting to the
    # broken $live[-1] must fail on the token that comes back, not on this
    read_start = re.search(r"^\s*\$last = ", text, re.M).start()
    read_end = text.index("\n", text.index("$token = (", read_start))
    return build + "\n" + text[read_start:read_end]


def _run(env_lines: str, tmp_path: Path) -> str:
    env_file = tmp_path / ".env"
    env_file.write_text(env_lines, encoding="utf-8")
    script = (f'$envFile = "{env_file}"\n'
              + _token_block()
              # ${token} braced: a bare $token: reads as a drive-qualified
              # variable, the way $env:PATH does, and PowerShell refuses it
              + '\nWrite-Output "TOKEN:${token}:$($token.Length)"\n')
    runner = tmp_path / "probe.ps1"
    runner.write_text(script, encoding="utf-8")
    out = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(runner)],
        capture_output=True, text=True, timeout=90,
    )
    assert out.returncode == 0, out.stderr
    line = next(l for l in out.stdout.splitlines() if l.startswith("TOKEN:"))
    return line[len("TOKEN:"):]


def test_a_single_token_line_is_read_whole(tmp_path):
    """The one that broke: one live line, and PowerShell hands back a string."""
    assert _run(f"PEXELS_API_KEY=abc\nVIDSMITH_TOKEN={TOKEN}\n", tmp_path) \
        == f"{TOKEN}:{len(TOKEN)}"


def test_commented_tokens_are_ignored(tmp_path):
    """-NoToken comments the live line out; those must not come back as the token."""
    body = (f"# VIDSMITH_TOKEN=old-one-here\n"
            f"# VIDSMITH_TOKEN=another-old\n"
            f"VIDSMITH_TOKEN={TOKEN}\n")
    assert _run(body, tmp_path) == f"{TOKEN}:{len(TOKEN)}"


def test_the_last_live_line_wins(tmp_path):
    """Two live lines is a mess, but the newest is the one the server reads."""
    body = f"VIDSMITH_TOKEN=superseded\nVIDSMITH_TOKEN={TOKEN}\n"
    assert _run(body, tmp_path) == f"{TOKEN}:{len(TOKEN)}"


def test_no_live_line_yields_nothing(tmp_path):
    """Which is what makes the script refuse rather than open an ungated tunnel."""
    assert _run("# VIDSMITH_TOKEN=commented\nPEXELS_API_KEY=abc\n", tmp_path) == ":0"


def test_the_script_does_not_index_a_possibly_unrolled_value(tmp_path):
    """Guard the shape, not just the result.

    $live[-1] is correct on an array and silently wrong on a string, and the two
    are indistinguishable by .Count.
    """
    # comments explain the bug and quote the broken forms, so only look at code
    code = "\n".join(
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#"))
    assert "$live[-1]" not in code, "indexing $live breaks when PowerShell unrolls it"
    assert "$live = if (" not in code, "assigning an if's output unrolls the array"
