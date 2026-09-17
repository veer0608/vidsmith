"""Footage follows what is being said, not what a scene was called.

A viewer watched two finished videos and called the clips mostly unrelated.
Every scene ran one stock search for fifteen to thirty seconds: 27 seconds of
forest trails while the narration moved from tree nodes to disk reads, six
keyboards in a row, and the same dancer in the same library aisle four times.
The searches were also metaphors the narration never stated. A scene longer
than `beat_seconds` is now cut into beats, each searched for in words written
from its own passage, and a scene takes one clip per creator.
"""
from __future__ import annotations

import json

import pytest

from conftest import make_scene

from vidsmith import visuals
from vidsmith.config import ThemeConfig, VisualConfig
from vidsmith.llm import LLMUnavailable
from vidsmith.theme import resolve
from vidsmith.visuals import VisualBuilder, plan_beats

LONG = ("A search that reads every row gets slower as the table grows. With a hundred "
        "rows that is instant. With a hundred million it takes minutes, every single "
        "time anyone asks. Sorting the rows lets you open the list in the middle and "
        "throw away half of it. Each look halves what is left, so a billion rows need "
        "only about thirty looks. Writing is the trouble, because every insert has to "
        "shuffle everything after it along to make room.")


def _builder(tmp_path, lines=None, **cfg):
    workdir = tmp_path / "build" / "visuals"
    workdir.mkdir(parents=True, exist_ok=True)
    log = lines.append if lines is not None else (lambda *a: None)
    return VisualBuilder(VisualConfig(provider="pexels", **cfg), (640, 360), 24,
                         workdir, keys={"gemini": "x", "pexels": "x"}, log=log,
                         theme=resolve("midnight"), theme_cfg=ThemeConfig(),
                         total_scenes=2)


def _long_scene(index=0, **kwargs):
    return make_scene(LONG, index=index, heading="Halving the problem",
                      query="dictionary pages flipping", **kwargs)


# --------------------------------------------------------------------------- #
# plan_beats
# --------------------------------------------------------------------------- #
def test_a_long_scene_is_cut_into_beats_of_whole_shots():
    beats = plan_beats([3.0] * 10, 8.0)
    assert beats == [(0, 3), (3, 6), (6, 10)], "a 3s tail joins the beat before it"


def test_beats_cover_every_shot_once():
    for plan in ([2.5, 4.0, 5.5, 3.0, 2.4, 4.4, 5.0], [5.5] * 9, [2.4] * 13):
        beats = plan_beats(plan, 8.0)
        assert beats[0][0] == 0 and beats[-1][1] == len(plan)
        assert all(a[1] == b[0] for a, b in zip(beats, beats[1:]))


def test_a_short_scene_is_one_beat():
    assert plan_beats([3.0, 3.5], 8.0) == [(0, 2)]


def test_beats_can_be_turned_off():
    assert plan_beats([3.0] * 10, 0) == [(0, 10)]


# --------------------------------------------------------------------------- #
# passages
# --------------------------------------------------------------------------- #
def test_each_beat_carries_the_words_spoken_across_it(tmp_path):
    scene = _long_scene()
    builder = _builder(tmp_path)
    beats = builder._passages(scene, builder._plan(scene))

    assert len(beats) > 2
    spoken = " ".join(b["text"] for b in beats)
    assert spoken.split() == LONG.split(), "a word was lost or repeated between beats"
    assert all(b["query"] == "dictionary pages flipping" for b in beats), \
        "a beat with no search written yet keeps the scene's"


def test_a_one_beat_scene_is_judged_against_its_whole_text(tmp_path, scene):
    builder = _builder(tmp_path)
    [beat] = builder._passages(scene, builder._plan(scene))
    assert beat["text"] == scene.text


def test_a_one_beat_scene_still_gets_a_search_from_its_words(tmp_path, monkeypatch, scene):
    """A short scene's own [visual:] can be the metaphor too: "someone closing a
    laptop lid" sat under "you might think Claude is just another chatbot"."""
    calls = []
    _write_searches(monkeypatch, calls)
    builder = _builder(tmp_path)

    builder.prepare_beats([scene])

    assert calls == [[scene.text]]
    assert builder._beats[0][0]["query"] == "search 1-0"


