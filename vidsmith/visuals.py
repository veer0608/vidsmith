"""Pick or generate the picture for every scene, then normalise it to clips.

A scene is not one shot. Narration runs six to eight seconds, and a single
unbroken take that long is the thing that makes generated video look generated,
so each scene is cut into shots on the sentence boundaries the TTS already gave
us - the picture changes exactly where the speaker lands a full stop.

Providers:
  cards   - no API key, generated gradient title cards with Ken Burns motion
  pexels  - free stock footage, needs PEXELS_API_KEY
  pixabay - free stock footage, needs PIXABAY_API_KEY
  local   - your own clips/stills in a folder, keyword-matched on filename
"""
from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import requests
from PIL import Image

from .config import ThemeConfig, VisualConfig
from .script_parser import Scene
from .theme import Theme, resolve as resolve_theme
from . import cards
from . import captions as cap
from . import llm
from . import ffmpeg_util as ff

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "is",
    "are", "was", "were", "it", "its", "this", "that", "these", "those", "with",
    "as", "at", "by", "from", "you", "your", "we", "our", "they", "their", "he",
    "she", "her", "not", "so", "if", "then", "than", "what", "how", "why",
    "can", "will", "just", "about", "into", "out", "over", "most", "people",
    "think", "here", "there", "one", "two", "more", "very", "really", "much",
}
TIMEOUT = 45
SENTENCE_END = (".", "!", "?")
CLAUSE_END = (",", ";", ":", "—")
MULTI_SHOT_PROVIDERS = ("pexels", "pixabay", "local")


# --------------------------------------------------------------------------- #
# keyword extraction
# --------------------------------------------------------------------------- #
def keywords(text: str, limit: int = 4) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", text.lower())
    seen: List[str] = []
    for w in words:
        if w in STOPWORDS or len(w) < 4 or w in seen:
            continue
        seen.append(w)
        if len(seen) >= limit:
            break
    return seen


def card_headline(scene: Scene, mode: str = "auto") -> str:
    """What a generated card should say.

    Scene headings are usually production labels ("The hook"), which look wrong
    burned into a frame, so the default pulls the first clause of the narration
    instead - that is a line the viewer is about to hear anyway.
    """
    if mode == "none":
        return ""
    if mode == "heading" and scene.heading:
        return scene.heading
    if mode == "query" and scene.query:
        return scene.query
    return cards.headline_phrase(scene.text, 58) or scene.heading or scene_query(scene)


def scene_query(scene: Scene) -> str:
    if scene.query and scene.query.strip():
        return scene.query.strip()
    kw = keywords(scene.text)
    return " ".join(kw) if kw else (scene.heading or "abstract background")


# --------------------------------------------------------------------------- #
# shot planning
# --------------------------------------------------------------------------- #
def _boundaries(scene: Scene, lead_in: float, min_s: float,
                max_s: float) -> List[float]:
    """Cut times inside a scene, in seconds from the start of its clip."""
    total = scene.duration
    words = cap.attach_punctuation(scene.words, scene.text) if scene.words else []
    sentences = [lead_in + w["end"] for w in words
                 if w["text"].rstrip().endswith(SENTENCE_END)]
    clauses = [lead_in + w["end"] for w in words
               if w["text"].rstrip().endswith(CLAUSE_END)]

    cuts: List[float] = []
    start = 0.0
    for end in sorted(set(sentences + [total])):
        if end - start < min_s:
            continue
        # A sentence that outruns max_s gets subdivided at the latest breath
        # point that still leaves a legal shot on either side.
        while end - start > max_s:
            legal = [c for c in clauses
                     if start + min_s <= c <= min(start + max_s, end - min_s)]
            if not legal:
                break
            start = max(legal)
            cuts.append(start)
        cuts.append(end)
        start = end

    if not cuts:
        return [total]
    if cuts[-1] < total - 0.01:
        # a short tail is absorbed rather than left as a flash frame
        if total - cuts[-1] < min_s:
            cuts[-1] = total
        else:
            cuts.append(total)
    cuts[-1] = total
    return cuts


