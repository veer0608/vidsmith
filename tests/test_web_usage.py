"""The allowance read-out on the page."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient        # noqa: E402

from vidsmith import usage                       # noqa: E402
from web import app as web_app                   # noqa: E402


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "")
    monkeypatch.setattr(web_app, "_keys", lambda: {"gemini": "g", "pexels": "p", "pixabay": ""})
    return TestClient(web_app.app)


def test_the_page_is_told_the_models_count_and_the_stock_allowance(api):
    usage.gemini_request(web_app.llm.DEFAULT_MODEL)
    usage.stock_headers("pexels", {"X-Ratelimit-Limit": "25000", "X-Ratelimit-Remaining": "150"}, 200)

    body = api.get("/api/usage").json()

    assert body["gemini"]["model"] == web_app.llm.DEFAULT_MODEL
    assert body["gemini"]["requests"] == 1 and body["gemini"]["limit"] > 0
    assert body["pexels"]["remaining"] == 150 and body["pixabay"] is None
    assert body["keys"] == {"gemini": True, "pexels": True, "pixabay": False}


def test_the_allowance_is_behind_the_token(api, monkeypatch):
    monkeypatch.setattr(web_app, "TOKEN", "secret")
    assert api.get("/api/usage").status_code == 401


def test_the_page_warns_before_render_rather_than_after():
    page = (Path(web_app.HERE) / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="allowance"' in page and "/api/usage" in page
    assert "fall back to generated cards" in page, "the cost of running out is said, not implied"
