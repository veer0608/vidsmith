"""The Polly narration provider.

The licensed path: edge-tts is unofficial and Microsoft grants no commercial
use of it, so anything with revenue attached needs this one.

Polly differs from edge-tts in a way that matters, and most of this file is
about that difference. A Polly word mark carries a start time and nothing else -
no duration - so an end has to be constructed rather than read.
Get that wrong and every caption is the wrong length, silently, in a way that
only shows up on screen.

boto3 is faked throughout: these tests are about the adapter, and real
credentials would make them slow, billed and unrunnable on CI.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from vidsmith import voice, voice_polly
from vidsmith.config import VoiceConfig

MARKS = b"\n".join([
    json.dumps({"time": 100, "type": "word", "value": "Your"}).encode(),
    json.dumps({"time": 400, "type": "sentence", "value": "Your bank"}).encode(),
    json.dumps({"time": 450, "type": "word", "value": "bank"}).encode(),
    json.dumps({"time": 850, "type": "word", "value": "statement"}).encode(),
])


def _fake_boto(marks=MARKS, audio=b"ID3-fake-mp3"):
    calls = []

    class Client:
        def synthesize_speech(self, **kw):
            calls.append(kw)
            body = audio if kw["OutputFormat"] == "mp3" else marks
            return {"AudioStream": types.SimpleNamespace(read=lambda: body)}

    module = types.ModuleType("boto3")
    module.client = lambda *a, **k: Client()
    module.calls = calls
    return module


@pytest.fixture
def boto(monkeypatch):
    def install(**kw):
        module = _fake_boto(**kw)
        monkeypatch.setattr(voice_polly, "_client",
                            lambda *a, **k: module.client())
        return module
    return install


@pytest.fixture(autouse=True)
def stub_duration(monkeypatch):
    """The last word ends at the audio's length, which needs no real encode."""
    from vidsmith import ffmpeg_util as ff
    monkeypatch.setattr(ff, "duration", lambda p: 1.40)


# --------------------------------------------------------------------------- #
# the timings, which Polly does not fully provide
# --------------------------------------------------------------------------- #
def test_marks_become_seconds(boto, tmp_path):
    boto()
    words = asyncio.run(voice_polly.synthesize("Your bank statement",
                                               tmp_path / "s.mp3", VoiceConfig(),
                                               "k", "s", "ap-south-1"))
    assert [w["text"] for w in words] == ["Your", "bank", "statement"]
    assert words[0]["start"] == pytest.approx(0.10)
    assert words[1]["start"] == pytest.approx(0.45)


def test_a_word_ends_where_the_next_one_starts(boto, tmp_path):
    """Polly gives no durations, so ends are constructed.

    Contiguous rather than gapped is deliberate: a karaoke highlight that drops
    out between words reads as a stutter.
    """
    boto()
    words = asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "s", "r"))
    assert words[0]["end"] == pytest.approx(words[1]["start"])
    assert words[1]["end"] == pytest.approx(words[2]["start"])


def test_the_last_word_ends_at_the_audio_length(boto, tmp_path):
    """There is no next word to borrow from, and guessing outlives the audio."""
    boto()
    words = asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "s", "r"))
    assert words[-1]["end"] == pytest.approx(1.40)


def test_sentence_marks_are_ignored(boto, tmp_path):
    """Only `word` marks are words; a sentence mark would duplicate the text."""
    boto()
    words = asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "s", "r"))
    assert "Your bank" not in [w["text"] for w in words]


def test_the_shape_matches_the_other_providers(boto, tmp_path):
    boto()
    words = asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "s", "r"))
    for w in words:
        assert set(w) == {"text", "start", "end"}
        assert isinstance(w["start"], float) and isinstance(w["end"], float)
        assert w["end"] >= w["start"]


def test_a_malformed_mark_line_is_survivable(boto, tmp_path):
    boto(marks=b'{"time": 100, "type": "word", "value": "Your"}\nnot json\n')
    words = asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "s", "r"))
    assert [w["text"] for w in words] == ["Your"]


# --------------------------------------------------------------------------- #
# two requests, not one
# --------------------------------------------------------------------------- #
def test_audio_and_marks_are_separate_calls(boto, tmp_path):
    """Polly bills them separately, so a video costs its script length twice."""
    module = boto()
    asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3", VoiceConfig(),
                                       "k", "s", "r"))
    formats = [c["OutputFormat"] for c in module.calls]
    assert formats == ["mp3", "json"]
    marks_call = module.calls[1]
    assert marks_call["SpeechMarkTypes"] == ["word"]


def test_the_audio_is_written(boto, tmp_path):
    boto(audio=b"ID3-real-bytes")
    out = tmp_path / "s.mp3"
    asyncio.run(voice_polly.synthesize("x", out, VoiceConfig(), "k", "s", "r"))
    assert out.read_bytes() == b"ID3-real-bytes"


