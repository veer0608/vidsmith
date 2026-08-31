"""Paths handed to a filtergraph.

The ASS file and the bundled fonts reach ffmpeg as `subtitles='<path>'`, and a
path is not a string ffmpeg reads literally: two layers of its own parsing sit
between the quotes and the filename. Anything those layers eat is a file it
cannot open, and the failure is a dead render rather than a missing caption.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vidsmith import ffmpeg_util as ff

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narration,Arial,24,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Narration,,0,0,0,,hello
"""


def test_a_drive_colon_is_escaped(tmp_path):
    """An unescaped colon splits the filter's own options."""
    escaped = ff.escape_filter_path(tmp_path / "captions.ass")
    assert ":" not in escaped.replace("\\:", "")


def test_backslashes_become_forward_slashes(tmp_path):
    assert "\\" not in ff.escape_filter_path(tmp_path / "a" / "b.ass").replace("\\:", "")


def test_an_apostrophe_is_actually_escaped(tmp_path):
    """`.replace("'", "\\'")` was a no-op: that string is just an apostrophe.

    The character has to satisfy the filtergraph's quoting and the filter's own
    option parsing at once, so a bare apostrophe is not enough.
    """
    escaped = ff.escape_filter_path(tmp_path / "O'Brien" / "captions.ass")
    assert "'" in escaped
    assert escaped.count("'") > 1, "a lone apostrophe would end the quoted section"
    assert "O\\'\\''Brien" in escaped