# --------------------------------------------------------------------------- #
# writing the searches
# --------------------------------------------------------------------------- #
def _write_searches(monkeypatch, calls):
    def fake(passages, api_key, log=None, **k):
        calls.append([p["text"] for p in passages])
        return [f"search {len(calls)}-{i}" for i in range(len(passages))]

    monkeypatch.setattr(visuals.llm, "beat_queries", fake)


def test_every_beat_of_the_video_is_searched_for_in_one_request(tmp_path, monkeypatch):
    calls = []
    _write_searches(monkeypatch, calls)
    other = make_scene(LONG.replace("rows", "records"), index=1, heading="Sorted lists",
                       query="index cards in a drawer")
    scenes = [_long_scene(0), other]
    scenes[1].start = scenes[0].duration
    builder = _builder(tmp_path)

    builder.prepare_beats(scenes)

    assert len(calls) == 1
    beats = builder._beats[0] + builder._beats[1]
    assert len(calls[0]) == len(beats)
    assert [b["query"] for b in beats] == [f"search 1-{i}" for i in range(len(beats))]


def test_the_scenes_visual_is_not_offered_to_the_search_writer(tmp_path, monkeypatch):
    """Offered as the writer's plan, it was kept where it did not fit: "person
    closing laptop" under a line about chatbots, where the same passage without
    it came back "chatbot interface on screen"."""
    seen = []
    monkeypatch.setattr(visuals.llm, "beat_queries",
                        lambda passages, *a, **k: seen.extend(passages) or
                        ["x"] * len(passages))
    _builder(tmp_path).prepare_beats([_long_scene()])
    assert seen and all("visual" not in p for p in seen)
    assert all(p["heading"] == "Halving the problem" for p in seen)


def test_a_rebuild_and_a_second_cut_reuse_the_searches(tmp_path, monkeypatch):
    calls = []
    _write_searches(monkeypatch, calls)
    _builder(tmp_path).prepare_beats([_long_scene()])

    again = _builder(tmp_path)
    again.prepare_beats([_long_scene()])

    assert len(calls) == 1
    assert (tmp_path / "build" / "beats.json").exists(), "not shared between cuts"
    assert again._beats[0][0]["query"] == "search 1-0"


def test_a_redrafted_passage_gets_a_new_search(tmp_path, monkeypatch):
    """Keyed by the words, so a scene five that is now a different scene cannot
    inherit the old one's searches."""
    calls = []
    _write_searches(monkeypatch, calls)
    _builder(tmp_path).prepare_beats([_long_scene()])
    edited = make_scene(LONG.replace("hundred", "thousand"), heading="Halving the problem",
                        query="dictionary pages flipping")

    _builder(tmp_path).prepare_beats([edited])

    assert len(calls) == 2


def test_choosing_a_genre_writes_new_searches_in_that_style(tmp_path, monkeypatch):
    """A genre is part of the beat key, or a rebuild that picked one would be
    served the searches written without it and look exactly the same."""
    seen = []

    def fake(passages, api_key, log=None, genre="any", **k):
        seen.append(genre)
        return [f"{genre} {i}" for i in range(len(passages))]

    monkeypatch.setattr(visuals.llm, "beat_queries", fake)
    _builder(tmp_path).prepare_beats([_long_scene()])
    styled = _builder(tmp_path, genre="cinematic")
    styled.prepare_beats([_long_scene()])

    assert seen == ["any", "cinematic"]
    assert styled._beats[0][0]["query"] == "cinematic 0"


def test_the_default_genre_keeps_the_searches_already_cached(tmp_path, monkeypatch):
    calls = []
    _write_searches(monkeypatch, calls)
    _builder(tmp_path).prepare_beats([_long_scene()])
    _builder(tmp_path, genre="any").prepare_beats([_long_scene()])
    assert len(calls) == 1


def test_no_model_means_each_scene_keeps_its_own_search(tmp_path, monkeypatch):
    lines = []

    def down(*a, **k):
        raise LLMUnavailable("quota")

    monkeypatch.setattr(visuals.llm, "beat_queries", down)
    builder = _builder(tmp_path, lines)

    builder.prepare_beats([_long_scene()])

    assert all(b["query"] == "dictionary pages flipping" for b in builder._beats[0])
    assert "each scene keeps its own search" in "\n".join(lines)