# --------------------------------------------------------------------------- #
# SSML, which Polly reads its own way
# --------------------------------------------------------------------------- #
def test_no_voice_element():
    """Polly takes the voice as a parameter and rejects it in the document."""
    assert "<voice" not in voice_polly.ssml("hello", VoiceConfig())


def test_the_script_is_escaped():
    doc = voice_polly.ssml("Tom & Jerry <b>", VoiceConfig())
    assert "&amp;" in doc and "&lt;b&gt;" in doc


def test_neural_prosody_omits_pitch():
    """AWS documents pitch as unavailable on neural; sending it is noise."""
    doc = voice_polly.ssml("hi", VoiceConfig(), engine="neural")
    assert "rate=" in doc and "volume=" in doc and "pitch=" not in doc


def test_standard_prosody_keeps_pitch():
    doc = voice_polly.ssml("hi", VoiceConfig(pitch="-2Hz"), engine="standard")
    assert 'pitch="-2Hz"' in doc


def test_a_pitch_neural_cannot_honour_is_refused(boto, tmp_path):
    """The setting would be dropped in silence otherwise.

    This project's recurring failure is a value that looks applied and is not,
    so a pitch the engine will ignore stops the build rather than the video.
    """
    boto()
    with pytest.raises(RuntimeError, match="pitch"):
        asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                           VoiceConfig(pitch="-3Hz"),
                                           "k", "s", "r"))


def test_the_default_pitch_is_not_treated_as_a_change(boto, tmp_path):
    boto()
    words = asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(pitch="+0Hz"),
                                               "k", "s", "r"))
    assert words, "a default pitch must not block the neural engine"


# --------------------------------------------------------------------------- #
# failing loudly
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,secret,region", [
    ("", "s", "r"), ("k", "", "r"), ("k", "s", ""),
])
def test_missing_credentials_are_refused(key, secret, region):
    with pytest.raises(RuntimeError, match="AWS_"):
        voice_polly._client(key, secret, region)


def test_empty_audio_is_an_error(boto, tmp_path):
    boto(audio=b"")
    with pytest.raises(RuntimeError, match="no audio"):
        asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                           VoiceConfig(), "k", "s", "r"))


def test_a_missing_boto3_names_the_install(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "boto3":
            raise ImportError("no module named boto3")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="requirements-polly.txt"):
        voice_polly._client("k", "s", "r")


# --------------------------------------------------------------------------- #
# dispatch, and the fake against the real SDK
# --------------------------------------------------------------------------- #
def test_the_provider_setting_picks_polly(boto, tmp_path, monkeypatch):
    boto()
    called = {}

    async def not_edge(*a, **k):
        called["edge"] = True
        return []
    monkeypatch.setattr(voice, "_edge", not_edge)

    from vidsmith.script_parser import Scene
    words = asyncio.run(voice._synthesize_one(
        Scene(index=0, text="Your bank statement"), tmp_path / "s.mp3",
        VoiceConfig(provider="polly"),
        {"aws_key": "k", "aws_secret": "s", "aws_region": "ap-south-1"}))

    assert "edge" not in called
    assert [w["text"] for w in words] == ["Your", "bank", "statement"]


def test_the_fake_matches_the_real_api():
    """A fake that has drifted from the service tests nothing.

    Checked against boto3's own service model, which ships with the library and
    needs no credentials, so this is real verification rather than a restatement
    of what the fake does. Skipped where boto3 is absent - it is deliberately
    not in requirements.txt.
    """
    pytest.importorskip("boto3", reason="boto3 is an optional dependency")
    import botocore.session

    op = (botocore.session.get_session()
          .get_service_model("polly")
          .operation_model("SynthesizeSpeech"))
    members = set(op.input_shape.members)
    for param in ("Text", "TextType", "VoiceId", "Engine", "OutputFormat",
                  "SpeechMarkTypes"):
        assert param in members, f"polly no longer accepts {param}"

    assert "json" in op.input_shape.members["OutputFormat"].enum
    assert "word" in op.input_shape.members["SpeechMarkTypes"].member.enum
    for engine in voice_polly.PITCHLESS_ENGINES:
        assert engine in op.input_shape.members["Engine"].enum
    assert "AudioStream" in op.output_shape.members


# --------------------------------------------------------------------------- #
# the engine, which decides whether timings exist at all
# --------------------------------------------------------------------------- #
def test_generative_is_refused_because_it_has_no_speech_marks(boto, tmp_path):
    """AWS lists speech marks on standard, neural and long-form. Not generative.

    It synthesizes perfectly and reports no word timings, which here means a
    finished video with no captions and one held shot per scene. Silent, at the
    end of a paid render. Exactly the failure this project keeps having.
    """
    boto()
    with pytest.raises(RuntimeError, match="no speech marks"):
        asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3", VoiceConfig(),
                                           "k", "s", "r", engine="generative"))


def test_generative_is_not_even_a_configurable_value():
    from vidsmith.config import _CLOSED_SETS
    assert "generative" not in _CLOSED_SETS[("voice", "engine")]


