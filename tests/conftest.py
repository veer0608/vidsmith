"""Scene fixtures that look like real edge-tts output.

Word timings drive the edit, the captions and the mix, so almost every invariant
worth testing needs a Scene whose words are shaped the way the TTS reports them:
punctuation stripped, times in seconds from the start of the speech.
"""
from __future__ import annotations

import re
from typing import List, Optional

import pytest

from vidsmith.pipeline import KEY_ENV
from vidsmith.script_parser import Scene


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch):
    """No test reads the credentials of the machine it runs on.

    `config.env()` prefers `os.environ` over every dotenv, deliberately, so a
    developer with real keys exported hands them to anything that resolves one.
    `test_env_handles_a_bom` asserted against a live `GEMINI_API_KEY` that way:
    red on the machine it was written on, green in CI, and read as a `main`
    failure through two PRs. Clearing them here also means no test can spend
    real Gemini quota or reach a provider by accident.

    The names come from `KEY_ENV` rather than a list kept here, so a credential
    added there is covered without anyone remembering this fixture exists.
    """
    for var in KEY_ENV.values():
        monkeypatch.delenv(var, raising=False)


def make_scene(text: str, index: int = 0, wps: float = 2.6,
               duration: Optional[float] = None, gap: float = 0.35,
               lead_in: float = 0.25, **kwargs) -> Scene:
    """A scene whose word timings are consistent with its text and duration."""
    tokens = [re.sub(r"[^\w'-]", "", w) for w in text.split()]
    tokens = [t for t in tokens if t]

    step = 1.0 / wps
    words = []
    for i, token in enumerate(tokens):
        start = i * step
        words.append({"text": token, "start": start, "end": start + step * 0.82})

    spoken = words[-1]["end"] if words else 0.0
    scene = Scene(index=index, text=text, **kwargs)
    scene.words = words
    scene.duration = duration if duration is not None else lead_in + spoken + gap
    return scene


@pytest.fixture
def scene():
    return make_scene(
        "Your bank statement is not a record of what you spent. "
        "It is a record of what your bank found convenient to store."
    )


@pytest.fixture
def scenes() -> List[Scene]:
    out = [
        make_scene("Your bank statement is not a record of what you spent. "
                   "It is a record of what your bank found convenient to store.",
                   index=0),
        make_scene("The merchant name is typed by the payment processor, not the "
                   "shop. That is why a coffee costs money at a company you have "
                   "never heard of.", index=1),
        make_scene("Reconcile against receipts, not memory.", index=2),
    ]
    clock = 0.0
    for s in out:
        s.start = clock
        clock += s.duration
    return out
