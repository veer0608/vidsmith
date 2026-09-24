"""Put a different clip under one shot of a finished build.

The footage is the part of a render most often wrong, and the only way to fix
one bad shot was to build the whole video again: new searches, new verdicts,
new downloads, and every other shot free to change as well. A retake changes
the one shot asked for. It encodes the replacement clip to exactly the slot the
old one filled, so the scene still sums to its narration, updates the credit
ledger, and runs `pipeline.build(retake=True)` to cut, master and re-credit the
video from everything else already on disk.

The choice is the person's, not the model's. The candidates are the search that
shot came from, or one they type, with the reranker's verdicts shown beside
them rather than obeyed: it has already been wrong about this shot once.

A clip is only ever taken from a search result, never from a URL the caller
supplies, because this downloads whatever it is given.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import captions as cap
from . import ffmpeg_util as ff
from . import pipeline, snapshot, visuals
from .config import Config, aspect_tag, clip_exclusion, load_config
from .script_parser import Scene, load_scenes

# Only a stock search has other candidates to offer. A card, a local file and a
# drawn diagram are the build's own, and changing them is a script edit.
SWAPPABLE = ("pexels", "pixabay")
MAX_QUERY = 100


class RetakeRefused(ValueError):
    """A retake that cannot happen, with a reason a person can act on."""


# --------------------------------------------------------------------------- #
# reading a finished build
# --------------------------------------------------------------------------- #
class Build:
    """The parts of a finished build a retake reads, resolved once."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.proj = pipeline.Project(self.root)
        self.cfg: Config = load_config(self.proj.config_path)
        self.tag = aspect_tag(self.cfg.render.aspect)
        self.vis = self.proj.build / f"visuals{self.tag}"
        scenes_json = self.proj.build / "scenes.json"
        if not scenes_json.exists() or not (self.vis / "credits.json").exists():
            raise RetakeRefused("this render kept no working files, so its shots "
                                "cannot be changed; render the script again")
        self.scenes: List[Scene] = load_scenes(scenes_json)
        # this cut's shots, which the shared file stops holding once there are two
        pipeline.read_shots(self.vis, self.scenes)
        self.ledger: Dict[str, Dict[str, str]] = _read(self.vis / "credits.json")
        self.decided: Dict[str, Any] = _read(self.proj.build / "diagram_scenes.json")
        # visuals.exclude, parsed; load_config has already refused a bad entry
        self.excluded = [clip_exclusion(e) for e in self.cfg.visuals.exclude]

    @property
    def provider(self) -> str:
        return self.cfg.visuals.provider

    def drawn(self, scene: Scene) -> bool:
        return bool(scene.diagram) or self.decided.get(str(scene.index)) is True

    def shot(self, scene_index: int, shot_index: int) -> Tuple[Scene, Dict[str, Any]]:
        scene = next((s for s in self.scenes if s.index == scene_index), None)
        if scene is None or not 0 <= shot_index < len(scene.shots):
            raise RetakeRefused(f"this render has no shot {shot_index} in scene {scene_index}")
        return scene, scene.shots[shot_index]

    def swappable(self, scene: Scene) -> bool:
        return self.provider in SWAPPABLE and not self.drawn(scene)

    def entry(self, scene_index: int, shot_index: int) -> Dict[str, str]:
        return self.ledger.get(f"{scene_index}:{shot_index}") or {}

    def excludes(self, clip_id: str) -> bool:
        return visuals.is_excluded(self.excluded, self.provider, clip_id)

    def used(self) -> Dict[str, str]:
        """Every clip in the video, by id, and which shot holds it."""
        return {entry["id"]: key for key, entry in self.ledger.items()
                if (entry or {}).get("id")}

    def shot_path(self, scene_index: int, shot_index: int) -> Path:
        return self.vis / f"scene_{scene_index:03d}_{shot_index:02d}.mp4"


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def shots(root: Path) -> Dict[str, Any]:
    """Every shot in play order, with what was said over it and where it came from."""
    build = Build(root)
    lead_in = build.cfg.voice.lead_in
    rows: List[Dict[str, Any]] = []
    for scene in build.scenes:
        words = cap.attach_punctuation(scene.words, scene.text) if scene.words else []
        offset = 0.0
        for j, shot in enumerate(scene.shots):
            duration = float(shot.get("duration") or 0.0)
            last = j == len(scene.shots) - 1
            said = " ".join(w["text"] for w in words
                            if offset <= lead_in + w["start"]
                            and (last or lead_in + w["start"] < offset + duration))
            entry = build.entry(scene.index, j)
            rows.append({
                "scene": scene.index, "shot": j, "heading": scene.heading,
                "start": round(scene.start + offset, 2), "duration": round(duration, 2),
                "said": said, "credit": entry.get("credit") or shot.get("credit", ""),
                "query": entry.get("query") or shot.get("query", ""),
                "clip": entry.get("id") or shot.get("clip", ""),
                "swappable": build.swappable(scene),
            })
            offset += duration
    return {"provider": build.provider, "aspect": build.cfg.render.aspect, "shots": rows}


