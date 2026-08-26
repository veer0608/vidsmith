"""Narration through Amazon Polly, the other licensed path.

edge-tts is an unofficial client for the endpoint behind Edge's Read Aloud and
Microsoft grants no commercial use of it, so anything with revenue attached
needs a service that licenses what it sells.

Polly qualifies because it reports word timings, which almost nothing else does.
The contract is `voice._synthesize_one`'s: write an mp3 and return one dict per
spoken word, `{"text", "start", "end"}`, seconds from the start of that scene.
Get that right and the captions, the shot plan and the mix cannot tell which
engine spoke.

Three things about it shape this module, and each is a way a naive adapter
would be wrong:

**Two calls, not one.** Audio and speech marks are separate
`synthesize_speech` requests, each billed its own characters, so a video costs
its script length twice.

**No durations, only starts.** A word mark carries `time` in milliseconds and
nothing about how long the word lasts, while edge-tts reports both. See
`_timings` for what that forces.

**Prosody is not written the way edge-tts writes it.** `rate` happily takes
`+8%`, but `volume` is decibels or a named level and rejects a percentage
outright, and `pitch` is refused on the engines that do not support it. All
three were measured against the live service rather than read off the docs,
which describe pitch as unsupported without saying it is a hard error.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape

from .config import VoiceConfig

IMPORT_HINT = (
    "the polly voice provider needs boto3:\n"
    "  pip install -r requirements-polly.txt"
)
KEY_HINT = (
    "the polly voice provider needs AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\n"
    "  and AWS_REGION (environment, or any .env pipeline.find_keys() reads)"
)
# Engines whose prosody support drops pitch. Not a guess: AWS documents rate and
# volume as available and pitch as not, for exactly these.
PITCHLESS_ENGINES = ("neural", "long-form", "generative")
DEFAULT_ENGINE = "neural"

# The engine that cannot do the one thing this pipeline needs. AWS documents
# speech marks as available on standard, neural and long-form, and generative is
# absent from that list - it synthesises fine and reports no word timings at all,
# which here means a finished video with no captions and a single held shot per
# scene, and nothing to say why. config.py keeps it out of the closed set; this
# catches an engine passed directly.
NO_SPEECH_MARKS = ("generative",)


def _client(key: str, secret: str, region: str):
    if not (key and secret and region):
        raise RuntimeError(KEY_HINT)
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(IMPORT_HINT) from exc
    return boto3.client("polly", aws_access_key_id=key,
                        aws_secret_access_key=secret, region_name=region)


NAMED_VOLUMES = ("default", "silent", "x-soft", "soft", "medium", "loud", "x-loud")


def polly_volume(volume: str) -> str:
    """Translate an edge-tts volume into one Polly will accept.

    The two services do not agree on the unit. edge-tts writes a percentage and
    Polly takes decibels or a named level, rejecting `+0%` with a bare "Invalid
    SSML request" that names no attribute - so a project switching provider hits
    a failure whose message points nowhere. Measured against the live service:
    `rate="+8%"` is fine and `volume="+0%"` is not.

    A percentage is a linear gain and a decibel is logarithmic, so this is a
    real conversion rather than a relabel: +0% is +0dB, and +50% is about
    +3.5dB. Anything already in Polly's own vocabulary is passed through
    untouched.
    """
    import math

    v = (volume or "").strip()
    if not v:
        return "+0dB"
    if v.lower() in NAMED_VOLUMES or v.lower().endswith("db"):
        return v
    if v.endswith("%"):
        try:
            pct = float(v.rstrip("%"))
        except ValueError:
            return "+0dB"
        gain = 1.0 + pct / 100.0
        if gain <= 0:
            return "silent"
        return f"{20 * math.log10(gain):+.1f}dB"
    return "+0dB"


def ssml(text: str, cfg: VoiceConfig, engine: str = DEFAULT_ENGINE) -> str:
    """The scene as SSML, carrying whatever prosody this engine honours.

    No `<voice>` element: Polly takes the voice as a request parameter and does
    not support selecting one in the document.

    The text is escaped because SSML is XML and a script is arbitrary prose. An
    ampersand would otherwise end the document, and this is the only place in
    the pipeline where narration is parsed rather than spoken.
    """
    attrs = [f'rate="{escape(cfg.rate)}"',
             f'volume="{escape(polly_volume(cfg.volume))}"']
    if engine not in PITCHLESS_ENGINES:
        attrs.append(f'pitch="{escape(cfg.pitch)}"')
    return (f'<speak><prosody {" ".join(attrs)}>{escape(text)}'
            f"</prosody></speak>")


def _timings(marks: bytes, spoken: float) -> List[Dict[str, Any]]:
    """Turn Polly's speech marks into the word shape the pipeline reads.

    The marks arrive as newline-delimited JSON, one object per line, each with
    `time` in milliseconds and no duration at all. Every other provider here
    reports both, so an end has to be constructed rather than read.

    A word therefore ends where the next one starts. That is a real choice and
    not a fudge: it makes the highlight contiguous, which is what karaoke
    captions want anyway - a gap between words would read as a stutter. The last
    word ends at the audio's own duration, measured from the encoded file rather
    than guessed, because there is no next word to borrow from and guessing
    there is how a caption outlives its audio.
    """
    words: List[Dict[str, Any]] = []
    for line in marks.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            mark = json.loads(line)
        except ValueError:
            continue                      # a malformed line is not worth a build
        if mark.get("type") != "word" or not str(mark.get("value", "")).strip():
            continue
        words.append({"text": mark["value"], "start": mark["time"] / 1000.0})

    words.sort(key=lambda w: w["start"])
    for i, word in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else spoken
        # never let an end precede its start, whatever the marks claimed
        word["end"] = max(nxt, word["start"])
    return words


def _synthesize_blocking(text: str, out: Path, cfg: VoiceConfig, key: str,
                         secret: str, region: str,
                         engine: str = DEFAULT_ENGINE) -> Tuple[bytes, bytes]:
    """Audio and marks, in that order, as two billed requests."""
    if engine in NO_SPEECH_MARKS:
        raise RuntimeError(
            f"polly's {engine} engine returns no speech marks, so there would be "
            f"no word timings: the video would render with no captions and one "
            f"shot per scene. Use standard, neural or long-form."
        )
    if engine in PITCHLESS_ENGINES and cfg.pitch.strip() not in ("", "+0Hz", "0Hz"):
        raise RuntimeError(
            f"polly's {engine} engine does not support prosody pitch, and would "
            f"drop voice.pitch={cfg.pitch!r} without saying so. Set it back to "
            f"'+0Hz', or use engine 'standard'."
        )

    polly = _client(key, secret, region)
    document = ssml(text, cfg, engine)
    common = dict(Text=document, TextType="ssml", VoiceId=cfg.name, Engine=engine)

    audio = polly.synthesize_speech(OutputFormat="mp3", **common)["AudioStream"].read()
    if not audio:
        raise RuntimeError("polly returned no audio")
    marks = polly.synthesize_speech(OutputFormat="json", SpeechMarkTypes=["word"],
                                    **common)["AudioStream"].read()
    return audio, marks


async def synthesize(text: str, out: Path, cfg: VoiceConfig, key: str,
                     secret: str, region: str,
                     engine: str = DEFAULT_ENGINE) -> List[Dict[str, Any]]:
    """Async face on a synchronous SDK.

    boto3 is blocking, and voice.py is asyncio because edge-tts is. A worker
    thread keeps the scene concurrency the module already manages rather than
    inventing a second idea of how many scenes may be in flight.
    """
    from . import ffmpeg_util as ff

    audio, marks = await asyncio.to_thread(_synthesize_blocking, text, out, cfg,
                                           key, secret, region, engine)
    out.write_bytes(audio)
    # the encoded file is the authority on length, and the last word needs it
    return _timings(marks, ff.duration(out))
