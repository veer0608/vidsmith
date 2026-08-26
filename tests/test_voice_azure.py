"""The Azure narration provider.

It exists for a licence, not for audio quality: edge-tts is an unofficial client
for the endpoint behind Edge's Read Aloud and Microsoft grants no commercial use
of it, so anything with revenue attached needs this path.

Which makes the contract the only thing worth testing here. Word timings drive
the captions, the shot plan and the mix, so a provider is correct exactly when
its words come out in the same shape edge-tts produces: `{"text", "start",
"end"}`, seconds from the start of that scene, punctuation stripped. The SDK is
faked throughout - these tests are about the adapter, and a real key would make
them slow, paid and unrunnable on CI.
"""
from __future__ import annotations

import asyncio
import types
from datetime import timedelta

import pytest

from vidsmith import voice, voice_azure
from vidsmith.config import VoiceConfig

TICKS = 1e7


# --------------------------------------------------------------------------- #
# a fake SDK, shaped like the real one
# --------------------------------------------------------------------------- #
class _Boundary:
    """Matches azure.cognitiveservices.speech.SpeechSynthesisWordBoundaryEventArgs.

    audio_offset in 100ns ticks, duration a timedelta - both verified against
    the installed SDK rather than assumed.
    """

    def __init__(self, text, offset_ticks, duration_s, kind):
        self.text = text
        self.audio_offset = offset_ticks
        self.duration = timedelta(seconds=duration_s)
        self.boundary_type = kind


def _fake_sdk(boundaries, audio=b"ID3-fake-mp3", completed=True, cancel_detail=""):
    m = types.ModuleType("azure.cognitiveservices.speech")

    class BoundaryType:
        Word, Punctuation, Sentence = "word", "punct", "sentence"

    class ResultReason:
        SynthesizingAudioCompleted, Canceled = "done", "canceled"

    class Result:
        def __init__(self):
            self.reason = (ResultReason.SynthesizingAudioCompleted if completed
                           else ResultReason.Canceled)
            self.audio_data = audio

    class Synthesizer:
        def __init__(self, speech_config=None, audio_config=None):
            self.spoken = None
            self.synthesis_word_boundary = self

        def connect(self, fn):
            self._cb = fn

        def speak_ssml_async(self, ssml):
            Synthesizer.last_ssml = ssml
            for b in boundaries:
                self._cb(b)
            return types.SimpleNamespace(get=lambda: Result())

    class SpeechConfig:
        def __init__(self, subscription=None, region=None):
            self.subscription, self.region = subscription, region

        def set_speech_synthesis_output_format(self, fmt):
            self.fmt = fmt

    m.SpeechSynthesisBoundaryType = BoundaryType
    m.ResultReason = ResultReason
    m.SpeechSynthesizer = Synthesizer
    m.SpeechConfig = SpeechConfig
    m.SpeechSynthesisOutputFormat = types.SimpleNamespace(
        Audio24Khz96KBitRateMonoMp3="mp3-96")
    m.SpeechSynthesisCancellationDetails = lambda r: types.SimpleNamespace(
        reason="Denied", error_details=cancel_detail)
    m._Synthesizer = Synthesizer
    return m


@pytest.fixture
def sdk(monkeypatch):
    """Install a fake SDK for the duration of one test."""
    def install(boundaries, **kw):
        module = _fake_sdk(boundaries, **kw)
        monkeypatch.setattr(voice_azure, "_sdk", lambda: module)
        return module
    return install


WORDS = [
    _Boundary("Your", int(0.10 * TICKS), 0.30, "word"),
    _Boundary(",", int(0.40 * TICKS), 0.05, "punct"),
    _Boundary("bank", int(0.45 * TICKS), 0.35, "word"),
    _Boundary("statement", int(0.85 * TICKS), 0.50, "word"),
]


# --------------------------------------------------------------------------- #
# the shape the rest of the pipeline reads
# --------------------------------------------------------------------------- #
def test_words_come_back_in_seconds(sdk, tmp_path):
    """Ticks in, seconds out. Everything downstream reads seconds."""
    sdk(WORDS)
    out = tmp_path / "scene.mp3"
    words = asyncio.run(voice_azure.synthesize("Your bank statement", out,
                                               VoiceConfig(), "k", "eastus"))
    assert [w["text"] for w in words] == ["Your", "bank", "statement"]
    assert words[0]["start"] == pytest.approx(0.10)
    assert words[0]["end"] == pytest.approx(0.40)
    assert words[-1]["end"] == pytest.approx(1.35)


def test_punctuation_boundaries_are_dropped(sdk, tmp_path):
    """Azure reports commas as boundaries; edge-tts does not.

    Letting them through would put ',' in the caption stream as a spoken word.
    captions.attach_punctuation puts punctuation back from the script, and it
    matches on the words the engine actually said.
    """
    sdk(WORDS)
    words = asyncio.run(voice_azure.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "eastus"))
    assert "," not in [w["text"] for w in words]


def test_the_shape_matches_what_edge_produces(sdk, tmp_path):
    """The contract, stated as a test: same keys, same types, same units."""
    sdk(WORDS)
    words = asyncio.run(voice_azure.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "eastus"))
    for w in words:
        assert set(w) == {"text", "start", "end"}
        assert isinstance(w["text"], str)
        assert isinstance(w["start"], float) and isinstance(w["end"], float)
        assert w["end"] > w["start"]
    assert all(a["start"] <= b["start"] for a, b in zip(words, words[1:]))


