"""How much of the free allowances this instance has used, as far as it can tell.

Both limits that matter fail in silence. Pexels running out does not stop a
build: every scene falls back to a generated card, and the video is a slideshow
nobody asked for. Gemini running out does not stop one either: searches fall
back to keywords, reranking to search order, and the description to nothing.
The page could only find out afterwards, from the video.

The two report what they know differently, so they are recorded differently.

- **Pexels says exactly**, in `X-Ratelimit-Limit`, `-Remaining` and `-Reset` on
  every API response, for the key's monthly allowance. The last one seen is kept.
- **Gemini says nothing until it is gone.** No header carries the remaining
  daily budget; only the 429 that refuses a request names the limit. So this
  counts the requests this instance sends per model per Pacific day, which is the
  window Google resets on, and remembers a refusal. Another machine using the
  same key spends the same budget unseen, so the count is a floor, and is
  labelled as this server's.

Nothing here may break a build. Every read and write swallows its own failure.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

# The free tier's generate requests a day per model, as the 429 bodies have
# named it. Only a default: a refusal names the real number and replaces it.
GEMINI_DAILY = int(os.environ.get("VIDSMITH_GEMINI_DAILY", "500"))

_lock = threading.Lock()


def _path() -> Path:
    return Path(os.environ.get(
        "VIDSMITH_USAGE",
        Path(__file__).resolve().parent.parent / ".cache" / "usage.json"))


# --------------------------------------------------------------------------- #
# the Pacific day
# --------------------------------------------------------------------------- #
def _nth_sunday(year: int, month: int, n: int) -> datetime:
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def pacific(now: Optional[datetime] = None) -> datetime:
    """Pacific local time, without a timezone database.

    Windows Python ships no tz data, so `zoneinfo` cannot name Los Angeles
    there. The US rule is short enough to state: daylight time runs from 2am on
    the second Sunday of March to 2am on the first Sunday of November.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    standard = now - timedelta(hours=8)
    start = _nth_sunday(standard.year, 3, 2) + timedelta(hours=2)
    end = _nth_sunday(standard.year, 11, 1) + timedelta(hours=1)   # 2am daylight is 1am standard
    return standard + timedelta(hours=1) if start <= standard < end else standard


def pacific_day(now: Optional[datetime] = None) -> str:
    return pacific(now).strftime("%Y-%m-%d")


def next_pacific_midnight(now: Optional[datetime] = None) -> datetime:
    """When the Gemini day rolls over, in UTC."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local = pacific(now)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # the offset in force at that midnight, found by asking what UTC time lands there
    for hours in (7, 8):
        candidate = midnight + timedelta(hours=hours)
        if pacific(candidate.replace(tzinfo=timezone.utc)) == midnight:
            return candidate.replace(tzinfo=timezone.utc)
    return (midnight + timedelta(hours=8)).replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# the ledger
# --------------------------------------------------------------------------- #
def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".json.part")
        partial.write_text(json.dumps(data, indent=2), encoding="utf-8")
        partial.replace(path)
    except OSError:
        pass


def _model_today(data: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Today's entry for `model`, starting a new day or mending a mangled file.

    Checked by shape rather than wrapped in a broad `except`: a ledger someone
    edited by hand must not stop a build, and a typo here must not hide.
    """
    day = pacific_day()
    gemini = data.get("gemini")
    if not isinstance(gemini, dict) or gemini.get("day") != day \
            or not isinstance(gemini.get("models"), dict):
        gemini = data["gemini"] = {"day": day, "models": {}}
    entry = gemini["models"].get(model)
    if not isinstance(entry, dict) or not isinstance(entry.get("requests", 0), int):
        entry = gemini["models"][model] = {"requests": 0}
    return entry


def gemini_request(model: str) -> None:
    """One request sent to Gemini, retries included: each one spends the budget."""
    with _lock:
        data = _load()
        entry = _model_today(data, model)
        entry["requests"] = entry.get("requests", 0) + 1
        _save(data)


def gemini_spent(model: str, message: str, limit: Optional[Any] = None) -> None:
    """Google refused a request because the day's allowance for `model` is gone."""
    with _lock:
        data = _load()
        entry = _model_today(data, model)
        entry["spent"] = message
        if str(limit or "").isdigit():
            entry["limit"] = int(limit)
        _save(data)


def stock_headers(provider: str, headers: Mapping[str, str], status: int) -> None:
    """Keep the allowance a stock API reported on its latest response."""
    lower = {k.lower(): v for k, v in (headers or {}).items()}
    try:
        limit = int(lower["x-ratelimit-limit"])
        remaining = int(lower["x-ratelimit-remaining"])
    except (KeyError, TypeError, ValueError):
        if status != 429:
            return
        limit, remaining = 0, 0
    reset = lower.get("x-ratelimit-reset", "")
    with _lock:
        data = _load()
        data[provider] = {"limit": limit, "remaining": 0 if status == 429 else remaining,
                          "reset": int(reset) if str(reset).isdigit() else 0,
                          "seen": int(time.time())}
        _save(data)


def report(model: str) -> Dict[str, Any]:
    """What the page shows: today's Gemini count for `model`, and each stock API."""
    with _lock:
        data = _load()
    gemini = data.get("gemini") if isinstance(data.get("gemini"), dict) else {}
    models = gemini.get("models") if gemini.get("day") == pacific_day() else None
    entry = models.get(model) if isinstance(models, dict) else None
    entry = entry if isinstance(entry, dict) else {}
    requests_today = entry.get("requests", 0)
    limit = entry.get("limit")
    body: Dict[str, Any] = {
        "gemini": {"model": model,
                   "requests": requests_today if isinstance(requests_today, int) else 0,
                   "limit": limit if isinstance(limit, int) and limit > 0 else GEMINI_DAILY,
                   "spent": entry.get("spent", ""),
                   "resets": next_pacific_midnight().isoformat()},
    }
    for provider in ("pexels", "pixabay"):
        seen = data.get(provider)
        body[provider] = seen if isinstance(seen, dict) else None
    return body