def frame(root: Path, scene_index: int, shot_index: int) -> Path:
    """A still from the middle of a shot, as it plays in the video."""
    build = Build(root)
    _, shot = build.shot(scene_index, shot_index)
    clip = build.shot_path(scene_index, shot_index)
    if not clip.exists():
        raise RetakeRefused("that shot's clip is not on disk")
    out = build.proj.build / f"frames{build.tag}" / f"{clip.stem}.jpg"
    # a retake replaces the clip under the same name, so the still follows it
    if not out.exists() or out.stat().st_mtime < clip.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        middle = float(shot.get("duration") or 0.0) / 2
        ff.run(["-ss", f"{middle:.3f}", "-i", str(clip), "-frames:v", "1",
                "-update", "1", "-vf", "scale=320:-2", "-q:v", "5", str(out)])
    return out


# --------------------------------------------------------------------------- #
# the other clips
# --------------------------------------------------------------------------- #
def _search(build: Build, query: str, keys: Dict[str, str]) -> List[Dict[str, Any]]:
    provider = build.provider
    if not keys.get(provider):
        raise RetakeRefused(f"searching {provider} needs its API key on this instance")
    want_h = min(build.cfg.size)
    try:
        if provider == "pexels":
            return visuals.pexels_search(query, keys["pexels"],
                                         build.cfg.visuals.orientation, want_h)
        return visuals.pixabay_search(query, keys["pixabay"], want_h)
    except Exception as exc:                  # the network or the provider
        raise RetakeRefused(f"the {provider} search failed ({exc})") from exc


def _query(build: Build, scene: Scene, shot_index: int, asked: Optional[str]) -> str:
    asked = " ".join((asked or "").split())[:MAX_QUERY]
    return (asked or build.entry(scene.index, shot_index).get("query")
            or scene.shots[shot_index].get("query") or visuals.scene_query(scene))


def _verdicts(build: Build, scene: Scene, query: str) -> Tuple[List[str], set]:
    """What the reranker made of this search for this scene, if it judged it."""
    order: List[str] = []
    reject: set = set()
    for key, verdict in _read(build.vis / "rerank.json").items():
        if str(key).split(".")[0].rstrip("~") != str(scene.index):
            continue
        if not isinstance(verdict, dict) or verdict.get("query", query) != query:
            continue
        order += [i for i in verdict.get("order") or [] if i not in order]
        reject |= set(verdict.get("reject") or [])
    return order, reject


