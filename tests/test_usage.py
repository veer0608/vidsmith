"""Knowing how much free allowance is left before a render spends it.

Both limits fail in silence: Pexels running out turns a video into generated
cards, Gemini running out turns searches into keywords and drops the
description. Pexels reports its allowance on every response; Gemini reports
nothing until it refuses, so its requests are counted per Pacific day.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from test_quota import DAILY, Reply

from vidsmith import llm, usage, visuals


def _utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# the Pacific day, without a timezone database
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("when, local", [
    (_utc(2026, 3, 8, 9, 59), datetime(2026, 3, 8, 1, 59)),     # still standard time
    (_utc(2026, 3, 8, 10, 0), datetime(2026, 3, 8, 3, 0)),      # clocks jump at 2am
    (_utc(2026, 11, 1, 8, 59), datetime(2026, 11, 1, 1, 59)),   # still daylight time
    (_utc(2026, 11, 1, 9, 0), datetime(2026, 11, 1, 1, 0)),     # 2am falls back to 1am
    (_utc(2026, 9, 16, 6, 59), datetime(2026, 9, 15, 23, 59)),
])
def test_pacific_time_follows_daylight_saving(when, local):
    assert usage.pacific(when) == local


def test_the_gemini_day_rolls_over_at_pacific_midnight():
    assert usage.pacific_day(_utc(2026, 9, 16, 6, 59)) == "2026-09-15"
    assert usage.pacific_day(_utc(2026, 9, 16, 7, 0)) == "2026-09-16"
    assert usage.next_pacific_midnight(_utc(2026, 9, 16, 6, 59)) == _utc(2026, 9, 16, 7, 0)
    assert usage.next_pacific_midnight(_utc(2026, 12, 1, 12, 0)) == _utc(2026, 12, 2, 8, 0)


# --------------------------------------------------------------------------- #
# the ledger
# --------------------------------------------------------------------------- #
def test_requests_are_counted_per_model_and_start_again_each_day(monkeypatch):
    monkeypatch.setattr(usage, "pacific_day", lambda now=None: "2026-09-16")
    for _ in range(3):
        usage.gemini_request("gemini-x")
    usage.gemini_request("gemini-y")

    assert usage.report("gemini-x")["gemini"]["requests"] == 3
    assert usage.report("gemini-y")["gemini"]["requests"] == 1

    monkeypatch.setattr(usage, "pacific_day", lambda now=None: "2026-09-17")
    assert usage.report("gemini-x")["gemini"]["requests"] == 0
    usage.gemini_request("gemini-x")
    assert usage.report("gemini-x")["gemini"]["requests"] == 1


def test_a_refusal_marks_the_day_spent_and_names_the_real_limit():
    usage.gemini_spent("gemini-x", "the Gemini free tier is spent", "250")
    body = usage.report("gemini-x")["gemini"]
    assert body["spent"] == "the Gemini free tier is spent" and body["limit"] == 250


def test_a_mangled_ledger_never_stops_a_build(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    monkeypatch.setenv("VIDSMITH_USAGE", str(path))
    for bad in ("not json", "[]", json.dumps({"gemini": {"day": usage.pacific_day(),
                                                        "models": {"m": "oops"}}})):
        path.write_text(bad, encoding="utf-8")
        usage.gemini_request("m")
        usage.stock_headers("pexels", {"X-Ratelimit-Limit": "x"}, 200)
        assert usage.report("m")["gemini"]["requests"] == 1


def test_the_stock_allowance_is_what_the_last_response_said():
    usage.stock_headers("pexels", {"X-Ratelimit-Limit": "25000", "X-Ratelimit-Remaining": "23276",
                                   "X-Ratelimit-Reset": "1790365632"}, 200)
    assert usage.report("m")["pexels"]["remaining"] == 23276
    assert usage.report("m")["pexels"]["reset"] == 1790365632

    usage.stock_headers("pexels", {}, 200)                    # nothing said: nothing changes
    assert usage.report("m")["pexels"]["remaining"] == 23276
    usage.stock_headers("pexels", {}, 429)                    # refused: none left
    assert usage.report("m")["pexels"]["remaining"] == 0


# --------------------------------------------------------------------------- #
# where the numbers come from
# --------------------------------------------------------------------------- #
def test_every_gemini_attempt_is_counted_and_a_spent_day_recorded(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    replies = iter([Reply({}, status=503), Reply(DAILY)])
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: next(replies))

    with pytest.raises(llm.QuotaExhausted):
        llm.generate("hello", "key", model="gemini-x")

    body = usage.report("gemini-x")["gemini"]
    assert body["requests"] == 2, "a retry spends the budget too"
    assert "spent" in body["spent"] and body["limit"] == 500


def test_a_pexels_search_records_its_allowance(monkeypatch):
    class Response:
        status_code = 200
        headers = {"X-Ratelimit-Limit": "25000", "X-Ratelimit-Remaining": "100"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"photos": []}

    monkeypatch.setattr(visuals.requests, "get", lambda *a, **k: Response())
    visuals._pexels_photo_fetch("q", "key", "landscape", 12)

    assert usage.report("m")["pexels"]["remaining"] == 100


def test_the_test_suite_never_writes_the_checkouts_own_ledger():
    """Stubbed calls are counted like real ones; conftest points them elsewhere."""
    assert "pytest" in str(usage._path()).lower() or "tmp" in str(usage._path()).lower()
