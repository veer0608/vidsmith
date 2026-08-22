"""End-to-end checks that need real ffmpeg.

The invariant these exist for: the picture must be exactly as long as the
narration slot it covers. When that broke, the video looked fine for two
seconds and every cut after the first was out of sync with the voice.
"""
from __future__ import annotations

import pytest

from vidsmith import ffmpeg_util as ff
from vidsmith import music
from vidsmith.config import RenderConfig, ThemeConfig, VisualConfig
from vidsmith.render import build_narration, build_picture
from vidsmith.theme import resolve
from vidsmith.visuals import VisualBuilder

pytestmark = pytest.mark.slow

SIZE = (640, 360)
FPS = 24


@pytest.fixture(scope="module")
def have_ffmpeg():
    try:
        ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")


def _tone(path, seconds, freq=440):
    ff.run(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path)])
    return path


def _clip(path, seconds, colour="black"):
    ff.run(["-f", "lavfi", "-i", f"color=c={colour}:s={SIZE[0]}x{SIZE[1]}:d={seconds}",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(path)])
    return path


# --------------------------------------------------------------------------- #
# narration
# --------------------------------------------------------------------------- #
def test_narration_is_exactly_the_requested_length(have_ffmpeg, tmp_path, scenes):
    for s in scenes:
        s.audio = str(_tone(tmp_path / f"a{s.index}.wav", 1.0))
    total = sum(s.duration for s in scenes) + 2.0
    out = build_narration(scenes, tmp_path / "narration.wav", 0.25, total)
    assert ff.duration(out) == pytest.approx(total, abs=0.05)


def test_each_scene_speaks_at_its_own_start(have_ffmpeg, tmp_path, scenes):
    """Silence where a scene begins would mean the whole mix is offset."""
    for s in scenes:
        s.audio = str(_tone(tmp_path / f"a{s.index}.wav", 0.8))
    total = sum(s.duration for s in scenes)
    out = build_narration(scenes, tmp_path / "narration.wav", 0.25, total)

    for s in scenes:
        probe = tmp_path / f"probe{s.index}.wav"
        ff.run(["-ss", f"{s.start + 0.30:.3f}", "-t", "0.3", "-i", str(out),
                "-c:a", "pcm_s16le", str(probe)])
        stats = ff.probe(probe)
        assert float(stats["format"]["duration"]) > 0.2


# --------------------------------------------------------------------------- #
# picture
# --------------------------------------------------------------------------- #
def test_picture_length_is_the_sum_of_its_shots(have_ffmpeg, tmp_path):
    clips = [_clip(tmp_path / f"c{i}.mp4", d)
             for i, d in enumerate((2.0, 3.0, 1.5))]
    # measure the clips rather than trusting the requested lengths: lavfi's
    # colour source runs at 25fps, so -r 24 re-times it a little
    expected = sum(ff.duration(c) for c in clips)
    out = build_picture(clips, tmp_path / "picture.mp4", tmp_path,
                        RenderConfig(fps=FPS), SIZE)
    assert ff.duration(out) == pytest.approx(expected, abs=0.05)


def test_missing_clip_fails_loudly(have_ffmpeg, tmp_path):
    with pytest.raises(RuntimeError, match="missing scene clips"):
        build_picture([tmp_path / "nope.mp4"], tmp_path / "p.mp4", tmp_path,
                      RenderConfig(fps=FPS), SIZE)


# --------------------------------------------------------------------------- #
# the cache regression
# --------------------------------------------------------------------------- #
def _builder(workdir):
    return VisualBuilder(VisualConfig(provider="cards"), SIZE, FPS, workdir,
                         keys={}, log=lambda *a: None, theme=resolve("midnight"),
                         theme_cfg=ThemeConfig(), total_scenes=1)


def test_a_cached_clip_of_the_right_length_is_reused(have_ffmpeg, tmp_path, scene):
    scene.duration = 4.0
    builder = _builder(tmp_path)
    builder.build(scene)
    first = ff.duration(tmp_path / "scene_000_00.mp4")

    stamp = (tmp_path / "scene_000_00.mp4").stat().st_mtime
    builder.build(scene)
    assert (tmp_path / "scene_000_00.mp4").stat().st_mtime == stamp
    assert first == pytest.approx(4.0, abs=0.15)


def test_a_stale_clip_of_the_wrong_length_is_rebuilt(have_ffmpeg, tmp_path, scene):
    """The bug: a leftover clip from an older plan was reused on its filename
    alone, and the picture came out shorter than the speech."""
    scene.duration = 6.0
    stale = _clip(tmp_path / "scene_000_00.mp4", 2.0)
    assert ff.duration(stale) == pytest.approx(2.0, abs=0.15)

    _builder(tmp_path).build(scene)
    assert ff.duration(tmp_path / "scene_000_00.mp4") == pytest.approx(6.0, abs=0.15)
    assert sum(s["duration"] for s in scene.shots) == pytest.approx(6.0, abs=1e-6)


def test_shots_always_cover_the_whole_scene(have_ffmpeg, tmp_path, scene):
    scene.duration = 7.0
    _builder(tmp_path).build(scene)
    on_disk = sum(ff.duration(s["path"]) for s in scene.shots)
    assert on_disk == pytest.approx(scene.duration, abs=0.15)


# --------------------------------------------------------------------------- #
# music
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mood", music.moods())
def test_every_mood_renders_audible_audio(have_ffmpeg, tmp_path, mood):
    bed = music.build_loop(tmp_path / f"{mood}.wav", mood)
    assert ff.duration(bed) == pytest.approx(
        music.CHORD_SECONDS * 4, abs=music.CHORD_SECONDS)

    out = ff.run(["-i", str(bed), "-af", "volumedetect", "-f", "null", "-"],
                 quiet=False)
    levels = out.stderr
    mean = float(levels.split("mean_volume:")[1].split("dB")[0])
    # loud enough to hear under a -14 LUFS voice, quiet enough not to clip
    assert -25 < mean < -10, f"bed sits at {mean} dB"


def test_the_bed_is_built_once_and_reused(have_ffmpeg, tmp_path):
    first = music.ensure_bed(tmp_path, "calm")
    stamp = first.stat().st_mtime
    again = music.ensure_bed(tmp_path, "calm")
    assert again == first and again.stat().st_mtime == stamp


def test_an_unknown_mood_falls_back_rather_than_failing(have_ffmpeg, tmp_path):
    bed = music.ensure_bed(tmp_path, "nonsense")
    assert bed.exists() and bed.name.endswith(f"{music.DEFAULT_MOOD}.wav")
