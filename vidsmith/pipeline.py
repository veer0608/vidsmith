"""The build itself: script in, finished mp4 out.

Every stage writes its output into build/ and is skipped when that output is
already there, so a failed render does not cost you the narration again.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import captions as cap
from . import ffmpeg_util as ff
from . import cards, llm, music, render, thumbs, visuals, voice
from .config import Config, aspect_tag, load_config
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


def invalidate(proj: "Project", log=print) -> None:
    """Drop everything keyed by scene index after the script changes.

    The diagram decisions, the rerank verdicts and the attribution ledger are all
    keyed by position, with nothing tying them to the words. Redraft a script and
    scene five is a different scene, so the old "draw this one" would land on the
    wrong scene and the credits would name a clip that is no longer in the video.

    The downloaded footage in cache/ survives: it is keyed by provider id, so it
    is still valid and still worth not fetching twice.
    """
    removed = 0
    # narration.wav is the one that actually reached the viewer: it is only
    # rebuilt when it is missing, so a redraft left the previous script's voice
    # mixed under the new picture and simply truncated to the shorter runtime.
    for name in ("diagram_scenes.json", "diagrams.json", "narration.wav"):
        path = proj.build / name
        if path.exists():
            path.unlink()
            removed += 1
    for vis in proj.build.glob("visuals*"):
        if not vis.is_dir():
            continue
        for name in ("rerank.json", "credits.json"):
            path = vis / name
            if path.exists():
                path.unlink()
                removed += 1
        stale = (list(vis.glob("scene_*.mp4"))
                 + list(vis.glob("intro*.mp4"))
                 + list(vis.glob("end*.mp4")))
        for clip in stale:
            clip.unlink()
            removed += 1
    for picture in proj.build.glob("picture*.mp4"):
        picture.unlink()
        removed += 1
    if removed:
        log(f"script   changed since the last build; dropped {removed} stale artifacts")


# The keys this project reads, and the variable each comes from. One mapping,
# because `doctor` grew its own hardcoded list of three and then did not learn
# about the ones a new voice provider added - so the command whose entire job is
# "which keys resolve" answered the question incompletely and confidently.
# /healthz was right the whole time because it derives from find_keys() instead.
KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
    "aws_key": "AWS_ACCESS_KEY_ID",
    "aws_secret": "AWS_SECRET_ACCESS_KEY",
    "aws_region": "AWS_REGION",
}

KEY_NOTES = {
    "gemini": "optional - b-roll queries, diagrams and YouTube metadata",
    "pexels": "optional - real stock footage; free at pexels.com/api",
    "pixabay": "optional - alternative stock source",
    "aws_key": "only for voice.provider: polly - see COMMERCIAL.md",
    "aws_secret": "the secret half of the polly access key",
    "aws_region": "required alongside the AWS key, e.g. ap-south-1",
}


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
    return {name: env(var, *dotenvs) for name, var in KEY_ENV.items()}


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
    tag = aspect_tag(cfg.render.aspect)
    theme = resolve_theme(cfg.theme.preset, cfg.theme.accent, cfg.theme.font)
    keys = find_keys(project_root)
    force = set(force)
    scenes_json = proj.build / "scenes.json"

    def done(stage: str) -> bool:
        return bool(stop_after) and STAGES.index(stage) >= STAGES.index(stop_after)

    # ---- parse ---------------------------------------------------------- #
    title, scenes = parse_script(proj.script)
    resolve_title(proj, cfg, title)
    log(f"script   {len(scenes)} scenes, ~{sum(s.est_seconds for s in scenes):.0f}s estimated")

    # reuse cached timings/queries unless the script changed under them
    if scenes_json.exists():
        cached = load_scenes(scenes_json)
        same = len(cached) == len(scenes) and all(
            c.source_key() == s.source_key() for c, s in zip(cached, scenes)
        )
        if not same:
            invalidate(proj, log)
        elif "voice" not in force and "parse" not in force:
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
    log(f"voice    {cfg.voice.name} at {cfg.voice.rate} via {cfg.voice.provider}")
    voice.narrate(scenes, proj.build / "audio", cfg.voice,
                  force="voice" in force, log=log, keys=keys)

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
                      theme_cfg=cfg.theme, lead_in=cfg.voice.lead_in,
                      caption_cfg=cfg.captions)
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
    ass: Optional[Path] = proj.build / f"captions{tag}.ass"
    srt = proj.out / f"captions{tag}.srt"
    vtt = proj.out / f"captions{tag}.vtt"
    wants_overlay = cfg.theme.watermark or cfg.theme.lower_thirds
    if (cfg.captions.enabled and cfg.captions.style != "none") or wants_overlay:
        cap.write_ass(scenes, ass, cfg.captions, cfg.size, cfg.voice.lead_in,
                      theme, cfg.theme, total)
        cap.write_srt(scenes, srt, cfg.captions, cfg.voice.lead_in)
        cap.write_vtt(scenes, vtt, cfg.captions, cfg.voice.lead_in)
        log(f"captions {ass.name} + {srt.name} + {vtt.name}")
    else:
        # Nothing is burned in, so there is no subtitle file to hand the render.
        # This must be None and not Path(""): Path("") is Path("."), which is
        # both truthy and existing, so the "is there a caption file" guard below
        # passed and ffmpeg was asked to read the current directory as an ASS
        # file. Every build made with --captions none died in the master pass.
        ass = None
    if done("captions"):
        return ass or proj.build

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
    thumb_credit = None
    final = proj.out / f"{slug}{tag}.mp4"
    render.master(picture, narration, final, cfg.render, cfg.audio,
                  ass if ass and ass.exists() else None, total,
                  theme, cfg.theme, cfg.size, scrim=scrim, hold_tail=hold_tail)
    # Sampled from the picture track, not the delivery file: the delivery file
    # has captions, watermark and progress bar burned in, none of which belong
    # on a thumbnail. The frame is chosen for relevance, then titled.
    try:
        hook = scenes[0].text if scenes else ""
        target = (1280, 720) if cfg.size[0] >= cfg.size[1] else None
        # the scenes' own visual directives, which are what the video shows -
        # not the hook, which is where every script keeps its frustration
        subjects = ", ".join(dict.fromkeys(
            visuals.scene_query(s) for s in scenes))
        stock = thumbs.from_stock(cfg.title, subjects, cfg.size, keys,
                                  proj.build / ".thumbstock", log=log)
        if stock:
            source = stock["path"]
            thumb_credit = stock
        else:
            drawn = _drawn_ranges(proj, scenes, intro)
            frame = thumbs.choose(picture, proj.build / ".thumbframes", cfg.title,
                                  hook, keys["gemini"], log=log, include=drawn)
            source = frame.path
            thumb_credit = None
        thumbs.titled(source, proj.out / f"{slug}{tag}.jpg", cfg.title,
                      theme, target)
    except Exception as exc:
        log(f"         thumbnail fell back to a plain frame ({exc})")
        render.thumbnail(final, proj.out / f"{slug}{tag}.jpg",
                         at=intro + min(2.0, speech / 3))

    # Each aspect fetches its own clips (portrait searches return different
    # footage), so attribution is per cut - one shared file would silently drop
    # the creators of whichever aspect was built first.
    credits = credits_block(scenes, cfg.visuals.provider)
    named = len(credits.splitlines()) - 1 if credits else 0
    # The thumbnail's photographer is credited on their own terms, never the
    # footage's. Requiring `credits` to be non-empty first meant a cards or
    # local build - which correctly owes no footage attribution - dropped a
    # real Pexels photographer and wrote no credits file at all, because an
    # empty block short-circuits the whole condition.
    if thumb_credit and thumb_credit.get("author"):
        credits += thumbnail_credit_line(thumb_credit)
        named += 1
    if credits:
        (proj.out / f"credits{tag}.txt").write_text(credits, encoding="utf-8")
        log(f"credits  {named} creators to attribute")
    # `done` is logged once, here at the end, and not before the work below.
    # The web stepper reads it as the run completing, so announcing it early
    # drove the label from "done" back to "writing the description" on every
    # build that got that far. One call site rather than one per return path:
    # a second copy of this line is how the two would drift apart.
    if not done("render"):
        # ---- upload metadata --------------------------------------------- #
        if keys["gemini"]:
            try:
                meta = llm.upload_metadata(cfg.title, scenes, keys["gemini"],
                                           log=log)
                write_metadata(proj.out, meta)
                log(f"meta     {proj.out / 'youtube.txt'} + description.txt")
            except Exception as exc:
                log(f"meta     skipped ({exc})")

        # Read the delivery back before anyone else does. Reported, never
        # raised: the same rule the thumbnail follows, that a finished render
        # is not thrown away over something wrong beside it. Only after a full
        # build, because a --stop-after run is incomplete by design and would
        # report that as fault.
        write_build_info(proj.out, cfg)

        from .check import check

        try:
            problems = check(proj.out)
            # says so when it passes too: a silent check and a check that never
            # ran look identical in a log, and that ambiguity has cost time here
            for problem in problems:
                log(f"check    {problem}")
            if not problems:
                log("check    delivery is consistent")
        except Exception as exc:                  # never lose a render to this
            log(f"check    skipped ({exc})")

    log(f"done     {final}  ({ff.duration(final):.1f}s, "
        f"{final.stat().st_size / 1e6:.1f} MB, {time.time() - started:.0f}s to build)")
    return final


THUMB_CREDIT = "Thumbnail: "


def thumbnail_credit_line(stock: Dict[str, Any]) -> str:
    """How a thumbnail photographer is credited, in one place.

    Two callers compose this now, and the format has to match: the refresh
    replaces the line the build wrote, and it can only find it by prefix.
    """
    line = f"{THUMB_CREDIT}{stock['author']} - {stock.get('page', '')}"
    return line.rstrip(" -") + "\n"


def set_thumbnail_credit(path: Path, stock: Optional[Dict[str, Any]]) -> None:
    """Point the credits file at whichever photo is actually on the thumbnail.

    Attribution is a licence condition, not a nicety, so a stale one is worse
    than none: it names someone whose work is not being used and omits the
    person whose work is. `thumbs --refresh` replaced the image and left the
    credit untouched, because the line was only ever composed during a build,
    so every refreshed project credited the photographer it had dropped.
    """
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    kept = [ln for ln in old.splitlines(True) if not ln.startswith(THUMB_CREDIT)]
    text = "".join(kept)
    if stock and stock.get("author"):
        text += thumbnail_credit_line(stock)
    if text:
        path.write_text(text, encoding="utf-8")


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


def write_build_info(out_dir: Path, cfg: Config) -> Path:
    """Record how this build was configured, beside what it delivered.

    `check` reads `out/` and nothing else, which is the property that makes it
    worth trusting. The cost is that it had to *infer* things it could have been
    told, and inferring the footage provider from the credits ledger produced two
    false positives in a row: every scene of a cards build reported as a frozen
    shot, then a mixed scene under-counted when that was fixed by reading the
    ledger differently. It also meant the frozen-shot threshold was a constant
    rather than the project's own `max_shot_seconds`.

    So the build states it. Small and derived, never authoritative: a delivery
    without this file is still checked, on the inference that is still there.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    from . import build_info

    body = {
        "provider": cfg.visuals.provider,
        "min_shot_seconds": cfg.visuals.min_shot_seconds,
        "max_shot_seconds": cfg.visuals.max_shot_seconds,
        "commit": build_info.commit(),
    }
    path = out_dir / "build.json"
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def write_metadata(out_dir: Path, meta: Dict[str, Any]) -> str:
    """Write youtube.json, youtube.txt and description.txt for one build.

    The single writer, deliberately. `vidsmith meta` used to keep its own copy
    that wrote youtube.txt without the credits block and left the other two
    files stale beside it, so regenerating a description silently stripped the
    attribution out of the exact file you paste into YouTube - a licence
    condition, lost by a command whose whole job is to rewrite that file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "youtube.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    block = all_credits(out_dir)
    text = _readable_meta(meta)
    if block:
        text += "\nCREDITS\n" + block
    (out_dir / "youtube.txt").write_text(text, encoding="utf-8")

    # One description per cut, each carrying only that cut's credits.
    #
    # There used to be a single description.txt holding every aspect's block
    # stacked under [16:9] and [9:16] labels, and it is the file whose whole
    # purpose is to be pasted into YouTube. Pasting it named photographers whose
    # clips are not in the video you are publishing and, when the blocks were
    # trimmed by hand instead, dropped ones that are. Both happened on real
    # uploads. youtube.txt keeps the labelled everything, because that one is
    # for reading rather than pasting.
    #
    # The 16:9 file stays `description.txt`, unsuffixed, because that is what
    # aspect_tag() calls it and a second naming convention here is how the
    # `*{tag}.mp4` family of faults keeps happening.
    written = False
    for credits_file in sorted(out_dir.glob("credits*.txt")):
        tag = credits_file.stem[len("credits"):]
        (out_dir / f"description{tag}.txt").write_text(
            description_box(meta, credits_file.read_text(encoding="utf-8")),
            encoding="utf-8")
        written = True
    if not written:
        # a cards or local build owes no footage credit and writes no ledger
        (out_dir / "description.txt").write_text(
            description_box(meta, ""), encoding="utf-8")
    return text


def all_credits(out_dir: Path) -> str:
    """Every cut's attribution, labelled, so one build cannot erase another's."""
    chunks = []
    for path in sorted(out_dir.glob("credits*.txt")):
        label = path.stem.replace("credits", "").lstrip("-") or "16x9"
        chunks.append(f"[{label.replace('x', ':')}]\n"
                      + path.read_text(encoding="utf-8").strip())
    return "\n\n".join(chunks) + "\n" if chunks else ""


def _drawn_ranges(proj: "Project", scenes: Sequence[Scene],
                  intro: float) -> List[tuple]:
    """When the drawn scenes play, so a thumbnail can always consider one."""
    path = proj.build / "diagram_scenes.json"
    if not path.exists():
        return []
    try:
        decided = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    # the closing card sits immediately after the last scene, so trim the tail
    # of each range rather than let a designed text frame qualify as footage
    # A scene with an explicit [diagram:] skips the search entirely, so _decide
    # never runs for it and the decision file does not mention it. It is still a
    # drawn scene, and the thumbnail must be able to consider it.
    return [(s.start, s.start + s.duration - 0.4)
            for s in scenes if decided.get(str(s.index)) or s.diagram]


def description_box(meta: Dict, credits: str = "") -> str:
    """Exactly what goes in YouTube's description field, ready to paste.

    youtube.txt is annotated with headings for a human to read; pasting it
    wholesale would put the word DESCRIPTION into the description. This is the
    same content with the scaffolding removed, in the order YouTube wants it:
    prose, then chapters starting at 0:00, then attribution.
    """
    parts = [str(meta.get("description", "")).strip()]
    chapters = meta.get("chapters") or []
    if chapters:
        parts.append("\n".join(
            f"{c.get('time', '')} {c.get('label', '')}".strip()
            for c in chapters))
    if credits.strip():
        parts.append(credits.strip())
    return "\n\n".join(p for p in parts if p) + "\n"


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


def resolve_title(proj, cfg, title: Optional[str] = None) -> str:
    """The name this project's outputs carry, from the config or the script.

    Every entry point has to agree on this. `build()` resolved an empty config
    title from the script heading and wrote it back; `thumbs --refresh` read
    `cfg.title` raw, so on a project whose config had never been written back it
    slugged to "untitled" and wrote a pair of orphan jpgs beside the real
    thumbnails, which it left stale. The delivery files were named from the
    script all along, so nothing looked wrong until the mtimes were compared.

    The same divergence had already happened once between the CLI and the web
    job, which is why it lives in one function now rather than in each caller.
    """
    if title is None:
        title, _ = parse_script(proj.script)
    if cfg.title in ("", "Untitled"):
        cfg.title = title
        # Write it back, or everything that reads the config afterwards still
        # sees "Untitled" while the video itself is named from the script: the
        # web job reported exactly that, an untitled render of a titled video.
        _persist_title(proj.config_path, cfg.title)
    return cfg.title


def _persist_title(path: Path, title: str) -> None:
    """Record the title the script gave, leaving every other key alone.

    A failure here must not stop a build: the title is already resolved in
    memory, and the video it names will come out correct either way.
    """
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if raw.get("title") == title:
            return
        raw["title"] = title
        path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except (OSError, ValueError, yaml.YAMLError):
        pass


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
