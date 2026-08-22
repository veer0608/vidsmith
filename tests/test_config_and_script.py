"""Config and script parsing.

The config round-trip matters more than it looks: a written config that pins
values the theme is supposed to supply will silently override the theme forever
after, which is how a project ended up with amber captions on an ink theme.
"""
from __future__ import annotations

import pytest
import yaml

from vidsmith.config import (ASPECTS, CaptionConfig, Config, env, load_config,
                             write_default_config)
from vidsmith.pipeline import _apply_overrides, _slug
from vidsmith.script_parser import load_scenes, parse_script, save_scenes


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_written_config_round_trips_to_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    write_default_config(path, "A Title")
    loaded = load_config(path)
    fresh = Config(title="A Title")

    assert loaded.title == "A Title"
    assert loaded.to_dict() == fresh.to_dict()


def test_written_config_leaves_theme_driven_colours_blank(tmp_path):
    """Pinned caption colours beat the theme, so the default must not pin any."""
    path = tmp_path / "config.yaml"
    write_default_config(path, "T")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for field in ("primary", "highlight", "outline", "font"):
        assert raw["captions"][field] == "", f"captions.{field} would override the theme"


def test_written_config_covers_every_field(tmp_path):
    """A field missing from the file falls back to a code default silently."""
    path = tmp_path / "config.yaml"
    write_default_config(path, "T")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    expected = Config().to_dict()
    for section, values in expected.items():
        if isinstance(values, dict):
            assert set(raw[section]) == set(values), f"{section} is missing keys"


def test_missing_keys_keep_code_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("title: Partial\naudio:\n  mood: tense\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.audio.mood == "tense"
    assert cfg.audio.duck is True
    assert cfg.voice.name == Config().voice.name


def test_overrides_apply(tmp_path):
    cfg = Config()
    _apply_overrides(cfg, {"aspect": "9:16", "theme": "ink", "watermark": "@x",
                           "mood": "warm", "provider": "pexels"})
    assert cfg.render.aspect == "9:16"
    assert cfg.visuals.orientation == "portrait"
    assert cfg.theme.preset == "ink"
    assert cfg.theme.watermark == "@x"
    assert cfg.audio.mood == "warm"


@pytest.mark.parametrize("value", ["none", "off", "NONE"])
def test_music_can_be_switched_off(value):
    cfg = Config()
    _apply_overrides(cfg, {"music": value})
    assert cfg.audio.music == ""


def test_no_cards_override_clears_both_cards():
    cfg = Config()
    _apply_overrides(cfg, {"no_cards": "1"})
    assert not cfg.theme.title_card and not cfg.theme.end_card


@pytest.mark.parametrize("aspect", sorted(ASPECTS))
def test_every_aspect_has_even_dimensions(aspect):
    """Odd dimensions break yuv420p encoding."""
    w, h = ASPECTS[aspect]
    assert w % 2 == 0 and h % 2 == 0


def test_env_reads_dotenv_files(tmp_path):
    a = tmp_path / "a.env"
    a.write_text('PEXELS_API_KEY="from-a"\n# comment\n', encoding="utf-8")
    b = tmp_path / "b.env"
    b.write_text("PEXELS_API_KEY=from-b\n", encoding="utf-8")

    assert env("PEXELS_API_KEY", a, b) == "from-a"
    assert env("PEXELS_API_KEY", tmp_path / "missing.env", b) == "from-b"
    assert env("NOT_SET_ANYWHERE", a, b) == ""


def test_env_handles_a_bom(tmp_path):
    """Windows editors write UTF-8 with a BOM and it lands on the first key."""
    path = tmp_path / "bom.env"
    path.write_text("GEMINI_API_KEY=abc\n", encoding="utf-8-sig")
    assert env("GEMINI_API_KEY", path) == "abc"


def test_slug_is_filesystem_safe():
    assert _slug("Why Your Bank Statement Lies!") == "why-your-bank-statement-lies"
    assert _slug("***") == "video"


# --------------------------------------------------------------------------- #
# script parsing
# --------------------------------------------------------------------------- #
SCRIPT = """# My Video

## The hook
[visual: aerial city at sunrise]
First scene narration here. It has two sentences.

Second scene, split by the blank line.

## Body
> a production note that is never spoken
[hold: 4.0]
[b-roll: coins stacking macro]
Third scene with **bold** and a [link](http://x.test) in it.
"""


def _write(tmp_path, text=SCRIPT):
    path = tmp_path / "script.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_title_and_scene_count(tmp_path):
    title, scenes = parse_script(_write(tmp_path))
    assert title == "My Video"
    assert len(scenes) == 3


def test_directives_attach_to_their_scene(tmp_path):
    _, scenes = parse_script(_write(tmp_path))
    assert scenes[0].query == "aerial city at sunrise"
    assert scenes[2].query == "coins stacking macro"
    assert scenes[2].hold == 4.0


def test_production_notes_are_not_spoken(tmp_path):
    _, scenes = parse_script(_write(tmp_path))
    assert all("production note" not in s.text for s in scenes)


def test_markdown_is_stripped_from_narration(tmp_path):
    _, scenes = parse_script(_write(tmp_path))
    assert "**" not in scenes[2].text
    assert "http" not in scenes[2].text
    assert "link" in scenes[2].text


def test_headings_carry_to_scenes(tmp_path):
    _, scenes = parse_script(_write(tmp_path))
    assert scenes[0].heading == "The hook"
    assert scenes[2].heading == "Body"


def test_scenes_round_trip_through_json(tmp_path, scenes):
    scenes[0].shots = [{"path": "a.mp4", "duration": 2.5, "credit": "Someone",
                        "credit_url": "http://x.test"}]
    path = tmp_path / "scenes.json"
    save_scenes(scenes, path)
    loaded = load_scenes(path)
    assert [s.to_dict() for s in loaded] == [s.to_dict() for s in scenes]


def test_untitled_script_falls_back_to_the_filename(tmp_path):
    path = tmp_path / "my-great-video.md"
    path.write_text("Just narration, no heading.\n", encoding="utf-8")
    title, scenes = parse_script(path)
    assert title == "My Great Video"
    assert len(scenes) == 1