def test_beats_off_writes_no_searches(tmp_path, monkeypatch):
    monkeypatch.setattr(visuals.llm, "beat_queries",
                        lambda *a, **k: pytest.fail("searched with beats off"))
    builder = _builder(tmp_path, beat_seconds=0)
    builder.prepare_beats([_long_scene()])
    assert len(builder._passages(_long_scene(), builder._plan(_long_scene()))) == 1


# --------------------------------------------------------------------------- #
# building a scene beat by beat
# --------------------------------------------------------------------------- #
def _encodes(monkeypatch):
    done = []

    def fake(src, out, duration, *a, **k):
        done.append((src.name, duration))
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(visuals, "normalise_video", fake)
    monkeypatch.setattr(visuals, "normalise_still",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)
    monkeypatch.setattr(visuals.cards, "scene_card", lambda out, *a, **k: out)
    return done


def _batches(tmp_path, asked, empty=()):
    """A stub search: distinct long clips per call, from a creator per call."""
    def batch(query, count, scene, text=None, key=None, need=None):
        asked.append({"query": query, "text": text, "key": key, "count": count})
        if query in empty:
            return []
        n = len(asked)
        return [{"id": f"{n}-{i}", "path": tmp_path / f"{n}-{i}.mp4", "length": 60.0,
                 "author": f"creator {n}-{i}", "page": f"https://p/{n}-{i}", "query": query}
                for i in range(count)]
    return batch


def test_each_beat_is_searched_with_its_own_words(tmp_path, monkeypatch):
    calls = []
    _write_searches(monkeypatch, calls)
    scene = _long_scene()
    builder = _builder(tmp_path)
    builder.prepare_beats([scene])
    asked = []
    monkeypatch.setattr(builder, "_stock_batch", _batches(tmp_path, asked))
    _encodes(monkeypatch)

    builder.build(scene)

    beats = builder._beats[0]
    assert [a["query"] for a in asked] == [b["query"] for b in beats]
    assert [a["text"] for a in asked] == [b["text"] for b in beats]
    assert [a["key"] for a in asked] == [f"0.{i}" for i in range(len(beats))]
    assert sum(s["duration"] for s in scene.shots) == pytest.approx(scene.duration)
    assert {s["query"] for s in scene.shots} == {b["query"] for b in beats}


def test_a_beat_that_finds_nothing_falls_back_to_the_scene_search(tmp_path, monkeypatch):
    calls = []
    _write_searches(monkeypatch, calls)
    scene = _long_scene()
    builder = _builder(tmp_path)
    builder.prepare_beats([scene])
    asked = []
    monkeypatch.setattr(builder, "_stock_batch",
                        _batches(tmp_path, asked, empty={"search 1-1"}))
    _encodes(monkeypatch)

    builder.build(scene)

    assert [a["query"] for a in asked][:3] == ["search 1-0", "search 1-1",
                                               "dictionary pages flipping"]
    assert all(s["credit"] for s in scene.shots), "the fallback left a card behind"


def test_a_scene_with_no_footage_at_all_is_one_card(tmp_path, monkeypatch):
    """Not a card per beat: a repeated card restarts its move at every cut."""
    scene = _long_scene()
    builder = _builder(tmp_path)
    monkeypatch.setattr(builder, "_stock_batch", lambda *a, **k: [])
    _encodes(monkeypatch)

    builder.build(scene)

    assert len(scene.shots) == 1 and scene.shots[0]["credit"] == ""


# --------------------------------------------------------------------------- #
# one clip per creator
# --------------------------------------------------------------------------- #
def _hits(authors):
    return [{"id": str(i), "url": str(i), "author": a, "page": f"https://p/{i}",
             "preview": ""} for i, a in enumerate(authors)]


def _no_downloads(monkeypatch, builder, tmp_path):
    monkeypatch.setattr(builder, "_rerank", lambda hits, *a, **k: hits)
    monkeypatch.setattr(visuals, "_download",
                        lambda url, dest, *a, **k: dest.write_bytes(b"x"))
    monkeypatch.setattr(visuals.ff, "duration", lambda p: 20.0)
    monkeypatch.setattr(builder, "_twin", lambda *a, **k: None)


def test_one_clip_per_creator_in_a_scene(tmp_path, monkeypatch, scene):
    """Three takes of one keyboard from one studio, within ten seconds."""
    lines = []
    builder = _builder(tmp_path, lines)
    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: _hits(
        ["Nothing Ahead", "Nothing Ahead", "Nothing Ahead", "Ada", "Grace"]))
    _no_downloads(monkeypatch, builder, tmp_path)

    picked = builder._stock_batch("q", 3, scene)

    assert [p["author"] for p in picked] == ["Nothing Ahead", "Ada", "Grace"]


