"""Print the ffmpeg and ffprobe vidsmith will actually run, and refuse a mismatch.

Two probes of the macOS narration hang measured ffmpeg 9.0.1 while every
recorded hang had run on 8.1.2. Both installed with `brew install ffmpeg` and
took the version written in CLAUDE.md on trust. Neither number meant anything.

This asks the resolver the pipeline uses rather than the shell, since the two can
disagree: FFMPEG_BINARY and the repo's bin/ both win over PATH. Set EXPECT to a
version and the job stops here instead of producing a confident wrong answer.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidsmith import ffmpeg_util as ff  # noqa: E402


def first_line(path: str) -> str:
    out = subprocess.run([path, "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout
    return out.splitlines()[0] if out else "<no output>"


def main() -> int:
    want = os.environ.get("EXPECT", "").strip()
    wrong = []
    for name, path in (("ffmpeg", ff.ffmpeg_bin()), ("ffprobe", ff.ffprobe_bin())):
        line = first_line(path)
        print(f"vidsmith resolves {name} -> {path}")
        print(f"  {line}")
        if want and f" version {want} " not in f"{line} ":
            wrong.append(f"{name} is not {want}: {line}")
    if wrong:
        print("")
        for w in wrong:
            print(f"REFUSING: {w}")
        print("This job would measure a different ffmpeg from the one under test.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
