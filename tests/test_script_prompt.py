"""The drafting prompt.

Measured against the old one on three topics: length went from 51% of the
requested runtime to 84%, diagram directives from none at all to two a script,
and every scene no longer had an identical sentence count. These guard the
constraints that produced that, since the prompt is prose and easy to erode.
"""
from __future__ import annotations

import pytest

from vidsmith import llm
from vidsmith.llm import undash

PROMPT = llm.SCRIPT_PROMPT


def _rendered(minutes: float = 3.0) -> str:
    words = int(minutes * llm.WORDS_PER_MINUTE)
    scenes = max(5, min(18, round(words / llm.WORDS_PER_SCENE)))
    return PROMPT.format(topic="a topic", words=words, scenes=scenes,
                         lo=int(words / scenes * 0.8), hi=int(words / scenes * 1.25))


def test_the_prompt_formats_with_the_fields_draft_script_supplies():
    assert "a topic" in _rendered()


@pytest.mark.parametrize("minutes,expected_scenes", [
    (1.0, 5), (2.0, 7), (3.0, 11), (5.0, 18), (10.0, 18),
])
def test_scene_count_scales_with_runtime(minutes, expected_scenes):
    words = int(minutes * llm.WORDS_PER_MINUTE)
    assert max(5, min(18, round(words / llm.WORDS_PER_SCENE))) == expected_scenes


def test_the_speaking_rate_is_the_measured_one():
    """155 a minute was assumed and never measured, and it was wrong by a fifth.

    Fourteen real builds, 3,054 words of en-US-AndrewNeural at +8%, spoke at
    201 words a minute from first word to last. A script sized for eight minutes
    at 155 came out as a 391 second video.
    """
    from vidsmith.script_parser import WPS

    assert 190 <= WPS * 60 <= 210


def test_words_per_minute_is_derived_from_the_speaking_rate():
    """Two hand-written rates are how this went wrong: the parser said 2.6 a
    second and drafting said 155 a minute, and nothing held them together."""
    from vidsmith.config import VoiceConfig
    from vidsmith.script_parser import WPS

    pause = VoiceConfig.lead_in + VoiceConfig.gap
    assert llm.WORDS_PER_MINUTE == int(60 / (1 / WPS + pause / llm.WORDS_PER_SCENE))
    # measured across real builds with their pauses: 182 to 195 by scene length
    assert 180 <= llm.WORDS_PER_MINUTE <= 200


def test_the_budget_is_stated_per_scene_not_only_in_total():
    """A lone total was undershot by half; the per-scene figure is the fix."""
    body = _rendered()
    words = int(3.0 * llm.WORDS_PER_MINUTE)
    scenes = max(5, min(18, round(words / llm.WORDS_PER_SCENE)))
    assert str(words) in body, "total word budget missing"
    assert "hard" in body.lower() and "budget" in body.lower()
    assert (str(int(words / scenes * 0.8)) in body
            and str(int(words / scenes * 1.25)) in body), "per-scene range missing"


def test_every_scene_is_told_to_use_a_visual():
    assert "always [visual:]" in PROMPT.lower()


def test_the_draft_is_never_offered_a_diagram():
    """Drafted scripts explained their subject as box-and-arrow frames, which
    read as slides rather than a video. The output template used to offer
    `[visual: ...]  or  [diagram: ...]`, and a template is followed more
    faithfully than any instruction above it."""
    low = PROMPT.lower()
    assert "never write [diagram:]" in low
    template = PROMPT[PROMPT.index("OUTPUT exactly"):]
    assert "[diagram" not in template, "the output template still offers a diagram"


def test_abstract_ideas_are_filmed_not_drawn():
    """The hard case is the idea with no obvious subject. Without worked
    examples a model reaches for 'flowchart of ...', which is a diagram by
    another name and returns stock infographics."""
    low = PROMPT.lower()
    assert "camera" in low, "no test for what belongs in [visual:]"
    assert "good" in low and "bad" in low, "no worked examples of the distinction"
    assert "bad   [visual: flowchart" in low, "no bad example of a disguised diagram"
    assert "bad   [visual: pricing comparison table" in low
    assert "describe a graphic" in low, "the bad examples are not explained"


def test_a_visual_is_the_subject_not_a_metaphor_for_it():
    """The prompt used to teach a librarian at the shelves for a database index.
    Drafts followed it into "fork in a wooded trail" for a tree structure and
    "man holding a ring of keys" for a B-tree node, and a viewer who watched the
    result called the footage mostly unrelated."""
    low = PROMPT.lower()
    assert "never a metaphor" in low
    assert "bad   [visual: fork in a forest trail]" in low, "no bad example of a metaphor"
    assert "librarian" not in low, "the prompt still teaches the metaphor it replaced"


