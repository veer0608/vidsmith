"""Narration through Azure Speech, for anyone who has to be licensed for it.

`edge-tts` is the default because it is free and returns word boundaries, but it
is an unofficial client for the endpoint behind Edge's Read Aloud, and Microsoft
publishes no terms permitting commercial use of it. Their own support answers
send commercial users here instead. That is the whole reason this module exists:
not better audio, a licence.

The contract it has to meet is `voice._synthesize_one`'s: write an mp3 and
return one dict per spoken word, `{"text", "start", "end"}`, in seconds from the
start of that scene's audio. Everything downstream - caption timing, the shot
plan, the mix - reads those and nothing else, so a provider that gets this shape
right is invisible to the rest of the pipeline and one that gets it wrong
desynchronises the whole video.

Azure makes that possible because it reports `audio_offset` in the same unit
edge-tts does: ticks of 100 nanoseconds. The arithmetic is identical, which is
why `voice.TICKS` is shared rather than redefined here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape

from .config import VoiceConfig
# Shared, not redefined: both engines report 100-nanosecond ticks, and two
# copies of that constant is two places for it to drift. voice.py imports
# this module lazily, inside the provider dispatch, so this stays one-way.
from .voice import TICKS

# The SDK ships native binaries and is not needed by the default provider, so it
# stays out of requirements.txt and is imported only when someone asks for it.
IMPORT_HINT = (
    "the azure voice provider needs the Speech SDK:\n"
    "  pip install -r requirements-azure.txt"
)
KEY_HINT = (
    "the azure voice provider needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION\n"
    "  (environment, or any .env pipeline.find_keys() reads)"
)


def _sdk():
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:
        raise RuntimeError(IMPORT_HINT) from exc
    return speechsdk


def ssml(text: str, cfg: VoiceConfig) -> str:
    """Wrap a scene in SSML carrying the same prosody edge-tts is given.

    `rate`, `pitch` and `volume` are already written in the form SSML wants
    (`+8%`, `+0Hz`), so a project switching providers keeps its voice settings
    rather than silently reverting to default delivery.

    The text is escaped. A script is arbitrary prose and an ampersand in it
    would otherwise end the document: SSML is XML, and this is the one place in
    the pipeline where the narration is parsed rather than spoken.
    """
    locale = "-".join(cfg.name.split("-")[:2]) if "-" in cfg.name else "en-US"
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{locale}">'
        f'<voice name="{escape(cfg.name)}">'
        f'<prosody rate="{escape(cfg.rate)}" pitch="{escape(cfg.pitch)}" '
        f'volume="{escape(cfg.volume)}">'
        f"{escape(text)}"
        f"</prosody></voice></speak>"
    )


def _synthesize_blocking(text: str, cfg: VoiceConfig, key: str,
                         region: str) -> Tuple[bytes, List[Dict[str, Any]]]:
    """One scene, synchronously. Called in a worker thread by the async wrapper."""
    speechsdk = _sdk()
    if not key or not region:
        raise RuntimeError(KEY_HINT)

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    # 24 kHz mono mp3 to match what edge-tts writes, so scene audio is the same
    # shape whichever provider made it and the mix does not have to care.
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz96KBitRateMonoMp3)

    # audio_config=None keeps the bytes in the result instead of playing them at
    # whatever machine is running the build, which on a laptop is startling and
    # on a server is nothing.
    synth = speechsdk.SpeechSynthesizer(speech_config=speech_config,
                                        audio_config=None)

    words: List[Dict[str, Any]] = []

    def on_boundary(evt) -> None:
        # Word only. Azure also reports punctuation and sentence boundaries, and
        # letting those through would put ',' in the caption stream as if it
        # were spoken - edge-tts reports words with punctuation stripped, and
        # captions.attach_punctuation puts it back from the script.
        if evt.boundary_type != speechsdk.SpeechSynthesisBoundaryType.Word:
            return
        # audio_offset is ticks, the same unit edge-tts reports; duration is
        # already a timedelta, so only the offset needs converting.
        start = evt.audio_offset / TICKS
        words.append({
            "text": evt.text,
            "start": start,
            "end": start + evt.duration.total_seconds(),
        })

    synth.synthesis_word_boundary.connect(on_boundary)
    result = synth.speak_ssml_async(ssml(text, cfg)).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        detail = ""
        if result.reason == speechsdk.ResultReason.Canceled:
            cancel = speechsdk.SpeechSynthesisCancellationDetails(result)
            detail = f": {cancel.reason} {cancel.error_details or ''}".rstrip()
        raise RuntimeError(f"azure speech did not synthesize{detail}")
    if not result.audio_data:
        raise RuntimeError("azure speech returned no audio")

    words.sort(key=lambda w: w["start"])
    return bytes(result.audio_data), words


async def synthesize(text: str, out: Path, cfg: VoiceConfig, key: str,
                     region: str) -> List[Dict[str, Any]]:
    """Async face on a synchronous SDK.

    The Speech SDK is callback-and-blocking, and voice.py is asyncio because
    edge-tts is. Running it in a worker thread keeps the scene-level concurrency
    the rest of the module already manages rather than adding a second, separate
    idea of how many scenes may be in flight.
    """
    audio, words = await asyncio.to_thread(_synthesize_blocking, text, cfg,
                                           key, region)
    out.write_bytes(audio)
    return words
