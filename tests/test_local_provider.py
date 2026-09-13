"""The `local` provider: which file each shot gets, and where the folder is.

Both faults here were silent. A multi-shot scene took a different file per
shot even when only one matched, so "[visual: byte pointing]" cut to
celebrating and then thinking. And a relative `local_dir` was read from the
current directory, so running from anywhere but the project found no files and
turned every scene into a generated card without a word.
"""
from __future__ import annotations

from pathlib import Path

from conftest import make_scene

from vidsmith.config import ThemeConfig, VisualConfig
from vidsmith.theme import resolve
from vidsmith.visuals import VisualBuilder, plan_shots

POSES = ("byte-waving", "byte-pointing", "byte-thinking", "byte-celebrating")

# long enough that the default shot lengths cut it at three sentence ends
POINTING = ("Look at the top of the screen first. That number is the one that "
            "matters most today. Everything else on the page follows from it.")


def _folder(root: Path, stems) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (root / f"{stem}.png").write_bytes(b"")
    return root


def _builder(tmp_path, local_dir, project_root=None, lines=None):
    workdir = tmp_path / "build" / "visuals"
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = VisualConfig(provider="local", local_dir=str(local_dir))
    log = (lines.append if lines is not None else (lambda *a: None))
    return VisualBuilder(cfg, (640, 360), 24, workdir, keys={}, log=log,
                         theme=resolve("midnight"), theme_cfg=ThemeConfig(),
                         total_scenes=1, project_root=project_root)


def _stems(batch):
    return [Path(s["path"]).stem for s in batch]


def test_the_pointing_scene_really_plans_three_shots():
    """Otherwise the tests below would be asking for a count nothing produces."""
    cfg = VisualConfig()
    scene = make_scene(POINTING, query="byte pointing")
    assert len(plan_shots(scene, 0.25, cfg.min_shot_seconds,
                          cfg.max_shot_seconds)) == 3


def test_a_multi_shot_scene_does_not_pull_files_that_match_less(tmp_path):
    """One pointing image and three other poses: every shot is pointing."""
    clips = _folder(tmp_path / "clips", POSES)
    builder = _builder(tmp_path, clips)
    scene = make_scene(POINTING, query="byte pointing")

    batch = builder._local_batch(scene, "byte pointing", count=3)

    # one distinct file, and the shot plan collapses to it - never a
    # celebrating or thinking frame under a line about pointing
    assert _stems(batch) == ["byte-pointing"]


def test_a_later_scene_reuses_the_best_match_over_an_unused_worse_one(tmp_path):
    """`self.used` used to push a second pointing scene onto a fresh wrong pose."""
    clips = _folder(tmp_path / "clips", POSES + ("byte-sitting", "byte-running"))
    builder = _builder(tmp_path, clips)
    first = make_scene(POINTING, index=0, query="byte pointing")
    again = make_scene(POINTING, index=3, query="byte pointing")

    builder._local_batch(first, "byte pointing", count=3)
    assert _stems(builder._local_batch(again, "byte pointing", count=3)) == ["byte-pointing"]


def test_numbered_variants_tie_with_the_best_match_and_are_taken(tmp_path):
    clips = _folder(tmp_path / "clips",
                    POSES + ("byte-pointing-2", "byte-pointing-3"))
    builder = _builder(tmp_path, clips)
    scene = make_scene(POINTING, query="byte pointing")

    batch = builder._local_batch(scene, "byte pointing", count=3)

    assert sorted(_stems(batch)) == ["byte-pointing", "byte-pointing-2",
                                     "byte-pointing-3"]


def test_equally_good_variants_spread_across_scenes(tmp_path):
    """Stock-style variety survives: the unused variant goes first next time."""
    clips = _folder(tmp_path / "clips", POSES + ("byte-pointing-2",))
    builder = _builder(tmp_path, clips)
    first = make_scene(POINTING, index=0, query="byte pointing")
    second = make_scene("Point at it again.", index=1, query="byte pointing")

    assert _stems(builder._local_batch(first, "byte pointing", count=1)) == ["byte-pointing"]
    assert _stems(builder._local_batch(second, "byte pointing", count=1)) == ["byte-pointing-2"]


def test_a_scene_nothing_matches_is_logged(tmp_path):
    lines = []
    clips = _folder(tmp_path / "clips", POSES)
    builder = _builder(tmp_path, clips, lines=lines)
    scene = make_scene("Rain falls on the harbour at dusk.", query="harbour rain")

    assert len(builder._local_batch(scene, "harbour rain", count=2)) == 2
    assert "no file name matches 'harbour rain'" in "\n".join(lines)


def test_a_relative_local_dir_is_read_from_the_project(tmp_path, monkeypatch):
    project = tmp_path / "projects" / "demo"
    _folder(project / "assets" / "clips", POSES)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    builder = _builder(project, "assets/clips", project_root=project)

    assert sorted(p.stem for p in builder._local) == sorted(POSES)


def test_an_absolute_local_dir_is_left_alone(tmp_path):
    clips = _folder(tmp_path / "shared" / "clips", POSES)
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)

    builder = _builder(project, clips, project_root=project)

    assert len(builder._local) == len(POSES)


def test_a_missing_folder_says_so(tmp_path):
    lines = []
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)

    builder = _builder(project, "assets/clips", project_root=project, lines=lines)

    assert builder._local == []
    said = "\n".join(lines)
    assert "does not exist" in said
    assert "generated card" in said
    assert str(project / "assets" / "clips") in said


def test_an_empty_folder_says_so(tmp_path):
    lines = []
    project = tmp_path / "projects" / "demo"
    clips = project / "assets" / "clips"
    clips.mkdir(parents=True)
    (clips / "notes.txt").write_text("not footage", encoding="utf-8")

    builder = _builder(project, "assets/clips", project_root=project, lines=lines)

    assert builder._local == []
    assert "holds no video or image files" in "\n".join(lines)


def test_a_folder_left_under_the_current_directory_is_pointed_at(tmp_path, monkeypatch):
    """The layout that used to work by accident should not now fail in silence."""
    lines = []
    _folder(tmp_path / "assets" / "clips", POSES)
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)

    builder = _builder(project, "assets/clips", project_root=project, lines=lines)

    assert builder._local == []
    assert "current directory" in "\n".join(lines)


def test_other_providers_never_look_for_a_folder(tmp_path):
    lines = []
    workdir = tmp_path / "visuals"
    VisualBuilder(VisualConfig(provider="cards"), (640, 360), 24, workdir, keys={},
                  log=lines.append, theme=resolve("midnight"),
                  theme_cfg=ThemeConfig(), total_scenes=1, project_root=tmp_path)
    assert not any("local:" in line for line in lines)
