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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import ffmpeg_util as ff
from .config import Config, aspect_tag
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


def _credits(vis_dir: Path) -> Dict[str, Dict[str, str]]:
    try:
        data = json.loads((vis_dir / "credits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _page(title: str, video: Path, rows: Sequence[Dict[str, Any]]) -> str:
    cells = []
    for row in rows:
        credit = row.get("credit") or ""
        cells.append(f"""
  <figure>
    <img src="{html.escape(row['frame'])}" alt="scene {row['scene']} shot {row['shot']}">
    <figcaption>
      <p class="at">{row['start']:.1f}s &middot; {row['seconds']:.1f}s &middot;
         scene {row['scene']}.{row['shot']} &middot; {html.escape(row['heading'])}</p>
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
<h1>{html.escape(title)} &mdash; {len(rows)} shots &mdash; {html.escape(video.name)}</h1>
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

    scenes_json = proj.build / "scenes.json"
    if not scenes_json.exists():
        raise SheetFailed(f"no build at {proj.build}; run vidsmith build first")
    scenes = load_scenes(scenes_json)
    vis_dir = proj.build / f"visuals{tag}"
    read_shots(vis_dir, scenes)
    if not (vis_dir / "shots.json").exists():
        # `scenes.json` holds the shots of whichever cut was built last, so on a
        # project from before per-cut shots the frame times can belong to the
        # other shape. Silence here would be the empty-tag family all over again.
        log(f"sheet    warning: no {vis_dir.name}/shots.json, so these times come "
            f"from whichever cut was built last, not necessarily {aspect}")

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
            raise SheetFailed(f"no {aspect} video in {proj.build} or {proj.out}")
        video = delivered[0]

    offset = cfg.theme.title_seconds if cfg.theme.title_card else 0.0
    rows = shot_times(scenes, offset, cfg.voice.lead_in)
    credits = _credits(proj.build / f"visuals{tag}")
    out = Path(out_dir) if out_dir else proj.build / f"sheet{tag}"
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
    page.write_text(_page(cfg.title, video, rows), encoding="utf-8")
    log(f"sheet    {len(rows)} shots from {video.name} -> {page}")
    return page