@pytest.mark.parametrize("engine", ["standard", "neural", "long-form"])
def test_every_configurable_engine_can_report_marks(boto, tmp_path, engine):
    boto()
    words = asyncio.run(voice_polly.synthesize("x", tmp_path / "s.mp3",
                                               VoiceConfig(), "k", "s", "r",
                                               engine=engine))
    assert [w["text"] for w in words] == ["Your", "bank", "statement"]


def test_the_configured_engine_reaches_polly(boto, tmp_path, monkeypatch):
    """A long-form voice fails on the neural engine, so this must be honoured."""
    module = boto()
    monkeypatch.setattr(voice_polly, "_client", lambda *a, **k: module.client())

    from vidsmith.script_parser import Scene
    asyncio.run(voice._synthesize_one(
        Scene(index=0, text="x"), tmp_path / "s.mp3",
        VoiceConfig(provider="polly", engine="long-form", name="Gregory"),
        {"aws_key": "k", "aws_secret": "s", "aws_region": "us-east-1"}))

    assert module.calls[0]["Engine"] == "long-form"
    assert module.calls[0]["VoiceId"] == "Gregory"


def test_an_empty_mark_stream_yields_no_words():
    """What generative would have produced, stated so the refusal has a reason."""
    assert voice_polly._timings(b"", 5.0) == []


# --------------------------------------------------------------------------- #
# volume, where the two services disagree about the unit
# --------------------------------------------------------------------------- #
def test_a_percentage_volume_becomes_decibels():
    """edge-tts writes percent; Polly rejects it outright.

    Measured against the live service, not read off the docs: rate="+8%" is
    accepted and volume="+0%" returns InvalidSsmlException - a bare "Invalid
    SSML request" that names no attribute, so the failure points nowhere. Every
    scene of a project switching provider would have died on it.
    """
    assert voice_polly.polly_volume("+0%").endswith("dB")
    assert voice_polly.polly_volume("+0%").startswith("+0")


def test_the_conversion_is_a_conversion_not_a_relabel():
    """A percentage is linear gain and a decibel is logarithmic."""
    assert voice_polly.polly_volume("+50%") == "+3.5dB"
    assert voice_polly.polly_volume("-50%") == "-6.0dB"


@pytest.mark.parametrize("value", ["+6dB", "-3dB", "loud", "x-soft", "medium"])
def test_pollys_own_vocabulary_passes_through(value):
    assert voice_polly.polly_volume(value) == value


@pytest.mark.parametrize("value", ["", "nonsense", "++%"])
def test_anything_unreadable_falls_back_to_no_change(value):
    """A bad volume must not cost a build that has already paid for a script."""
    assert voice_polly.polly_volume(value) == "+0dB"


def test_silence_is_named_rather_than_negative_infinity():
    assert voice_polly.polly_volume("-100%") == "silent"


def test_the_ssml_carries_the_converted_volume():
    doc = voice_polly.ssml("hi", VoiceConfig(volume="+0%"), engine="long-form")
    assert "+0%" not in doc
    assert "dB" in doc


# --------------------------------------------------------------------------- #
# the quickstart a buyer follows
# --------------------------------------------------------------------------- #
def _quickstart() -> dict:
    """The `voice:` block COMMERCIAL.md tells a paying user to paste."""
    import re
    from pathlib import Path

    import yaml

    doc = (Path(__file__).resolve().parents[1] / "COMMERCIAL.md").read_text(
        encoding="utf-8")
    block = re.search(r"```yaml\n(voice:.*?)```", doc, re.S)
    assert block, "COMMERCIAL.md no longer carries a voice: quickstart block"
    return yaml.safe_load(block.group(1))["voice"]


def test_the_quickstart_names_the_polly_provider():
    assert _quickstart()["provider"] == "polly"


def test_the_quickstart_engine_can_actually_return_speech_marks():
    """`generative` is the one engine that cannot time captions or the cut.

    A quickstart is pasted rather than read, so an engine named there that the
    code refuses would be a refusal at the buyer, on their first build, right
    after paying.
    """
    engine = _quickstart()["engine"]
    assert engine not in voice_polly.NO_SPEECH_MARKS
    assert engine in ("standard", "neural", "long-form"), engine


def test_the_quickstart_pitch_is_accepted_by_the_engine_it_names():
    """neural and long-form refuse a prosody pitch rather than ignoring it."""
    quick = _quickstart()
    if quick["engine"] in voice_polly.PITCHLESS_ENGINES:
        assert str(quick["pitch"]).strip() in ("", "+0Hz", "0Hz"), \
            "the quickstart pastes a pitch its own engine will refuse"


def test_the_quickstart_voice_is_not_an_edge_tts_name():
    """The likeliest mistake, and the one the quickstart exists to prevent.

    `provider` is the obvious key to change and the config already carries a
    name, so an edge-tts VoiceId survives the edit and Polly rejects it.
    """
    name = _quickstart()["name"]
    assert not name.endswith("Neural"), name
    assert "-" not in name, f"{name} looks like an edge-tts name, not a VoiceId"
