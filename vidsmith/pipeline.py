"""The build itself: script in, finished mp4 out.

Every stage writes its output into build/ and is skipped when that output is
already there, so a failed render does not cost you the narration again.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import captions as cap
from . import ffmpeg_util as ff
from . import cards, llm, music, render, visuals, voice
from .config import Config, load_config
from .theme import resolve as resolve_theme
from .script_parser import Scene, load_scenes, parse_script, save_scenes

STAGES = ["parse", "queries", "voice", "visuals", "captions", "render", "meta"]


@dataclass
class Project:
    root: Path

    @property
    def script(self) -> Path:
        return self.root / "script.md"

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def dirs(self) -> None:
        for d in (self.build, self.build / "audio", self.build / "visuals", self.out):
            d.mkdir(parents=True, exist_ok=True)


def find_keys(project_root: Path) -> Dict[str, str]:
    from .config import env

    home = Path.home()
    # projects live at <repo>/projects/<name>, so the repo root is two levels up
    # from the project - and that is where the shared .env actually sits.
    repo_root = Path(__file__).resolve().parent.parent
    dotenvs = [
        project_root / ".env",
        project_root.parent / ".env",
        repo_root / ".env",
        home / "claude" / "schemablind" / ".env",
        home / "claude" / "moneytrail" / ".env",
    ]
    return {
        "gemini": env("GEMINI_API_KEY", *dotenvs),
        "pexels": env("PEXELS_API_KEY", *dotenvs),
        "pixabay": env("PIXABAY_API_KEY", *dotenvs),
    }


def build(project_root: Path, force: Sequence[str] = (), stop_after: str = "",
          overrides: Optional[Dict[str, str]] = None, log=print) -> Path:
    started = time.time()
    proj = Project(project_root)
    if not proj.script.exists():
        raise FileNotFoundError(
            f"no script at {proj.script} - run:  vidsmith new {project_root.name}"
        )
    proj.dirs()

    cfg = load_config(proj.config_path)
    _apply_overrides(cfg, overrides or {})
    # Picture, captions and the delivery file all depend on frame size, so each
    # aspect gets its own artifacts. Narration is shape-independent and shared.
    tag = "" if cfg.render.aspect == "16:9" else "-" + cfg.render.aspect.replace(":", "x")
    theme = resolve_theme(cfg.theme.preset, cfg.theme.accent, cfg.theme.font)
    keys = find_keys(project_root)
    force = set(force)
    scenes_json = proj.build / "scenes.json"

    def done(stage: str) -> bool:
        return bool(stop_after) and STAGES.index(stage) >= STAGES.index(stop_after)

    # ---- parse ---------------------------------------------------------- #
    title, scenes = parse_script(proj.script)
    if cfg.title in ("", "Untitled"):
        cfg.title = title
    log(f"script   {len(scenes)} scenes, ~{sum(s.est_seconds for s in scenes):.0f}s estimated")

    # reuse cached timings/queries unless the script changed under them
    if scenes_json.exists() and "voice" not in force and "parse" not in force:
        cached = load_scenes(scenes_json)
        if len(cached) == len(scenes) and all(
            c.text == s.text for c, s in zip(cached, scenes)
        ):
            scenes = cached
            log("         reusing cached scene timings")
    if done("parse"):
        save_scenes(scenes, scenes_json)
        return scenes_json

    # ---- b-roll queries -------------------------------------------------- #
    if cfg.visuals.provider in ("pexels", "pixabay") and keys["gemini"]:
        filled = llm.suggest_queries(scenes, keys["gemini"], log=log)
        if filled:
            log(f"queries  {filled} b-roll searches written by Gemini")
    if done("queries"):
        save_scenes(scenes, scenes_json)
        return scenes_json

    # ---- narration ------------------------------------------------------- #
    log(f"voice    {cfg.voice.name} at {cfg.voice.rate}")
    voice.narrate(scenes, proj.build / "audio", cfg.voice,
                  force="voice" in force, log=log)

    # A scene's clip is exactly its narration slot, so any floor on clip length
    # has to be applied to the slot itself - otherwise the picture runs longer
    # than the speech and everything after it drifts.
    intro = cfg.theme.title_seconds if cfg.theme.title_card else 0.0
    end_len = cfg.theme.end_seconds if cfg.theme.end_card else 0.0
    hold_tail = 0.0 if cfg.theme.end_card else cfg.render.outro_seconds
    clock = intro
    for scene in scenes:
        scene.duration = max(scene.duration, cfg.visuals.min_clip_seconds)
        scene.start = clock
        clock += scene.duration

    speech = sum(s.duration for s in scenes)
    total = intro + speech + end_len + hold_tail
    save_scenes(scenes, scenes_json)
    log(f"         {speech:.1f}s of narration across {len(scenes)} scenes")
    if done("voice"):
        return scenes_json

    # ---- visuals --------------------------------------------------------- #
    log(f"visuals  provider={cfg.visuals.provider} {cfg.size[0]}x{cfg.size[1]}")
    if cfg.visuals.provider == "pexels" and not keys["pexels"]:
        log("         no PEXELS_API_KEY found - generating cards instead")
        cfg.visuals.provider = "cards"
    if cfg.visuals.provider == "pixabay" and not keys["pixabay"]:
        log("         no PIXABAY_API_KEY found - generating cards instead")
        cfg.visuals.provider = "cards"
    vis_dir = proj.build / f"visuals{tag}"
    visuals.build_all(scenes, cfg.visuals, cfg.size, cfg.render.fps, vis_dir, keys,
                      force="visuals" in force, log=log, theme=theme,
                      theme_cfg=cfg.theme, lead_in=cfg.voice.lead_in)
    clips = [Path(shot["path"]) for s in scenes for shot in s.shots]
    shot_count = len(clips)

    if intro > 0:
        card = cards.title_card(vis_dir / "cache" / f"title{tag}.png", cfg.size, theme,
                                cfg.title, cfg.theme.subtitle)
        clips.insert(0, visuals.normalise_still(
            card, vis_dir / f"intro{tag}.mp4", intro, cfg.size, cfg.render.fps,
            cfg.visuals.ken_burns, 1.06, 0))
        log(f"  title    {intro:.1f}s opening card")
    if end_len > 0:
        line = cfg.theme.end_line or cards.first_clause(scenes[-1].text, 60)
        card = cards.end_card(vis_dir / "cache" / f"end{tag}.png", cfg.size, theme, line)
        clips.append(visuals.normalise_still(
            card, vis_dir / f"end{tag}.mp4", end_len, cfg.size, cfg.render.fps,
            cfg.visuals.ken_burns, 1.06, 0))
        log(f"  end      {end_len:.1f}s closing card")

    save_scenes(scenes, scenes_json)
    if done("visuals"):
        return proj.build / f"visuals{tag}"

    # ---- captions -------------------------------------------------------- #
    ass = proj.build / f"captions{tag}.ass"
    srt = proj.out / f"captions{tag}.srt"
    wants_overlay = cfg.theme.watermark or cfg.theme.lower_thirds
    if (cfg.captions.enabled and cfg.captions.style != "none") or wants_overlay:
        cap.write_ass(scenes, ass, cfg.captions, cfg.size, cfg.voice.lead_in,
                      theme, cfg.theme, total)
        cap.write_srt(scenes, srt, cfg.captions, cfg.voice.lead_in)
        log(f"captions {ass.name} + {srt.name}")
    else:
        ass = Path("")
    if done("captions"):
        return ass

    # ---- render ---------------------------------------------------------- #
    narration = proj.build / "narration.wav"
    if not narration.exists() or "render" in force or "voice" in force:
        render.build_narration(scenes, narration, cfg.voice.lead_in, total)
    picture = proj.build / f"picture{tag}.mp4"
    if picture.exists():
        picture.unlink()
    render.build_picture(clips, picture, proj.build, cfg.render, cfg.size)

    if cfg.audio.music.strip().lower() == "auto":
        bed = music.ensure_bed(proj.build, cfg.audio.mood)
        cfg.audio.music = str(bed)
        log(f"music    generated {cfg.audio.mood} bed, ducked under the voice")
    elif cfg.audio.music and not Path(cfg.audio.music).exists():
        log(f"music    {cfg.audio.music} not found - rendering without a bed")
        cfg.audio.music = ""

    scrim = None
    if cfg.theme.scrim:
        scrim = cards.scrim(proj.build / f"scrim{tag}.png", cfg.size, theme)
    log(f"render   {ff.duration(picture):.1f}s of picture across {shot_count} shots, "
        f"mixing and encoding")

    slug = _slug(cfg.title)
    final = proj.out / f"{slug}{tag}.mp4"
    render.master(picture, narration, final, cfg.render, cfg.audio,
                  ass if ass and Path(ass).exists() else None, total,
                  theme, cfg.theme, cfg.size, scrim=scrim, hold_tail=hold_tail)
    render.thumbnail(final, proj.out / f"{slug}{tag}.jpg",
                     at=intro + min(2.0, speech / 3))

    # Each aspect fetches its own clips (portrait searches return different
    # footage), so attribution is per cut - one shared file would silently drop
    # the creators of whichever aspect was built first.
    credits = credits_block(scenes, cfg.visuals.provider)
    if credits:
        (proj.out / f"credits{tag}.txt").write_text(credits, encoding="utf-8")
        log(f"credits  {len(credits.splitlines()) - 1} creators to attribute")
    log(f"done     {final}  ({ff.duration(final):.1f}s, "
        f"{final.stat().st_size / 1e6:.1f} MB, {time.time() - started:.0f}s to build)")
    if done("render"):
        return final

    # ---- upload metadata ------------------------------------------------- #
    if keys["gemini"]:
        try:
            meta = llm.upload_metadata(cfg.title, scenes, keys["gemini"])
            (proj.out / "youtube.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            text = _readable_meta(meta)
            block = all_credits(proj.out)
            if block:
                text += "\nCREDITS\n" + block
            (proj.out / "youtube.txt").write_text(text, encoding="utf-8")
            log(f"meta     {proj.out / 'youtube.txt'}")
        except Exception as exc:
            log(f"meta     skipped ({exc})")

    return final


def credits_block(scenes: Sequence[Scene], provider: str) -> str:
    """Attribution text for the YouTube description.

    Pexels and Pixabay both ask that API users credit the creator and link back
    to the source, so this is generated from what the search actually returned
    rather than written by hand.
    """
    rows = []
    seen = set()
    for scene in scenes:
        for shot in (scene.shots or [{"credit": scene.credit,
                                      "credit_url": scene.credit_url}]):
            name = shot.get("credit", "")
            if not name or name in seen:
                continue
            seen.add(name)
            rows.append(f"{name} - {shot.get('credit_url', '')}".strip(" -"))
    if not rows:
        return ""
    site = {"pexels": "Pexels (https://www.pexels.com)",
            "pixabay": "Pixabay (https://pixabay.com)"}.get(provider, provider)
    return f"Footage from {site}\n" + "\n".join(rows) + "\n"


def all_credits(out_dir: Path) -> str:
    """Every cut's attribution, labelled, so one build cannot erase another's."""
    chunks = []
    for path in sorted(out_dir.glob("credits*.txt")):
        label = path.stem.replace("credits", "").lstrip("-") or "16x9"
        chunks.append(f"[{label.replace('x', ':')}]\n"
                      + path.read_text(encoding="utf-8").strip())
    return "\n\n".join(chunks) + "\n" if chunks else ""


def _readable_meta(meta: Dict) -> str:
    lines = [f"TITLE\n{meta.get('title', '')}\n", "DESCRIPTION"]
    lines.append(str(meta.get("description", "")).strip())
    chapters = meta.get("chapters") or []
    if chapters:
        lines.append("\nCHAPTERS")
        for c in chapters:
            lines.append(f"{c.get('time', '')} {c.get('label', '')}")
    tags = meta.get("tags") or []
    if tags:
        lines.append("\nTAGS\n" + ", ".join(tags))
    return "\n".join(lines) + "\n"


def _slug(title: str) -> str:
    import re

    s = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return s or "video"


def _apply_overrides(cfg: Config, ov: Dict[str, str]) -> None:
    if ov.get("aspect"):
        cfg.render.aspect = ov["aspect"]
        cfg.visuals.orientation = "portrait" if ov["aspect"] in ("9:16", "4:5") else "landscape"
    if ov.get("provider"):
        cfg.visuals.provider = ov["provider"]
    if ov.get("voice"):
        cfg.voice.name = ov["voice"]
    if ov.get("music"):
        value = ov["music"]
        cfg.audio.music = "" if value.lower() in ("none", "off") else value
    if ov.get("mood"):
        cfg.audio.mood = ov["mood"]
    if ov.get("captions"):
        cfg.captions.style = ov["captions"]
        cfg.captions.enabled = ov["captions"] != "none"
    if ov.get("theme"):
        cfg.theme.preset = ov["theme"]
    if ov.get("accent"):
        cfg.theme.accent = ov["accent"]
    if ov.get("watermark"):
        cfg.theme.watermark = ov["watermark"]
    if ov.get("no_cards"):
        cfg.theme.title_card = False
        cfg.theme.end_card = False
