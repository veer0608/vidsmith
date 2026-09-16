"""What pipeline.build actually hands to the render, with the encode stubbed out.

These are not about the video. They are about the arguments: the render stage
takes optional inputs, and an optional input that is wrongly present is a broken
build rather than a missing feature. Everything that shells out to ffmpeg or the
network is replaced, so the whole file runs in milliseconds.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from vidsmith import pipeline as pl
from vidsmith.config import Config, write_default_config

SCRIPT = """# A Test Video

## One
[visual: hands sorting paper]
A first line of narration for the test to speak.

## Two
[visual: wide empty road]
A second line, so the build has more than one scene.
"""


def _touch(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stub")
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    write_default_config(root / "config.yaml", "A Test Video")
    return root


def _configure(root: Path, **sections) -> None:
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    for name, values in sections.items():
        raw[name].update(values)
    (root / "config.yaml").write_text(yaml.safe_dump(raw, sort_keys=False),
                                      encoding="utf-8")


@pytest.fixture
def rendered(monkeypatch):
    """Stub every stage that costs time, and record the render's arguments."""
    calls = {}

    # this machine has real keys in sibling .env files; a test must not spend them
    monkeypatch.setattr(pl, "find_keys",
                        lambda root: {"gemini": "", "pexels": "", "pixabay": ""})

    def fake_narrate(scenes, audio_dir, cfg, force=False, log=print, keys=None):
        clock = 0.0
        for scene in scenes:
            scene.audio = str(_touch(audio_dir / f"scene_{scene.index:03d}.mp3"))
            scene.words = [{"text": w, "start": i * 0.4, "end": i * 0.4 + 0.3}
                           for i, w in enumerate(scene.text.split())]
            scene.duration = 4.0
            scene.start = clock
            clock += scene.duration
        return scenes

    def fake_build_all(scenes, cfg, size, fps, workdir, keys, **kwargs):
        for scene in scenes:
            clip = _touch(workdir / f"scene_{scene.index:03d}_00.mp4")
            scene.shots = [{"path": str(clip), "duration": scene.duration,
                            "credit": "", "credit_url": ""}]
            scene.visual = str(clip)

    def fake_master(picture, narration, out, cfg, audio_cfg, captions, total,
                    theme, theme_cfg, size, scrim=None, hold_tail=0.0):
        calls["captions"] = captions
        calls["scrim"] = scrim
        return _touch(out)

    monkeypatch.setattr(pl.voice, "narrate", fake_narrate)
    monkeypatch.setattr(pl.visuals, "build_all", fake_build_all)
    monkeypatch.setattr(pl.visuals, "normalise_still",
                        lambda src, out, *a, **k: _touch(out))
    monkeypatch.setattr(pl.cards, "title_card", lambda path, *a, **k: _touch(path))
    monkeypatch.setattr(pl.cards, "end_card", lambda path, *a, **k: _touch(path))
    monkeypatch.setattr(pl.cards, "scrim", lambda path, *a, **k: _touch(path))
    monkeypatch.setattr(pl.music, "ensure_bed",
                        lambda workdir, mood: _touch(workdir / f"music-{mood}.wav"))
    monkeypatch.setattr(pl.render, "build_narration",
                        lambda scenes, out, lead_in, total: _touch(out))
    monkeypatch.setattr(pl.render, "build_picture",
                        lambda clips, out, workdir, cfg, size: _touch(out))
    monkeypatch.setattr(pl.render, "master", fake_master)
    monkeypatch.setattr(pl.render, "thumbnail", lambda video, out, at=1.0: _touch(out))
    monkeypatch.setattr(pl.thumbs, "from_stock", lambda *a, **k: None)
    monkeypatch.setattr(pl.thumbs, "choose",
                        lambda video, workdir, *a, **k: SimpleNamespace(
                            path=_touch(Path(workdir) / "frame.jpg"), time=1.0))
    monkeypatch.setattr(pl.thumbs, "titled",
                        lambda frame, out, *a, **k: _touch(out))
    monkeypatch.setattr(pl.ff, "duration", lambda path: 8.0)
    return calls


def _build(root):
    return pl.build(root, stop_after="render", log=lambda *a: None)


# --------------------------------------------------------------------------- #
# the subtitle file
# --------------------------------------------------------------------------- #
def test_captions_off_sends_no_subtitle_file(project, rendered):
    """`--captions none` on a plain project must burn nothing in.

    It used to send Path(""), which is Path(".") - truthy, and it exists - so
    the guard let it through and ffmpeg was told to read the current directory
    as an ASS file. Every such build died in the master pass with "Unable to
    open .".
    """
    _configure(project, captions={"enabled": False, "style": "none"},
               theme={"watermark": "", "lower_thirds": False})
    _build(project)
    assert rendered["captions"] is None