def candidates(root: Path, scene_index: int, shot_index: int,
               keys: Optional[Dict[str, str]] = None,
               query: Optional[str] = None) -> Dict[str, Any]:
    """The clips this shot could hold instead, best first.

    Kept by the reranker first, in its order, then anything it never judged,
    then what it rejected. Rejected clips are still offered: a person looking at
    the shot may disagree, and the reason for this feature is that the model
    was wrong about it. Clips already somewhere in the video are left out, and
    so are the ones `visuals.exclude` names: the project has already decided
    against them, and the reranker offered the Matrix clip it had just
    excluded as a kept candidate.
    """
    build = Build(root)
    scene, _ = build.shot(scene_index, shot_index)
    if not build.swappable(scene):
        raise RetakeRefused(_why_fixed(build, scene))
    keys = keys if keys is not None else pipeline.find_keys(build.root)
    query = _query(build, scene, shot_index, query)
    hits = _search(build, query, keys)
    order, reject = _verdicts(build, scene, query)
    used = build.used()
    current = build.entry(scene_index, shot_index).get("id", "")

    def rank(hit: Dict[str, Any]) -> Tuple[int, int]:
        if hit["id"] in reject:
            return 2, 0
        if hit["id"] in order:
            return 0, order.index(hit["id"])
        return 1, 0

    fresh = [h for h in hits if h["id"] not in used and not build.excludes(h["id"])]
    fresh.sort(key=rank)                      # stable, so search order breaks ties
    return {
        "query": query, "current": current,
        "duration": round(float(scene.shots[shot_index].get("duration") or 0.0), 2),
        "candidates": [{
            "id": h["id"], "preview": h.get("preview", ""), "author": h.get("author", ""),
            "page": h.get("page", ""), "duration": h.get("duration") or 0,
            "verdict": ("rejected" if h["id"] in reject
                        else "kept" if h["id"] in order else ""),
        } for h in fresh],
    }


SHEET_MAX = 12      # candidates tiled into one image, in the order listed