def test_a_creator_already_in_the_scene_is_passed_over(tmp_path, monkeypatch, scene):
    lines = []
    builder = _builder(tmp_path, lines)
    builder._scene_creators = {"Ada"}
    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: _hits(["Ada", "Ada"]))
    _no_downloads(monkeypatch, builder, tmp_path)

    assert builder._stock_batch("q", 2, scene) == []
    assert "passed over 2 from Ada" in "\n".join(lines)


def test_the_next_scene_does_not_open_on_the_same_creator(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    builder._last_creator = "Ada"
    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: _hits(["Ada", "Grace"]))
    _no_downloads(monkeypatch, builder, tmp_path)

    assert [p["author"] for p in builder._stock_batch("q", 1, scene)] == ["Grace"]


def test_a_clip_with_no_named_creator_is_never_limited(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: _hits(["", "", ""]))
    _no_downloads(monkeypatch, builder, tmp_path)

    assert len(builder._stock_batch("q", 3, scene)) == 3


def test_the_scene_remembers_its_creators_across_beats(tmp_path, monkeypatch):
    calls = []
    _write_searches(monkeypatch, calls)
    scene = _long_scene()
    builder = _builder(tmp_path)
    builder.prepare_beats([scene])
    seen = []

    def batch(query, count, sc, text=None, key=None, need=None):
        seen.append(set(builder._scene_creators))
        return [{"id": query, "path": tmp_path / "c.mp4", "length": 60.0,
                 "author": f"by {query}", "page": "", "query": query}]

    monkeypatch.setattr(builder, "_stock_batch", batch)
    _encodes(monkeypatch)

    builder.build(scene)

    assert seen[0] == set() and seen[1] == {"by search 1-0"}
    assert builder._last_creator == f"by {builder._beats[0][-1]['query']}"


# --------------------------------------------------------------------------- #
# the request itself
# --------------------------------------------------------------------------- #
def test_the_beat_prompt_asks_for_the_subject_not_a_metaphor():
    from vidsmith.llm import BEAT_QUERY_PROMPT

    low = BEAT_QUERY_PROMPT.lower()
    assert "never a metaphor" in low
    assert "forest path is not a tree structure" in low, "no example of the fault"
    assert "no proper nouns" in low and "one per passage" in low
    assert "generic filler" in low, "person-typing footage fits every line and none"
    assert "really hold footage" in low, "a product feature is not a stock search"


def test_beat_queries_reads_one_search_per_passage(monkeypatch):
    from vidsmith import llm

    sent = []
    monkeypatch.setattr(llm, "generate", lambda prompt, *a, **k: sent.append(prompt) or
                        '["hard drive close up", "engineer reading code!"]')
    got = llm.beat_queries([{"text": "Disks are slow.", "heading": "Disks"},
                            {"text": "Code reads it."}], "k")

    assert got == ["hard drive close up", "engineer reading code"]
    assert "1. [scene: Disks] Disks are slow." in sent[0]
    assert "2. Code reads it." in sent[0]
    assert "STYLE" not in sent[0], "no genre chosen, so nothing added"


@pytest.mark.parametrize("writer", ["beat_queries", "suggest_queries"])
def test_a_genre_steers_the_search_writer_but_not_past_the_subject(monkeypatch, writer):
    from vidsmith import llm

    sent = []
    monkeypatch.setattr(llm, "generate", lambda prompt, *a, **k: sent.append(prompt) or
                        '["forest at dawn"]')
    if writer == "beat_queries":
        llm.beat_queries([{"text": "Trees grow slowly."}], "k", genre="nature")
    else:
        llm.suggest_queries([make_scene("Trees grow slowly.", heading="Trees")], "k",
                            genre="nature", log=lambda *a: None)

    assert "STYLE: the footage for this video should be nature" in sent[0]
    assert "subject of the passage always comes first" in sent[0]
    # pushed harder, it moved "the mug on your desk" outdoors and lost the ship
    assert "place or setting the passage names is part of the subject" in sent[0]
    assert "Only when the passage names no place" in sent[0]
    # technology's words made every shot a screen, doorstep included
    assert "never becomes its subject" in sent[0]