def test_captions_on_sends_the_ass_file(project, rendered):
    _build(project)
    captions = rendered["captions"]
    assert captions is not None
    assert captions.suffix == ".ass" and captions.exists()


def test_an_overlay_still_needs_the_ass_file_when_captions_are_off(project, rendered):
    """The watermark and the lower thirds live in the same ASS file, so turning
    captions off must not take them with it."""
    _configure(project, captions={"enabled": False, "style": "none"},
               theme={"watermark": "@channel"})
    _build(project)
    captions = rendered["captions"]
    assert captions is not None and captions.exists()
    assert "@channel" in captions.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# attribution
# --------------------------------------------------------------------------- #
def _stock_thumbnail(monkeypatch, tmp_path):
    """A real Pexels photograph came back for the thumbnail."""
    photo = _touch(tmp_path / "stock.jpg")
    monkeypatch.setattr(pl.thumbs, "from_stock",
                        lambda *a, **k: {"path": photo, "query": "q",
                                         "author": "Jane Doe",
                                         "page": "https://pexels.com/photo/1"})


def test_a_stock_thumbnail_is_credited_even_when_the_footage_needs_none(
        project, rendered, monkeypatch, tmp_path):
    """Cards owe nobody attribution; the photograph on the thumbnail does.

    The thumbnail credit used to be appended only when the footage block was
    already non-empty, so an empty block short-circuited the condition and a
    cards or local build named no one - and wrote no credits file at all -
    while shipping a real photographer's work on the thumbnail. Pexels' terms
    require the credit and the link back.
    """
    _stock_thumbnail(monkeypatch, tmp_path)
    _configure(project, visuals={"provider": "cards"})
    _build(project)

    files = list((project / "out").glob("credits*.txt"))
    assert files, "a Pexels photograph was used and nothing was written"
    text = files[0].read_text(encoding="utf-8")
    assert "Jane Doe" in text and "pexels.com/photo/1" in text


def test_footage_and_thumbnail_creators_are_both_named(project, rendered,
                                                       monkeypatch, tmp_path):
    _stock_thumbnail(monkeypatch, tmp_path)
    stubbed = pl.visuals.build_all

    def credited(scenes, cfg, size, fps, workdir, keys, **kwargs):
        stubbed(scenes, cfg, size, fps, workdir, keys, **kwargs)
        for scene in scenes:
            scene.shots[0]["credit"] = "Ada Lovelace"
            scene.shots[0]["credit_url"] = "https://pexels.com/@ada"

    monkeypatch.setattr(pl.visuals, "build_all", credited)
    _build(project)

    text = next(iter((project / "out").glob("credits*.txt"))).read_text(encoding="utf-8")
    assert "Ada Lovelace" in text and "Jane Doe" in text
    # the search is kept, so the page can offer its other photographs later
    import json
    choice = json.loads((project / "build" / "thumbnail.json").read_text(encoding="utf-8"))
    assert choice["kind"] == "photo" and choice["query"] == "q"


def test_nothing_is_written_when_nobody_is_owed_a_credit(project, rendered):
    """An empty block over generated cards is correct, not a bug."""
    _configure(project, visuals={"provider": "cards"})
    _build(project)
    assert not list((project / "out").glob("credits*.txt"))


def test_stopping_after_captions_returns_a_real_path(project, rendered):
    """--stop-after captions has to name something on disk either way."""
    _configure(project, captions={"enabled": False, "style": "none"},
               theme={"watermark": "", "lower_thirds": False})
    out = pl.build(project, stop_after="captions", log=lambda *a: None)
    assert isinstance(out, Path) and out.exists()


# --------------------------------------------------------------------------- #
# what the log says, and when
# --------------------------------------------------------------------------- #
def test_done_is_the_last_thing_a_full_build_says(project, rendered):
    """`done` means the run is over, so nothing may follow it.

    The web stepper reads that line as the run completing. It used to be logged
    before the metadata and the delivery check, so on every full build the
    stage label went forwards to done and then back to "writing the
    description". Progress survived it - the bar takes a max - but the label
    someone is watching moved backwards at the end of every render.
    """
    lines = []
    pl.build(project, log=lines.append)

    stages = [line.split(" ", 1)[0] for line in lines if line.strip()]
    assert "done" in stages, stages
    assert stages[-1] == "done", f"something was logged after done: {stages}"
    assert stages.count("done") == 1, f"done was logged twice: {stages}"
    assert "check" in stages, "the delivery check did not run on a full build"
    assert stages.index("check") < stages.index("done")


