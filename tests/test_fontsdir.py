"""Which directory libass is pointed at, and when it must not be pointed anywhere.

The themes name Windows families. A Linux host has none of them, so the only
thing standing between the captions and a substituted face is `fontsdir`. The
failure this covers is not a crash: the wrong answer renders a whole video in
the wrong face, reports success, and looks correct until someone who knows the
theme watches it.

`FONT_DIR` is derived from the package file, so it is the repo's `assets/fonts`
only while vidsmith is imported from a checkout. Installed into site-packages
it points inside the venv, at a directory no deploy script writes to. Pillow
searches the system on its own and libass does not, so that install renders
every surface correctly except the captions.
"""
from __future__ import annotations

from pathlib import Path

from vidsmith import cards
from vidsmith import ffmpeg_util as ff
from vidsmith import render


def _isolate(monkeypatch, bundled: Path, system=()) -> None:
    """Point both lookups at directories a test controls."""
    monkeypatch.setattr(cards, "FONT_DIR", bundled)
    monkeypatch.setattr(cards, "SYSTEM_FONT_DIRS", tuple(system))


def test_a_bundled_face_is_named(tmp_path, monkeypatch):
    fonts = tmp_path / "bundled"
    fonts.mkdir()
    (fonts / "DejaVuSans.ttf").write_bytes(b"not a real face, but a .ttf")
    _isolate(monkeypatch, fonts)

    assert cards.font_dir() == fonts
    assert render.fontsdir_option().startswith(":fontsdir=")


def test_an_empty_bundled_directory_is_not_named(tmp_path, monkeypatch):
    """The state a documented apt deploy leaves behind.

    `deploy/aws.md` installs fonts-dejavu-core system-wide, which satisfies
    Pillow's own lookup and never touches `assets/fonts`. Naming an empty
    directory to libass substitutes exactly as if it had not been named.
    """
    fonts = tmp_path / "bundled"
    fonts.mkdir()
    _isolate(monkeypatch, fonts)

    assert cards.font_dir() is None
    assert render.fontsdir_option() == ""


def test_a_missing_directory_is_not_named(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "nothing here")

    assert cards.font_dir() is None
    assert render.fontsdir_option() == ""


def test_a_system_directory_is_used_when_nothing_is_bundled(tmp_path, monkeypatch):
    """The install-layout case, and the reason this is not just an exists check.

    A site-packages install derives a bundled directory nothing writes to. The
    host still has DejaVu from its package manager, so there is a usable face
    to name and the captions do not have to degrade.
    """
    system = tmp_path / "usr-share-fonts"
    system.mkdir()
    (system / "DejaVuSans-Bold.ttf").write_bytes(b"not a real face, but a .ttf")
    _isolate(monkeypatch, tmp_path / "venv" / "assets" / "fonts", [system])

    assert cards.font_dir() == system
    # escaped, because a Windows drive colon would otherwise split the filter's
    # own options: the raw path is not what reaches ffmpeg
    assert ff.escape_filter_path(system) in render.fontsdir_option()


def test_a_bundled_face_wins_over_the_system_one(tmp_path, monkeypatch):
    """A host that bundled its own faces meant to use them."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "DejaVuSans.ttf").write_bytes(b"not a real face, but a .ttf")
    system = tmp_path / "usr-share-fonts"
    system.mkdir()
    (system / "DejaVuSans.ttf").write_bytes(b"not a real face, but a .ttf")
    _isolate(monkeypatch, bundled, [system])

    assert cards.font_dir() == bundled


def test_only_a_ttf_counts(tmp_path, monkeypatch):
    """So a directory of licence files does not read as a directory of fonts."""
    fonts = tmp_path / "bundled"
    fonts.mkdir()
    (fonts / "LICENSE.txt").write_text("not a face")
    _isolate(monkeypatch, fonts)

    assert cards.font_dir() is None


def test_the_render_and_healthz_answer_from_one_resolver(tmp_path, monkeypatch):
    """`/healthz` is the only view anyone has of a deployed instance.

    It reported an empty list while the render was substituting a face, and
    both statements were true of different directories. They ask
    `cards.font_dir()` now, so the report cannot describe a directory the
    filtergraph would not name.
    """
    system = tmp_path / "usr-share-fonts"
    system.mkdir()
    (system / "DejaVuSans.ttf").write_bytes(b"not a real face, but a .ttf")
    _isolate(monkeypatch, tmp_path / "venv" / "assets" / "fonts", [system])

    found = cards.font_dir()
    reported = sorted(p.name for p in found.glob("*.ttf")) if found else []
    assert reported == ["DejaVuSans.ttf"]
    assert ff.escape_filter_path(found) in render.fontsdir_option()
