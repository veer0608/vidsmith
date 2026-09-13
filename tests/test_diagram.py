"""Drawn diagrams for scenes no footage can serve.

The spec comes from a language model, so the parsing has to survive whatever it
returns: unknown layouts, missing fields, a compare with one column, twenty
nodes. A bad spec must degrade to "keep the footage", never to a crash.
"""
from __future__ import annotations

import pytest
from PIL import Image

from vidsmith.diagram import KINDS, MAX_NODES, Spec, render, reveal_steps
from vidsmith.theme import hex_rgb, resolve

THEME = resolve("midnight")
SIZE = (960, 540)

FLOW = {"kind": "flow", "title": "What a write costs",
        "nodes": ["Insert row", "Update table", "Rewrite tree"]}
TREE = {"kind": "tree", "title": "How an index branches",
        "nodes": ["Root node", "Leaf 1", "Leaf 2", "Leaf 3"]}
STACK = {"kind": "stack", "title": "Where time goes",
         "nodes": ["Disk", "Index pages", "Query planner"]}
COMPARE = {"kind": "compare", "title": "Reads against writes",
           "groups": [{"label": "Faster", "items": ["Lookups", "Range scans"]},
                      {"label": "Slower", "items": ["Inserts", "Updates"]}]}
ALL = {"flow": FLOW, "tree": TREE, "stack": STACK, "compare": COMPARE}


# --------------------------------------------------------------------------- #
# spec parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_kind_round_trips(kind):
    spec = Spec.from_dict(ALL[kind])
    assert spec.kind == kind
    assert spec.is_drawable()


def test_an_unknown_kind_falls_back_to_flow():
    assert Spec.from_dict({"kind": "sankey", "nodes": ["a", "b"]}).kind == "flow"


def test_node_count_is_capped():
    spec = Spec.from_dict({"kind": "flow", "nodes": [f"n{i}" for i in range(40)]})
    assert len(spec.nodes) == MAX_NODES


def test_blank_nodes_are_dropped():
    spec = Spec.from_dict({"kind": "flow", "nodes": ["Real", "  ", "", "Also real"]})
    assert spec.nodes == ["Real", "Also real"]


def test_a_single_node_is_not_drawable():
    assert not Spec.from_dict({"kind": "flow", "nodes": ["Only one"]}).is_drawable()


def test_an_empty_spec_is_not_drawable():
    assert not Spec.from_dict({}).is_drawable()


def test_compare_needs_two_populated_columns():
    one_side = {"kind": "compare",
                "groups": [{"label": "Faster", "items": ["Lookups"]}]}
    assert not Spec.from_dict(one_side).is_drawable()

    empty_side = {"kind": "compare",
                  "groups": [{"label": "A", "items": ["x"]},
                             {"label": "B", "items": []}]}
    assert not Spec.from_dict(empty_side).is_drawable()


def test_compare_ignores_a_third_column():
    spec = Spec.from_dict({"kind": "compare", "groups": [
        {"label": "A", "items": ["x"]}, {"label": "B", "items": ["y"]},
        {"label": "C", "items": ["z"]}]})
    assert len(spec.groups) == 2


def test_junk_types_do_not_crash_parsing():
    spec = Spec.from_dict({"kind": 7, "title": None, "nodes": [1, 2, None, "three"]})
    assert spec.kind == "flow"
    assert "three" in spec.nodes


def test_element_count_drives_the_reveal():
    assert Spec.from_dict(TREE).elements == 4
    assert Spec.from_dict(COMPARE).elements == 4     # 2 + 2 items


# --------------------------------------------------------------------------- #
# drawing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_each_kind_draws_at_the_requested_size(tmp_path, kind):
    out = render(Spec.from_dict(ALL[kind]), tmp_path / f"{kind}.png", SIZE, THEME)
    assert Image.open(out).size == SIZE


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_portrait_frames_draw_too(tmp_path, kind):
    out = render(Spec.from_dict(ALL[kind]), tmp_path / f"{kind}-p.png",
                 (540, 960), THEME)
    assert Image.open(out).size == (540, 960)


def test_a_partial_reveal_differs_from_the_full_one(tmp_path):
    spec = Spec.from_dict(TREE)
    early = render(spec, tmp_path / "a.png", SIZE, THEME, reveal=0.25)
    full = render(spec, tmp_path / "b.png", SIZE, THEME, reveal=1.0)
    assert Image.open(early).tobytes() != Image.open(full).tobytes()


def test_the_first_element_is_always_shown(tmp_path):
    """Reveal 0 would be an empty frame, which reads as a dropped shot."""
    spec = Spec.from_dict(TREE)
    nothing = render(spec, tmp_path / "z.png", SIZE, THEME, reveal=0.0)
    one = render(spec, tmp_path / "o.png", SIZE, THEME, reveal=1 / spec.elements)
    assert Image.open(nothing).tobytes() == Image.open(one).tobytes()