def test_every_style_word_survives_the_search_clean_up():
    """Searches are cut to six words and stripped of digits, so a two-word style
    term lost its second half ("slow motion" reached Pexels as "slow") and "3D"
    would arrive as "D"."""
    import re

    from vidsmith.genres import GENRES

    for name, genre in GENRES.items():
        for word in filter(None, (w.strip() for w in genre.words.split(","))):
            assert " " not in word, f"{name}: {word!r} is two words"
            assert re.fullmatch(r"[A-Za-z\-]+", word), f"{name}: {word!r} will be mangled"


def test_the_style_asks_for_a_search_that_fits_the_limit():
    from vidsmith.genres import prompt_block

    block = prompt_block("cinematic")
    # asked for "one or two words" that "fit the limit", two of three searches
    # came back with no style word at all
    assert "A search with no style word is wrong" in block and "cut off" in block


def test_no_genre_offers_a_screen_as_a_style_word():
    """A noun offered as a style word is taken as a subject."""
    from vidsmith.genres import GENRES

    for name, genre in GENRES.items():
        words = {w.strip() for w in genre.words.split(",")}
        assert not words & {"screen", "laptop", "phone", "data", "digital"}, name


@pytest.mark.parametrize("reply", ['["only one"]', "not json at all"])
def test_a_reply_that_does_not_line_up_is_unavailable(monkeypatch, reply):
    """Searches paired with the wrong passages are worse than the scene's own."""
    from vidsmith import llm

    monkeypatch.setattr(llm, "generate", lambda *a, **k: reply)
    with pytest.raises(LLMUnavailable):
        llm.beat_queries([{"text": "a"}, {"text": "b"}], "k")


# --------------------------------------------------------------------------- #
# verdicts per beat
# --------------------------------------------------------------------------- #
def _judge(monkeypatch, builder):
    calls = []

    def fake(line, query, images, key, log=None, genre="any"):
        calls.append((line, query) if genre == "any" else (line, query, genre))
        return list(range(len(images))), [], True

    monkeypatch.setattr(builder, "_preview", lambda url: b"jpg")
    monkeypatch.setattr(visuals.llm, "rank_clips", fake)
    return calls


def _stills(n=8):
    return [{"id": str(i), "url": "", "preview": f"s{i}", "author": "", "page": ""}
            for i in range(n)]


