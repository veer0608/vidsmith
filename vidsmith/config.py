"""Project configuration: defaults + config.yaml overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict

import yaml

# 16:9 landscape, 9:16 shorts, 1:1 square
ASPECTS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


@dataclass
class VoiceConfig:
    name: str = "en-US-AndrewNeural"   # warm male narrator; see `vidsmith voices`
    rate: str = "+8%"                  # slightly brisk reads better on YouTube
    pitch: str = "+0Hz"
    volume: str = "+0%"
    gap: float = 0.35                  # seconds of silence appended after each scene
    lead_in: float = 0.25              # silence before the first word of a scene


@dataclass
class VisualConfig:
    provider: str = "cards"            # cards | pexels | pixabay | local
    local_dir: str = "assets/clips"
    orientation: str = "landscape"     # passed to stock providers
    ken_burns: bool = True             # pan/zoom for stills and cards
    zoom: float = 1.12                 # end zoom factor for ken burns
    min_clip_seconds: float = 2.0
    rerank: bool = True               # let Gemini vision pick the matching clip
    rerank_pool: int = 8              # candidates shown to the model per scene
    cut_on_sentences: bool = True     # split a scene into shots at its full stops
    min_shot_seconds: float = 2.4     # never cut faster than this
    max_shot_seconds: float = 5.5     # a longer sentence is broken at a comma
    card_text: str = "auto"           # auto | heading | query | none
    per_scene_queries: int = 1


@dataclass
class ThemeConfig:
    preset: str = "midnight"           # midnight | ink | sunset | forest | paper | mono
    accent: str = ""                   # "#RRGGBB" to override the preset accent
    font: str = ""                     # override the headline/caption family
    watermark: str = ""                # channel handle, drawn small and muted
    title_card: bool = True            # generated opening frame with the title
    title_seconds: float = 2.4
    subtitle: str = ""                 # small line under the title
    end_card: bool = True
    end_seconds: float = 2.2
    end_line: str = ""                 # defaults to the last line of narration
    lower_thirds: bool = False         # scene heading chip; off, headings are labels
    progress_bar: bool = True
    scrim: bool = True                 # bottom gradient so captions read over footage
    scene_counter: bool = True


@dataclass
class CaptionConfig:
    enabled: bool = True
    style: str = "karaoke"             # karaoke | block | none
    font: str = ""                     # blank = the theme caption family
    size: int = 62                     # points against a 1920-wide frame
    primary: str = ""                  # blank = theme text colour
    highlight: str = ""                # blank = theme accent
    outline: str = ""                  # blank = theme stroke
    outline_width: int = 4
    shadow: int = 1
    box: bool = False                  # solid plate behind the words
    fade_ms: int = 110
    pop: bool = True                   # active word scales up slightly
    margin_v: int = 150                # from bottom, in pixels at 1080p
    max_chars: int = 34                # wrap caption groups at this width
    max_words: int = 6
    uppercase: bool = False


@dataclass
class AudioConfig:
    music: str = ""                    # path to a background music file, or ""
    music_gain_db: float = -22.0
    duck: bool = True                  # sidechain-duck music under narration
    normalize: bool = True             # loudnorm the final mix to -14 LUFS
    lufs: float = -14.0


@dataclass
class RenderConfig:
    aspect: str = "16:9"
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    transition: str = "cut"            # cut | fade
    transition_seconds: float = 0.4
    intro_seconds: float = 0.0
    outro_seconds: float = 1.0


@dataclass
class Config:
    title: str = "Untitled"
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    visuals: VisualConfig = field(default_factory=VisualConfig)
    captions: CaptionConfig = field(default_factory=CaptionConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    render: RenderConfig = field(default_factory=RenderConfig)

    @property
    def size(self) -> tuple[int, int]:
        return ASPECTS.get(self.render.aspect, ASPECTS["16:9"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _merge(dc, data: Dict[str, Any]):
    for k, v in (data or {}).items():
        if hasattr(dc, k):
            setattr(dc, k, v)
    return dc


def load_config(path: Path) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.title = raw.get("title", cfg.title)
    _merge(cfg.theme, raw.get("theme"))
    _merge(cfg.voice, raw.get("voice"))
    _merge(cfg.visuals, raw.get("visuals"))
    _merge(cfg.captions, raw.get("captions"))
    _merge(cfg.audio, raw.get("audio"))
    _merge(cfg.render, raw.get("render"))
    return cfg


def write_default_config(path: Path, title: str) -> None:
    cfg = Config(title=title)
    data = cfg.to_dict()
    path.write_text(
        "# vidsmith project config - every key is optional\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def env(name: str, *dotenvs: Path) -> str:
    """Read a key from the environment, falling back to .env files."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    for p in dotenvs:
        try:
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""
