"""Telling a spent budget apart from a busy minute.

Gemini returns 429 RESOURCE_EXHAUSTED for both, and the two need opposite
handling: a per-minute burst clears on its own, a daily cap does not clear
until tomorrow. Guessing wrong is expensive in both directions - retrying a
spent day burns what little is left, and refusing a busy minute kills a build
that would have finished if it had waited.

The bodies below are real responses, captured from the live API rather than
written from the documentation, because the shape is the whole point.
"""
from __future__ import annotations

import json

import pytest

from vidsmith import llm

DAILY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota. * Quota exceeded for metric: "
                   "generativelanguage.googleapis.com/generate_content_free_tier_"
                   "requests, limit: 500, model: gemini-3.5-flash-lite Please "
                   "retry in 29.492509393s.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{
                 "quotaMetric": "generativelanguage.googleapis.com/"
                                "generate_content_free_tier_requests",
                 "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                 "quotaDimensions": {"model": "gemini-3.5-flash-lite",
                                     "location": "global"},
                 "quotaValue": "500"}]},
            # advertised beside a cap that was still refusing many minutes later
            {"@type": "type.googleapis.com/google.rpc.RetryInfo",
             "retryDelay": "29s"},
        ],
    }
}

PER_MINUTE = {
    "error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{
                 "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                 "quotaDimensions": {"model": "gemini-3.5-flash-lite"},
                 "quotaValue": "15"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo",
             "retryDelay": "56s"},
        ],
    }
}


class Reply:
    """Enough of a requests.Response for the guard to read."""

    def __init__(self, payload, status: int = 429):
        self.status_code = status
        self.text = json.dumps(payload)
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def no_waiting(monkeypatch):
    """Record sleeps instead of taking them, so the suite stays seconds long."""
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    return slept


def _answering(monkeypatch, *replies):
    """Serve these responses in order, and count the calls."""
    calls = []

    def post(*a, **k):
        calls.append(1)
        return replies[min(len(calls) - 1, len(replies) - 1)]

    monkeypatch.setattr(llm.requests, "post", post)
    return calls


# --------------------------------------------------------------------------- #
# the day is gone
# --------------------------------------------------------------------------- #
def test_a_daily_cap_refuses_on_the_first_reply(monkeypatch, no_waiting):
    calls = _answering(monkeypatch, Reply(DAILY))
    with pytest.raises(llm.QuotaExhausted):
        llm.generate("hello", "key")
    assert len(calls) == 1, f"retried a spent day {len(calls)} times"


def test_the_advertised_delay_is_not_waited_out_on_a_daily_cap(monkeypatch, no_waiting):
    """The captured body says "retry in 29s". Four honoured waits of 8s, 56s,
    56s and 52s all met another 429, so the number is not a promise."""
    _answering(monkeypatch, Reply(DAILY))
    with pytest.raises(llm.QuotaExhausted):
        llm.generate("hello", "key")
    assert no_waiting == [], "waited on a delay that was measured and does not help"


def test_the_refusal_names_the_limit_and_the_model(monkeypatch, no_waiting):
    """"Out of quota" leaves you guessing. The budget is per model and per day,
    so the number and the model name are what decide the next move."""
    _answering(monkeypatch, Reply(DAILY))
    with pytest.raises(llm.QuotaExhausted) as exc:
        llm.generate("hello", "key")
    said = str(exc.value)
    assert "500" in said and "gemini-3.5-flash-lite" in said
    assert "requests" in said, "500 tokens and 500 requests are different problems"


def test_a_token_budget_is_not_called_a_request_budget():
    violation = {"quotaId": "GenerateContentInputTokensPerModelPerDay-FreeTier",
                 "quotaDimensions": {"model": "m"}, "quotaValue": "1000000"}
    assert "tokens" in llm._spent_message(violation)


# --------------------------------------------------------------------------- #
# the minute is busy
# --------------------------------------------------------------------------- #
def test_a_per_minute_cap_is_retried_not_refused(monkeypatch, no_waiting):
    """This one really does clear on its own, and aborting the build over it
    throws away every scene already rendered."""
    ok = Reply({"candidates": [{"content": {"parts": [{"text": "fine"}]}}]}, 200)
    calls = _answering(monkeypatch, Reply(PER_MINUTE), ok)
    assert llm.generate("hello", "key") == "fine"
    assert len(calls) == 2


