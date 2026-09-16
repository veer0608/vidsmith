"""Choose a finished render's thumbnail by hand.

The build picks one: a model writes a photo search and ranks eight results,
and when that is unavailable the sharpest frame wins. It is the first thing a
viewer sees, and it was the one part of a delivery nobody could change without
building again. This offers the other photographs from the search the build
used, or any search typed, and a frame from any shot of the video, then titles
the one picked exactly the way the build does.

Everything downstream of a thumbnail changes with it. A photograph is owed its
photographer's credit and a frame of the footage is not, so the credits file is
corrected, and the description is rewritten from `youtube.json` through the one
writer, because it is composed from the credits and is the file that gets pasted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from . import ffmpeg_util as ff
from . import manifest, pipeline, thumbs, visuals
from .check import delivered
from .config import Config, aspect_tag, load_config
from .retake import MAX_QUERY, Build, RetakeRefused
from .theme import resolve as resolve_theme


class Render:
    """The delivered cut a thumbnail belongs to, and how to title it."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.proj = pipeline.Project(self.root)
        self.cfg: Config = load_config(self.proj.config_path)
        self.aspect = self.cfg.render.aspect
        self.tag = aspect_tag(self.aspect)
        cuts = delivered(self.proj.out) if self.proj.out.is_dir() else []
        # the file name the build already chose, never the title slugged again
        cut = next((p for a, p in cuts if a == self.aspect), None)
        if cut is None:
            raise RetakeRefused("this render delivered no video to give a thumbnail")
        self.cut = cut
        self.jpg = cut.with_suffix(".jpg")
        self.credits = self.proj.out / f"credits{self.tag}.txt"

    @property
    def portrait(self) -> bool:
        width, height = self.cfg.size
        return height > width

    def choice(self) -> Dict[str, Any]:
        path = self.proj.build / f"thumbnail{self.tag}.json"
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def default_query(self) -> str:
        """The search the build used, else what the video shows."""
        asked = self.choice().get("query")
        if asked:
            return asked
        title = pipeline.resolve_title(self.proj, self.cfg)
        return " ".join(visuals.keywords(title, limit=3)) or title


def _photos(render: Render, query: str, keys: Dict[str, str]) -> List[Dict[str, Any]]:
    if not keys.get("pexels"):
        raise RetakeRefused("choosing a stock photograph needs PEXELS_API_KEY on this instance")
    try:
        # the same count the build asks for, so its cached search is the one read
        return visuals.pexels_photos(query, keys["pexels"],
                                     "portrait" if render.portrait else "landscape")
    except Exception as exc:
        raise RetakeRefused(f"the photo search failed ({exc})") from exc


def _query(render: Render, asked: Optional[str]) -> str:
    return " ".join((asked or "").split())[:MAX_QUERY] or render.default_query()


def candidates(root: Path, keys: Optional[Dict[str, str]] = None,
               query: Optional[str] = None) -> Dict[str, Any]:
    """Photographs the thumbnail could be made from, in search order."""
    render = Render(root)
    keys = keys if keys is not None else pipeline.find_keys(render.root)
    query = _query(render, query)
    current = render.choice()
    return {
        "query": query, "current": current, "aspect": render.aspect,
        "photos": [{"id": p["id"], "preview": p.get("preview", ""),
                    "author": p.get("author", ""), "page": p.get("page", ""),
                    "alt": p.get("alt", "")}
                   for p in _photos(render, query, keys)],
    }


def _photo(render: Render, photo_id: str, query: Optional[str],
           keys: Optional[Dict[str, str]]) -> Tuple[str, Dict[str, Any]]:
    keys = keys if keys is not None else pipeline.find_keys(render.root)
    query = _query(render, query)
    # only a search result is ever downloaded, never an address the caller sends
    hit = next((p for p in _photos(render, query, keys) if p["id"] == str(photo_id)), None)
    if hit is None:
        raise RetakeRefused("that photograph is not in the results for this search")
    return query, hit


def _frame_source(render: Render, scene: int, shot: int) -> Path:
    build = Build(render.root)
    build.shot(scene, shot)
    clip = build.shot_path(scene, shot)
    if not clip.exists():
        raise RetakeRefused("that shot's clip is not on disk")
    return clip


def use_photo(root: Path, photo_id: str, keys: Optional[Dict[str, str]] = None,
              query: Optional[str] = None, log: Callable[[str], None] = print) -> Path:
    """Title a stock photograph as the thumbnail and credit its photographer."""
    render = Render(root)
    query, hit = _photo(render, str(photo_id), query, keys)
    source = render.proj.build / ".thumbstock" / f"stock_{hit['id']}.jpg"
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        try:
            with manifest.timed("stock", "downloads"):
                r = requests.get(hit["url"], timeout=60)
            r.raise_for_status()
        except Exception as exc:
            raise RetakeRefused(f"the photograph did not download ({exc})") from exc
        source.write_bytes(r.content)
    stock = {"author": hit.get("author", ""), "page": hit.get("page", "")}
    _apply(render, source, stock, {"kind": "photo", "query": query, "id": hit["id"]})
    log(f"thumbnail photo {hit['id']} by {stock['author'] or 'an unnamed photographer'}")
    return render.jpg


def use_frame(root: Path, scene: int, shot: int,
              log: Callable[[str], None] = print) -> Path:
    """Title the middle frame of one shot as the thumbnail."""
    render = Render(root)
    clip = _frame_source(render, scene, shot)
    shot_info = Build(render.root).shot(scene, shot)[1]
    source = render.proj.build / ".thumbframes" / f"scene_{scene:03d}_{shot:02d}.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    middle = float(shot_info.get("duration") or 0.0) / 2
    # from the shot's own clip, which carries no captions, watermark or progress bar
    ff.run(["-ss", f"{middle:.3f}", "-i", str(clip), "-frames:v", "1",
            "-update", "1", "-q:v", "2", str(source)])
    # footage on a thumbnail is already credited with the footage
    _apply(render, source, None, {"kind": "frame", "scene": scene, "shot": shot})
    log(f"thumbnail the frame from scene {scene} shot {shot}")
    return render.jpg


def _apply(render: Render, source: Path, stock: Optional[Dict[str, str]],
           choice: Dict[str, Any]) -> None:
    cfg = render.cfg
    theme = resolve_theme(cfg.theme.preset, cfg.theme.accent, cfg.theme.font)
    title = pipeline.resolve_title(render.proj, cfg)
    target = None if render.portrait else (1280, 720)
    # Composed outside out/ and moved in, so a failed compose never leaves half a
    # jpg, and `check` never finds a stray one matching no delivered cut.
    partial = render.proj.build / f"thumbnail{render.tag}.part.jpg"
    thumbs.titled(source, partial, title, theme, target)
    partial.replace(render.jpg)

    pipeline.set_thumbnail_credit(render.credits, stock)
    pipeline.write_thumbnail_choice(render.proj.build, render.tag, choice)
    meta = render.proj.out / "youtube.json"
    if meta.exists():
        pipeline.write_metadata(render.proj.out,
                                json.loads(meta.read_text(encoding="utf-8")),
                                source=cfg.source)