def test_the_clip_judge_rejects_a_blank_green_screen():
    """A laptop with a keying-green screen sat behind a line about honesty."""
    from vidsmith.llm import RERANK_PROMPT

    assert "green" in RERANK_PROMPT.lower() and "unusable" in RERANK_PROMPT.lower()


@pytest.mark.parametrize("draft,expected", [
    ("## A\n[diagram: four boxes]\nText.\n", "## A\nText.\n"),
    ("## A\n  [Diagram: boxes]  \nText.\n", "## A\nText.\n"),
    ("## A\n[visual: hands typing]\nText.\n", "## A\n[visual: hands typing]\nText.\n"),
    ("## A\n[diagram: last line, no newline]", "## A\n"),
])
def test_a_diagram_the_model_wrote_anyway_is_removed(draft, expected):
    """The prompt forbids it; the output is repaired as well, the same way
    dashes are, because instructing a model is not enough on its own."""
    assert llm.strip_diagrams(draft) == expected


def test_stripping_leaves_narration_that_mentions_a_diagram_alone():
    text = "## A\n[visual: whiteboard]\nThe [diagram: word] inside a sentence stays.\n"
    assert llm.strip_diagrams(text) == text


def test_the_draft_goes_through_the_repair(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda *a, **k:
                        "# T\n\n## One\n[diagram: boxes and arrows]\nNarration.\n")

    out = llm.draft_script("a topic", 1.0, "key")

    assert "[diagram" not in out and "Narration." in out


def test_invented_facts_are_forbidden():
    low = PROMPT.lower()
    assert "do not invent" in low
    for trap in ("version numbers", "percentages", "named studies"):
        assert trap in low, f"{trap} not called out"


def test_rhythm_variation_is_demanded():
    """Every scene was two sentences, eleven times in a row."""
    low = PROMPT.lower()
    assert "vary the rhythm" in low
    assert "single short sentence" in low


def test_headings_must_be_distinct():
    """Four scenes came back all headed 'The Mechanism'."""
    assert "never repeat a heading" in PROMPT.lower()


def test_the_shape_covers_hook_through_takeaway():
    low = PROMPT.lower()
    for beat in ("hook", "mechanism", "what to do", "takeaway"):
        assert beat in low, f"the {beat} beat is missing"


# --------------------------------------------------------------------------- #
# lengthening a draft that came back short
# --------------------------------------------------------------------------- #
import re                                              # noqa: E402

from vidsmith.script_parser import narration_words      # noqa: E402

ASK = re.compile(r"^- ## (.+?): about (\d+) words \(it has (\d+)\)$", re.MULTILINE)


def _draft(sizes, title="# A Title"):
    """A drafted script whose scenes hold these many words of narration."""
    body = [title, ""]
    for i, n in enumerate(sizes):
        body += [f"## Scene {i}", f"[visual: hands on a keyboard {i}]",
                 " ".join(["word"] * n), ""]
    return "\n".join(body)


class _Model:
    """Stands in for `generate`: the draft first, then whatever `rewrite` says."""

    def __init__(self, draft, rewrite=None):
        self.draft, self.rewrite, self.prompts = draft, rewrite, []

    def __call__(self, prompt, *a, **k):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return self.draft
        if self.rewrite is None:
            return ""
        return self.rewrite(prompt)

    @property
    def asks(self):
        return [ASK.findall(p) for p in self.prompts[1:]]


def _as_asked(prompt):
    """A model that writes exactly the length each scene was asked for."""
    return "\n\n".join(f"## {h}\n" + " ".join(["more"] * int(n))
                       for h, n, _ in ASK.findall(prompt))


def test_a_short_draft_is_lengthened_to_its_budget(monkeypatch):
    """Measured at nine minutes on three topics, the one-shot draft came back at
    69%, 44% and 57% of its budget. The page drafted a 684 word script for a
    nine minute video and nothing downstream said so."""
    model = _Model(_draft([20] * 5), _as_asked)
    monkeypatch.setattr(llm, "generate", model)

    out = llm.draft_script("a topic", 1.0, "key")

    target = int(1.0 * llm.WORDS_PER_MINUTE)
    assert narration_words(out) >= target * llm.LONG_ENOUGH, out
    assert len(model.prompts) == 2, "one draft and one lengthening request"


