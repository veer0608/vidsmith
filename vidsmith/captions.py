"""Word-timed captions and on-frame overlays, written as one ASS file.

Karaoke mode re-emits the whole caption line once per word, recolouring the
active word. That is more events than \\k tags would need, but it renders
identically in every libass build and survives re-timing.

Word events keep the line's glyph widths identical, so nothing reflows as the
highlight moves. Motion is applied per caption group instead: a short scale-up
on entry, a fade on the way in and out.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import CaptionConfig, ThemeConfig
from .script_parser import Scene
from .theme import Theme, ass_color

SCRIPT_INFO = """[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""

EVENTS = """
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

TRAILING = ",.!?;:…—\")”’"


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def attach_punctuation(words: Sequence[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """edge-tts strips punctuation from its word events; put it back from the script.

    Without this the captions read as one unbroken run and never break on a
    sentence, because there is no full stop left to break on.
    """
    out: List[Dict[str, Any]] = []
    pos = 0
    for w in words:
        item = dict(w)
        tok = item["text"]
        i = text.find(tok, pos)
        if i < 0:
            out.append(item)
            continue
        j = i + len(tok)
        while j < len(text) and text[j] in TRAILING:
            j += 1
        item["text"] = text[i:j]
        pos = j
        out.append(item)
    return out


def group_words(words: Sequence[Dict[str, Any]], cfg: CaptionConfig) -> List[List[Dict[str, Any]]]:
    """Chunk words into caption-sized lines, breaking on sentence punctuation."""
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    width = 0
    for w in words:
        token = w["text"]
        if cur and (len(cur) >= cfg.max_words or width + len(token) + 1 > cfg.max_chars):
            groups.append(cur)
            cur, width = [], 0
        cur.append(w)
        width += len(token) + 1
        if token.rstrip().endswith((".", "!", "?", ":", "—")) and len(cur) >= 2:
            groups.append(cur)
            cur, width = [], 0
    if cur:
        groups.append(cur)
    # A one-word tail reads as a glitch on screen; fold it back into the line
    # before it unless that line is already at its limit.
    if len(groups) > 1 and len(groups[-1]) == 1:
        prev = groups[-2]
        tail = groups[-1][0]
        if len(prev) < cfg.max_words + 1:
            prev.append(tail)
            groups.pop()
    return groups


def scene_groups(scene: Scene, cfg: CaptionConfig) -> List[List[Dict[str, Any]]]:
    return group_words(attach_punctuation(scene.words, scene.text), cfg)


# --------------------------------------------------------------------------- #
# styles
# --------------------------------------------------------------------------- #
def _metrics(size: Tuple[int, int], cfg: CaptionConfig) -> Tuple[float, bool]:
    """Caption size is authored against a 1920-wide frame.

    Readability tracks the fraction of frame WIDTH the text covers, so the scale
    comes off width - then portrait frames get a bump, because vertical video is
    watched with much larger captions than a 16:9 cut.
    """
    w, h = size
    portrait = h > w
    scale = w / 1920.0
    if portrait:
        scale *= 2.2
    return scale, portrait


def caption_top(size: Tuple[int, int], cfg: CaptionConfig,
                lines: int = 2) -> float:
    """The highest y the captions can occupy.

    Anything else drawn in the frame has to stay above this. It is computed from
    the same numbers that build the ASS styles, so changing `size` or `margin_v`
    in a project moves the diagrams out of the way instead of letting a caption
    land across them.
    """
    w, h = size
    scale, portrait = _metrics(size, cfg)
    if not cfg.enabled or cfg.style == "none":
        return h * 0.96
    font = max(18, int(cfg.size * scale))
    margin = int(h * 0.22) if portrait else max(20, int(cfg.margin_v * (h / 1080.0)))
    # a plate is taller than an outline, and a long line can wrap to two
    padding = font * (0.60 if cfg.box else 0.30)
    return h - margin - font * 1.25 * max(1, lines) - padding


def _styles(size: Tuple[int, int], cfg: CaptionConfig, theme: Theme) -> List[str]:
    w, h = size
    scale, portrait = _metrics(size, cfg)

    primary = cfg.primary or ass_color(theme.text)
    highlight = cfg.highlight or ass_color(theme.accent)
    outline = cfg.outline or ass_color(theme.stroke)
    family = cfg.font or theme.caption_font

    if cfg.box:
        border_style, ow, shadow = 3, max(6, int(18 * scale)), 0
        back = ass_color(theme.bg, alpha=60)
    else:
        border_style, ow, shadow = 1, max(2, int(cfg.outline_width * scale)), cfg.shadow
        back = ass_color("#000000", alpha=120)

    mv = int(h * 0.22) if portrait else max(20, int(cfg.margin_v * (h / 1080.0)))
    side = int(w * 0.06)

    styles = [
        f"Style: Narration,{family},{max(18, int(cfg.size * scale))},{primary},"
        f"{highlight},{outline},{back},-1,0,0,0,100,100,0,0,{border_style},{ow},"
        f"{shadow},2,{side},{side},{mv},1"
    ]
    # lower third: same family, small, top-left, no box
    kick = max(14, int(cfg.size * scale * 0.42))
    styles.append(
        f"Style: Kicker,{theme.kicker_font},{kick},{ass_color(theme.text)},"
        f"{ass_color(theme.accent)},{ass_color(theme.stroke)},{ass_color(theme.bg, 40)},"
        f"0,0,0,0,100,100,{max(1, int(3 * scale))},0,1,{max(2, int(3 * scale))},0,7,"
        f"{side},{side},{int(h * 0.07)},1"
    )
    # persistent channel mark, bottom-right and deliberately quiet
    mark = max(12, int(cfg.size * scale * 0.34))
    styles.append(
        f"Style: Mark,{theme.kicker_font},{mark},{ass_color(theme.muted, 70)},"
        f"{ass_color(theme.muted, 70)},{ass_color(theme.stroke, 120)},"
        f"{ass_color(theme.bg, 255)},0,0,0,0,100,100,{max(1, int(2 * scale))},0,1,"
        f"{max(1, int(2 * scale))},0,3,{side},{side},{int(h * 0.035)},1"
    )
    return styles


def _line(start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{text}"


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
def caption_events(scenes: Sequence[Scene], cfg: CaptionConfig, theme: Theme,
                   lead_in: float) -> List[str]:
    primary = cfg.primary or ass_color(theme.text)
    highlight = cfg.highlight or ass_color(theme.accent)
    out: List[str] = []

    for scene in scenes:
        if not scene.words:
            continue
        base = scene.start + lead_in
        scene_end = scene.start + scene.duration
        groups = scene_groups(scene, cfg)

        for gi, group in enumerate(groups):
            g_start = base + group[0]["start"]
            # never let one line hang over the start of the next, or libass
            # stacks them and two captions sit on screen at once
            nxt = (base + groups[gi + 1][0]["start"] - 0.02
                   if gi + 1 < len(groups) else scene_end)
            g_end = min(base + group[-1]["end"] + 0.18, nxt, scene_end)
            if g_end <= g_start:
                continue

            tokens = [(t["text"].upper() if cfg.uppercase else t["text"]) for t in group]
            entrance = ""
            if cfg.pop:
                entrance = "{\\fscx92\\fscy92\\t(0,130,\\fscx100\\fscy100)}"

            if cfg.style == "block":
                fade = f"{{\\fad({cfg.fade_ms},{cfg.fade_ms})}}"
                out.append(_line(g_start, g_end, "Narration",
                                 fade + entrance + _esc(" ".join(tokens))))
                continue

            last = len(group) - 1
            for i, word in enumerate(group):
                w_start = max(base + word["start"], g_start)
                w_end = base + group[i + 1]["start"] if i < last else g_end
                w_end = min(max(w_end, w_start + 0.06), g_end)

                if i == 0 and last == 0:
                    prefix = f"{{\\fad({cfg.fade_ms},{cfg.fade_ms})}}" + entrance
                elif i == 0:
                    prefix = f"{{\\fad({cfg.fade_ms},0)}}" + entrance
                elif i == last:
                    prefix = f"{{\\fad(0,{cfg.fade_ms})}}"
                else:
                    prefix = ""

                parts = []
                for j, token in enumerate(tokens):
                    tok = _esc(token)
                    if j == i:
                        parts.append("{\\c" + highlight + "}" + tok + "{\\c" + primary + "}")
                    else:
                        parts.append(tok)
                out.append(_line(w_start, w_end, "Narration", prefix + " ".join(parts)))
    return out


def overlay_events(scenes: Sequence[Scene], theme_cfg: ThemeConfig, theme: Theme,
                   total: float = 0.0, hold: float = 2.8) -> List[str]:
    """Lower-third chips carrying the scene heading, plus the channel mark."""
    out: List[str] = []
    if theme_cfg.watermark and total > 0:
        out.append(_line(0.0, total, "Mark", _esc(theme_cfg.watermark)))
    if not theme_cfg.lower_thirds:
        return out
    seen = ""
    for scene in scenes:
        heading = (scene.heading or "").strip()
        if not heading or heading == seen:
            continue
        seen = heading
        start = scene.start + 0.20
        end = min(start + hold, scene.start + scene.duration)
        if end <= start:
            continue
        text = ("{\\fad(220,220)}{\\c" + ass_color(theme.accent) + "}▬  {\\c"
                + ass_color(theme.text) + "}" + _esc(heading.upper()))
        out.append(_line(start, end, "Kicker", text))
    return out


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def build_ass(scenes: Sequence[Scene], cfg: CaptionConfig, size: Tuple[int, int],
              lead_in: float, theme: Theme,
              theme_cfg: Optional[ThemeConfig] = None, total: float = 0.0) -> str:
    w, h = size
    parts = [SCRIPT_INFO.format(w=w, h=h)]
    parts += [s + "\n" for s in _styles(size, cfg, theme)]
    parts.append(EVENTS)

    events: List[str] = []
    if cfg.enabled and cfg.style != "none":
        events += caption_events(scenes, cfg, theme, lead_in)
    if theme_cfg is not None:
        events += overlay_events(scenes, theme_cfg, theme, total)
    parts.append("\n".join(events) + "\n")
    return "".join(parts)


def write_ass(scenes: Sequence[Scene], path: Path, cfg: CaptionConfig,
              size: Tuple[int, int], lead_in: float, theme: Theme,
              theme_cfg: Optional[ThemeConfig] = None, total: float = 0.0) -> Path:
    path.write_text(build_ass(scenes, cfg, size, lead_in, theme, theme_cfg, total),
                    encoding="utf-8")
    return path


def write_srt(scenes: Sequence[Scene], path: Path, cfg: CaptionConfig,
              lead_in: float) -> Path:
    """A plain .srt alongside the ASS, for uploading to YouTube as a caption track."""
    def srt_ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks: List[str] = []
    n = 1
    for scene in scenes:
        if not scene.words:
            continue
        base = scene.start + lead_in
        groups = scene_groups(scene, cfg)
        for gi, group in enumerate(groups):
            start = base + group[0]["start"]
            nxt = (base + groups[gi + 1][0]["start"] - 0.02
                   if gi + 1 < len(groups) else scene.start + scene.duration)
            end = min(base + group[-1]["end"] + 0.18, nxt)
            blocks.append(
                f"{n}\n{srt_ts(start)} --> {srt_ts(end)}\n"
                + " ".join(t["text"] for t in group)
            )
            n += 1
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path