def plan_shots(scene: Scene, lead_in: float, min_s: float,
               max_s: float) -> List[float]:
    """Shot durations that sum to exactly scene.duration."""
    if scene.duration < min_s * 2 or not scene.words:
        return [scene.duration]

    durations: List[float] = []
    prev = 0.0
    for cut in _boundaries(scene, lead_in, min_s, max_s):
        if cut - prev > 0.05:
            durations.append(cut - prev)
            prev = cut
    if not durations:
        return [scene.duration]
    # rounding must never cost or add a frame; the picture would drift
    durations[-1] += scene.duration - sum(durations)
    return durations


def collapse(durations: Sequence[float], n: int) -> List[float]:
    """Merge a shot plan down to n shots, keeping total length identical.

    Used when the provider could not supply as many distinct clips as the plan
    asked for - cutting back to the same footage reads as a glitch, so it is
    better to hold the shot longer.
    """
    out = list(durations)
    while len(out) > max(1, n):
        i = min(range(len(out) - 1), key=lambda k: out[k] + out[k + 1])
        out[i] += out.pop(i + 1)
    return out


# --------------------------------------------------------------------------- #
# clip normalisation
# --------------------------------------------------------------------------- #
def _fit(size: Tuple[int, int]) -> str:
    w, h = size
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def normalise_video(src: Path, out: Path, duration: float, size: Tuple[int, int],
                    fps: int, start: float = 0.0) -> Path:
    """Trim (or loop) a source video to exactly `duration`, filled to `size`."""
    src_len = ff.duration(src)
    args: List[str] = []
    if src_len and src_len < duration - 0.05:
        args += ["-stream_loop", "-1"]
    elif start > 0:
        args += ["-ss", f"{min(start, max(0.0, src_len - duration)):.3f}"]
    args += ["-i", str(src), "-t", f"{duration:.3f}"]
    args += [
        "-an", "-vf", f"{_fit(size)},fps={fps},format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-video_track_timescale", "90000", str(out),
    ]
    ff.run(args)
    return out


def normalise_still(src: Path, out: Path, duration: float, size: Tuple[int, int],
                    fps: int, ken_burns: bool = True, zoom: float = 1.12,
                    drift: int = 0) -> Path:
    """Turn a still into a clip, optionally with a slow Ken Burns move."""
    w, h = size
    frames = max(1, int(round(duration * fps)))
    if ken_burns:
        step = max(0.0, zoom - 1.0) / frames
        y_expr = "ih/2-(ih/zoom/2)"
        if drift % 2 == 0:
            x_expr = "iw/2-(iw/zoom/2)"
        else:
            x_expr = "iw/2-(iw/zoom/2)+(on/" + str(frames) + ")*(iw*0.03)"
        # Upscaling first is what keeps zoompan from stair-stepping.
        vf = (
            "scale=4000:-2,"
            f"zoompan=z='min(zoom+{step:.6f},{zoom})':d={frames}"
            f":x='{x_expr}':y='{y_expr}':s={w}x{h}:fps={fps},format=yuv420p"
        )
    else:
        vf = f"{_fit(size)},fps={fps},format=yuv420p"
    ff.run([
        "-loop", "1", "-i", str(src), "-t", f"{duration:.3f}",
        "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-video_track_timescale", "90000", str(out),
    ])
    return out


# --------------------------------------------------------------------------- #
# stock providers
# --------------------------------------------------------------------------- #
def _download(url: str, out: Path, headers: Optional[Dict[str, str]] = None) -> Path:
    with requests.get(url, headers=headers or {}, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
    return out


def pexels_search(query: str, key: str, orientation: str, want_h: int) -> List[Dict]:
    r = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": 15, "orientation": orientation,
                "size": "medium"},
        headers={"Authorization": key},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    results = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("link")]
        if not files:
            continue
        # smallest file that still covers the target height, else the biggest
        ok = [f for f in files if (f.get("height") or 0) >= want_h]
        best = min(ok, key=lambda f: f["height"]) if ok else max(
            files, key=lambda f: f.get("height") or 0
        )
        results.append({
            "id": str(v["id"]), "url": best["link"],
            "duration": v.get("duration", 0),
            # Pexels' API terms require crediting the creator and linking back,
            # so carry it through rather than reconstructing it later.
            "author": (v.get("user") or {}).get("name", ""),
            "page": v.get("url", ""),
            # a still of the clip, cheap enough to judge before downloading video
            "preview": v.get("image", ""),
        })
    return results


