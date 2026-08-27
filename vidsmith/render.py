"""Assemble scenes into the finished video.

Three ffmpeg passes, deliberately, so a failure tells you which stage broke:
  1. narration mix   - every scene mp3 delayed to its start time
  2. picture cut     - scene clips joined (stream-copied, or crossfaded)
  3. final master    - scrim, progress bar, captions, ducked music, loudnorm
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .config import AudioConfig, RenderConfig, ThemeConfig
from .script_parser import Scene
from .theme import Theme, hex_rgb
from . import cards
from . import ffmpeg_util as ff


def _hexc(value: str) -> str:
    r, g, b = hex_rgb(value)
    return f"0x{r:02X}{g:02X}{b:02X}"


def build_narration(scenes: Sequence[Scene], out: Path, lead_in: float,
                    total: float) -> Path:
    """Lay each scene's speech onto one continuous track at its start offset."""
    inputs: List[str] = []
    parts: List[str] = []
    labels: List[str] = []
    for i, scene in enumerate(scenes):
        inputs += ["-i", str(Path(scene.audio))]
        delay_ms = int(round((scene.start + lead_in) * 1000))
        parts.append(f"[{i}:a]aresample=48000,adelay={delay_ms}:all=1[a{i}]")
        labels.append(f"[a{i}]")

    n = len(scenes)
    if n == 1:
        graph = parts[0] + f";[a0]apad,atrim=0:{total:.3f},asetpts=N/SR/TB[out]"
    else:
        graph = ";".join(parts)
        graph += (
            ";" + "".join(labels)
            + f"amix=inputs={n}:normalize=0:dropout_transition=0[mixed]"
            + f";[mixed]apad,atrim=0:{total:.3f},asetpts=N/SR/TB[out]"
        )

    ff.run(inputs + [
        "-filter_complex", graph, "-map", "[out]",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out),
    ])
    return out


def _concat_copy(clips: Sequence[Path], out: Path, workdir: Path) -> Path:
    listfile = workdir / "concat.txt"
    listfile.write_text(
        "\n".join("file '" + ff.escape_concat_path(p) + "'" for p in clips),
        encoding="utf-8",
    )
    ff.run([
        "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-c", "copy", str(out),
    ])
    return out


def _concat_xfade(clips: Sequence[Path], out: Path, size: Tuple[int, int], fps: int,
                  dur: float) -> Path:
    """Crossfade successive clips. Each transition eats `dur` seconds of runtime."""
    inputs: List[str] = []
    for p in clips:
        inputs += ["-i", str(p)]
    lengths = [ff.duration(p) for p in clips]

    graph: List[str] = []
    prev = "[0:v]"
    offset = lengths[0] - dur
    for i in range(1, len(clips)):
        label = f"[x{i}]" if i < len(clips) - 1 else "[vout]"
        graph.append(
            f"{prev}[{i}:v]xfade=transition=fade:duration={dur:.3f}"
            f":offset={max(0.0, offset):.3f}{label}"
        )
        prev = label
        offset += lengths[i] - dur

    w, h = size
    ff.run(inputs + [
        "-filter_complex", ";".join(graph), "-map", "[vout]",
        "-r", str(fps), "-s", f"{w}x{h}", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out),
    ])
    return out


def build_picture(clips: Sequence[Path], out: Path, workdir: Path,
                  cfg: RenderConfig, size: Tuple[int, int]) -> Path:
    clips = [Path(c) for c in clips]
    missing = [c for c in clips if not c.exists()]
    if missing:
        raise RuntimeError(f"missing scene clips: {[str(m) for m in missing]}")
    if cfg.transition == "fade" and len(clips) > 1:
        return _concat_xfade(clips, out, size, cfg.fps, cfg.transition_seconds)
    return _concat_copy(clips, out, workdir)


