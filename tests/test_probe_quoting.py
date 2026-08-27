"""TEMPORARY. Asks each runner's ffmpeg which subtitles= spelling it accepts.

Staging the file out of an apostrophe directory did not help: macOS rejected a
staged path with nothing special in it at all, which means the quoting itself is
what that build will not take, not the character inside it. That cannot be
worked out from here, so this asks.

It fails on purpose, with the results in the assertion message, because that is
the only thing pytest shows from a passing-or-failing run in CI logs. Delete it
once the answer is in.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vidsmith import ffmpeg_util as ff

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Alignment, Encoding
Style: D,Arial,20,&H00FFFFFF,2,1

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:01.00,D,hello
"""


def _colon(p: str) -> str:
    """Escape the drive colon, which is a separate and already-solved problem.

    Without this every Windows result is just the colon splitting options, and
    the run says nothing about quoting.
    """
    return p.replace(":", chr(92) + ":")


def _spellings(plain: str, apostrophe: str):
    """(label, -vf value) for each way of naming a subtitle file."""
    return [
        ("plain, unquoted", f"subtitles={plain}"),
        ("plain, quoted", f"subtitles='{plain}'"),
        ("plain, filename= unquoted", f"subtitles=filename={plain}"),
        ("plain, filename= quoted", f"subtitles=filename='{plain}'"),
        ("apostrophe, quoted, backslash-quote",
         "subtitles='" + apostrophe.replace("'", "\\'") + "'"),
        ("apostrophe, quoted, close-escape-reopen",
         "subtitles='" + apostrophe.replace("'", "'\\''") + "'"),
        ("apostrophe, quoted, current escape",
         "subtitles='" + apostrophe.replace("'", "\\'\\''") + "'"),
        ("apostrophe, unquoted, backslash-quote",
         "subtitles=" + apostrophe.replace("'", "\\'")),
    ]


def test_which_subtitles_spellings_this_ffmpeg_accepts(tmp_path):
    try:
        binary = ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")

    version = subprocess.run([binary, "-version"], capture_output=True,
                             text=True).stdout.splitlines()[0]

    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    (plain_dir / "captions.ass").write_text(ASS, encoding="utf-8")

    quote_dir = tmp_path / "O'Brien"
    quote_dir.mkdir()
    (quote_dir / "captions.ass").write_text(ASS, encoding="utf-8")

    plain = _colon((plain_dir / "captions.ass").resolve().as_posix())
    apostrophe = _colon((quote_dir / "captions.ass").resolve().as_posix())

    filters = subprocess.run([binary, "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    has_subtitles = [l.strip() for l in filters.splitlines() if " subtitles " in l]
    cfg = subprocess.run([binary, "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout
    libass = [w for w in cfg.split() if "ass" in w.lower()]

    lines = [version,
             f"subtitles filter present: {bool(has_subtitles)} {has_subtitles}",
             f"ass in build config: {libass}"]
    for label, vf in _spellings(plain, apostrophe):
        out = tmp_path / "frame.png"
        out.unlink(missing_ok=True)
        proc = subprocess.run(
            [binary, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
             "-vf", vf, "-frames:v", "1", str(out)],
            capture_output=True, text=True,
        )
        ok = proc.returncode == 0 and out.exists()
        detail = "" if ok else "  " + proc.stderr.strip().splitlines()[0][:90]
        lines.append(f"{'PASS' if ok else 'FAIL'}  {label}{detail}")

    raise AssertionError("QUOTING PROBE\n" + "\n".join(lines))
