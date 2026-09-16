"""Changing one shot of a finished build.

A viewer called the footage of a finished video mostly unrelated, and the only
fix was to build the whole thing again: new searches, new verdicts, new
downloads, and every other shot free to change with it. A retake puts a clip
the person chose under one shot and delivers the video again from what is
already on disk. These cover the choosing and the swap; the encode is stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from conftest import make_scene

from vidsmith import retake, visuals
from vidsmith.config import VisualConfig, write_default_config
from vidsmith.script_parser import save_scenes

KEYS = {"pexels": "px", "pixabay": "pb", "gemini": ""}
LEDGER = {
    "0:0": {"credit": "Ada", "url": "https://pexels.com/v/10", "id": "10", "query": "server rack"},
    "0:1": {"credit": "Grace", "url": "https://pexels.com/v/11", "id": "11", "query": "server rack"},
    "1:0": {"credit": "Linus", "url": "https://pexels.com/v/12", "id": "12", "query": "keyboard"},
}


def finished_build(root: Path, provider: str = "pexels") -> Path:
    """What a finished stock render leaves on disk once its downloads are gone."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "script.md").write_text(
        "# A Title\n\n## Racks\nRacks hold the servers. Each one hums all night long.\n\n"
        "Then someone types the fix.\n", encoding="utf-8")
    write_default_config(root / "config.yaml", "A Title")
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    raw["visuals"]["provider"] = provider
    (root / "config.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    build, out = root / "build", root / "out"
    vis = build / "visuals"
    vis.mkdir(parents=True)
    out.mkdir()
    first = make_scene("Racks hold the servers. Each one hums all night long.",
                       index=0, duration=6.0, heading="Racks")
    second = make_scene("Then someone types the fix.", index=1, duration=4.0)
    first.start, second.start = 2.4, 8.4
    for scene, cuts in ((first, [3.0, 3.0]), (second, [4.0])):
        scene.shots = []
        for j, d in enumerate(cuts):
            clip = vis / f"scene_{scene.index:03d}_{j:02d}.mp4"
            clip.write_bytes(f"clip {scene.index}:{j}".encode())
            entry = LEDGER[f"{scene.index}:{j}"]
            scene.shots.append({"path": str(clip), "duration": d, "credit": entry["credit"],
                                "credit_url": entry["url"]})
    save_scenes([first, second], build / "scenes.json")
    (vis / "credits.json").write_text(json.dumps(LEDGER), encoding="utf-8")
    (out / "a-title.mp4").write_bytes(b"the video before")
    (out / "credits.txt").write_text("Footage from Pexels\nAda - x\n", encoding="utf-8")
    return root


def _hits(*ids):
    return [{"id": i, "url": f"https://videos.pexels.com/{i}.mp4", "duration": 15,
             "author": f"creator {i}", "page": f"https://pexels.com/v/{i}",
             "preview": f"https://images.pexels.com/{i}.jpg"} for i in ids]


@pytest.fixture
def root(tmp_path):
    return finished_build(tmp_path / "job")


@pytest.fixture
def searches(monkeypatch):
    asked = []

    def search(query, key, orientation, want_h):
        asked.append(query)
        return _hits("10", "20", "21", "22", "23")

    monkeypatch.setattr(visuals, "pexels_search", search)
    return asked


# --------------------------------------------------------------------------- #
# the shots
# --------------------------------------------------------------------------- #
def test_every_shot_is_listed_where_it_plays_with_what_is_said_over_it(root):
    body = retake.shots(root)
    rows = body["shots"]

    assert [(r["scene"], r["shot"]) for r in rows] == [(0, 0), (0, 1), (1, 0)]
    assert [r["start"] for r in rows] == [2.4, 5.4, 8.4], "the title card comes first"
    assert rows[0]["clip"] == "10" and rows[0]["query"] == "server rack"
    assert rows[0]["credit"] == "Ada" and rows[0]["heading"] == "Racks"
    said = f"{rows[0]['said']} {rows[1]['said']}".split()
    assert said == "Racks hold the servers. Each one hums all night long.".split(), \
        "every word lands under exactly one shot"
    assert rows[0]["said"] and rows[1]["said"]
    assert all(r["swappable"] for r in rows)


def test_a_drawn_scene_has_no_other_clips(root):
    (root / "build" / "diagram_scenes.json").write_text('{"1": true}', encoding="utf-8")

    rows = retake.shots(root)["shots"]

    assert [r["swappable"] for r in rows] == [True, True, False]
    with pytest.raises(retake.RetakeRefused, match="diagram"):
        retake.candidates(root, 1, 0, keys=KEYS)


def test_a_cards_build_has_no_other_clips(tmp_path):
    root = finished_build(tmp_path / "job", provider="cards")

    assert not any(r["swappable"] for r in retake.shots(root)["shots"])
    with pytest.raises(retake.RetakeRefused, match="no other clips"):
        retake.candidates(root, 0, 0, keys=KEYS)


def test_a_render_that_kept_no_working_files_says_so(tmp_path):
    root = finished_build(tmp_path / "job")
    (root / "build" / "scenes.json").unlink()

    with pytest.raises(retake.RetakeRefused, match="kept no working files"):
        retake.shots(root)


def test_a_shot_that_does_not_exist_is_refused(root):
    with pytest.raises(retake.RetakeRefused, match="no shot 5"):
        retake.candidates(root, 0, 5, keys=KEYS)


# --------------------------------------------------------------------------- #
# the candidates
# --------------------------------------------------------------------------- #
def test_the_candidates_come_from_the_shots_own_search(root, searches):
    body = retake.candidates(root, 0, 0, keys=KEYS)

    assert searches == ["server rack"]
    assert body["query"] == "server rack" and body["current"] == "10"
    assert body["duration"] == 3.0
    ids = [c["id"] for c in body["candidates"]]
    assert "10" not in ids, "a clip already in the video is not offered again"


def test_kept_clips_come_first_and_rejected_ones_last_but_still_offered(root, searches):
    """The model chose the shot being replaced, so its verdict is shown, not obeyed."""
    (root / "build" / "visuals" / "rerank.json").write_text(json.dumps({
        "0.0": {"order": ["22", "20", "21"], "reject": ["21"], "query": "server rack"},
        # another scene's verdict on its own search says nothing about this one
        "1": {"order": ["23"], "reject": ["20"], "query": "keyboard"},
    }), encoding="utf-8")

    body = retake.candidates(root, 0, 0, keys=KEYS)

    assert [c["id"] for c in body["candidates"]] == ["22", "20", "23", "21"]
    assert [c["verdict"] for c in body["candidates"]] == ["kept", "kept", "", "rejected"]


def test_a_typed_search_replaces_the_shots_own(root, searches):
    (root / "build" / "visuals" / "rerank.json").write_text(json.dumps({
        "0": {"order": ["22"], "reject": ["20"], "query": "server rack"}}), encoding="utf-8")

    body = retake.candidates(root, 0, 0, keys=KEYS, query="  blinking   lights " + "x" * 200)

    assert searches[0].startswith("blinking lights x") and len(searches[0]) == retake.MAX_QUERY
    assert all(c["verdict"] == "" for c in body["candidates"]), \
        "a verdict on another search is not about these clips"


def test_searching_needs_the_providers_key(root, searches):
    with pytest.raises(retake.RetakeRefused, match="needs its API key"):
        retake.candidates(root, 0, 0, keys={"pexels": ""})
    assert not searches


def test_a_failed_search_is_a_refusal_with_the_reason(root, monkeypatch):
    def down(*a, **k):
        raise ConnectionError("pexels is down")

    monkeypatch.setattr(visuals, "pexels_search", down)
    with pytest.raises(retake.RetakeRefused, match="pexels is down"):
        retake.candidates(root, 0, 0, keys=KEYS)


# --------------------------------------------------------------------------- #
# the swap
# --------------------------------------------------------------------------- #
@pytest.fixture
def encodes(monkeypatch, searches):
    """Stub the download, the probe, the clip encode and the build around it."""
    calls = {"normalise": [], "build": []}
    monkeypatch.setattr(visuals, "_download",
                        lambda url, out, headers=None: out.parent.mkdir(parents=True, exist_ok=True)
                        or out.write_bytes(b"downloaded") or out)
    monkeypatch.setattr(retake.ff, "duration", lambda path: 15.0)

    def normalise(src, out, duration, size, fps, start=0.0):
        calls["normalise"].append((src.name, out.name, duration, start))
        out.write_bytes(b"the new clip")
        return out

    def build(root, log=print, retake=False):
        calls["build"].append(retake)
        (Path(root) / "out" / "a-title.mp4").write_bytes(b"the video after")
        return Path(root) / "out" / "a-title.mp4"

    monkeypatch.setattr(visuals, "normalise_video", normalise)
    monkeypatch.setattr(retake.pipeline, "build", build)
    return calls


def test_a_swap_encodes_the_new_clip_to_the_old_slot_and_delivers_again(root, encodes):
    lines = []
    final = retake.replace(root, 0, 1, "22", keys=KEYS, log=lines.append)

    vis = root / "build" / "visuals"
    assert (vis / "scene_000_01.mp4").read_bytes() == b"the new clip"
    assert (vis / "scene_000_00.mp4").read_bytes() == b"clip 0:0", "no other shot moved"
    src, written, duration, start = encodes["normalise"][0]
    assert src == "pexels_22.mp4" and duration == 3.0, "the scene must still sum to its narration"
    assert start == 1.0, "a long clip skips its first second, as the build does"
    assert not written.startswith("scene_"), \
        "a half-written clip named scene_* would be cut into the video beside the real one"
    assert encodes["build"] == [True]
    assert final.read_bytes() == b"the video after"

    ledger = json.loads((vis / "credits.json").read_text(encoding="utf-8"))
    assert ledger["0:1"] == {"credit": "creator 22", "url": "https://pexels.com/v/22",
                             "id": "22", "query": "server rack"}
    assert ledger["0:0"] == LEDGER["0:0"]
    assert not (root / "build" / retake.BACKUP).exists()
    assert "retake   scene 0 shot 1: pexels 22 by creator 22" in lines[0]


@pytest.mark.parametrize("clip, why", [
    ("10", "already has"),            # this shot's own clip
    ("11", "already in the video"),   # the shot beside it
    ("99", "not in the results"),     # nothing the search returned
])
def test_a_clip_that_cannot_be_used_is_refused_before_anything_changes(root, encodes, clip, why):
    with pytest.raises(retake.RetakeRefused, match=why):
        retake.replace(root, 0, 0, clip, keys=KEYS)

    assert not encodes["normalise"] and not encodes["build"]
    assert (root / "build" / "visuals" / "scene_000_00.mp4").read_bytes() == b"clip 0:0"


def test_a_failed_swap_leaves_the_video_it_started_from(root, encodes, monkeypatch):
    def broken(root, log=print, retake=False):
        (Path(root) / "out" / "a-title.mp4").write_bytes(b"half a vid")
        (Path(root) / "out" / "stray.txt").write_text("x")
        raise RuntimeError("ffmpeg fell over in the master pass")

    monkeypatch.setattr(retake.pipeline, "build", broken)
    with pytest.raises(RuntimeError, match="master pass"):
        retake.replace(root, 0, 1, "22", keys=KEYS)

    assert (root / "out" / "a-title.mp4").read_bytes() == b"the video before"
    assert not (root / "out" / "stray.txt").exists()
    assert (root / "build" / "visuals" / "scene_000_01.mp4").read_bytes() == b"clip 0:1"
    assert json.loads((root / "build" / "visuals" / "credits.json").read_text()) == LEDGER
    assert not (root / "build" / retake.BACKUP).exists()


def test_a_stopped_swap_is_put_back_too(root, encodes, monkeypatch):
    """The web page stops a run with a BaseException, which `except Exception` misses."""
    class Stopped(BaseException):
        pass

    def stopped(root, log=print, retake=False):
        (Path(root) / "out" / "a-title.mp4").write_bytes(b"half a vid")
        raise Stopped()

    monkeypatch.setattr(retake.pipeline, "build", stopped)
    with pytest.raises(Stopped):
        retake.replace(root, 0, 1, "22", keys=KEYS)

    assert (root / "out" / "a-title.mp4").read_bytes() == b"the video before"


def test_a_swap_the_process_died_during_is_put_back_at_the_next_start(root):
    build = retake.Build(root)
    target = build.shot_path(0, 1)
    retake._stash(build, target)
    (root / "out" / "a-title.mp4").write_bytes(b"truncated")
    target.write_bytes(b"the new clip")

    assert retake.recover(root) is True
    assert (root / "out" / "a-title.mp4").read_bytes() == b"the video before"
    assert target.read_bytes() == b"clip 0:1"
    assert retake.recover(root) is False, "nothing is left to recover"


def test_a_shots_still_follows_its_clip(root, monkeypatch):
    runs = []

    def grab(args):
        runs.append(args)
        Path(args[-1]).write_bytes(b"jpg")

    monkeypatch.setattr(retake.ff, "run", grab)
    first = retake.frame(root, 0, 1)
    retake.frame(root, 0, 1)
    assert first.exists() and len(runs) == 1, "an unchanged shot is not decoded twice"

    import os
    clip = root / "build" / "visuals" / "scene_000_01.mp4"
    later = first.stat().st_mtime + 10
    os.utime(clip, (later, later))
    retake.frame(root, 0, 1)
    assert len(runs) == 2, "a replaced clip gets a new still"


# --------------------------------------------------------------------------- #
# what the build records for later
# --------------------------------------------------------------------------- #
def test_the_ledger_remembers_each_shots_clip_and_search_across_a_rebuild(tmp_path, monkeypatch):
    scene = make_scene("Look at the top of the screen first.", duration=4.0)
    builder = visuals.VisualBuilder(VisualConfig(provider="pexels", rerank=False),
                                    (1920, 1080), 30, tmp_path / "visuals", {},
                                    log=lambda *a: None)
    monkeypatch.setattr(builder, "_stock_batch", lambda *a, **k: [
        {"id": "4242", "path": tmp_path / "c.mp4", "length": 30.0, "author": "Ada",
         "page": "https://pexels.com/v/4242", "query": "a screen"}])
    monkeypatch.setattr(visuals, "normalise_video",
                        lambda src, out, *a, **k: out.write_bytes(b"x") or out)

    builder.build(scene)
    ledger = json.loads((tmp_path / "visuals" / "credits.json").read_text())
    assert ledger["0:0"]["id"] == "4242" and ledger["0:0"]["query"] == "a screen"

    monkeypatch.setattr(visuals.ff, "duration", lambda p: 4.0)
    again = make_scene("Look at the top of the screen first.", duration=4.0)
    builder.build(again)
    assert again.shots[0]["clip"] == "4242" and again.shots[0]["query"] == "a screen", \
        "a reused shot lost what it was, so a later rebuild would forget it"
