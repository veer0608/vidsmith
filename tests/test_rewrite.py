"""Rewriting one scene of a finished render.

The words change in the script itself, in place, and only when nothing else
about the script moves with them; the build then re-voices and re-films that
scene alone. A failed or stopped edit hands back the script and the video it
started from.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_retake import finished_build

from vidsmith import rewrite, snapshot
from vidsmith.script_parser import parse_text, replace_scene_text

SCRIPT = """# Title

## One
[visual: server racks]
Racks hold the servers.
> say this slowly
Each one hums all night.

Second paragraph of one.

## Two
Then someone types the fix.
"""


# --------------------------------------------------------------------------- #
# the script, edited in place
# --------------------------------------------------------------------------- #
def test_one_scenes_lines_are_replaced_and_every_other_line_kept():
    out = replace_scene_text(SCRIPT, 0, "Racks  hold\nthousands of servers.")

    assert "Racks hold thousands of servers." in out
    assert "> say this slowly" in out, "a note is the writer's and stays"
    assert "[visual: server racks]" in out and "## Two" in out
    assert "Each one hums" not in out
    _, scenes = parse_text(out)
    assert [s.text for s in scenes] == ["Racks hold thousands of servers.",
                                        "Second paragraph of one.",
                                        "Then someone types the fix."]
    assert out.endswith("\n")


def test_a_blank_line_in_new_words_cannot_split_the_scene():
    _, scenes = parse_text(replace_scene_text(SCRIPT, 2, "One.\n\nTwo."))
    assert len(scenes) == 3 and scenes[2].text == "One. Two."


@pytest.fixture
def root(tmp_path):
    root = finished_build(tmp_path / "job")
    (root / "script.md").write_text(SCRIPT, encoding="utf-8")
    return root


def test_the_scenes_are_listed_as_the_script_has_them(root):
    body = rewrite.scenes(root)
    assert [s["text"] for s in body["scenes"]][1] == "Second paragraph of one."
    assert body["words"] == sum(s["words"] for s in body["scenes"])


@pytest.mark.parametrize("text, why", [
    ("## A heading now", "heading, a directive or a note|how many scenes"),
    ("[visual: a different shot]", "more of the script|how many scenes"),
    ("> only a note", "how many scenes"),
    ("Second paragraph of one.", "already says"),
])
def test_words_that_would_change_more_than_the_narration_are_refused(root, text, why):
    with pytest.raises(rewrite.RetakeRefused, match=why):
        rewrite.check(root, 1, text)


def test_a_scene_that_does_not_exist_is_refused(root):
    with pytest.raises(rewrite.RetakeRefused, match="no scene 9"):
        rewrite.check(root, 9, "Anything.")


def test_the_word_limit_counts_the_whole_script_after_the_edit(root):
    with pytest.raises(rewrite.RetakeRefused, match="over this instance's limit"):
        rewrite.check(root, 1, "word " * 50, word_cap=40)


def test_a_render_without_its_build_cannot_rebuild_one_scene(root):
    (root / "build" / "scenes.json").unlink()
    with pytest.raises(rewrite.RetakeRefused, match="kept no working files"):
        rewrite.check(root, 1, "New words.")


# --------------------------------------------------------------------------- #
# the rebuild
# --------------------------------------------------------------------------- #
def test_an_edit_writes_the_script_and_builds_in_edit_mode(root, monkeypatch):
    calls = []

    def build(root, log=print, edit=False):
        calls.append(edit)
        assert "Brand new words." in (Path(root) / "script.md").read_text(encoding="utf-8")
        return Path(root) / "out" / "a-title.mp4"

    monkeypatch.setattr(rewrite.pipeline, "build", build)
    rewrite.apply(root, 1, "Brand new words.", log=lambda *a: None)

    assert calls == [True]
    assert not (root / snapshot.BACKUP).exists()


def test_a_failed_edit_puts_back_the_script_the_build_and_the_video(root, monkeypatch):
    def broken(root, log=print, edit=False):
        root = Path(root)
        (root / "out" / "a-title.mp4").write_bytes(b"half")
        (root / "build" / "scenes.json").write_text("[]", encoding="utf-8")
        (root / "build" / "stray.txt").write_text("x")
        raise RuntimeError("the voice service is down")

    before = (root / "build" / "scenes.json").read_text(encoding="utf-8")
    monkeypatch.setattr(rewrite.pipeline, "build", broken)
    with pytest.raises(RuntimeError, match="voice service"):
        rewrite.apply(root, 1, "Brand new words.")

    assert (root / "script.md").read_text(encoding="utf-8") == SCRIPT
    assert (root / "out" / "a-title.mp4").read_bytes() == b"the video before"
    assert (root / "build" / "scenes.json").read_text(encoding="utf-8") == before
    assert not (root / "build" / "stray.txt").exists()
    assert not (root / snapshot.BACKUP).exists()


def test_a_backup_that_never_finished_changes_nothing_when_restored(root):
    """No list means the copy died before the change began."""
    (root / snapshot.BACKUP / "files").mkdir(parents=True)
    assert snapshot.restore(root) is True
    assert (root / "out" / "a-title.mp4").read_bytes() == b"the video before"
    assert not (root / snapshot.BACKUP).exists()


def test_a_path_that_did_not_exist_before_is_removed_on_restore(root):
    snapshot.take(root, ["out/new.txt", "out/credits.txt"])
    (root / "out" / "new.txt").write_text("made by the change")
    assert snapshot.restore(root)
    assert not (root / "out" / "new.txt").exists()
