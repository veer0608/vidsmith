"""Whether libass is pointed at a font directory, and when it must not be.

The themes name Windows families. A Linux host has none of them, so the only
thing standing between the captions and a substituted face is `fontsdir`. The
failure this covers is not a crash: an empty `assets/fonts` renders a whole
video in the wrong face, reports success, and looks correct until someone who
knows the theme watches it.
"""
from __future__ import annotations

from vidsmith import cards
from vidsmith import render


def test_a_directory_holding_a_face_is_named(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "DejaVuSans.ttf").write_bytes(b"not a real face, but a .ttf")
    monkeypatch.setattr(cards, "FONT_DIR", fonts)

    option = render.fontsdir_option()
    assert option.startswith(":fontsdir=")
    assert "fonts" in option


def test_an_empty_directory_is_not_named(tmp_path, monkeypatch):
    """The state a documented apt deploy leaves behind.

    `deploy/aws.md` installs fonts-dejavu-core system-wide, which satisfies
    Pillow's own lookup and never touches `assets/fonts`. Naming an empty
    directory to libass substitutes exactly as if it had not been named, so the
    guard has to be about faces rather than about the directory.
    """
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    monkeypatch.setattr(cards, "FONT_DIR", fonts)

    assert render.fontsdir_option() == ""


def test_a_missing_directory_is_not_named(tmp_path, monkeypatch):
    monkeypatch.setattr(cards, "FONT_DIR", tmp_path / "nothing here")

    assert render.fontsdir_option() == ""


def test_the_render_and_healthz_agree_on_what_counts(tmp_path, monkeypatch):
    """Both ask for `*.ttf`, so a box cannot report fonts it will not use.

    `/healthz` is the only view anyone has of a deployed instance. If it
    counted a face the filtergraph would not name, the report would say the
    captions are fine while they render in something else.
    """
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "notes.txt").write_text("not a face")
    monkeypatch.setattr(cards, "FONT_DIR", fonts)

    reported = sorted(p.name for p in cards.FONT_DIR.glob("*.ttf"))
    assert reported == []
    assert render.fontsdir_option() == ""