def test_lengthening_keeps_the_title_headings_and_directives(monkeypatch):
    draft = _draft([20] * 5)
    monkeypatch.setattr(llm, "generate", _Model(draft, _as_asked))

    out = llm.draft_script("a topic", 1.0, "key")

    kept = lambda text: [l for l in text.splitlines()
                         if l.startswith("#") or l.startswith("[visual:")]
    assert kept(out) == kept(draft)


def test_a_draft_long_enough_already_costs_one_request(monkeypatch):
    draft = _draft([40] * 4)
    model = _Model(draft, _as_asked)
    monkeypatch.setattr(llm, "generate", model)

    out = llm.draft_script("a topic", 1.0, "key")

    assert len(model.prompts) == 1
    assert out == draft.strip() + "\n", "a draft that needed nothing was rewritten"


def test_each_scene_is_asked_for_its_share_of_what_is_missing(monkeypatch):
    """Asked for a flat per-scene size, three real drafts overshot to 107%, and
    past the instance's word limit that is a script its own page refuses."""
    model = _Model(_draft([60, 20, 20, 20]), _as_asked)
    monkeypatch.setattr(llm, "generate", model)

    llm.draft_script("a topic", 1.0, "key")

    target = int(1.0 * llm.WORDS_PER_MINUTE)
    asks = model.asks[0]
    untouched = 60
    assert "Scene 0" not in [h for h, _, _ in asks], "a long scene was sent back"
    assert abs(untouched + sum(int(n) for _, n, _ in asks) - target) <= len(asks)


def test_one_line_scenes_are_left_as_one_line(monkeypatch):
    """The drafting prompt asks for scenes that are a single short sentence,
    and inflating them would undo the rhythm it asked for."""
    model = _Model(_draft([5, 20, 20, 20]), _as_asked)
    monkeypatch.setattr(llm, "generate", model)

    llm.draft_script("a topic", 1.0, "key")

    assert "Scene 0" not in [h for h, _, _ in model.asks[0]]


@pytest.mark.parametrize("reply", [
    lambda prompt: "## Some Other Heading\n" + " ".join(["more"] * 90),
    lambda prompt: "\n\n".join(f"## {h}\nshort" for h, _, _ in ASK.findall(prompt)),
    lambda prompt: "I cannot help with that.",
])
def test_a_reply_that_loses_a_heading_or_shrinks_changes_nothing(monkeypatch, reply):
    draft = _draft([20] * 5)
    model = _Model(draft, reply)
    monkeypatch.setattr(llm, "generate", model)

    out = llm.draft_script("a topic", 1.0, "key")

    assert narration_words(out) == narration_words(draft)
    assert len(model.prompts) == 1 + llm.LENGTHEN_ROUNDS, \
        "a model that never lengthens anything must not be asked forever"


def test_long_drafts_are_lengthened_in_chunks_that_fit_the_reply(monkeypatch):
    """Eighteen scenes rewritten in one reply would not fit `maxOutputTokens`."""
    model = _Model(_draft([30] * 18), _as_asked)
    monkeypatch.setattr(llm, "generate", model)

    out = llm.draft_script("a topic", 9.0, "key")

    assert all(0 < len(a) <= llm.LENGTHEN_CHUNK for a in model.asks)
    assert sum(len(a) for a in model.asks) == 18
    assert narration_words(out) >= int(9.0 * llm.WORDS_PER_MINUTE) * llm.LONG_ENOUGH


def test_a_long_rewrite_arrives_as_scenes_with_their_own_footage(monkeypatch):
    """Seven scenes where eighteen were asked for, lengthened, made seven
    scenes of about 215 words: over eighty seconds each on one stock search."""
    from vidsmith.script_parser import parse_text

    def in_paragraphs(prompt):
        out = []
        for h, n, _ in ASK.findall(prompt):
            paras, left = [], int(n)
            while left > 0:
                take = min(llm.PARAGRAPH_WORDS, left)
                paras.append(" ".join(["more"] * take))
                left -= take
            body = paras[0] + "".join(f"\n\n[visual: hands turning a key {j}]\n{p}"
                                      for j, p in enumerate(paras[1:]))
            out.append(f"## {h}\n{body}")
        return "\n\n".join(out)

    draft = _draft([30] * 4)
    monkeypatch.setattr(llm, "generate", _Model(draft, in_paragraphs))

    out = llm.draft_script("a topic", 3.0, "key")

    _, scenes = parse_text(out)
    assert len(scenes) > 4, "the long rewrites stayed single scenes"
    assert all(s.directive for s in scenes), "a paragraph arrived with no footage"
    assert max(len(s.text.split()) for s in scenes) <= llm.PARAGRAPH_WORDS
    assert [s.directive for s in scenes if "hands on a keyboard" in s.directive] == \
        [f"hands on a keyboard {i}" for i in range(4)], "the draft's own visuals moved"