def test_a_beat_is_judged_against_its_own_passage(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    calls = _judge(monkeypatch, builder)

    builder._rerank(_stills(), scene, "hard drive close up", want=2,
                    text="Fetching from a disk is thousands of times slower.", key="0.1")

    assert calls == [("Fetching from a disk is thousands of times slower.",
                      "hard drive close up")]
    saved = json.loads((builder.workdir / "rerank.json").read_text())["0.1"]
    assert saved["query"] == "hard drive close up" and saved["line"].startswith("Fetching")


def test_a_verdict_for_another_search_is_not_reused(tmp_path, monkeypatch, scene):
    """Its clips are not these, so every one of them would be dropped as absent
    and the beat would come back with nothing judged at all."""
    builder = _builder(tmp_path)
    (builder.workdir / "rerank.json").write_text(json.dumps(
        {"0.1": {"order": ["0", "1"], "reject": [], "filmable": True, "rounds": 3,
                 "query": "forest trail"}}), encoding="utf-8")
    calls = _judge(monkeypatch, builder)

    builder._rerank(_stills(), scene, "hard drive close up", want=2, text="t", key="0.1")

    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# a genre reaches the reranker
# --------------------------------------------------------------------------- #
def test_the_reranker_is_told_the_genre(tmp_path, monkeypatch, scene):
    """A first nature build moved its searches by a word and its picks not at
    all, because the reranker never heard a style had been chosen."""
    builder = _builder(tmp_path, genre="nature")
    calls = _judge(monkeypatch, builder)

    builder._rerank(_stills(), scene, "coffee mug outdoors", want=2, text="t", key="0.1")

    assert calls == [("t", "coffee mug outdoors", "nature")]
    saved = json.loads((builder.workdir / "rerank.json").read_text())["0.1"]
    assert saved["genre"] == "nature"


def test_a_verdict_judged_in_another_style_is_not_reused(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path, genre="nature")
    (builder.workdir / "rerank.json").write_text(json.dumps(
        {"0.1": {"order": [str(i) for i in range(8)], "reject": [], "filmable": True,
                 "rounds": 3, "query": "coffee mug"}}), encoding="utf-8")
    calls = _judge(monkeypatch, builder)

    builder._rerank(_stills(), scene, "coffee mug", want=2, text="t", key="0.1")

    assert len(calls) == 1


def test_a_verdict_with_no_genre_is_still_reused_by_a_default_build(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    (builder.workdir / "rerank.json").write_text(json.dumps(
        {"0.1": {"order": [str(i) for i in range(8)], "reject": [], "filmable": True,
                 "rounds": 3, "query": "coffee mug"}}), encoding="utf-8")
    calls = _judge(monkeypatch, builder)

    builder._rerank(_stills(), scene, "coffee mug", want=2, text="t", key="0.1")

    assert calls == []


@pytest.mark.parametrize("genre,present", [("nature", True), ("any", False)])
def test_the_rerank_prompt_carries_the_style_only_when_chosen(monkeypatch, genre, present):
    from vidsmith import llm

    sent = []
    monkeypatch.setattr(llm, "generate_vision",
                        lambda prompt, *a, **k: sent.append(prompt) or
                        '{"ranked": [1, 0], "reject": [], "filmable": true}')
    llm.rank_clips("line", "query", [b"a", b"b"], "k", genre=genre)

    assert ("STYLE: this video's footage should be nature" in sent[0]) is present
    if present:
        assert "never rejected for being off style" in sent[0]


# --------------------------------------------------------------------------- #
# footage too dark to read
# --------------------------------------------------------------------------- #
def _still(lit_fraction, bright=200, dim=20):
    from io import BytesIO

    from PIL import Image

    img = Image.new("L", (100, 100), dim)
    lit = int(100 * lit_fraction)
    if lit:
        img.paste(bright, (0, 0, lit, 100))
    buf = BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=95)
    return buf.getvalue()


@pytest.mark.parametrize("lit,dark", [(0.05, True), (0.0, True), (0.2, False), (0.9, False)])
def test_a_still_is_too_dark_when_almost_nothing_is_lit(lit, dark):
    """The night-highway truck measured 5% lit; night streets 15 to 40%."""
    assert visuals.too_dark(_still(lit)) is dark


def test_bytes_that_are_not_an_image_are_not_called_dark():
    assert visuals.too_dark(b"jpg") is False


def test_a_dark_clip_is_never_judged_or_picked(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    shown = []

    def fake(line, query, images, key, log=None, genre="any"):
        shown.append(len(images))
        return list(range(len(images))), [], True

    stills = {f"s{i}": _still(0.02 if i == 0 else 0.8) for i in range(8)}
    monkeypatch.setattr(builder, "_preview", lambda url: stills[url])
    monkeypatch.setattr(visuals.llm, "rank_clips", fake)

    kept = builder._rerank(_stills(), scene, "truck at night", want=2, text="t", key="0.1")

    assert shown == [7], "the dark still was shown to the model"
    assert "0" not in {h["id"] for h in kept}


def test_every_rerank_rejects_words_and_adverts():
    """Pixabay's animated picks put a SUBSCRIBE title and a FREE advert under
    the narration; a clip that is mainly text is somebody else's message."""
    from vidsmith.llm import RERANK_PROMPT

    assert '"subscribe"' in RERANK_PROMPT and "an advert" in RERANK_PROMPT


def test_there_is_no_animation_style():
    """Built, rendered from Pexels and Pixabay, and removed: neither library
    holds animation about a concrete subject. See genres.py before re-adding."""
    from vidsmith.genres import GENRES

    assert "animation" not in GENRES


# --------------------------------------------------------------------------- #
# technology never invents a screen
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query,narration,expected", [
    # the live search that put a phone on a dashboard under the doorstep line
    ("modern delivery driver handheld gps",
     "The driver plans a route that stops every few minutes.",
     "modern delivery driver handheld"),
    ("delivery truck GPS routing screen", "Trucks carry thousands of boxes.",
     "delivery truck routing"),
    # the narration names the device, so the screen is the literal subject
    ("phone checkout screen closeup", "You tap buy on your phone.",
     "phone checkout screen closeup"),
    ("code on a monitor", "The software checks every order.", "code on a monitor"),
])
def test_technology_drops_a_device_the_narration_never_named(query, narration, expected):
    from vidsmith.genres import scrub

    assert scrub("technology", query, narration) == expected


