"""CLAUDE.md stays a short core, and every detail doc stays reachable from it.

CLAUDE.md is loaded into every session before any work starts. It had grown to
1404 lines, about 55k tokens, because each lesson was written in full where it
was learned, and the rules that mattered were buried under the stories of
learning them. It was split in #143: the core keeps the task table, one-line
rules and commands, and the stories live in `docs/claude/`.

The rule that keeps it that way was written into the file itself, which is the
kind of rule this project has learned not to trust. So it is enforced here: the
file has a line budget, and a detail doc that CLAUDE.md never links to is one
no session will ever be pointed at.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_LINES = 200


def over_budget(text: str, limit: int = MAX_LINES) -> int:
    """Lines past the budget, or 0."""
    return max(0, len(text.splitlines()) - limit)


def unlinked(text: str, docs: list[str]) -> list[str]:
    linked = set(re.findall(r"\]\(docs/claude/([^)#]+\.md)", text))
    return sorted(set(docs) - linked)


def _claude_md() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_claude_md_stays_within_its_line_budget():
    extra = over_budget(_claude_md())
    assert not extra, (
        f"CLAUDE.md is {extra} lines over its {MAX_LINES}-line budget. Move the "
        "story into the right docs/claude/ file and keep a one-line rule or a "
        "table row here.")


def test_every_detail_doc_is_linked_from_claude_md():
    docs = [p.name for p in (ROOT / "docs" / "claude").glob("*.md")]
    assert docs, "docs/claude/ is empty or moved; update this test"
    missing = unlinked(_claude_md(), docs)
    assert not missing, (
        f"docs/claude/ files no session will be pointed at: {missing}. Add "
        "each to the docs map at the bottom of CLAUDE.md.")


def test_the_gate_can_still_fail():
    assert over_budget("x\n" * MAX_LINES) == 0
    assert over_budget("x\n" * (MAX_LINES + 3)) == 3
    text = "| [tests.md](docs/claude/tests.md) | ... |"
    assert unlinked(text, ["tests.md"]) == []
    assert unlinked(text, ["tests.md", "new.md"]) == ["new.md"]
