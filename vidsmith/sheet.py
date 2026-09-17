"""A frame per shot, beside the words spoken over it.

Every footage fault in this project was found this way and never by reading a
log: 27 seconds of forest trails under three different ideas, a black frame
with two headlights, a hot dog under a parcel line, a phone map under "the box
lands on your doorstep". Each time the sheet was rebuilt by hand with ffmpeg,
about fifteen times in one day, so it is a command now.

The sheet is HTML rather than a montage image: the words belong beside the
frame at a readable size, and a two-minute video is forty shots.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import ffmpeg_util as ff
from .config import ASPECTS, Config, aspect_tag
from .script_parser import Scene

FRAME_WIDTH = 420


class SheetFailed(RuntimeError):
    pass


def shot_times(scenes: Sequence[Scene], offset: float = 0.0,
               lead_in: float = 0.25) -> List[Dict[str, Any]]:
    """Where every shot sits in the finished video, and what is said over it.

    `offset` is whatever plays before the first scene - a title card, when the
    theme draws one - because the frame is sampled from the delivered file.
    `lead_in` is the silence before a scene's first word, which is what the
    shot plan itself is measured against.
    """
    rows: List[Dict[str, Any]] = []
    for scene in scenes:
        within = 0.0                      # seconds into this scene's own clip
        for i, shot in enumerate(scene.shots or [scene.duration]):
            # a built scene records a dict per shot, carrying the clip and who
            # shot it; a plan on its way to one is a bare length
            length = float(shot["duration"]) if isinstance(shot, dict) else float(shot)
            spoken = [w["text"] for w in (scene.words or [])
                      if within <= lead_in + w.get("start", 0.0) < within + length]
            start = offset + (scene.start or 0.0) + within
            credit = ""
            if isinstance(shot, dict) and shot.get("credit"):
                credit = f"{shot['credit']} - {shot.get('credit_url', '')}".rstrip(" -")
            rows.append({
                "scene": scene.index, "shot": i, "start": start, "seconds": length,
                "middle": start + length / 2,
                "heading": scene.heading or "",
                "words": " ".join(spoken),
                "query": (scene.query or "").strip(),
                "credit": credit,
            })
            within += length
    return rows


def recorded_by(scenes: Sequence[Scene]) -> str:
    """Which cut wrote the shots in `scenes.json`, read off a clip's folder.

    `visuals-9x16/scene_000_00.mp4` says the 9:16 build recorded them, so a
    sheet for another shape can say so instead of hedging.
    """
    for scene in scenes:
        for shot in scene.shots or []:
            if isinstance(shot, dict) and shot.get("path"):
                folder = Path(shot["path"]).parent.name
                if folder.startswith("visuals"):
                    tail = folder[len("visuals"):].lstrip("-")
                    return f"the {tail.replace('x', ':')} cut" if tail else "the 16:9 cut"
    return ""


SRT_LINE = re.compile(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)")
BLOCK_SECONDS = 6.0


def _stamp_seconds(stamp: str) -> float:
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest.replace(",", "."))


def caption_rows(srt: str, block_seconds: float = BLOCK_SECONDS) -> List[Dict[str, Any]]:
    """Rows from a delivered `captions.srt`, for a project whose build is gone.

    A published video keeps its `out/` and loses `build/`, so there are no shots
    to cut on and no searches to show: the sheet falls back to the captions,
    grouped into blocks of about `block_seconds` so a three-minute video is
    thirty rows rather than a hundred and twenty.
    """
    cues: List[Dict[str, Any]] = []
    stamps: Optional[Dict[str, float]] = None
    words: List[str] = []
    for line in srt.splitlines():
        found = SRT_LINE.search(line)
        if found:
            if stamps and words:
                cues.append({**stamps, "text": " ".join(words)})
            stamps = {"start": _stamp_seconds(found.group(1)),
                      "end": _stamp_seconds(found.group(2))}
            words = []
        elif stamps is not None and line.strip() and not line.strip().isdigit():
            words.append(line.strip())
    if stamps and words:
        cues.append({**stamps, "text": " ".join(words)})

    rows: List[Dict[str, Any]] = []
    for cue in cues:
        if rows and cue["end"] - rows[-1]["start"] <= block_seconds:
            rows[-1]["words"] += " " + cue["text"]
            rows[-1]["seconds"] = cue["end"] - rows[-1]["start"]
        else:
            rows.append({"scene": len(rows), "shot": 0, "start": cue["start"],
                         "label": f"caption block {len(rows) + 1}",
                         "seconds": cue["end"] - cue["start"], "words": cue["text"],
                         "heading": "", "query": "", "credit": ""})
    for row in rows:
        row["middle"] = row["start"] + row["seconds"] / 2
    return rows


def _credits(vis_dir: Path) -> Dict[str, Dict[str, str]]:
    try:
        data = json.loads((vis_dir / "credits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _page(title: str, video: Path, rows: Sequence[Dict[str, Any]],
          noun: str = "shots") -> str:
    cells = []
    for row in rows:
        credit = row.get("credit") or ""
        cells.append(f"""
  <figure>
    <img src="{html.escape(row['frame'])}" alt="scene {row['scene']} shot {row['shot']}">
    <figcaption>
      <p class="at">{row['start']:.1f}s &middot; {row['seconds']:.1f}s &middot;
         {html.escape(row.get('label') or f"scene {row['scene']}.{row['shot']}")}
         &middot; {html.escape(row['heading'])}</p>
      <p class="said">{html.escape(row['words']) or '<em>no words over this shot</em>'}</p>
      <p class="meta">searched: {html.escape(row['query']) or '-'}</p>
      <p class="meta">{html.escape(credit) or ''}</p>
    </figcaption>
  </figure>""")
    return f"""<!doctype html>