def test_other_styles_leave_device_words_alone():
    from vidsmith.genres import scrub

    assert scrub("city", "courier with phone map", "The box arrives.") == \
        "courier with phone map"
    assert scrub("any", "phone screen", "Nothing here.") == "phone screen"


def test_beat_searches_are_scrubbed_against_their_own_passage(monkeypatch):
    from vidsmith import llm

    monkeypatch.setattr(llm, "generate", lambda *a, **k:
                        '["phone showing checkout", "delivery van gps screen"]')
    got = llm.beat_queries([{"text": "You tap buy."},
                            {"text": "The van stops every few minutes."}],
                           "k", genre="technology")

    assert got == ["modern phone showing checkout", "modern delivery van"]


def test_a_scene_search_left_empty_keeps_the_scene_fallback(monkeypatch):
    from vidsmith import llm

    monkeypatch.setattr(llm, "generate", lambda *a, **k: '["gps screen"]')
    scene = make_scene("The van stops every few minutes.", heading="Van")
    filled = llm.suggest_queries([scene], "k", genre="technology", log=lambda *a: None)

    assert filled == 0 and scene.query != "gps screen"


def test_the_technology_reranker_rejects_a_screen_the_narration_never_named(monkeypatch):
    """After "gps" was scrubbed the search read "sleek van navigation", and the
    pick was a phone map on a dashboard under the doorstep line."""
    from vidsmith import llm

    sent = []
    monkeypatch.setattr(llm, "generate_vision",
                        lambda prompt, *a, **k: sent.append(prompt) or
                        '{"ranked": [0, 1], "reject": [], "filmable": true}')
    llm.rank_clips("The box lands on your doorstep.", "sleek van navigation",
                   [b"a", b"b"], "k", genre="technology")
    llm.rank_clips("You tap buy on your phone.", "phone checkout",
                   [b"a", b"b"], "k", genre="technology")
    llm.rank_clips("The box lands on your doorstep.", "van",
                   [b"a", b"b"], "k", genre="city")

    rule = "names no phone, screen or computer"
    assert rule in sent[0]
    assert rule not in sent[1], "the narration names the phone, so it is the subject"
    assert rule not in sent[2], "only Technology has the rule"


def test_navigation_is_a_device_word():
    from vidsmith.genres import scrub

    assert scrub("technology", "sleek van navigation", "The van stops.") == "sleek van"


# --------------------------------------------------------------------------- #
# every styled search carries its style
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("genre,query,expected", [
    # the three live Technology searches that came back with no style word
    ("technology", "delivery trucks loaded overnight", "modern delivery trucks loaded overnight"),
    ("technology", "warehouse worker scanning barcode", "modern warehouse worker scanning barcode"),
    # already styled: untouched
    ("technology", "automated warehouse parcel scanning", "automated warehouse parcel scanning"),
    ("nature", "coffee mug on sunlit desk", "coffee mug on sunlit desk"),
    # at the limit: filler makes the room
    ("city", "driver walking to the front door", "urban driver walking to front door"),
    # at the limit with no filler: the subject wins
    ("city", "delivery driver carrying heavy cardboard boxes",
     "delivery driver carrying heavy cardboard boxes"),
    # no style chosen, or nothing left to style
    ("any", "delivery van", "delivery van"),
    ("technology", "", ""),
])
def test_a_search_with_no_style_word_gets_the_looks(genre, query, expected):
    from vidsmith.genres import ensure_style

    assert ensure_style(genre, query) == expected


def test_every_style_has_a_look_that_is_not_a_place():
    """"outdoors" or "office" in front of a search would move its subject."""
    from vidsmith.genres import GENRES

    for name, genre in GENRES.items():
        if name == "any":
            continue
        assert genre.look and " " not in genre.look, name
        assert genre.look not in {"outdoors", "office", "street", "city", "forest",
                                  "field", "mountain", "river", "downtown"}, name


def test_beat_searches_are_styled_after_they_are_scrubbed(monkeypatch):
    from vidsmith import llm

    monkeypatch.setattr(llm, "generate", lambda *a, **k: '["delivery van gps screen"]')
    got = llm.beat_queries([{"text": "The van stops every few minutes."}], "k",
                           genre="technology")

    assert got == ["modern delivery van"]
