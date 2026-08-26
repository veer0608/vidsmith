"""The two CLI commands that read a finished build.

Both used to reach for a file by pattern and get the wrong one, quietly. Neither
needs ffmpeg to prove it: what is wrong is which path is chosen and what is
written, not what the encoder does with it.
"""
from __future__ import annotations

import io
import json
import sys

import pytest
import yaml

from pathlib import Path

from vidsmith import cli, pipeline
from vidsmith.config import write_default_config

META = {
    "title": "Why Your Bank Statement Lies",
    "description": "A short description of the video.",
    "chapters": [{"time": "0:00", "label": "The hook"}],
    "tags": ["finance", "explainer"],
}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "out").mkdir(parents=True)
    (root / "build").mkdir(parents=True)
    write_default_config(root / "config.yaml", "A Test Video")
    return pipeline.Project(root)


# --------------------------------------------------------------------------- #
# metadata
# --------------------------------------------------------------------------- #
def test_regenerating_metadata_keeps_the_credits(project):
    """Pexels' terms require the credit; `vidsmith meta` used to drop it.

    The pipeline folds all_credits() into youtube.txt under a CREDITS heading.
    cmd_meta kept its own writer that emitted _readable_meta() alone, so
    regenerating a description stripped the attribution out of the one file you
    paste into YouTube - and left description.txt stale beside it.
    """
    (project.out / "credits.txt").write_text(
        "Footage from Pexels (https://www.pexels.com)\nAda Lovelace - http://x.test/1\n",
        encoding="utf-8")

    pipeline.write_metadata(project.out, META)

    text = (project.out / "youtube.txt").read_text(encoding="utf-8")
    assert "CREDITS" in text
    assert "Ada Lovelace" in text


def test_regenerating_metadata_refreshes_every_file(project):
    """youtube.json and description.txt must not go stale beside youtube.txt."""
    (project.out / "youtube.json").write_text('{"title": "stale"}', encoding="utf-8")
    (project.out / "description.txt").write_text("stale", encoding="utf-8")

    pipeline.write_metadata(project.out, META)

    assert json.loads((project.out / "youtube.json").read_text())["title"] == META["title"]
    assert "stale" not in (project.out / "description.txt").read_text(encoding="utf-8")


def test_metadata_without_credits_writes_no_credits_heading(project):
    """Generated cards owe nobody a credit, so an absent block is correct."""
    pipeline.write_metadata(project.out, META)
    assert "CREDITS" not in (project.out / "youtube.txt").read_text(encoding="utf-8")


def test_the_cli_and_the_pipeline_share_one_writer():
    """The bug was two writers, so the fix is that there is only one."""
    source = (cli.__file__)
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "write_metadata" in text
    assert "_readable_meta" not in text, "cmd_meta must not format metadata itself"


# --------------------------------------------------------------------------- #
# printing what a stock library gives you
# --------------------------------------------------------------------------- #
CREATOR = "Nguyễn Thị Hồng"          # a real Pexels credit; U+1ECB is not cp1252


def test_a_creator_name_is_written_as_utf8(project):
    """The files are utf-8 throughout, whatever the console is."""
    (project.out / "credits.txt").write_text(
        f"Footage from Pexels (https://www.pexels.com)\n{CREATOR} - http://x.test/1\n",
        encoding="utf-8")
    pipeline.write_metadata(project.out, META)
    assert CREATOR in (project.out / "youtube.txt").read_text(encoding="utf-8")


def test_printing_a_creator_name_does_not_kill_the_command(monkeypatch):
    """A Windows console is cp1252 and a photographer's name is not.

    `vidsmith meta` wrote every file correctly and then died printing the
    credits block, which is the worst possible moment to fail: the work was
    done and the exit code said otherwise.
    """
    raw = io.BytesIO()
    monkeypatch.setattr(
        sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", write_through=True))
    with pytest.raises(UnicodeEncodeError):
        print(CREATOR)

    cli._printable_console()
    print(CREATOR)                    # must not raise
    assert "Nguy" in raw.getvalue().decode("utf-8", "replace")


def test_reconfiguring_a_stream_that_cannot_be_reconfigured_is_survivable(monkeypatch):
    """Under pytest's capture, or a pipe, stdout may not offer reconfigure."""
    monkeypatch.setattr(sys, "stdout", object())
    cli._printable_console()          # must not raise


# --------------------------------------------------------------------------- #
# which cut gets sampled
# --------------------------------------------------------------------------- #
def _cut(project, name):
    path = project.out / name
    path.write_bytes(b"stub")
    return path


def test_the_widescreen_cut_is_not_confused_for_another_aspect(project):
    """16:9 has an empty tag, so `*{tag}.mp4` collapsed to `*.mp4`.

    That matched every cut in out/, and `demo-1x1.mp4` sorts before `demo.mp4`,
    so asking for the widescreen thumbnails sampled the square video instead.
    """
    cfg = pipeline.load_config(project.config_path)
    for name in ("a-test-video-1x1.mp4", "a-test-video-4x5.mp4",
                 "a-test-video-9x16.mp4", "a-test-video.mp4"):
        _cut(project, name)

    picked = cli._delivery_file(project, cfg, "")
    assert picked.name == "a-test-video.mp4"


@pytest.mark.parametrize("aspect,expected", [
    ("9:16", "a-test-video-9x16.mp4"),
    ("1:1", "a-test-video-1x1.mp4"),
    ("4:5", "a-test-video-4x5.mp4"),
])
def test_each_other_aspect_finds_its_own_cut(project, aspect, expected):
    cfg = pipeline.load_config(project.config_path)
    cfg.render.aspect = aspect
    for name in ("a-test-video-1x1.mp4", "a-test-video-4x5.mp4",
                 "a-test-video-9x16.mp4", "a-test-video.mp4"):
        _cut(project, name)

    picked = cli._delivery_file(project, cfg, cli.aspect_tag(aspect))
    assert picked.name == expected


def test_a_renamed_title_still_resolves(project):
    """The config title may have moved since the build, so the scan still runs."""
    cfg = pipeline.load_config(project.config_path)
    _cut(project, "an-older-name.mp4")
    assert cli._delivery_file(project, cfg, "").name == "an-older-name.mp4"


def test_nothing_built_for_this_aspect_is_none(project):
    cfg = pipeline.load_config(project.config_path)
    cfg.render.aspect = "9:16"
    _cut(project, "a-test-video.mp4")
    assert cli._delivery_file(project, cfg, "-9x16") is None


# --------------------------------------------------------------------------- #
# doctor answers the question it is asked
# --------------------------------------------------------------------------- #
def test_doctor_reports_every_key_the_build_reads(capsys, tmp_path):
    """It kept its own list of three and stayed at three.

    A new voice provider added keys to find_keys() and `doctor` - the command
    whose entire job is "which keys resolve" - went on answering for three,
    with nothing to show it was incomplete. /healthz was right the whole time,
    because it derives from find_keys() rather than repeating it.
    """
    from vidsmith import cli as cli_mod

    cli_mod.cmd_doctor(object())
    printed = capsys.readouterr().out
    for var in pipeline.KEY_ENV.values():
        assert var in printed, f"doctor never mentions {var}"


def test_the_key_mapping_is_the_only_list():
    """find_keys returns exactly the keys KEY_ENV names, and no others."""
    resolved = pipeline.find_keys(Path(__file__).parent)
    assert set(resolved) == set(pipeline.KEY_ENV)


def test_every_key_has_a_note_for_doctor_to_print():
    missing = set(pipeline.KEY_ENV) - set(pipeline.KEY_NOTES)
    assert not missing, f"no doctor note for {missing}"
