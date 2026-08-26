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
