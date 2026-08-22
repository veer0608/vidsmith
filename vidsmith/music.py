"""A generated underscore, so a video is never dry.

There is no free API for licensed music, and an unlicensed track is a copyright
strike waiting to happen - so the bed is synthesised here. It is a slow chord
pad: a few detuned sines per chord, soft attack and release, low-passed and
smeared with an echo until it reads as atmosphere rather than as notes.

That is deliberately all it is. It is normalised to a known loudness, sits well under the narration, and
ducks further when anyone speaks, so its job is to fill silence without ever
asking for attention.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from . import ffmpeg_util as ff

# (root, third, fifth) in Hz, voiced low and close so nothing sounds like a solo
PROGRESSIONS: Dict[str, List[Tuple[float, float, float]]] = {
    # A minor - Fmaj - Cmaj - Gmaj: the safe, slightly wistful default
    "calm": [(110.00, 261.63, 329.63), (87.31, 261.63, 349.23),
             (130.81, 261.63, 392.00), (98.00, 246.94, 392.00)],
    # C - Am - F - G: brighter, for anything upbeat or explanatory
    "warm": [(130.81, 261.63, 329.63), (110.00, 261.63, 329.63),
             (87.31, 261.63, 349.23), (98.00, 246.94, 392.00)],
    # D minor - Bb - Gm - A: unresolved, for anything with stakes
    "tense": [(73.42, 293.66, 349.23), (58.27, 233.08, 349.23),
              (98.00, 233.08, 293.66), (110.00, 277.18, 329.63)],
}
DEFAULT_MOOD = "calm"
CHORD_SECONDS = 6.0
FADE = 1.8          # attack and release inside each chord
SAMPLE_RATE = 48000


def _voice(freq: float, gain: float, detune: float) -> str:
    return f"{gain:.4f}*sin(2*PI*{freq * (1 + detune):.4f}*t)"


def _chord_expr(chord: Sequence[float], detune: float) -> str:
    """One channel of a chord: the triad, plus a sub an octave under the root."""
    root, third, fifth = chord
    parts = [
        _voice(root, 0.20, detune),
        _voice(third, 0.15, -detune),
        _voice(fifth, 0.13, detune * 0.5),
        _voice(root / 2, 0.10, 0.0),
        # a quiet fifth up gives the pad some air without adding a melody
        _voice(fifth * 2, 0.05, -detune * 1.5),
    ]
    envelope = (f"min(t/{FADE},1)*min(max({CHORD_SECONDS}-t,0)/{FADE},1)")
    return f"({'+'.join(parts)})*{envelope}"


def _escape(expr: str) -> str:
    """Commas separate filters in a filtergraph, so min(a,b) has to be escaped."""
    return expr.replace(",", "\\,")


def build_loop(out: Path, mood: str = DEFAULT_MOOD) -> Path:
    """Render one pass of the progression as a loopable wav.

    The last chord releases to silence and the first attacks from it, so the
    seam is inaudible and the caller can just -stream_loop it.
    """
    chords = PROGRESSIONS.get(mood, PROGRESSIONS[DEFAULT_MOOD])

    args: List[str] = []
    graph: List[str] = []
    for i, chord in enumerate(chords):
        # a few cents apart per channel is what makes it sound wide, not thin
        left = _escape(_chord_expr(chord, 0.0015))
        right = _escape(_chord_expr(chord, -0.0015))
        args += ["-f", "lavfi", "-i",
                 f"aevalsrc=exprs={left}|{right}:s={SAMPLE_RATE}:d={CHORD_SECONDS}"]
        graph.append(f"[{i}:a]")

    graph_str = "".join(graph) + f"concat=n={len(chords)}:v=0:a=1[pad]"
    # lowpass takes the buzz off the sines; the echoes smear the chord changes
    graph_str += (";[pad]lowpass=f=1600,"
                  "aecho=0.7:0.7:220|460:0.35|0.22,"
                  "lowpass=f=2600,"
                  # Normalise the bed to a known loudness so music_gain_db means
                  # "this far under the voice" instead of "this far under whatever
                  # amplitude the synthesis happened to land on".
                  "loudnorm=I=-16:TP=-2:LRA=7,"
                  f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[out]")

    out.parent.mkdir(parents=True, exist_ok=True)
    ff.run(args + ["-filter_complex", graph_str, "-map", "[out]",
                   "-c:a", "pcm_s16le", str(out)])
    return out


def ensure_bed(workdir: Path, mood: str = DEFAULT_MOOD) -> Path:
    """The generated bed for this mood, building it once and reusing it."""
    mood = mood if mood in PROGRESSIONS else DEFAULT_MOOD
    out = workdir / f"music-{mood}.wav"
    if not out.exists():
        build_loop(out, mood)
    return out


def moods() -> List[str]:
    return sorted(PROGRESSIONS)