def test_the_lengthening_prompt_asks_for_paragraphs_with_footage():
    rendered = " ".join(llm.LENGTHEN_PROMPT.format(
        target=1, total=1, wanted="", script="", paragraph=llm.PARAGRAPH_WORDS,
        short=llm.PARAGRAPH_WORDS * 2 // 3).lower().split())
    assert f"over {llm.PARAGRAPH_WORDS} words is written as paragraphs" in rendered
    assert "its own [visual:" in rendered
    assert "camera can point" in rendered and "never a diagram" in rendered


def test_running_out_of_quota_while_lengthening_keeps_the_draft(monkeypatch):
    """The draft was the request that mattered. Refusing it over a top-up would
    throw away a usable script and tell the page drafting failed."""
    draft = _draft([20] * 5)

    def model(prompt, *a, **k):
        if "lengthening scenes" in prompt:
            raise llm.QuotaExhausted("500 requests a day")
        return draft

    monkeypatch.setattr(llm, "generate", model)

    out = llm.draft_script("a topic", 1.0, "key")

    assert narration_words(out) == narration_words(draft)


def test_the_lengthening_prompt_keeps_the_drafting_rules():
    low = llm.LENGTHEN_PROMPT.lower()
    assert "no em dashes or en dashes" in low
    assert "do not invent specifics" in low
    for trap in ("version numbers", "percentages", "named studies"):
        assert trap in low, f"{trap} not called out"
    assert "copy each heading exactly" in low, "replies are matched by heading"
    assert "not padding" in low


# --------------------------------------------------------------------------- #
# dashes
# --------------------------------------------------------------------------- #
EM, EN = "—", "–"


@pytest.mark.parametrize("raw,expected", [
    (f"It is a record {EM} of what your bank stored.",
     "It is a record, of what your bank stored."),
    (f"Two vCPU{EM}enough to encode.", "Two vCPU, enough to encode."),
    (f"Two vCPU {EN} enough to encode.", "Two vCPU, enough to encode."),
    (f"The lock {EM} a traffic cop {EM} allows one thread.",
     "The lock, a traffic cop, allows one thread."),
    (f"That is the cost {EM}.", "That is the cost."),
])
def test_dashes_become_commas(raw, expected):
    assert undash(raw) == expected


def test_a_number_range_becomes_a_word():
    """A comma between digits reads as a thousands separator out loud."""
    assert undash(f"Wait 5{EN}10 seconds.") == "Wait 5 to 10 seconds."


@pytest.mark.parametrize("text", [
    "We handled 20,000 requests an hour.",
    "It costs 1,250 rupees a month.",
    "Between 1,000 and 10,000 rows.",
])
def test_a_thousands_separator_is_not_read_as_a_range(text):
    """Only a dash makes a range; a comma the writer typed is already correct.

    The range rule used to fire on any digit-comma-digit, which meant it could
    not tell a comma it had just made from one that was always there, and
    "20,000 requests" shipped into the description as "20 to 000 requests".
    """
    assert undash(text) == text


def test_a_range_still_converts_next_to_a_separated_number():
    assert (undash(f"Between 5{EN}10 of the 20,000 rows.")
            == "Between 5 to 10 of the 20,000 rows.")


def test_hyphens_are_left_alone():
    text = "A delivery-ready mp4 with word-level timings."
    assert undash(text) == text


def test_text_without_dashes_is_untouched():
    text = "Nothing to change here, at all."
    assert undash(text) == text


def test_no_dash_survives():
    for raw in (f"a {EM} b", f"a{EN}b", f"{EM}leading", f"trailing{EM}"):
        assert EM not in undash(raw) and EN not in undash(raw)


def test_the_parsers_still_understand_a_hand_written_dash():
    """undash cleans what a model wrote; a human script may still contain one,
    and the caption and shot splitters rely on it as a clause boundary."""
    from vidsmith.captions import TRAILING
    from vidsmith.visuals import CLAUSE_END

    assert EM in TRAILING
    assert EM in CLAUSE_END