def test_the_audio_is_written(sdk, tmp_path):
    sdk(WORDS, audio=b"ID3-some-bytes")
    out = tmp_path / "scene.mp3"
    asyncio.run(voice_azure.synthesize("x", out, VoiceConfig(), "k", "eastus"))
    assert out.read_bytes() == b"ID3-some-bytes"


# --------------------------------------------------------------------------- #
# SSML
# --------------------------------------------------------------------------- #
def test_the_script_is_escaped(sdk):
    """SSML is XML and a script is arbitrary prose.

    An unescaped ampersand ends the document, and this is the only place in the
    pipeline where the narration is parsed rather than spoken.
    """
    doc = voice_azure.ssml("Tom & Jerry <b> \"quoted\"", VoiceConfig())
    assert "&amp;" in doc and "&lt;b&gt;" in doc
    assert "Tom & Jerry" not in doc


def test_prosody_carries_the_project_settings():
    """A project switching providers keeps its voice, not a default delivery."""
    cfg = VoiceConfig(rate="+15%", pitch="-2Hz", volume="+3%")
    doc = voice_azure.ssml("hello", cfg)
    assert 'rate="+15%"' in doc and 'pitch="-2Hz"' in doc and 'volume="+3%"' in doc


def test_the_locale_comes_off_the_voice_name():
    assert 'xml:lang="en-IN"' in voice_azure.ssml(
        "hi", VoiceConfig(name="en-IN-PrabhatNeural"))


# --------------------------------------------------------------------------- #
# failing loudly
# --------------------------------------------------------------------------- #
def test_a_missing_key_says_which_one(sdk, tmp_path):
    """Narration is not optional, so this fails rather than degrading.

    Every other keyed feature here falls back to something. There is no
    fallback for having no voice, and silently reverting to edge would put the
    user back on the path they moved off for licensing reasons.
    """
    sdk(WORDS)
    with pytest.raises(RuntimeError, match="AZURE_SPEECH_KEY"):
        asyncio.run(voice_azure.synthesize("x", tmp_path / "s.mp3",
                                           VoiceConfig(), "", "eastus"))


def test_a_missing_region_is_refused_too(sdk, tmp_path):
    sdk(WORDS)
    with pytest.raises(RuntimeError, match="AZURE_SPEECH_REGION"):
        asyncio.run(voice_azure.synthesize("x", tmp_path / "s.mp3",
                                           VoiceConfig(), "k", ""))


def test_a_cancelled_synthesis_reports_why(sdk, tmp_path):
    sdk(WORDS, completed=False, cancel_detail="quota exceeded")
    with pytest.raises(RuntimeError, match="quota exceeded"):
        asyncio.run(voice_azure.synthesize("x", tmp_path / "s.mp3",
                                           VoiceConfig(), "k", "eastus"))


def test_empty_audio_is_an_error(sdk, tmp_path):
    sdk(WORDS, audio=b"")
    with pytest.raises(RuntimeError, match="no audio"):
        asyncio.run(voice_azure.synthesize("x", tmp_path / "s.mp3",
                                           VoiceConfig(), "k", "eastus"))


def test_a_missing_sdk_names_the_install(monkeypatch):
    """The SDK is optional on purpose; the error has to say how to get it."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("azure"):
            raise ImportError("no module named azure")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="requirements-azure.txt"):
        voice_azure._sdk()


# --------------------------------------------------------------------------- #
# the fake, checked against the real thing
# --------------------------------------------------------------------------- #


def test_the_fake_matches_the_real_sdk():
    """A fake that has drifted from the SDK tests nothing at all.

    Skipped where the SDK is absent, which is most places - it is deliberately
    not in requirements.txt. Where it is installed, this is what stops the
    tests above from passing against a shape Azure never produces.
    """
    speechsdk = pytest.importorskip(
        "azure.cognitiveservices.speech",
        reason="the Speech SDK is an optional dependency")

    args = speechsdk.SpeechSynthesisWordBoundaryEventArgs
    for attr in ("text", "audio_offset", "duration", "boundary_type"):
        assert hasattr(args, attr), f"the SDK no longer exposes {attr}"

    for name in ("Word", "Punctuation", "Sentence"):
        assert hasattr(speechsdk.SpeechSynthesisBoundaryType, name)
    assert hasattr(speechsdk.ResultReason, "SynthesizingAudioCompleted")
    assert hasattr(speechsdk.ResultReason, "Canceled")
    assert hasattr(speechsdk.SpeechSynthesisOutputFormat,
                   "Audio24Khz96KBitRateMonoMp3")


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
def test_edge_stays_the_default():
    """Nobody is moved onto a paid service by upgrading."""
    assert VoiceConfig().provider == "edge"


def test_the_provider_setting_picks_the_path(sdk, tmp_path, monkeypatch):
    sdk(WORDS)
    called = {}

    async def not_edge(*a, **k):
        called["edge"] = True
        return []
    monkeypatch.setattr(voice, "_edge", not_edge)

    from vidsmith.script_parser import Scene
    scene = Scene(index=0, text="Your bank statement")
    words = asyncio.run(voice._synthesize_one(
        scene, tmp_path / "s.mp3", VoiceConfig(provider="azure"),
        {"azure_speech": "k", "azure_region": "eastus"}))

    assert "edge" not in called, "azure was configured but edge ran"
    assert [w["text"] for w in words] == ["Your", "bank", "statement"]