def test_a_stopped_build_still_says_done(project, rendered):
    """--stop-after render skips the closing work, not the closing line.

    Moving `done` past that work is how it would go missing here: the early
    return used to carry its own copy, and one call site is the point.
    """
    lines = []
    pl.build(project, stop_after="render", log=lines.append)

    stages = [line.split(" ", 1)[0] for line in lines if line.strip()]
    assert stages[-1] == "done", stages
    assert "check" not in stages, "a stopped build is incomplete by design"


# --------------------------------------------------------------------------- #
# a retake
# --------------------------------------------------------------------------- #
def test_a_retake_asks_no_model_keeps_the_thumbnail_and_recredits_the_description(
        project, rendered, monkeypatch):
    """One shot changed; the title, chapters and thumbnail did not.

    A retake runs this same build rather than a copy of the render stage, so it
    must not spend what a first build spends: the thumbnail pick and the
    description are model calls, and the photograph could come back different.
    The credits are the one thing that has to change, in the file people paste.
    """
    import json

    monkeypatch.setattr(pl, "find_keys",
                        lambda root: {"gemini": "a-real-key", "pexels": "", "pixabay": ""})
    for name in ("suggest_queries", "upload_metadata"):
        monkeypatch.setattr(pl.llm, name, lambda *a, _n=name, **k: pytest.fail(f"{_n} was called"))
    monkeypatch.setattr(pl.thumbs, "from_stock", lambda *a, **k: pytest.fail("thumbnail chosen again"))
    monkeypatch.setattr(pl.thumbs, "choose", lambda *a, **k: pytest.fail("thumbnail chosen again"))
    stubbed = pl.visuals.build_all

    def swapped(scenes, cfg, size, fps, workdir, keys, **kwargs):
        assert not keys["gemini"], "the visuals stage could still spend a model call"
        stubbed(scenes, cfg, size, fps, workdir, keys, **kwargs)
        scenes[0].shots[0].update(credit="New Creator", credit_url="https://pexels.com/v/22")

    monkeypatch.setattr(pl.visuals, "build_all", swapped)
    out = project / "out"
    out.mkdir()
    (out / "a-test-video.jpg").write_bytes(b"the chosen thumbnail")
    (out / "credits.txt").write_text(
        "Footage from Pexels\nOld Creator - https://pexels.com/v/10\n"
        "Thumbnail: Jane Doe - https://pexels.com/photo/1\n", encoding="utf-8")
    (out / "youtube.json").write_text(json.dumps(
        {"title": "A Test Video", "description": "What it is about.", "tags": ["t"]}),
        encoding="utf-8")

    lines = []
    pl.build(project, log=lines.append, retake=True)

    assert (out / "a-test-video.jpg").read_bytes() == b"the chosen thumbnail"
    credits = (out / "credits.txt").read_text(encoding="utf-8")
    assert "New Creator" in credits and "Old Creator" not in credits
    assert credits.count("Thumbnail: Jane Doe") == 1, "the photograph is still owed its credit"
    description = (out / "description.txt").read_text(encoding="utf-8")
    assert "What it is about." in description and "New Creator" in description
    assert "Jane Doe" in description
    assert [l.split(" ", 1)[0] for l in lines][-1] == "done"


@pytest.mark.parametrize("model", ["answers", "is unavailable"])
def test_an_edit_keeps_the_thumbnail_and_never_publishes_stale_chapter_times(
        project, rendered, monkeypatch, model):
    """An edited scene moves every chapter after it.

    The description is written again when the model answers. When it does not,
    the old one is kept without its chapters: every time in it is still a valid
    time, and every one now points at the wrong moment.
    """
    import json

    key = "a-real-key" if model == "answers" else ""
    monkeypatch.setattr(pl, "find_keys",
                        lambda root: {"gemini": key, "pexels": "", "pixabay": ""})
    monkeypatch.setattr(pl.llm, "suggest_queries", lambda *a, **k: 0)
    monkeypatch.setattr(pl.llm, "upload_metadata", lambda *a, **k: {
        "title": "A Test Video", "description": "Written again.",
        "chapters": [{"time": "0:00", "label": "Start"}], "tags": []})
    monkeypatch.setattr(pl.thumbs, "from_stock", lambda *a, **k: pytest.fail("thumbnail chosen again"))
    out = project / "out"
    out.mkdir()
    (out / "a-test-video.jpg").write_bytes(b"the chosen thumbnail")
    (out / "youtube.json").write_text(json.dumps({
        "title": "A Test Video", "description": "The old one.", "tags": [],
        "chapters": [{"time": "0:00", "label": "Old"}, {"time": "0:12", "label": "Moved"}]}),
        encoding="utf-8")

    pl.build(project, log=lambda *a: None, edit=True)

    assert (out / "a-test-video.jpg").read_bytes() == b"the chosen thumbnail"
    description = (out / "description.txt").read_text(encoding="utf-8")
    if model == "answers":
        assert "Written again." in description
    else:
        assert "The old one." in description and "0:12" not in description