def pixabay_search(query: str, key: str, want_h: int) -> List[Dict]:
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": key, "q": query, "per_page": 20, "safesearch": "true"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    results = []
    for hit in r.json().get("hits", []):
        vids = hit.get("videos", {})
        pick = vids.get("large") or vids.get("medium") or vids.get("small")
        if not pick or not pick.get("url"):
            continue
        results.append({
            "id": str(hit["id"]), "url": pick["url"],
            "duration": hit.get("duration", 0),
            "author": hit.get("user", ""),
            "page": hit.get("pageURL", ""),
            "preview": pick.get("thumbnail", ""),
        })
    return results


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
class VisualBuilder:
    def __init__(self, cfg: VisualConfig, size: Tuple[int, int], fps: int,
                 workdir: Path, keys: Dict[str, str], log=print,
                 theme: Optional[Theme] = None,
                 theme_cfg: Optional[ThemeConfig] = None, total_scenes: int = 0,
                 lead_in: float = 0.25):
        self.cfg = cfg
        self.theme = theme or resolve_theme()
        self.theme_cfg = theme_cfg or ThemeConfig()
        self.total_scenes = total_scenes
        self.lead_in = lead_in
        self.size = size
        self.fps = fps
        self.workdir = workdir
        self.cache = workdir / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.keys = keys
        self.log = log
        self.used: set = set()
        self._local: List[Path] = []
        if cfg.provider == "local":
            root = Path(cfg.local_dir)
            if root.exists():
                self._local = sorted(
                    p for p in root.rglob("*")
                    if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT
                )

    # -- attribution ledger ------------------------------------------------- #
    def _ledger_path(self) -> Path:
        return self.workdir / "credits.json"

    def _load_ledger(self) -> Dict[str, Dict[str, str]]:
        path = self._ledger_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, scene: Scene) -> None:
        """Pin each shot's creator next to the footage it describes.

        scenes.json is shared across aspects, so a cached rebuild would other-
        wise credit whichever aspect happened to run last.
        """
        ledger = self._load_ledger()
        for j, shot in enumerate(scene.shots):
            ledger[f"{scene.index}:{j}"] = {
                "credit": shot.get("credit", ""), "url": shot.get("credit_url", "")
            }
        self._ledger_path().write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    # -- vision reranking ---------------------------------------------------- #
    def _preview(self, url: str) -> Optional[bytes]:
        """Fetch a candidate's still, small enough to be cheap to look at."""
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGB")
            img.thumbnail((320, 320), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, "JPEG", quality=72)
            return buf.getvalue()
        except Exception:
            return None

    def _rerank(self, hits: List[Dict], scene: Scene, query: str) -> List[Dict]:
        """Reorder search results by what the stills actually show.

        Stock search ranks by popularity, not by whether the clip depicts the
        line - "calendar pages turning" returns a book. Judging the preview
        stills costs one Gemini call per scene and no video downloads.
        """
        key = self.keys.get("gemini", "")
        if not (self.cfg.rerank and key) or len(hits) < 2:
            return hits

        cache = self._rank_cache()
        cached = cache.get(str(scene.index))
        if isinstance(cached, dict) and cached.get("order"):
            by_id = {h["id"]: h for h in hits}
            ordered = [by_id[i] for i in cached["order"] if i in by_id]
            rejected = set(cached.get("reject") or [])
            keepers = [h for h in ordered if h["id"] not in rejected]
            if ordered:
                return keepers or ordered[:1]

        pool = hits[:max(2, self.cfg.rerank_pool)]
        images: List[bytes] = []
        keep: List[Dict] = []
        for hit in pool:
            blob = self._preview(hit.get("preview", "")) if hit.get("preview") else None
            if blob:
                images.append(blob)
                keep.append(hit)
        if len(images) < 2:
            return hits

        try:
            order, rejected = llm.rank_clips(scene.text, query, images, key)
        except Exception as exc:
            self.log(f"    rerank skipped ({exc})")
            return hits

        # Only the judged candidates are eligible. Letting the unjudged tail of
        # the result list backfill would quietly reinstate exactly the wrong
        # subjects the reject pass just removed.
        ranked = [keep[i] for i in order]
        reject_ids = {keep[i]["id"] for i in rejected}
        keepers = [h for h in ranked if h["id"] not in reject_ids]

        if ranked[0]["id"] != hits[0]["id"]:
            self.log(f"    rerank: picked #{hits.index(ranked[0])} over the top result")
        if reject_ids:
            # dropping candidates can leave fewer clips than shots; the shot plan
            # collapses to match, which is better than cutting to a wrong subject
            self.log(f"    rerank: rejected {len(reject_ids)} of {len(keep)} "
                     f"as the wrong subject")
        if not keepers:
            self.log("    rerank: everything was rejected; keeping the best of a bad set")
            keepers = ranked[:1]

        cache[str(scene.index)] = {"order": [h["id"] for h in ranked],
                                   "reject": sorted(reject_ids)}
        self._rank_cache_path().write_text(json.dumps(cache, indent=2), encoding="utf-8")
        return keepers

    def _rank_cache_path(self) -> Path:
        return self.workdir / "rerank.json"

    def _rank_cache(self) -> Dict[str, List[str]]:
        path = self._rank_cache_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # -- source selection --------------------------------------------------- #
    def _stock_batch(self, query: str, count: int, scene: Scene) -> List[Dict]:
        """One search, up to `count` distinct clips taken from its results.

        Reusing a single search for every shot in a scene keeps the shots on the
        same subject and costs one API call instead of one per shot.
        """
        provider = self.cfg.provider
        want_h = min(self.size)
        try:
            if provider == "pexels":
                hits = pexels_search(query, self.keys.get("pexels", ""),
                                     self.cfg.orientation, want_h)
            else:
                hits = pixabay_search(query, self.keys.get("pixabay", ""), want_h)
        except Exception as exc:
            self.log(f"    {provider} lookup failed ({exc}); falling back to a card")
            return []

        hits = self._rerank(hits, scene, query)
        picked: List[Dict] = []
        for hit in hits:
            if len(picked) >= count:
                break
            if hit["id"] in self.used:
                continue
            dest = self.cache / f"{provider}_{hit['id']}.mp4"
            try:
                if not dest.exists():
                    _download(hit["url"], dest)
                ff.duration(dest)  # reject truncated downloads
            except Exception as exc:
                self.log(f"    download failed ({exc}); trying next result")
                dest.unlink(missing_ok=True)
                continue
            self.used.add(hit["id"])
            picked.append({"path": dest, "author": hit.get("author", ""),
                           "page": hit.get("page", "")})
        return picked

    def _local_batch(self, scene: Scene, query: str, count: int) -> List[Dict]:
        if not self._local:
            return []
        terms = set(keywords(query + " " + scene.text, limit=8))
        scored = sorted(
            self._local,
            key=lambda p: (
                -sum(1 for t in terms if t in p.stem.lower()),
                p.stem in self.used,
                p.stem,
            ),
        )
        picked: List[Dict] = []
        for p in scored:
            if len(picked) >= count:
                break
            if p.stem in self.used:
                continue
            self.used.add(p.stem)
            picked.append({"path": p, "author": "", "page": ""})
        if not picked and scored:
            picked.append({"path": scored[0], "author": "", "page": ""})
        return picked

    # -- public -------------------------------------------------------------- #
    def _shot_paths(self, scene: Scene, n: int) -> List[Path]:
        return [self.workdir / f"scene_{scene.index:03d}_{j:02d}.mp4"
                for j in range(n)]

    def build(self, scene: Scene, force: bool = False) -> List[str]:
        multi = (self.cfg.cut_on_sentences
                 and self.cfg.provider in MULTI_SHOT_PROVIDERS)
        plan = (plan_shots(scene, self.lead_in, self.cfg.min_shot_seconds,
                           self.cfg.max_shot_seconds)
                if multi else [scene.duration])
        query = scene_query(scene)

        # ---- reuse whatever this aspect already rendered -------------------- #
        if not force:
            ledger = self._load_ledger()
            for n in {len(plan), 1}:
                paths = self._shot_paths(scene, n)
                if all(p.exists() for p in paths):
                    got = collapse(plan, n) if n != len(plan) else plan
                    scene.shots = [
                        {"path": str(p), "duration": d,
                         "credit": ledger.get(f"{scene.index}:{j}", {}).get("credit", ""),
                         "credit_url": ledger.get(f"{scene.index}:{j}", {}).get("url", "")}
                        for j, (p, d) in enumerate(zip(paths, got))
                    ]
                    scene.visual = scene.shots[0]["path"]
                    return [s["path"] for s in scene.shots]

        # ---- source the footage --------------------------------------------- #
        if self.cfg.provider in ("pexels", "pixabay"):
            sources = self._stock_batch(query, len(plan), scene)
        elif self.cfg.provider == "local":
            sources = self._local_batch(scene, query, len(plan))
        else:
            sources = []

        if sources and len(sources) < len(plan):
            plan = collapse(plan, len(sources))
        elif not sources:
            plan = [scene.duration]

        outs = self._shot_paths(scene, len(plan))
        # stale clips from a previous, longer plan would be concatenated too
        for old in self.workdir.glob(f"scene_{scene.index:03d}_*.mp4"):
            if old not in outs:
                old.unlink(missing_ok=True)

        scene.shots = []
        for j, (out, duration) in enumerate(zip(outs, plan)):
            src = sources[j] if j < len(sources) else None
            path = src["path"] if src else None

            if path and path.suffix.lower() in VIDEO_EXT:
                head = 1.0 if ff.duration(path) > duration + 2 else 0.0
                normalise_video(path, out, duration, self.size, self.fps, start=head)
            elif path:
                normalise_still(path, out, duration, self.size, self.fps,
                                self.cfg.ken_burns, self.cfg.zoom, scene.index + j)
            else:
                counter = ""
                if self.theme_cfg.scene_counter and self.total_scenes:
                    counter = f"{scene.index + 1:02d} / {self.total_scenes:02d}"
                card = cards.scene_card(
                    self.cache / f"card_{scene.index:03d}.png", self.size, self.theme,
                    headline=card_headline(scene, self.cfg.card_text),
                    kicker=scene.heading if self.cfg.card_text != "heading" else "",
                    counter=counter,
                )
                normalise_still(card, out, duration, self.size, self.fps,
                                self.cfg.ken_burns, self.cfg.zoom, scene.index)

            scene.shots.append({
                "path": str(out), "duration": duration,
                "credit": src["author"] if src else "",
                "credit_url": src["page"] if src else "",
            })

        scene.visual = scene.shots[0]["path"]
        self._remember(scene)
        return [s["path"] for s in scene.shots]


def build_all(scenes: Sequence[Scene], cfg: VisualConfig, size: Tuple[int, int],
              fps: int, workdir: Path, keys: Dict[str, str], force: bool = False,
              log=print, theme: Optional[Theme] = None,
              theme_cfg: Optional[ThemeConfig] = None,
              lead_in: float = 0.25) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    builder = VisualBuilder(cfg, size, fps, workdir, keys, log, theme, theme_cfg,
                            total_scenes=len(scenes), lead_in=lead_in)
    for scene in scenes:
        builder.build(scene, force=force)
        cuts = "+".join(f"{s['duration']:.1f}" for s in scene.shots)
        log(f"  visual  scene {scene.index:>3}  {len(scene.shots)} shot"
            f"{'s' if len(scene.shots) != 1 else ' '}  {cuts:<22} "
            f"{scene_query(scene)[:38]}")
