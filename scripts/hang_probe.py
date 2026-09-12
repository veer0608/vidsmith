"""Turn the intermittent macOS narration hang into a measured rate.

`test_each_scene_speaks_at_its_own_start` has hung on macOS CI at least seven
times, always alone: ubuntu and windows green, a re-run of the same job passing.
Counted against the runs either side of it, that is roughly 4.4% of macOS runs,
which is frequent enough to be real and rare enough that watching main go green
proves very little. Forty-one clean runs after the `apad=whole_dur` change still
leaves about a one-in-six chance of a streak that lucky, so nothing so far can
tell a fix from a coincidence.

This calls the real `render.build_narration` in a loop against the same three
scenes and the same 0.8s tones the failing test uses, so the rate can be read in
minutes instead of weeks. It retypes no part of the filtergraph: the scenes come
out of the test suite's own fixture, and the graph is whatever `render.py` builds
today.

It reports rather than asserts by default. Two arms of the same workflow, one
with the pad bounded and one with the bare `apad` it replaced, answer the
question the code comment leaves open.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from vidsmith import ffmpeg_util as ff  # noqa: E402
from vidsmith import render  # noqa: E402


def real_scenes():
    """The failing test's own fixture, not a copy of it.

    pytest keeps the undecorated function on the fixture, so the harness can ask
    for exactly the scenes the suite builds. A hand-written set here would be a
    different input, and the whole point is to run the one that hangs.
    """
    import conftest

    fn = getattr(conftest.scenes, "__wrapped__", None)
    if fn is None:
        raise SystemExit(
            "conftest.scenes has no __wrapped__; this pytest version hides the "
            "fixture function. Fix the harness rather than retyping the scenes: "
            "an approximation measures a graph nobody runs.")
    return fn()


def tone(path: Path, seconds: float, freq: int = 440) -> Path:
    ff.run(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path)])
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=150)
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="seconds before a call counts as hung; a healthy "
                         "build of this graph takes about 0.3s")
    ap.add_argument("--workdir", default="")
    ap.add_argument("--fail-on-hang", action="store_true",
                    help="exit non-zero if anything hung, for use as a gate "
                         "rather than a measurement")
    args = ap.parse_args()

    # timeout_limit() reads the environment per call, so this is honoured for
    # every iteration rather than frozen at import.
    os.environ["VIDSMITH_FFMPEG_TIMEOUT"] = str(args.timeout)

    work = Path(args.workdir) if args.workdir else ROOT / ".hang-probe"
    work.mkdir(parents=True, exist_ok=True)

    print(f"ffmpeg: {ff.ffmpeg_bin()}", flush=True)
    print(f"pad in use: {render_pad_line()}", flush=True)

    scenes = real_scenes()
    for s in scenes:
        s.audio = str(tone(work / f"a{s.index}.wav", 0.8))
    total = sum(s.duration for s in scenes)
    print(f"{len(scenes)} scenes, total {total:.3f}s, "
          f"timeout {args.timeout:g}s, {args.iterations} iterations",
          flush=True)

    hangs, errors, durations = [], [], []
    started = time.time()
    for i in range(1, args.iterations + 1):
        out = work / f"narration{i}.wav"
        t0 = time.time()
        try:
            render.build_narration(scenes, out, 0.25, total)
            durations.append(time.time() - t0)
        except RuntimeError as exc:
            said = str(exc)
            elapsed = time.time() - t0
            if "did not finish within" in said:
                reached = "no progress reported"
                for line in said.splitlines():
                    if "out_time=" in line:
                        reached = line.strip()
                hangs.append((i, elapsed, reached))
                print(f"  HANG  iteration {i} after {elapsed:.1f}s :: {reached}",
                      flush=True)
            else:
                errors.append((i, said.splitlines()[0]))
                print(f"  ERROR iteration {i}: {said.splitlines()[0]}",
                      flush=True)
        finally:
            out.unlink(missing_ok=True)
        if i % 25 == 0:
            print(f"  ...{i}/{args.iterations}  hangs={len(hangs)}  "
                  f"elapsed={time.time() - started:.0f}s", flush=True)

    n = args.iterations
    rate = 100.0 * len(hangs) / n if n else 0.0
    median = sorted(durations)[len(durations) // 2] if durations else float("nan")
    print("")
    print(f"RESULT iterations={n} hangs={len(hangs)} errors={len(errors)} "
          f"rate={rate:.2f}% median_ok={median:.2f}s "
          f"wall={time.time() - started:.0f}s")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"| {os.environ.get('PROBE_ARM', '?')} | "
                     f"{os.environ.get('PROBE_SHARD', '?')} | {n} | "
                     f"{len(hangs)} | {rate:.2f}% | {len(errors)} |\n")

    if args.fail_on_hang and hangs:
        return 1
    return 0


def render_pad_line() -> str:
    """Report the pad actually compiled in, so a patched arm proves itself."""
    import inspect
    for line in inspect.getsource(render.build_narration).splitlines():
        if "pad =" in line:
            return line.strip()
    return "<no pad line found>"


if __name__ == "__main__":
    raise SystemExit(main())