def test_a_per_minute_cap_waits_as_long_as_it_was_told(monkeypatch, no_waiting):
    """The loop's own backoff starts at one second and tops out at eight, which
    cannot outlast a limit measured per minute."""
    ok = Reply({"candidates": [{"content": {"parts": [{"text": "fine"}]}}]}, 200)
    _answering(monkeypatch, Reply(PER_MINUTE), ok)
    llm.generate("hello", "key")
    assert no_waiting == [56.0]


def test_an_absurd_delay_is_clamped(monkeypatch):
    assert llm._retry_after(Reply({"error": {"details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo",
         "retryDelay": "86400s"}]}})) == 75.0


# --------------------------------------------------------------------------- #
# bodies that do not explain themselves
# --------------------------------------------------------------------------- #
def test_an_unexplained_429_still_refuses(monkeypatch, no_waiting):
    """No QuotaFailure detail means no evidence it is the recoverable kind, and
    the costly mistake is to keep spending against a budget that is gone."""
    calls = _answering(monkeypatch, Reply({"error": {"status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(llm.QuotaExhausted):
        llm.generate("hello", "key")
    assert len(calls) == 1


def test_a_body_that_is_not_json_does_not_crash_the_guard():
    """A proxy between here and Google can answer with an HTML page."""
    class Html:
        status_code = 429
        text = "<!DOCTYPE html><h1>429 Too Many Requests</h1>RESOURCE_EXHAUSTED"

        def json(self):
            raise ValueError("no json")

    with pytest.raises(llm.QuotaExhausted):
        llm._refuse_if_spent(Html())


def test_an_ordinary_failure_is_left_alone():
    """503 is the retry loop's business, not the quota guard's."""
    assert llm._refuse_if_spent(Reply({"error": {}}, 503)) == 0.0


@pytest.mark.parametrize("call", [
    lambda: llm.generate("hello", "key"),
    lambda: llm.generate_vision("hello", [b"a", b"b"], "key"),
])
def test_both_request_paths_read_the_quota_the_same_way(monkeypatch, no_waiting, call):
    """The guard was once added to the text path only, and every thumbnail went
    on spending three requests against a number that only tomorrow restores."""
    calls = _answering(monkeypatch, Reply(DAILY))
    with pytest.raises(llm.QuotaExhausted):
        call()
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# a deliberate wait has to look different from a hang
# --------------------------------------------------------------------------- #
def test_a_long_wait_is_announced(monkeypatch, no_waiting):
    """Reranking runs once per scene, so a per-minute limit can stack several
    of these inside one build. Silence for a minute reads as a hang, and the
    repo's own rule is that a hang has to name itself."""
    ok = Reply({"candidates": [{"content": {"parts": [{"text": "fine"}]}}]}, 200)
    _answering(monkeypatch, Reply(PER_MINUTE), ok)
    said = []
    llm.generate("hello", "key", log=said.append)
    assert len(said) == 1, said
    assert "56s" in said[0] and "rate limit" in said[0]


def test_a_short_backoff_stays_quiet(monkeypatch, no_waiting):
    """One second of retry is not worth a line; the log is read by someone
    watching a render, not debugging the client."""
    ok = Reply({"candidates": [{"content": {"parts": [{"text": "fine"}]}}]}, 200)
    _answering(monkeypatch, Reply({"error": {}}, 503), ok)
    said = []
    llm.generate("hello", "key", log=said.append)
    assert said == []


def test_the_wait_is_announced_on_the_vision_path_too(monkeypatch, no_waiting):
    """The path that actually loops: one vision call per scene."""
    ok = Reply({"candidates": [{"content": {"parts": [{"text": "fine"}]}}]}, 200)
    _answering(monkeypatch, Reply(PER_MINUTE), ok)
    said = []
    llm.generate_vision("hello", [b"a", b"b"], "key", log=said.append)
    assert len(said) == 1 and "56s" in said[0]


def test_reranking_hands_the_build_log_down(monkeypatch, no_waiting):
    """Without this the wait is announced into a log nobody is reading."""
    import inspect

    from vidsmith import visuals

    source = inspect.getsource(visuals)
    assert "llm.rank_clips(scene.text, query, images, key, log=self.log)" in source, \
        "rank_clips is called without the build log, so a wait would be silent"
    assert "log" in inspect.signature(llm.rank_clips).parameters
