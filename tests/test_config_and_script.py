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


def test_stock_footage_is_the_default_provider(tmp_path):
    """Untested until it drifted: `vidsmith new` wrote `cards` while the web
    page defaulted to stock, so the same script gave two different videos."""
    from vidsmith.config import Config

    assert Config().visuals.provider == "pexels"
    write_default_config(tmp_path / "config.yaml", "T")
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert raw["visuals"]["provider"] == "pexels"


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


@pytest.mark.parametrize("kind", ["visual", "b-roll", "broll", "footage",
                                  "shot", "image"])
def test_every_footage_directive_is_the_same_directive(tmp_path, kind):
    """`image` included, which is the one that reads as if it did more.

    It sets the scene's search and nothing else: there is no `image` field on
    Scene and nothing downstream looks for one, so a still only ever enters
    through the `local` provider matching an image file on disk. CLAUDE.md
    claimed it forced a still, which is a directive that appears to do
    something and quietly does not. This test is what keeps the two in step.
    """
    path = _write(tmp_path, f"# T\n\n## One\n[{kind}: a quiet desk]\nNarration here.\n")
    _, scenes = parse_script(path)
    assert len(scenes) == 1
    assert scenes[0].query == "a quiet desk"
    assert scenes[0].diagram == "", "only [diagram:] forces a drawn scene"


def test_a_diagram_directive_is_not_a_footage_directive(tmp_path):
    path = _write(tmp_path, "# T\n\n## One\n[diagram: how a b-tree splits]\nNarration.\n")
    _, scenes = parse_script(path)
    assert scenes[0].diagram == "how a b-tree splits"
    assert scenes[0].query != "how a b-tree splits"


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


def test_a_heading_covers_every_paragraph_under_it(tmp_path):
    """A heading is a section, not a label for one paragraph.

    Scene 1 is the second paragraph under `## The hook`, split off by a blank
    line rather than by a heading of its own. It belongs to that section, so it
    keeps the heading, and with no directive of its own the heading is also what
    its b-roll is searched on.
    """
    _, scenes = parse_script(_write(tmp_path))
    assert scenes[1].heading == "The hook"
    assert scenes[1].query == "The hook"


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


# --------------------------------------------------------------------------- #
# the title the build settles on
# --------------------------------------------------------------------------- #
def _project(tmp_path, heading, configured):
    from vidsmith.config import write_default_config

    root = tmp_path / "proj"
    root.mkdir()
    (root / "script.md").write_text(
        f"# {heading}\n\n## One\nA line of narration for the test.\n", encoding="utf-8")
    write_default_config(root / "config.yaml", configured)
    return root


def _build_parse_only(root):
    from vidsmith import pipeline

    pipeline.build(root, stop_after="parse", log=lambda *a: None)


def test_a_titled_script_does_not_stay_untitled(tmp_path):
    """The build named the video from the script's heading while the job kept
    reporting "Untitled", because the resolved title was never written back."""
    root = _project(tmp_path, "Why Wealth Explodes Late", "Untitled")
    _build_parse_only(root)

    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert raw["title"] == "Why Wealth Explodes Late"


def test_a_title_the_user_set_is_never_overwritten(tmp_path):
    """Only the placeholder is replaced; a chosen title outranks the heading."""
    root = _project(tmp_path, "Heading Title", "A Title I Chose")
    _build_parse_only(root)

    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert raw["title"] == "A Title I Chose"


def test_persisting_the_title_leaves_every_other_key_alone(tmp_path):
    root = _project(tmp_path, "Why Wealth Explodes Late", "Untitled")
    before = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    _build_parse_only(root)
    after = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))

    assert after.pop("title") != before.pop("title")
    assert after == before, "only the title should have moved"


def test_a_video_opens_on_its_hook_by_default():
    """The opening card put 2.4s of silence in front of the first spoken word.

    That is the window a viewer uses to decide whether to stay, spent on a
    title they have already read in the thumbnail. Measured on a real build:
    first word at 2.40s with the card, 0.00s without.

    It stays configurable, because a channel-identity beat is a real trade
    rather than a wrong answer. Only the default moved.
    """
    from vidsmith.config import ThemeConfig

    assert ThemeConfig().title_card is False, \
        "a fresh project opens on a static card instead of its hook"
    assert ThemeConfig().end_card is True, \
        "the end card costs nothing at the point people have already stayed"


def test_a_changed_visual_directive_invalidates_the_cache(tmp_path):
    """Editing only "[visual: ...]" used to be invisible to cache reuse.

    `pipeline.build()` compared `c.text == s.text`, so a rewritten directive left
    the cached scenes in place and the build reused the previous Gemini query.
    It reported success and fetched footage for a shot the script no longer asked
    for. Observed on a real build: the directive was changed to "close up of code
    scrolling on a monitor at night" and the log still read "progress bar filling
    on a computer mon".
    """
    body = "# T\n\n## One\n[visual: {shot}]\nThe narration does not change.\n"
    before = parse_script(_write(tmp_path, body.format(shot="a progress bar")))[1]
    after = parse_script(_write(tmp_path, body.format(shot="code on a monitor")))[1]

    assert before[0].text == after[0].text, "only the directive moved"
    assert before[0].source_key() != after[0].source_key()


def test_an_llm_written_query_does_not_look_like_a_script_change(tmp_path):
    """The reason `query` cannot be the thing compared.

    `llm.suggest_queries()` overwrites `query` for scenes with no directive, so a
    cached scene holds the model's search while a fresh parse holds the heading
    fallback. Comparing `query` would invalidate the cache on every single build
    of every undirected script, re-voicing narration that never changed.
    """
    path = _write(tmp_path, "# T\n\n## The empty studio\nNarration here.\n")
    _, scenes = parse_script(path)
    cached, = parse_script(path)[1]

    assert scenes[0].directive == "", "no directive was written"
    cached.query = "a ring light in an empty room"   # what Gemini would fill in

    assert cached.source_key() == scenes[0].source_key(), \
        "a model-written query is not an edit to the script"


@pytest.mark.parametrize("field,value", [("hold", 4.0), ("diagram", "a tree")])
def test_other_script_authored_directives_are_compared_too(tmp_path, field, value):
    path = _write(tmp_path, "# T\n\n## One\n[visual: a desk]\nNarration.\n")
    _, scenes = parse_script(path)
    edited, = parse_script(path)[1]
    setattr(edited, field, value)

    assert edited.source_key() != scenes[0].source_key()
