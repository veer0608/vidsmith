"""Run the whole suite in a loop and count how often the narration test hangs.

The first probe called `render.build_narration` 3000 times per arm, with the
bounded pad and with the bare `apad` it replaced, and got zero hangs either way.
At the historical 4.4% that is not a quiet result, it is a refutation: if a bare
apad were enough on its own, 3000 trials would have produced about 130 hangs.
So the hang is not a property of that call in isolation, and the pad cannot be
credited with fixing it.

What the first probe stripped away was the suite around the call. CLAUDE.md
already records that module state and threads outlive their tests here, and that
`test_filter_paths` sorting before `test_integration` once left a timeout guard
holding the wrong value for every later file. Those are exactly the conditions a
tight in-process loop removes.

So the unit of measurement here is one full suite run, which is also the unit the
4.4% was measured in. The command is the CI job's own, `VIDSMITH_FFMPEG_TIMEOUT`
included, because the environment that reproduces this includes how it is
invoked.

Time-budgeted rather than count-budgeted: a job packs in as many suite runs as
its timeout allows, and reports how many it managed, so the trial count is
honest about what the runner actually delivered.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = "test_each_scene_speaks_at_its_own_start"


def classify(out: str) -> str:
    """pass, hang, or some other failure. The three need telling apart.

    A hang is the target test failing with our own ffmpeg guard, not merely the
    target test failing: it has also failed for real reasons, and counting those
    as hangs would inflate the very number this exists to measure.
    """
    if f"FAILED tests/test_integration.py::{TARGET}" not in out:
        return "other-failure" if ("FAILED" in out or "ERROR" in out) else "pass"
    hang_markers = ("did not finish within", "Timeout >", "it reached out_time=",
                    "it never reported any progress")
    return "hang" if any(m in out for m in hang_markers) else "other-failure"


def progress_line(out: str) -> str:
    for pat in (r"it reached out_time=\S+", r"it never reported any progress"):
        m = re.search(pat, out)
        if m:
            return m.group(0)
    return "no progress line in output"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=25.0,
                    help="wall-clock budget; the loop stops starting new runs "
                         "once this is spent")
    ap.add_argument("--max-runs", type=int, default=1000)
    ap.add_argument("--ffmpeg-timeout", default="45",
                    help="CI uses 45, under the 120s pytest limit, so our own "
                         "guard speaks before pytest kills the test")
    args = ap.parse_args()

    env = dict(os.environ)
    env["VIDSMITH_FFMPEG_TIMEOUT"] = args.ffmpeg_timeout
    env.setdefault("PYTHONIOENCODING", "utf-8")

    # The CI job's flags, unchanged. Adding -x or narrowing the selection would
    # measure a different suite than the one that hangs, and the ordering of
    # whole files is a live suspect here.
    cmd = [sys.executable, "-m", "pytest", "--timeout=120", "--durations=10"]
    print(f"command: {' '.join(cmd)}", flush=True)
    print(f"VIDSMITH_FFMPEG_TIMEOUT={args.ffmpeg_timeout}  "
          f"budget={args.minutes:g}min  cap={args.max_runs}", flush=True)

    deadline = time.time() + args.minutes * 60
    counts = {"pass": 0, "hang": 0, "other-failure": 0}
    hangs = []
    runs = 0

    while runs < args.max_runs and time.time() < deadline:
        runs += 1
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=ROOT, env=env,
                              capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        verdict = classify(out)
        counts[verdict] += 1
        elapsed = time.time() - t0

        if verdict == "hang":
            line = progress_line(out)
            hangs.append((runs, elapsed, line))
            print(f"  HANG on suite run {runs} after {elapsed:.0f}s :: {line}",
                  flush=True)
        elif verdict == "other-failure":
            first = next((l for l in out.splitlines() if l.startswith("FAILED")),
                         "<no FAILED line>")
            print(f"  OTHER on suite run {runs} after {elapsed:.0f}s :: {first}",
                  flush=True)
        else:
            print(f"  ok {runs} ({elapsed:.0f}s)", flush=True)

    total = runs or 1
    rate = 100.0 * counts["hang"] / total
    print("")
    print(f"RESULT suite_runs={runs} hangs={counts['hang']} "
          f"other_failures={counts['other-failure']} passes={counts['pass']} "
          f"hang_rate={rate:.2f}%")
    for n, elapsed, line in hangs:
        print(f"  hang detail: run={n} elapsed={elapsed:.0f}s {line}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"| {os.environ.get('PROBE_ARM', '?')} | "
                     f"{os.environ.get('PROBE_SHARD', '?')} | {runs} | "
                     f"{counts['hang']} | {rate:.2f}% | "
                     f"{counts['other-failure']} |\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
