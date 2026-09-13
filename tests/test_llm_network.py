"""A network that drops the call has to look like a model that did not answer.

Every optional Gemini feature degrades by catching `LLMUnavailable`, and nothing
else. A connection reset used to escape both request loops as a requests
exception, which walked straight past every one of those handlers: a
`bench.rank_clips run` died on `ConnectionResetError(10054)` after 122 good
calls, and `/api/draft`, `thumbs --refresh` and the b-roll query step would
have gone the same way.
"""
from __future__ import annotations

import argparse
import inspect
import json

import pytest
import requests

from conftest import make_scene
from vidsmith import cli, llm
from vidsmith.config import write_default_config
from vidsmith.script_parser import save_scenes

KEY = "AIzaSECRET-not-a-real-key"
RESET = requests.exceptions.ConnectionError(
    "('Connection aborted.', ConnectionResetError(10054, 'An existing connection "
    "was forcibly closed by the remote host', None, 10054, None))")
# the shape urllib3 really produces for a DNS failure, checked against a live
# lookup: the whole URL is quoted, query string and key included
DNS = requests.exceptions.ConnectionError(
    "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): Max "
    f"retries exceeded with url: /v1beta/models/m:generateContent?key={KEY} "
    "(Caused by NameResolutionError(\"Failed to resolve\"))")


class Reply:
    def __init__(self, payload, status: int = 200):
        self.status_code = status
        self.text = json.dumps(payload)
        self._payload = payload

    def json(self):
        return self._payload


OK = Reply({"candidates": [{"content": {"parts": [{"text": "fine"}]}}]})


@pytest.fixture
def no_waiting(monkeypatch):
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    return slept


def _answering(monkeypatch, *outcomes):
    """Raise or return these in order, repeating the last, and count the calls."""
    calls = []

    def post(*a, **k):
        calls.append(1)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(llm.requests, "post", post)
    return calls


PATHS = [
    pytest.param(lambda: llm.generate("hello", KEY), 4, id="generate"),
    pytest.param(lambda: llm.generate_vision("hello", [b"a", b"b"], KEY), 3,
                 id="generate_vision"),
]


@pytest.mark.parametrize("call, retries", PATHS)
def test_a_reset_that_clears_is_retried(monkeypatch, no_waiting, call, retries):
    calls = _answering(monkeypatch, RESET, OK)
    assert call() == "fine"
    assert len(calls) == 2
    assert no_waiting == [1], "a network failure should back off like a 503"


@pytest.mark.parametrize("error", [
    RESET, DNS, requests.exceptions.Timeout("read timed out"),
], ids=["reset", "dns", "timeout"])
@pytest.mark.parametrize("call, retries", PATHS)
def test_a_failure_that_persists_is_llm_unavailable(monkeypatch, no_waiting,
                                                   call, retries, error):
    calls = _answering(monkeypatch, error)
    with pytest.raises(llm.LLMUnavailable) as exc:
        call()
    assert not isinstance(exc.value, requests.RequestException)
    assert len(calls) == retries
    said = str(exc.value)
    assert type(error).__name__ in said, said
    assert "network" in said, said


@pytest.mark.parametrize("call, retries", PATHS)
def test_the_message_does_not_carry_the_key(monkeypatch, no_waiting, call, retries):
    """The key rides in the query string and urllib3 quotes the URL. This text
    lands in a 502 body, the build log and bench results files."""
    _answering(monkeypatch, DNS)
    with pytest.raises(llm.LLMUnavailable) as exc:
        call()
    assert KEY not in str(exc.value)
    assert "Max retries exceeded" in str(exc.value), "redacted too much to read"


def test_a_network_failure_mid_loop_does_not_eat_a_real_error(monkeypatch, no_waiting):
    """A reset then a 400 is a 400: the network note must not mask it."""
    _answering(monkeypatch, RESET, Reply({"error": "bad request"}, 400))
    with pytest.raises(llm.LLMUnavailable) as exc:
        llm.generate("hello", KEY)
    assert "HTTP 400" in str(exc.value)


def test_every_request_loop_handles_the_network():
    """The invariant, not the two loops that currently satisfy it.

    A fix landing on one of the two request loops and not the other has happened
    twice in this module already. Any function that posts has to catch
    requests' own exceptions; a third loop that forgets fails here by name.
    """
    posting, bare = [], []
    for name, fn in sorted(vars(llm).items()):
        if not inspect.isfunction(fn) or fn.__module__ != "vidsmith.llm":
            continue
        source = inspect.getsource(fn)
        if "requests.post(" not in source:
            continue
        posting.append(name)
        if "except requests.RequestException" not in source:
            bare.append(name)

    assert len(posting) >= 2, f"found only {posting}; the scan no longer sees the loops"
    assert not bare, "a network failure escapes as a requests error from: " + ", ".join(bare)


# --------------------------------------------------------------------------- #
# the callers that catch LLMUnavailable and nothing broader
# --------------------------------------------------------------------------- #
def test_the_query_step_falls_back_instead_of_failing_the_build(monkeypatch, no_waiting):
    """suggest_queries catches (LLMUnavailable, ValueError) only, so a reset
    here used to end the whole build at the queries stage."""
    _answering(monkeypatch, RESET)
    scenes = [make_scene("Your bank statement is not a record of what you spent.")]
    said = []
    assert llm.suggest_queries(scenes, KEY, log=said.append) == 0
    assert any("falling back to keywords" in line for line in said), said


def test_a_thumbnail_refresh_refuses_on_a_dead_network(tmp_path, monkeypatch,
                                                      no_waiting, capsys):
    """`thumbs --refresh` catches LLMUnavailable to leave the thumbnails alone.
    A requests error went past it as a traceback."""
    root = tmp_path / "proj"
    (root / "build").mkdir(parents=True)
    (root / "out").mkdir()
    write_default_config(root / "config.yaml", "Bank Statements")
    (root / "script.md").write_text("# Bank Statements\n\nA line.\n", encoding="utf-8")
    save_scenes([make_scene("Your bank statement is not a record.")],
                root / "build" / "scenes.json")
    (root / "build" / "picture.mp4").write_bytes(b"")
    thumb = root / "out" / "bank-statements.jpg"
    thumb.write_bytes(b"the existing thumbnail")

    monkeypatch.setattr(cli, "find_keys", lambda _root: {"gemini": KEY, "pexels": "p"})
    _answering(monkeypatch, RESET)

    assert cli._refresh_thumbnails(argparse.Namespace(name=str(root))) == 1
    out = capsys.readouterr().out
    assert "not refreshing" in out and "the network failed" in out
    assert KEY not in out
    assert thumb.read_bytes() == b"the existing thumbnail"
