"""No shot shows the same footage twice.

A nine-minute build replayed stock clips a few seconds apart at least ten
times. The reranker had left most scenes two to four clips, `collapse()` merged
each scene's plan down to that many ten-to-thirty-second shots, and
`normalise_video` looped every clip shorter than the shot it was given with
`-stream_loop`. Rebuilt locally from the same script, 13 of its 36 footage shots
asked a clip for more than it held: 47 seconds of replayed picture.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess

import pytest

from conftest import make_scene

from vidsmith import ffmpeg_util as ff
from vidsmith import visuals
from vidsmith.config import ThemeConfig, VisualConfig
from vidsmith.theme import resolve
from vidsmith.visuals import (RERANK_ROUNDS, VisualBuilder, fit_shots, plan_shots,
                              same_footage)

MIN_S = 2.4

# long enough that the default shot lengths cut it at three sentence ends
THREE_SHOTS = ("Look at the top of the screen first. That number is the one that "
               "matters most today. Everything else on the page follows from it.")


# --------------------------------------------------------------------------- #
# fit_shots
# --------------------------------------------------------------------------- #
def test_no_clip_is_asked_for_more_than_it_holds():
    rnd = random.Random(20260915)
    for _ in range(500):
        plan = [rnd.uniform(MIN_S, 6.0) for _ in range(rnd.randint(1, 8))]
        lengths = [rnd.uniform(1.0, 25.0) for _ in range(rnd.randint(1, 10))]
        if sum(lengths) < sum(plan):
            continue
        fitted = fit_shots(plan, lengths, MIN_S)

        assert sum(d for d, _ in fitted) == pytest.approx(sum(plan), abs=1e-6)
        assert all(d > 0 for d, _ in fitted)
        for d, i in fitted:
            assert d <= lengths[i] + 1e-6, f"a {lengths[i]:.1f}s clip was given {d:.1f}s"
        clips = [i for _, i in fitted]
        assert len(clips) == len(set(clips)), "one clip played twice in a scene"


def test_too_little_footage_slows_every_clip_by_the_same_factor():
    """The one case left where a shot outlasts its clip, and it is not a loop."""
    fitted = fit_shots([5.0] * 6, [3.0, 7.0, 5.0], MIN_S)

    assert sorted(i for _, i in fitted) == [0, 1, 2], "every clip there is plays"
    assert sum(d for d, _ in fitted) == pytest.approx(30.0)
    for d, i in fitted:
        assert d / [3.0, 7.0, 5.0][i] == pytest.approx(2.0)


def test_the_plan_stands_when_the_clips_are_long_enough():
    """Cuts sit on sentence ends; nothing should move them without a reason."""
    fitted = fit_shots([3.0, 4.0, 5.0], [20.0] * 5, MIN_S)
    assert [d for d, _ in fitted] == pytest.approx([3.0, 4.0, 5.0])


def test_the_longest_clip_plays_the_longest_shot():
    fitted = fit_shots([3.0, 10.0], [4.0, 12.0], MIN_S)
    assert [i for _, i in fitted] == [0, 1]
    assert [d for d, _ in fitted] == pytest.approx([3.0, 10.0])


def test_a_short_clip_moves_the_cut_only_as_far_as_it_must():
    fitted = fit_shots([6.0, 6.0], [4.0, 20.0], MIN_S)
    by_clip = {i: d for d, i in fitted}
    assert by_clip[0] == pytest.approx(4.0)
    assert by_clip[1] == pytest.approx(8.0)


def test_fewer_clips_than_shots_still_merges_the_plan():
    assert len(fit_shots([3.0, 3.0, 3.0, 3.0], [20.0, 20.0], MIN_S)) == 2


def test_extra_clips_are_used_only_when_the_rest_cannot_cover_the_scene():
    assert [i for _, i in fit_shots([10.0], [12.0, 4.0], MIN_S)] == [0]

    fitted = fit_shots([10.0], [4.0, 4.0, 4.0], MIN_S)
    assert len(fitted) == 3
    assert all(d <= 4.0 + 1e-6 for d, _ in fitted)


def test_a_clip_of_unknown_length_holds_anything():
    assert fit_shots([3.0, 3.0], [float("inf"), float("inf")], MIN_S) == [
        (pytest.approx(3.0), 0), (pytest.approx(3.0), 1)]


def test_there_is_nothing_to_fit_without_a_clip():
    with pytest.raises(ValueError):
        fit_shots([3.0], [], MIN_S)


# --------------------------------------------------------------------------- #
# the builder
# --------------------------------------------------------------------------- #
def _builder(tmp_path, lines=None, **cfg):
    workdir = tmp_path / "build" / "visuals"
    workdir.mkdir(parents=True, exist_ok=True)
    log = lines.append if lines is not None else (lambda *a: None)
    return VisualBuilder(VisualConfig(provider="pexels", **cfg), (640, 360), 24,
                         workdir, keys={"gemini": "x", "pexels": "x"}, log=log,
                         theme=resolve("midnight"), theme_cfg=ThemeConfig(),
                         total_scenes=1)


def _source(tmp_path, name, length):
    return {"id": name, "path": tmp_path / f"{name}.mp4", "length": length,
            "author": name, "page": ""}


def _record_encodes(monkeypatch):
    encodes = []

    def fake(src, out, duration, *a, **k):
        encodes.append((src.name, duration))
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(visuals, "normalise_video", fake)
    return encodes


def test_the_builder_cuts_a_scene_to_the_lengths_of_its_clips(tmp_path, monkeypatch):
    scene = make_scene(THREE_SHOTS)
    builder = _builder(tmp_path)
    lengths = {"short.mp4": 2.5, "long.mp4": 40.0, "mid.mp4": 5.0}
    monkeypatch.setattr(builder, "_stock_batch", lambda *a, **k: [
        _source(tmp_path, name[:-4], length) for name, length in lengths.items()])
    encodes = _record_encodes(monkeypatch)

    builder.build(scene)

    assert sum(d for _, d in encodes) == pytest.approx(scene.duration, abs=1e-6)
    for name, d in encodes:
        assert d <= lengths[name] + 1e-6, f"{name} ({lengths[name]}s) was given {d:.2f}s"
    assert [s["duration"] for s in scene.shots] == [d for _, d in encodes]


def test_a_clip_taken_for_cover_and_not_needed_goes_back(tmp_path, monkeypatch):
    scene = make_scene("Short line here.", duration=4.0)
    builder = _builder(tmp_path)
    builder.used = {"a", "b"}
    monkeypatch.setattr(builder, "_stock_batch", lambda *a, **k: [
        _source(tmp_path, "a", 10.0), _source(tmp_path, "b", 10.0)])
    _record_encodes(monkeypatch)

    builder.build(scene)

    assert len(scene.shots) == 1
    assert builder.used == {"a"}, "an unplaced clip stayed reserved from later scenes"


def test_slowing_down_is_said_out_loud(tmp_path, monkeypatch):
    lines = []
    scene = make_scene(THREE_SHOTS)
    builder = _builder(tmp_path, lines)
    monkeypatch.setattr(builder, "_stock_batch",
                        lambda *a, **k: [_source(tmp_path, "tiny", 2.0)])
    _record_encodes(monkeypatch)

    builder.build(scene)

    assert "slowing it to fit rather than looping" in "\n".join(lines)


def test_a_scene_cut_to_any_number_of_shots_is_reused(tmp_path, monkeypatch):
    """Reuse used to recognise only the plan's count or one shot, and a scene
    cut to its clips is often neither."""
    scene = make_scene(THREE_SHOTS)
    assert len(plan_shots(scene, 0.25, MIN_S, 5.5)) == 3
    builder = _builder(tmp_path)
    for j in range(2):
        builder._shot_paths(scene, 2)[j].write_bytes(b"x")
    monkeypatch.setattr(visuals.ff, "duration", lambda p: scene.duration / 2)
    monkeypatch.setattr(builder, "_stock_batch",
                        lambda *a, **k: pytest.fail("a finished scene searched again"))

    builder.build(scene)

    assert len(scene.shots) == 2


def test_a_gap_in_the_shots_on_disk_is_not_reused(tmp_path, monkeypatch):
    scene = make_scene(THREE_SHOTS)
    builder = _builder(tmp_path)
    paths = builder._shot_paths(scene, 3)
    paths[0].write_bytes(b"x")
    paths[2].write_bytes(b"x")
    monkeypatch.setattr(visuals.ff, "duration", lambda p: scene.duration / 2)
    searched = []
    monkeypatch.setattr(builder, "_stock_batch",
                        lambda *a, **k: searched.append(1) or [])
    monkeypatch.setattr(visuals, "normalise_still",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)
    monkeypatch.setattr(visuals.cards, "scene_card", lambda out, *a, **k: out)

    builder.build(scene)

    assert searched


# --------------------------------------------------------------------------- #
# judging more candidates when a scene is short of clips
# --------------------------------------------------------------------------- #
def _hits(n):
    return [{"id": str(i), "url": "", "preview": f"still-{i}", "author": "", "page": ""}
            for i in range(n)]


def _judge(monkeypatch, builder, keep=2):
    """A reranker that keeps the first `keep` stills of every batch."""
    calls = []

    def fake(text, query, images, key, log=None):
        calls.append(len(images))
        return list(range(len(images))), list(range(keep, len(images))), True

    monkeypatch.setattr(builder, "_preview", lambda url: b"jpg")
    monkeypatch.setattr(visuals.llm, "rank_clips", fake)
    return calls


def test_a_starved_scene_judges_the_next_candidates(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    calls = _judge(monkeypatch, builder)

    keepers = builder._rerank(_hits(30), scene, "q", want=6)

    assert calls == [8, 8, 8]
    assert [h["id"] for h in keepers] == ["0", "1", "8", "9", "16", "17"]


def test_the_rounds_stop_at_the_limit(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    calls = _judge(monkeypatch, builder)

    builder._rerank(_hits(30), scene, "q", want=20)

    assert len(calls) == RERANK_ROUNDS


def test_a_scene_with_enough_keepers_spends_one_call(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    calls = _judge(monkeypatch, builder, keep=6)

    assert len(builder._rerank(_hits(30), scene, "q", want=4)) == 6
    assert calls == [8]


def test_clips_an_earlier_scene_took_do_not_count(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    builder.used = {"0", "1"}
    calls = _judge(monkeypatch, builder)

    builder._rerank(_hits(30), scene, "q", want=2)

    assert calls == [8, 8]


def test_a_rebuild_spends_no_more_calls_than_the_first_build(tmp_path, monkeypatch, scene):
    first = _builder(tmp_path)
    _judge(monkeypatch, first)
    kept = [h["id"] for h in first._rerank(_hits(30), scene, "q", want=20)]

    again = _builder(tmp_path)
    calls = _judge(monkeypatch, again)

    assert [h["id"] for h in again._rerank(_hits(30), scene, "q", want=20)] == kept
    assert calls == []


def test_a_verdict_cached_before_rounds_counts_as_one(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    (builder.workdir / "rerank.json").write_text(json.dumps(
        {"0": {"order": [str(i) for i in range(8)],
               "reject": [str(i) for i in range(2, 8)], "filmable": True}}),
        encoding="utf-8")
    calls = _judge(monkeypatch, builder)

    builder._rerank(_hits(30), scene, "q", want=20)

    assert len(calls) == RERANK_ROUNDS - 1


def test_a_failed_first_call_keeps_the_search_order(tmp_path, monkeypatch, scene):
    builder = _builder(tmp_path)
    monkeypatch.setattr(builder, "_preview", lambda url: b"jpg")

    def down(*a, **k):
        raise visuals.llm.LLMUnavailable("no")

    monkeypatch.setattr(visuals.llm, "rank_clips", down)
    hits = _hits(30)
    assert builder._rerank(hits, scene, "q", want=6) == hits


# --------------------------------------------------------------------------- #
# one footage under two ids
# --------------------------------------------------------------------------- #
FRAME = bytes(range(256)) * 2 + bytes(64)    # a 32x18 grey frame


def test_same_footage_is_the_same_length_and_the_same_frame():
    assert same_footage((10.96, FRAME), (10.96, FRAME))
    # a re-upload is a re-encode, so the frame is close rather than equal
    assert same_footage((10.96, FRAME), (11.0, bytes(min(255, b + 2) for b in FRAME)))


def test_different_footage_is_not_the_same():
    assert not same_footage((10.96, FRAME), (12.0, FRAME))
    assert not same_footage((10.96, FRAME), (10.96, bytes(255 - b for b in FRAME)))
    assert not same_footage((float("inf"), FRAME), (float("inf"), FRAME))


@pytest.mark.slow
def test_footage_uploaded_twice_is_played_once(tmp_path, monkeypatch, scene):
    """Pexels 853987 and 4671883: one phone clip, two uploaders, both in a scene."""
    shoot, reupload, other = (tmp_path / f"{n}.mp4" for n in ("shoot", "reupload", "other"))
    ff.run(["-f", "lavfi", "-i", "testsrc2=s=160x90:d=4:r=24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(shoot)])
    ff.run(["-i", str(shoot), "-c:v", "libx264", "-crf", "35", str(reupload)])
    ff.run(["-f", "lavfi", "-i", "color=c=teal:s=160x90:d=4:r=24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(other)])
    files = {"853987": shoot, "4671883": reupload, "7": other}

    lines = []
    builder = _builder(tmp_path, lines)
    monkeypatch.setattr(visuals, "pexels_search", lambda *a, **k: [
        {"id": i, "url": i, "author": i, "page": ""} for i in files])
    monkeypatch.setattr(builder, "_rerank", lambda hits, *a, **k: hits)
    monkeypatch.setattr(visuals, "_download",
                        lambda url, dest, *a, **k: shutil.copyfile(files[url], dest))

    picked = builder._stock_batch("q", 3, scene)

    assert [p["id"] for p in picked] == ["853987", "7"]
    assert "the same footage as 853987" in "\n".join(lines)


# --------------------------------------------------------------------------- #
# the encode, against real ffmpeg
# --------------------------------------------------------------------------- #
def _brightness(path, size=(64, 36)):
    raw = subprocess.run(
        [ff.ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    frame = size[0] * size[1]
    return [sum(raw[k:k + frame]) / frame for k in range(0, len(raw), frame)]


@pytest.mark.slow
def test_a_short_clip_is_slowed_to_its_shot_not_looped(tmp_path):
    """A ramp from black to bright: a loop drops back to black at two seconds."""
    src = tmp_path / "ramp.mp4"
    ff.run(["-f", "lavfi", "-i", "color=c=black:s=64x36:d=2:r=24,format=gray,geq=lum=T*110",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)])
    out = tmp_path / "shot.mp4"

    visuals.normalise_video(src, out, 5.0, (64, 36), 24)

    assert ff.duration(out) == pytest.approx(5.0, abs=1 / 24 + 1e-3)
    levels = _brightness(out)
    falls = [b - a for a, b in zip(levels, levels[1:]) if b < a - 12]
    assert not falls, f"the picture fell back {falls} - the clip replayed"
    assert levels[-1] > 150, "the shot never reached the end of its clip"


@pytest.mark.slow
def test_a_long_clip_is_still_trimmed_to_its_shot(tmp_path):
    src = tmp_path / "long.mp4"
    ff.run(["-f", "lavfi", "-i", "color=c=gray:s=64x36:d=6:r=24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)])
    out = tmp_path / "shot.mp4"

    visuals.normalise_video(src, out, 2.5, (64, 36), 24, start=1.0)

    assert ff.duration(out) == pytest.approx(2.5, abs=1 / 24 + 1e-3)


def test_the_encode_never_asks_ffmpeg_to_loop(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(visuals.ff, "duration", lambda p: 2.0)
    monkeypatch.setattr(visuals.ff, "run", lambda args, **k: seen.append(args))

    visuals.normalise_video(tmp_path / "a.mp4", tmp_path / "b.mp4", 9.0, (64, 36), 24)

    assert "-stream_loop" not in seen[0]
