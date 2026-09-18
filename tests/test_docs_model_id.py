"""The docs must name the model the code actually calls.

`docs/claude/architecture.md` said the default was the floating alias
`gemini-flash-lite-latest` for weeks after `llm.py` had pinned
`gemini-3.5-flash-lite`, and the pin exists precisely because that alias is a
trap. Nothing went red; it was found by reading. A session that trusts the doc
reaches for the model this project deliberately stopped using.

So every Gemini id in the prose must be `llm.DEFAULT_MODEL`. An id named on
purpose as history - the model we moved off, one that 404s - goes in
`HISTORICAL` with the reason, so an exception is a decision rather than drift.
"""
from __future__ import annotations

import re
from pathlib import Path

from vidsmith import llm

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = re.compile(r"\bgemini-[a-z0-9][a-z0-9.-]*[a-z0-9]", re.IGNORECASE)

# id -> why a doc may name it although the code does not call it
HISTORICAL: dict[str, str] = {}


def _docs() -> list[Path]:
    return [ROOT / "CLAUDE.md", ROOT / "README.md", ROOT / "COMMERCIAL.md",
            *sorted((ROOT / "docs" / "claude").glob("*.md"))]


def stray_ids(text: str, default: str = llm.DEFAULT_MODEL) -> set[str]:
    return {m for m in MODEL_ID.findall(text)
            if m != default and m not in HISTORICAL}


def test_docs_name_only_the_model_the_code_calls():
    found = {}
    for doc in _docs():
        if doc.exists():
            ids = stray_ids(doc.read_text(encoding="utf-8"))
            if ids:
                found[str(doc.relative_to(ROOT))] = sorted(ids)
    assert not found, (
        f"docs name a Gemini model the code does not call: {found}. "
        f"llm.DEFAULT_MODEL is {llm.DEFAULT_MODEL!r}. Fix the doc, or add the "
        "id to HISTORICAL with the reason it is named.")


def test_the_gate_can_still_fail():
    """The drift that prompted this file must be caught by it."""
    old = "The default model is the floating alias `gemini-flash-lite-latest`."
    assert stray_ids(old) == {"gemini-flash-lite-latest"}
    assert stray_ids(f"pinned to `{llm.DEFAULT_MODEL}`.") == set()


def test_the_docs_are_actually_read():
    """A glob that matched nothing would pass the first test vacuously."""
    names = {p.name for p in _docs() if p.exists()}
    assert {"CLAUDE.md", "architecture.md", "incidents.md"} <= names
    assert any(llm.DEFAULT_MODEL in p.read_text(encoding="utf-8")
               for p in _docs() if p.exists())