def test_reveal_steps_end_fully_revealed():
    for shots in range(1, 6):
        steps = reveal_steps(shots)
        assert len(steps) == shots
        assert steps[-1] == pytest.approx(1.0)
        assert steps == sorted(steps)


def test_a_title_is_optional(tmp_path):
    spec = Spec.from_dict({"kind": "flow", "nodes": ["A", "B", "C"]})
    assert render(spec, tmp_path / "t.png", SIZE, THEME).exists()


def test_long_labels_do_not_overflow_the_frame(tmp_path):
    spec = Spec.from_dict({"kind": "flow", "title": "A very long title indeed",
                           "nodes": ["An unreasonably long label here",
                                     "Another one just as long", "Third"]})
    out = render(spec, tmp_path / "long.png", SIZE, THEME)
    assert Image.open(out).size == SIZE


# --------------------------------------------------------------------------- #
# which side of a compare is lit
# --------------------------------------------------------------------------- #
# The launch-demo closing diagram listed "The engine" first and "An estimate"
# second, and the renderer gilded the estimate - the side the narration was
# arguing against - because it always lit the second group.
ENGINE = {"label": "The engine", "items": ["Exact per word", "Cannot drift"]}
ESTIMATE = {"label": "An estimate", "items": ["Guessed later", "Drifts"]}


def _compare(first, second, **flags):
    groups = [dict(first), dict(second)]
    for i, value in flags.get("accent", {}).items():
        groups[i]["accent"] = value
    # no title: it is drawn in the accent colour and would count for both sides
    return Spec.from_dict({"kind": "compare", "title": "", "groups": groups})


def _lit_side(path, theme) -> str:
    """Which half of the frame carries the accent, counted in exact pixels.

    The lit column's heading is drawn in the accent at full opacity, and so are
    its box borders once fully revealed; the muted column has neither.
    """
    img = Image.open(path).convert("RGB")
    w, h = img.size
    accent = hex_rgb(theme.accent)

    def count(box):
        half = img.crop(box)
        colours = half.getcolors(half.width * half.height) or []
        return next((n for n, c in colours if c == accent), 0)

    left, right = count((0, 0, w // 2, h)), count((w // 2, 0, w, h))
    assert left + right, "no accent drawn at all"
    return "left" if left > right else "right"


def _render_late(spec, tmp_path, size):
    # Sample the END of the scene. A compare animates in, so early on the
    # second column's boxes are still dim and look muted for that reason alone,
    # which is why the launch-demo fault was invisible in an early still. Only
    # at full reveal are the lit column's borders drawn in the exact accent.
    return render(spec, tmp_path / "c.png", size, THEME, reveal=1.0)


@pytest.mark.parametrize("size", [SIZE, (540, 960)], ids=["16:9", "9:16"])
def test_the_flagged_group_is_lit_when_it_comes_first(tmp_path, size):
    spec = _compare(ENGINE, ESTIMATE, accent={0: True})
    assert spec.accent_column == 0
    assert _lit_side(_render_late(spec, tmp_path, size), THEME) == "left"


@pytest.mark.parametrize("size", [SIZE, (540, 960)], ids=["16:9", "9:16"])
def test_the_flagged_group_is_lit_when_it_comes_second(tmp_path, size):
    spec = _compare(ESTIMATE, ENGINE, accent={1: True})
    assert spec.accent_column == 1
    assert _lit_side(_render_late(spec, tmp_path, size), THEME) == "right"


def test_an_unflagged_compare_still_lights_the_second_group(tmp_path):
    """Specs cached in diagrams.json before the flag existed must not change."""
    spec = _compare(ENGINE, ESTIMATE)
    assert spec.accent_column == 1
    assert _lit_side(_render_late(spec, tmp_path, SIZE), THEME) == "right"


def test_the_order_alone_no_longer_decides(tmp_path):
    lit_first = _render_late(_compare(ENGINE, ESTIMATE, accent={0: True}),
                             tmp_path / "a", SIZE)
    lit_second = _render_late(_compare(ENGINE, ESTIMATE, accent={1: True}),
                              tmp_path / "b", SIZE)
    assert _lit_side(lit_first, THEME) != _lit_side(lit_second, THEME)


@pytest.mark.parametrize("value,column", [
    (True, 0), ("true", 0), ("True", 0), (1, 0),
    (False, 1), ("false", 1), ("no", 1), (None, 1), (0, 1),
])
def test_the_accent_flag_survives_how_a_model_spells_it(value, column):
    """"false" is a truthy string, and it must not claim the emphasis."""
    assert _compare(ENGINE, ESTIMATE, accent={0: value}).accent_column == column


def test_the_diagram_prompt_teaches_the_flag():
    """The renderer reads `accent`; a prompt that never asks for it leaves the
    emphasis to group order again, which is the bug this replaced."""
    from vidsmith import llm
    body = llm.DIAGRAM_PROMPT.format(line="a line", query="a shot")
    assert '"accent"' in body
    assert "favours" in body
    assert "order" in body.lower(), "nothing tells the model order is not the flag"