@pytest.mark.slow
def test_ffmpeg_opens_a_subtitle_file_under_an_apostrophe(tmp_path):
    """The only check that matters: real ffmpeg, real path, real filtergraph.

    Every plausible spelling of this escape was tried and all but one silently
    dropped the character, so ffmpeg looked for `OBrien` and the render died.
    """
    try:
        binary = ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")
    if "subtitles" not in ff.filters():
        pytest.skip("this ffmpeg was built without libass")

    root = tmp_path / "O'Brien"
    root.mkdir()
    subs = root / "captions.ass"
    subs.write_text(ASS, encoding="utf-8")
    out = root / "frame.png"

    proc = subprocess.run(
        [binary, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
         "-vf", f"subtitles='{ff.escape_filter_path(subs)}'",
         "-frames:v", "1", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr.strip()[:400]
    assert out.exists()


# --------------------------------------------------------------------------- #
# the concat demuxer list
# --------------------------------------------------------------------------- #
def test_a_concat_path_escapes_the_quote_but_not_the_colon():
    """One layer here, unlike a filtergraph path.

    The demuxer reads the list file itself and no filter-option parser sits
    under it, so the drive colon is safe and only the apostrophe needs closing,
    escaping and reopening.
    """
    escaped = ff.escape_concat_path(Path("C:/tmp/O'Brien/clip.mp4"))
    assert "C:/tmp/" in escaped, "the colon must be left alone"
    assert "O'\\''Brien" in escaped


def test_the_two_escapes_do_not_agree(tmp_path):
    """They are different rules for different parsers; keep them apart."""
    subject = tmp_path / "O'Brien" / "clip.mp4"
    assert ff.escape_concat_path(subject) != ff.escape_filter_path(subject)


@pytest.mark.slow
def test_ffmpeg_concatenates_clips_under_an_apostrophe(tmp_path):
    """The default transition stream-copies through the concat demuxer.

    An unescaped apostrophe ended the quoted section early and ffmpeg reported
    "Impossible to open" against a path with the character missing, so the
    common render path died outright on a machine whose user folder has one.
    """
    from vidsmith.render import _concat_copy

    try:
        ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")

    root = tmp_path / "O'Brien"
    root.mkdir()
    clips = []
    for i, colour in enumerate(("red", "green")):
        clip = root / f"clip{i}.mp4"
        ff.run(["-f", "lavfi", "-i", f"color=c={colour}:s=320x240:d=1",
                "-r", "24", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(clip)])
        clips.append(clip)

    out = _concat_copy(clips, root / "joined.mp4", root)
    assert out.exists()
    assert ff.duration(out) == pytest.approx(2.0, abs=0.2)


# --------------------------------------------------------------------------- #
# builds without libass
# --------------------------------------------------------------------------- #
def test_a_missing_subtitles_filter_is_named_plainly(monkeypatch):
    """Homebrew's ffmpeg 8.1.2 has no libass, so `subtitles` does not exist.

    ffmpeg's own answer to that is "No option name near <path>", which reads
    like a quoting fault and cost an afternoon in escaping rules that were never
    wrong. The message has to say what is actually missing.
    """
    monkeypatch.setattr(ff, "_FILTERS", {"drawbox", "overlay"})
    with pytest.raises(RuntimeError) as exc:
        ff.require_filter("subtitles")
    assert "no 'subtitles' filter" in str(exc.value)
    assert "captions cannot be burned in" in str(exc.value)


def test_a_present_filter_passes_silently(monkeypatch):
    monkeypatch.setattr(ff, "_FILTERS", {"subtitles"})
    ff.require_filter("subtitles")


def test_the_filter_list_is_read_from_the_binary():
    """Not hardcoded: the whole point is that builds differ."""
    try:
        ff.ffmpeg_bin()
    except RuntimeError:
        pytest.skip("ffmpeg not installed")
    ff._FILTERS = None
    found = ff.filters()
    assert len(found) > 100, "that is not a real filter list"
    assert "overlay" in found and "scale" in found


# --------------------------------------------------------------------------- #
# ffmpeg that never returns
# --------------------------------------------------------------------------- #
def test_a_hung_ffmpeg_is_killed_and_named(monkeypatch):
    """Without a bound the call never returns at all.

    The web service holds one render slot and gives it back on the way out of
    the job; a subprocess that never exits takes no way out, so the instance
    stops accepting work for good. macOS CI showed the same shape, sitting for
    twenty-five minutes and reporting nothing.
    """
    import subprocess as sp

    def hang(*a, **k):
        raise sp.TimeoutExpired(cmd="ffmpeg", timeout=k.get("timeout", 900))

    monkeypatch.setattr(ff, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(ff.subprocess, "run", hang)
    with pytest.raises(RuntimeError) as exc:
        ff.run(["-i", "in.wav", "out.wav"])
    said = str(exc.value)
    assert "did not finish" in said
    assert "hang rather than a slow encode" in said, "a slow encode reads as a bug here"
    assert "VIDSMITH_FFMPEG_TIMEOUT" in said, "say how to raise it"


def test_the_timeout_is_a_bound_on_forever_not_a_budget(monkeypatch):
    """Killing an honest long encode would be worse than the hang it guards.

    The shipped default is what this is about, not whatever the current process
    was told. CI deliberately sets a much shorter one so its own guard fires
    before pytest's, and reading the live value made these two contradict each
    other: the suite went red on all three runners over 45 versus 600.
    """
    monkeypatch.delenv("VIDSMITH_FFMPEG_TIMEOUT", raising=False)
    assert ff.timeout_limit() >= 600


def test_the_timeout_is_overridable(monkeypatch):
    """A slow free instance encoding 1080p is minutes of real work."""
    monkeypatch.setenv("VIDSMITH_FFMPEG_TIMEOUT", "1234")
    assert ff.timeout_limit() == 1234.0


def test_the_limit_leaves_nothing_behind_for_the_next_test(monkeypatch):
    """The reason this is a function and not a constant read at import.

    Those two tests above used to reload the module to see the value, and
    reload again to put it back - but the second reload ran while monkeypatch
    was still in force, so it restored against the patched environment and left
    the module holding the default. Every test file sorting after this one then
    ran with 900s no matter what the environment said, which is why CI's 45s
    guard never fired and the macOS narration hang was reported twice as a
    stack trace through subprocess with nothing from ffmpeg in it.

    Reading the environment per call means there is no module state to leave
    behind, and this fails the moment anyone caches it again.
    """
    monkeypatch.setenv("VIDSMITH_FFMPEG_TIMEOUT", "7")
    assert ff.timeout_limit() == 7.0
    monkeypatch.setenv("VIDSMITH_FFMPEG_TIMEOUT", "8")
    assert ff.timeout_limit() == 8.0, "the limit was cached instead of read"


def test_the_narration_output_is_bounded_by_more_than_the_graph():
    """apad is infinite, so atrim is the only thing ending the output. One
    filter declining to pass EOF then hangs the encode, which is what macOS
    CI hung inside."""
    import inspect

    from vidsmith import render

    source = inspect.getsource(render.build_narration)
    assert '"-t", f"{total:.3f}"' in source, "the narration encode has no hard end"


def test_a_timeout_reports_what_ffmpeg_managed_to_say(monkeypatch):
    """The only evidence about where it stopped is whatever it printed first.

    Two macOS hangs were reported as a stack trace through subprocess with
    nothing from ffmpeg in it, because the partial output on TimeoutExpired was
    thrown away.
    """
    import subprocess as sp

    def hang(*a, **k):
        raise sp.TimeoutExpired(cmd="ffmpeg", timeout=45,
                                output=b"", stderr=b"Output #0, wav\n  Stream #0:0: Audio")

    monkeypatch.setattr(ff, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(ff.subprocess, "run", hang)
    with pytest.raises(RuntimeError) as exc:
        ff.run(["-i", "in.wav", "out.wav"])
    assert "Stream #0:0: Audio" in str(exc.value), "the partial output was dropped"


def test_a_silent_hang_says_that_too(monkeypatch):
    """"It said nothing" is itself a finding: it never reached the muxer."""
    import subprocess as sp

    monkeypatch.setattr(ff, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(ff.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        sp.TimeoutExpired(cmd="ffmpeg", timeout=45)))
    with pytest.raises(RuntimeError) as exc:
        ff.run(["-i", "in.wav", "out.wav"])
    assert "said nothing at all" in str(exc.value)


def test_every_ci_job_bounds_a_hang():
    """A hang on ubuntu or windows was as opaque as the macOS one: only macos
    carried a per-test timeout, and none set the ffmpeg one below it, so pytest
    always killed the test before our own guard could report anything."""
    from pathlib import Path

    workflow = (Path(__file__).resolve().parent.parent
                / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert workflow.count("--timeout=120") == 3, "a job can still hang unbounded"
    # the setting, not the prose: the comment above it names the variable too
    assert workflow.count('VIDSMITH_FFMPEG_TIMEOUT: "') == 3, \
        "without this under the pytest limit, ffmpeg never gets to explain itself"
