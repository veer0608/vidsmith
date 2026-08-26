"""Closed-set config values.

A key whose value is drawn from a closed set used to fall back silently when
the value was outside it. That is survivable when the fallback is visible - an
unknown theme is obvious the moment you look at the video - and not survivable
for `aspect`, which decides the frame size while the *filename* is built from
the string you typed.
"""
from __future__ import annotations

import pytest
import yaml

from vidsmith.config import Config, aspect_tag, load_config, write_default_config


def _project(tmp_path, **sections):
    path = tmp_path / "config.yaml"
    write_default_config(path, "A Test Video")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name, values in sections.items():
        raw[name].update(values)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_a_default_config_loads(tmp_path):
    """The validator must not reject what `vidsmith new` writes."""
    assert load_config(_project(tmp_path)).render.aspect == "16:9"


def test_a_typod_aspect_is_refused(tmp_path):
    """`9x16` is the likely typo, because that is the filename convention.

    It used to load: `cfg.size` fell back to 16:9 while the tag was built from
    the raw string, so a full encode produced a landscape video in a file named
    `-9x16.mp4` and nothing anywhere said so.
    """
    with pytest.raises(ValueError, match="render.aspect"):
        load_config(_project(tmp_path, render={"aspect": "9x16"}))


def test_the_error_names_the_file_and_the_allowed_values(tmp_path):
    with pytest.raises(ValueError) as exc:
        load_config(_project(tmp_path, render={"aspect": "9x16"}))
    message = str(exc.value)
    assert "config.yaml" in message
    assert "9:16" in message and "16:9" in message


@pytest.mark.parametrize("section,key,bad", [
    ("render", "transition", "dissolve"),
    ("captions", "style", "bouncing"),
    ("visuals", "provider", "unsplash"),
    ("visuals", "card_text", "everything"),
    ("theme", "preset", "midnght"),
])
def test_every_closed_set_is_checked(tmp_path, section, key, bad):
    with pytest.raises(ValueError, match=f"{section}.{key}"):
        load_config(_project(tmp_path, **{section: {key: bad}}))


def test_open_ended_keys_are_left_alone(tmp_path):
    """Only closed sets are checked; a voice or an accent is not one."""
    cfg = load_config(_project(
        tmp_path,
        voice={"name": "en-IN-PrabhatNeural"},
        theme={"accent": "#FF7A59", "watermark": "@channel"},
        audio={"music": "assets/bed.wav"},
    ))
    assert cfg.voice.name == "en-IN-PrabhatNeural"
    assert cfg.audio.music == "assets/bed.wav"


def test_a_misspelled_key_is_still_ignored_in_silence(tmp_path):
    """The documented behaviour, deliberately unchanged.

    _merge only sets a key the dataclass already has. This checks values, not
    key names, so the existing trap stands rather than being half-fixed.
    """
    cfg = load_config(_project(tmp_path, render={"asepct": "9:16"}))
    assert cfg.render.aspect == "16:9"


# --------------------------------------------------------------------------- #
# the tag
# --------------------------------------------------------------------------- #
def test_the_default_aspect_has_no_suffix():
    assert aspect_tag("16:9") == ""


@pytest.mark.parametrize("aspect,tag", [
    ("9:16", "-9x16"), ("1:1", "-1x1"), ("4:5", "-4x5"),
])
def test_every_other_aspect_is_suffixed(aspect, tag):
    assert aspect_tag(aspect) == tag


def test_the_tag_matches_the_frame_it_names():
    """The pair that lied. Whatever the tag says, the size must agree."""
    for aspect in ("16:9", "9:16", "1:1", "4:5"):
        cfg = Config()
        cfg.render.aspect = aspect
        w, h = cfg.size
        tag = aspect_tag(aspect)
        portrait_tag = tag in ("-9x16", "-4x5")
        assert (h > w) is portrait_tag, f"{aspect} tagged {tag!r} but sized {w}x{h}"