def master(picture: Path, narration: Path, out: Path, cfg: RenderConfig,
           audio_cfg: AudioConfig, captions: Optional[Path], total: float,
           theme: Theme, theme_cfg: ThemeConfig, size: Tuple[int, int],
           scrim: Optional[Path] = None, hold_tail: float = 0.0) -> Path:
    """Burn the frame furniture, mix music under the voice, encode delivery."""
    w, h = size
    inputs = ["-i", str(picture), "-i", str(narration)]
    idx = 2

    scrim_idx = -1
    if scrim and scrim.exists():
        inputs += ["-loop", "1", "-i", str(scrim)]
        scrim_idx = idx
        idx += 1

    music = Path(audio_cfg.music) if audio_cfg.music else None
    music_idx = -1
    if music and music.exists():
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        music_idx = idx
        idx += 1

    graph: List[str] = []

    # ---- audio ----------------------------------------------------------- #
    if music_idx >= 0:
        if audio_cfg.duck:
            graph.append("[1:a]aresample=48000,asplit=2[narr][key]")
            graph.append(f"[{music_idx}:a]aresample=48000,"
                         f"volume={audio_cfg.music_gain_db}dB[bg]")
            graph.append("[bg][key]sidechaincompress=threshold=0.03:ratio=12"
                         ":attack=15:release=350:makeup=1[duck]")
            graph.append("[narr][duck]amix=inputs=2:normalize=0"
                         ":dropout_transition=0[premix]")
        else:
            graph.append("[1:a]aresample=48000[narr]")
            graph.append(f"[{music_idx}:a]aresample=48000,"
                         f"volume={audio_cfg.music_gain_db}dB[bg]")
            graph.append("[narr][bg]amix=inputs=2:normalize=0"
                         ":dropout_transition=0[premix]")
    else:
        graph.append("[1:a]aresample=48000[premix]")

    tail = f"atrim=0:{total:.3f},asetpts=N/SR/TB"
    if audio_cfg.normalize:
        tail = f"loudnorm=I={audio_cfg.lufs}:TP=-1.5:LRA=11,{tail}"
    graph.append(f"[premix]{tail}[aout]")

    # ---- picture --------------------------------------------------------- #
    chain: List[str] = []
    if hold_tail > 0:
        chain.append(f"tpad=stop_mode=clone:stop_duration={hold_tail:.3f}")
    chain.append("format=yuv420p")
    graph.append("[0:v]" + ",".join(chain) + "[base]")
    cur = "[base]"

    if scrim_idx >= 0:
        graph.append(f"{cur}[{scrim_idx}:v]overlay=0:0:eof_action=repeat[scrimmed]")
        cur = "[scrimmed]"

    post: List[str] = []
    if theme_cfg.progress_bar:
        # drawbox evaluates w per frame, so `t` sweeps the bar across the frame
        bar = max(4, int(h * 0.007))
        post.append(f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}"
                    f":color={_hexc(theme.muted)}@0.28:t=fill")
        post.append(f"drawbox=x=0:y=ih-{bar}:w='iw*min(t/{total:.3f}\\,1)':h={bar}"
                    f":color={_hexc(theme.accent)}@0.95:t=fill")
    if captions and Path(captions).exists():
        ff.require_filter("subtitles")
        subs = f"subtitles='{ff.escape_filter_path(Path(captions))}'"
        # without fontsdir libass silently substitutes whatever it can find, and
        # a host with no Segoe UI renders the captions in something else
        if cards.FONT_DIR.exists():
            subs += f":fontsdir='{ff.escape_filter_path(cards.FONT_DIR)}'"
        post.append(subs)
    post.append("format=yuv420p")
    graph.append(f"{cur}" + ",".join(post) + "[vout]")

    ff.run(inputs + [
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
        "-profile:v", "high", "-level", "4.1", "-r", str(cfg.fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ])
    return out


def thumbnail(video: Path, out: Path, at: float = 1.0) -> Path:
    ff.run(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2",
            str(out)])
    return out
