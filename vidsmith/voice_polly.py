"""Narration through Amazon Polly, the other licensed path.

Same purpose as `voice_azure`: edge-tts is an unofficial client for the endpoint
behind Edge's Read Aloud and Microsoft grants no commercial use of it, so
anything with revenue attached needs a service that licenses what it sells.

Polly qualifies because it reports word timings, which almost nothing else does.
The contract is `voice._synthesize_one`'s: write an mp3 and return one dict per
spoken word, `{"text", "start", "end"}`, seconds from the start of that scene.
Get that right and the captions, the shot plan and the mix cannot tell which
engine spoke.

Three things differ from Azure and are the whole reason this is a separate
module rather than a parameter:

**Two calls, not one.** Azure emits boundaries alongside the audio. Polly does
not: audio and speech marks are separate `synthesize_speech` requests, and each
is billed its own characters. A video therefore costs its script length twice.

**No durations, only starts.** A Polly word mark carries `time` in milliseconds
and nothing about how long the word lasts. See `_timings` for what that forces.

**Neural voices ignore pitch.** `prosody` supports rate and volume on neural,
long-form and generative engines and silently drops pitch, so a project moving
here from edge would quietly lose its pitch setting. That is refused loudly
instead - this project has been bitten too many times by settings that appear
to apply and do not.
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


def _client(key: str, secret: str, region: str):
    if not (key and secret and region):
        raise RuntimeError(KEY_HINT)
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(IMPORT_HINT) from exc
    return boto3.client("polly", aws_access_key_id=key,
                        aws_secret_access_key=secret, region_name=region)


def ssml(text: str, cfg: VoiceConfig, engine: str = DEFAULT_ENGINE) -> str:
    """The scene as SSML, carrying whatever prosody this engine honours.

    No `<voice>` element: Polly takes the voice as a request parameter and does
    not support selecting one in the document, unlike Azure.

    The text is escaped because SSML is XML and a script is arbitrary prose. An
    ampersand would otherwise end the document, and this is the only place in
    the pipeline where narration is parsed rather than spoken.
    """
    attrs = [f'rate="{escape(cfg.rate)}"', f'volume="{escape(cfg.volume)}"']
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
    """Async face on a synchronous SDK, as with Azure.

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
