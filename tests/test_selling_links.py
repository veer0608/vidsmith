"""The buy link is revenue-bearing, so a placeholder must not be able to ship.

Every silent failure in this project's history has the same shape: something
licence-bearing or money-bearing that looked right in isolation and was wrong
beside the thing it referred to. A checkout link is the same shape and worse,
because a dead one fails at the buyer rather than at us. Nobody opens an issue
to report that they could not give you money.

The gate is deliberately blunt. While `COMMERCIAL.md` or `README.md` still
carries a `PASTE_GUMROAD_*` token, this file is red and the branch cannot merge,
because `main` requires all three checks. Filling the URLs in is what turns it
green. That is the whole design: the edit cannot be half done.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "COMMERCIAL.md")
PLACEHOLDER = re.compile(r"PASTE_\w*_LINK")


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_no_placeholder_checkout_link_can_ship():
    """Red until the real URLs are pasted over both tokens."""
    stuck = {name: sorted(set(PLACEHOLDER.findall(_text(name))))
             for name in DOCS}
    stuck = {name: tokens for name, tokens in stuck.items() if tokens}
    assert not stuck, (
        f"a placeholder would publish a dead checkout: {stuck}. Paste the "
        "live product URLs over the remaining tokens in both files.")


@pytest.mark.parametrize("price", ["$49", "$299"])
def test_both_files_state_the_same_price(price):
    """The price now lives in three places and one of them is a storefront.

    Only two of the three are in this repo, so only two can be held together
    here. A price that disagrees between the README and COMMERCIAL.md is the
    same fault as a credit that reached credits.txt and not the description:
    two writers, one fact.
    """
    for name in DOCS:
        assert price in _text(name), f"{name} does not state {price}"