def candidate_sheet(root: Path, scene_index: int, shot_index: int,
                    rows: List[Dict[str, Any]]) -> Optional[Path]:
    """The candidates' stills in one image, numbered as the list prints them.

    The page shows each candidate beside the shot; a terminal cannot, and a
    list of ids and creators is no way to choose footage. The stills are the
    ones the reranker judged, so this is the evidence it had. None when no
    still could be fetched.
    """
    from PIL import Image, ImageDraw

    build = Build(root)
    tiles = []
    for n, row in enumerate(rows[:SHEET_MAX], 1):
        blob = visuals.preview_still(row["preview"]) if row.get("preview") else None
        if not blob:
            continue
        try:
            img = Image.open(BytesIO(blob)).convert("RGB")
        except Exception:
            continue
        img.thumbnail((320, 180))
        tiles.append((f"{n}  {row['id']}  {row.get('verdict') or ''}".strip(), img))
    if not tiles:
        return None
    cols = min(3, len(tiles))
    rows_n = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 320, rows_n * 180), "black")
    draw = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(tiles):
        x, y = (i % cols) * 320, (i // cols) * 180
        sheet.paste(img, (x + (320 - img.width) // 2, y + (180 - img.height) // 2))
        draw.rectangle([x, y, x + 8 + 7 * len(label), y + 18], fill="black")
        draw.text((x + 4, y + 3), label, fill="yellow")
    out = build.proj.build / f"retake{build.tag}" / f"scene_{scene_index:03d}_{shot_index:02d}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=85)
    return out


def _why_fixed(build: Build, scene: Scene) -> str:
    if build.provider not in SWAPPABLE:
        return (f"this render used {build.provider} rather than stock footage, so "
                f"there are no other clips to choose from")
    return "that scene is a drawn diagram, not footage"


def _resolve(build: Build, scene_index: int, shot_index: int, clip_id: str,
             query: Optional[str], keys: Optional[Dict[str, str]]
             ) -> Tuple[Scene, Dict[str, Any], str, Dict[str, Any]]:
    """The shot, the search and the result a retake would use, or a refusal."""
    scene, shot = build.shot(scene_index, shot_index)
    if not build.swappable(scene):
        raise RetakeRefused(_why_fixed(build, scene))
    keys = keys if keys is not None else pipeline.find_keys(build.root)
    query = _query(build, scene, shot_index, query)
    holder = build.used().get(clip_id)
    if holder == f"{scene_index}:{shot_index}":
        raise RetakeRefused("that is the clip this shot already has")
    if holder:
        raise RetakeRefused("that clip is already in the video; using it twice "
                            "shows the same footage twice")
    if build.excludes(clip_id):
        raise RetakeRefused("that clip is in this project's visuals.exclude")
    # the search is cached for a day, so asking again costs no request
    hit = next((h for h in _search(build, query, keys) if h["id"] == clip_id), None)
    if hit is None:
        raise RetakeRefused("that clip is not in the results for this search")
    return scene, shot, query, hit


def check(root: Path, scene_index: int, shot_index: int, clip_id: str,
          query: Optional[str] = None, keys: Optional[Dict[str, str]] = None) -> None:
    """Refuse now, before a retake waits in line, anything that would fail later."""
    _resolve(Build(root), scene_index, shot_index, str(clip_id), query, keys)


# --------------------------------------------------------------------------- #
# the retake
# --------------------------------------------------------------------------- #
def replace(root: Path, scene_index: int, shot_index: int, clip_id: str,
            keys: Optional[Dict[str, str]] = None, query: Optional[str] = None,
            log: Callable[[str], None] = print) -> Path:
    """Swap one shot's clip and deliver the video again. Returns the new mp4.

    Everything a retake rewrites is copied aside first and put back if any part
    of it fails or is stopped, so a failed retake leaves the video it started
    from rather than half of a new one. The copy is removed once it is over; one
    still present means a process died partway, and `snapshot.restore()`
    finishes it.
    """
    build = Build(root)
    clip_id = str(clip_id)
    scene, shot, query, hit = _resolve(build, scene_index, shot_index, clip_id,
                                       query, keys)
    duration = float(shot.get("duration") or 0.0)
    log(f"retake   scene {scene_index} shot {shot_index}: {build.provider} {clip_id} "
        f"by {hit.get('author') or 'an unnamed creator'}, for '{query}'")

    target = build.shot_path(scene_index, shot_index)
    snapshot.take(build.root, touched(build, target))
    try:
        dest = build.vis / "cache" / f"{build.provider}_{clip_id}.mp4"
        if not dest.exists():
            visuals._download(hit["url"], dest)
        length = ff.duration(dest)            # also rejects a truncated download
        if not length:
            dest.unlink(missing_ok=True)
            raise RetakeRefused("the clip downloaded empty")
        if length < duration - 0.05:
            log(f"         the clip is {length:.1f}s for a {duration:.1f}s shot; "
                f"slowing it to fit rather than looping")
        # Not named scene_*: the build reuses every file matching that, and a
        # half-written replacement beside the real one would be cut in too.
        fresh = build.vis / f"retake_{scene_index:03d}_{shot_index:02d}.mp4"
        head = 1.0 if length > duration + 2 else 0.0
        visuals.normalise_video(dest, fresh, duration, build.cfg.size,
                                build.cfg.render.fps, start=head)
        fresh.replace(target)

        build.ledger[f"{scene_index}:{shot_index}"] = {
            "credit": hit.get("author", ""), "url": hit.get("page", ""),
            "id": clip_id, "query": query,
        }
        (build.vis / "credits.json").write_text(json.dumps(build.ledger, indent=2),
                                                encoding="utf-8")
        final = pipeline.build(build.root, log=log, retake=True)
    except BaseException:
        snapshot.restore(build.root)
        raise
    snapshot.discard(build.root)
    return final


def touched(build: Build, target: Path) -> List[str]:
    """What a retake rewrites, relative to the job: the delivery and the shot."""
    return [str(p.relative_to(build.root)).replace("\\", "/")
            for p in (build.proj.out, target, build.vis / "credits.json",
                      build.proj.build / "scenes.json")]