<meta charset="utf-8">
<title>{html.escape(title)} - shot sheet</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 24px; background: #111; color: #eee; }}
 h1 {{ font-size: 18px; font-weight: 600; }}
 figure {{ display: flex; gap: 16px; margin: 0 0 18px; align-items: flex-start; }}
 img {{ width: {FRAME_WIDTH}px; border-radius: 6px; background: #000; }}
 figcaption {{ max-width: 46em; }}
 .at {{ color: #9ad; margin: 0 0 6px; font-variant-numeric: tabular-nums; }}
 .said {{ margin: 0 0 6px; }}
 .meta {{ color: #999; margin: 0; font-size: 13px; }}
</style>
<h1>{html.escape(title)} &mdash; {len(rows)} {noun} &mdash; {html.escape(video.name)}</h1>
{''.join(cells)}
"""


def build_sheet(root: Path, aspect: str = "", out_dir: Optional[Path] = None,
                log: Callable[[str], None] = print,
                run: Callable[..., Any] = ff.run) -> Path:
    """Write `build/sheet<tag>/sheet.html` and its frames; return the html path."""
    from .config import load_config
    from .pipeline import Project, read_shots
    from .script_parser import load_scenes

    proj = Project(Path(root))
    cfg: Config = load_config(proj.config_path)
    aspect = aspect or cfg.render.aspect
    tag = aspect_tag(aspect)

    # the picture track has no captions or watermark burned in, so it shows what
    # was actually chosen; the delivered cut is the fallback once it is swept
    video = proj.build / f"picture{tag}.mp4"
    if not video.exists():
        delivered = sorted(p for p in proj.out.glob("*.mp4")
                           if aspect_tag(aspect) == "" or p.stem.endswith(tag))
        if tag == "":
            delivered = [p for p in delivered
                         if not any(p.stem.endswith(aspect_tag(a)) for a in
                                    ("9:16", "1:1", "4:5"))]
        if not delivered:
            # a project rendered only as a Short has no 16:9 anything, and the
            # aspect asked for is the config's default rather than one typed
            others = sorted({a for a in ASPECTS
                             for f in list(proj.build.glob("picture*.mp4")) + list(proj.out.glob("*.mp4"))
                             if aspect_tag(a) and f.stem.endswith(aspect_tag(a))})
            hint = (f"; this project has {', '.join(others)} - pass --aspect {others[0]}"
                    if others else "")
            raise SheetFailed(f"no {aspect} video in {proj.build} or {proj.out}{hint}")
        video = delivered[0]

    scenes_json = proj.build / "scenes.json"
    credits: Dict[str, Dict[str, str]] = {}
    if scenes_json.exists():
        scenes = load_scenes(scenes_json)
        vis_dir = proj.build / f"visuals{tag}"
        read_shots(vis_dir, scenes)
        recorded = recorded_by(scenes)
        if not (vis_dir / "shots.json").exists() and recorded != f"the {aspect} cut":
            # `scenes.json` holds the shots of whichever cut was built last, so
            # on a project from before per-cut shots the frame times can belong
            # to the other shape. Silence here would be the empty-tag family.
            # Each shot records the folder its clip came from, so the warning
            # names that cut rather than guessing at one.
            log(f"sheet    warning: no {vis_dir.name}/shots.json, so these times come "
                f"from {recorded or 'whichever cut was built last'}, "
                f"not necessarily {aspect}")
        offset = cfg.theme.title_seconds if cfg.theme.title_card else 0.0
        rows = shot_times(scenes, offset, cfg.voice.lead_in)
        noun = "shots"
        credits = _credits(vis_dir)
    else:
        # A published project keeps `out/` and loses `build/`, so there are no
        # shots to cut on. Its captions are still exact, being the timings the
        # whole pipeline exists to produce, so the sheet reads those instead.
        srt = proj.out / f"captions{tag}.srt"
        if not srt.exists():
            raise SheetFailed(f"no build at {proj.build} and no {srt.name} beside "
                              f"the delivered cut; run vidsmith build first")
        log(f"sheet    no build here, so this is {srt.name}, cut by caption rather "
            f"than by shot: no searches and no credits")
        rows = caption_rows(srt.read_text(encoding="utf-8"))
        noun = "caption blocks"

    out = Path(out_dir) if out_dir else (proj.build if proj.build.exists()
                                         else proj.root) / f"sheet{tag}"
    out.mkdir(parents=True, exist_ok=True)

    for row in rows:
        name = f"shot_{row['scene']:03d}_{row['shot']:02d}.jpg"
        run(["-ss", f"{row['middle']:.3f}", "-i", str(video), "-frames:v", "1",
             "-vf", f"scale={FRAME_WIDTH}:-2", str(out / name)])
        row["frame"] = name
        entry = credits.get(f"{row['scene']}:{row['shot']}") or {}
        if not row.get("credit") and entry.get("credit"):
            row["credit"] = f"{entry['credit']} - {entry.get('url', '')}".rstrip(" -")

    page = out / "sheet.html"
    page.write_text(_page(cfg.title, video, rows, noun), encoding="utf-8")
    log(f"sheet    {len(rows)} {noun} from {video.name} -> {page}")
    return page