def test_kept_thumbnail_credit_reads_only_the_thumbnail_lines(tmp_path):
    path = tmp_path / "credits.txt"
    assert pl.kept_thumbnail_credit(path) == ""
    path.write_text("Footage from Pexels\nAda - x\nThumbnail: Jane - y", encoding="utf-8")
    assert pl.kept_thumbnail_credit(path) == "Thumbnail: Jane - y\n"


# --------------------------------------------------------------------------- #
# what an edited directive costs
# --------------------------------------------------------------------------- #
def _parsed(root):
    pl.build(root, stop_after="parse", log=lambda *a: None)


def test_editing_one_directive_leaves_the_other_scene_alone(tmp_path):
    """The round trip this saves is a real one, measured on a real build.

    Rewording a single "[visual: ...]" line dropped the narration and every
    scene's clips, so both cuts were re-voiced, re-ranked and re-encoded to move
    one shot. The rerank is a vision call per scene against a daily budget that
    appears in no response header.
    """
    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    write_default_config(root / "config.yaml", "A Test Video")
    _parsed(root)

    build = root / "build"
    vis = build / "visuals"
    vis.mkdir(parents=True, exist_ok=True)
    (build / "narration.wav").write_bytes(b"the voice")
    (vis / "scene_000_00.mp4").write_bytes(b"scene one")
    (vis / "scene_001_00.mp4").write_bytes(b"scene two")

    (root / "script.md").write_text(
        SCRIPT.replace("wide empty road", "a lit window at night"), encoding="utf-8")
    _parsed(root)

    assert (build / "narration.wav").read_bytes() == b"the voice", \
        "no word of narration changed"
    assert (vis / "scene_000_00.mp4").exists(), "scene one was not touched"
    assert not (vis / "scene_001_00.mp4").exists(), "scene two is the edited one"


def test_editing_one_scenes_words_rebuilds_that_scene_and_keeps_the_rest(tmp_path):
    """A re-worded line used to re-voice and re-search the whole video.

    The mixed narration holds the old voice, so it goes, and so do the edited
    scene's clips, which were cut to its old slot. Every other scene keeps its
    clips: a clip is cut to its own scene's slot, which an edit elsewhere does
    not move, only where the slot starts.
    """
    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    write_default_config(root / "config.yaml", "A Test Video")
    _parsed(root)

    build = root / "build"
    vis = build / "visuals"
    vis.mkdir(parents=True, exist_ok=True)
    (build / "narration.wav").write_bytes(b"the voice")
    (vis / "scene_000_00.mp4").write_bytes(b"scene one")
    (vis / "scene_001_00.mp4").write_bytes(b"scene two")

    (root / "script.md").write_text(
        SCRIPT.replace("A second line,", "A rewritten second line,"),
        encoding="utf-8")
    _parsed(root)

    assert not (build / "narration.wav").exists(), "it holds the old words"
    assert not (vis / "scene_001_00.mp4").exists(), "cut to the old slot"
    assert (vis / "scene_000_00.mp4").read_bytes() == b"scene one"


def test_the_edited_scene_is_voiced_again_and_the_others_keep_their_timings(tmp_path):
    from vidsmith.script_parser import load_scenes, save_scenes

    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    write_default_config(root / "config.yaml", "A Test Video")
    _parsed(root)
    scenes = load_scenes(root / "build" / "scenes.json")
    for scene in scenes:
        scene.words = [{"text": "old", "start": 0.0, "end": 0.3}]
        scene.duration, scene.audio = 4.0, f"scene_{scene.index}.mp3"
    save_scenes(scenes, root / "build" / "scenes.json")

    (root / "script.md").write_text(
        SCRIPT.replace("A second line,", "A rewritten second line,"), encoding="utf-8")
    _parsed(root)

    after = load_scenes(root / "build" / "scenes.json")
    assert after[0].words and after[0].duration == 4.0, "an untouched scene lost its voice"
    assert not after[1].words and after[1].text.startswith("A rewritten"), \
        "the edited scene would be spoken with its old words"


def test_adding_a_scene_still_drops_everything(tmp_path):
    """Everything keyed by position points at the wrong scene once one is added."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    write_default_config(root / "config.yaml", "A Test Video")
    _parsed(root)

    build = root / "build"
    vis = build / "visuals"
    vis.mkdir(parents=True, exist_ok=True)
    (build / "narration.wav").write_bytes(b"the voice")
    (vis / "scene_000_00.mp4").write_bytes(b"scene one")

    (root / "script.md").write_text(SCRIPT + "\nA third scene arrives.\n", encoding="utf-8")
    _parsed(root)

    assert not (build / "narration.wav").exists()
    assert not (vis / "scene_000_00.mp4").exists()
