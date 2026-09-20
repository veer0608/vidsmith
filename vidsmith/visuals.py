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

import hashlib
import json
import math
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from PIL import Image

from .config import LONG_SHOT_FACTOR, CaptionConfig, ThemeConfig, VisualConfig
from .script_parser import Scene
from .theme import Theme, resolve as resolve_theme
from . import cards
from . import captions as cap
from . import diagram
from . import llm
from . import manifest
from . import usage
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
# Results asked of a stock search, and how many rerank calls a scene may spend
# judging them. A thirty-second scene plans six or seven shots and the reranker
# keeps two to four of every eight candidates, so one call starved it; three
# calls over thirty results is room for a scene that long.
SEARCH_RESULTS = 30
RERANK_ROUNDS = 3
# A still with under DARK_LIT of its pixels brighter than DARK_LUMA is footage
# nobody can read behind captions. "Night highway drive following a truck" was
# picked for two styles in a row and played as a black frame with two dots:
# 5% lit. Night streets with their lights on measured 15 to 40%, daylight 60 to
# 100%, so the cut sits clear of footage that is dark on purpose and readable.
DARK_LUMA = 60
DARK_LIT = 0.10


def too_dark(still: bytes) -> bool:
    """Whether a preview still is too dark to read. Unreadable bytes are not."""
    try:
        img = Image.open(BytesIO(still)).convert("L")
    except Exception:
        return False
    hist = img.histogram()
    total = sum(hist)
    return bool(total) and sum(hist[DARK_LUMA + 1:]) / total < DARK_LIT


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


def forget_beats(build_dir: Path, scene: Scene) -> List[str]:
    """Drop one scene's cached beat searches, and return what they were.

    `beats.json` is keyed by a hash of the heading and the passage, so there is
    no index to delete by; an entry is this scene's when its text is part of
    the scene's own. That keying is what makes the cache survive a redraft, so
    it is not changed to make deleting easier.

    The next build writes fresh searches for those beats. It is worth saying
    that the words decide the search: a scene that comes back wrong twice needs
    its narration changed, not another search.
    """
    path = Path(build_dir) / "beats.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(cache, dict):
        return []
    whole = " ".join((scene.text or "").split())
    dropped = []
    for key in list(cache):
        text = " ".join(str((cache[key] or {}).get("text") or "").split())
        if text and text in whole:
            dropped.append(str((cache[key] or {}).get("query") or ""))
            cache.pop(key)
    if dropped:
        path.write_text(json.dumps(cache, indent=1) + "\n", encoding="utf-8")
    return dropped


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
    word_ends = [lead_in + w["end"] for w in words]

    def breath(lo: float, hi: float) -> float:
        """The best place to cut inside a window, in order of preference.

        A comma is ideal, the gap between two words is acceptable, and an
        arbitrary time is the last resort - but something must always come back,
        because the alternative is holding one shot for the whole sentence,
        which is the exact thing this is here to prevent.
        """
        for points in (clauses, word_ends):
            legal = [p for p in points if lo <= p <= hi]
            if legal:
                return max(legal)
        return hi

    cuts: List[float] = []
    start = 0.0
    for end in sorted(set(sentences + [total])):
        if end - start < min_s:
            continue
        # A sentence that outruns max_s is subdivided at the latest breath point
        # that still leaves a legal shot on either side.
        while end - start > max_s:
            lo, hi = start + min_s, min(start + max_s, end - min_s)
            if hi < lo:
                break
            start = breath(lo, hi)
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


def fit_shots(plan: Sequence[float], lengths: Sequence[float],
              min_s: float = 0.0) -> List[Tuple[float, int]]:
    """Cut a scene across the clips it has, never asking one for more than it holds.

    Returns `(duration, clip index)` per shot in play order, summing to exactly
    `sum(plan)`. A clip of unknown length is passed as `math.inf`.

    `collapse()` alone matched shots to clips by count, so a scene the reranker
    left with three clips became three ten-second shots whatever those clips
    held, and `normalise_video` looped every one shorter than its shot: a
    nine-minute build replayed the same footage a few seconds apart at least
    ten times. Here the longest clip plays the longest shot, and a cut the
    plan put on a sentence end moves only as far as a clip's length forces it.
    Clips beyond the plan's count are used only when the rest cannot cover the
    scene, and when even all of them cannot, each plays in full and is slowed
    by the same factor - the one case left where a shot outlasts its clip.
    """
    if not lengths:
        raise ValueError("fit_shots needs at least one clip")
    total = float(sum(plan))
    longest_first = sorted(range(len(lengths)), key=lambda i: -lengths[i])
    k = min(len(plan), len(lengths))
    while k < len(lengths) and sum(lengths[i] for i in longest_first[:k]) < total:
        k += 1
    chosen = longest_first[:k]

    shots = collapse(plan, k) if k < len(plan) else list(plan)
    while len(shots) < k:
        i = max(range(len(shots)), key=lambda j: shots[j])
        shots[i:i + 1] = [shots[i] / 2, shots[i] / 2]
    by_shot = sorted(range(k), key=lambda j: -shots[j])
    clip = {j: i for j, i in zip(by_shot, chosen)}
    caps = [lengths[clip[j]] for j in range(k)]

    if sum(caps) < total:
        durations = [c * total / sum(caps) for c in caps]
    else:
        durations = []
        prev, target = 0.0, 0.0
        for j in range(k):
            target += shots[j]
            if j == k - 1:
                cut = total
            else:
                # hard: this clip's length, and enough left for the clips after it
                hard_lo, hard_hi = total - sum(caps[j + 1:]), prev + caps[j]
                # soft: no shot under min_s, when the lengths allow it
                lo = max(hard_lo, min(prev + min_s, hard_hi))
                hi = min(hard_hi, max(total - min_s * (k - 1 - j), hard_lo))
                cut = lo if lo > hi else min(max(target, lo), hi)
            durations.append(cut - prev)
            prev = cut

    fitted = [(d, clip[j]) for j, d in enumerate(durations) if d > 1e-6]
    # rounding must never cost or add a frame; the picture would drift
    last, i = fitted[-1]
    fitted[-1] = (last + total - sum(d for d, _ in fitted), i)
    return fitted


def plan_beats(plan: Sequence[float], beat_s: float) -> List[Tuple[int, int]]:
    """Group a scene's shots into beats, as `(first shot, end shot)` ranges.

    A beat is the stretch of narration one stock search illustrates. The shot
    plan already cuts on the full stops the speaker lands, so beats are whole
    runs of shots: each closes once it reaches `beat_s`, and a short tail joins
    the beat before it rather than earning a search of its own.
    """
    if beat_s <= 0 or len(plan) <= 1:
        return [(0, len(plan))]
    beats: List[List[int]] = []
    lo, held = 0, 0.0
    for i, d in enumerate(plan):
        held += d
        if held >= beat_s:
            beats.append([lo, i + 1])
            lo, held = i + 1, 0.0
    if lo < len(plan):
        if beats and held < beat_s / 2:
            beats[-1][1] = len(plan)
        else:
            beats.append([lo, len(plan)])
    return [(a, b) for a, b in beats]


# --------------------------------------------------------------------------- #
# clip normalisation
# --------------------------------------------------------------------------- #
def _fit(size: Tuple[int, int]) -> str:
    w, h = size
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def normalise_video(src: Path, out: Path, duration: float, size: Tuple[int, int],
                    fps: int, start: float = 0.0) -> Path:
    """Trim a source video to exactly `duration`, filled to `size`.

    A clip shorter than its shot is slowed until it fills it, never looped. A
    loop is the same footage twice a few seconds apart, which reads as a
    glitch; `fit_shots` keeps slowing to the scenes whose usable footage, all
    of it together, is shorter than the narration.
    """
    src_len = ff.duration(src)
    vf = f"{_fit(size)},fps={fps},format=yuv420p"
    args: List[str] = []
    if src_len and src_len < duration - 0.05:
        # Stretch the timestamps and let fps repeat frames. The stretched clip
        # ends a frame short of the shot, so tpad holds its last frame and -t
        # cuts the output to the exact slot.
        vf = (f"setpts=(PTS-STARTPTS)*{duration / src_len:.6f},{vf},"
              f"tpad=stop_mode=clone:stop_duration=1")
    elif start > 0:
        args += ["-ss", f"{min(start, max(0.0, src_len - duration)):.3f}"]
    args += ["-i", str(src), "-t", f"{duration:.3f}"]
    args += [
        "-an", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-video_track_timescale", "90000", str(out),
    ]
    ff.run(args)
    return out


def footage_print(path: Path, length: float, out: Path) -> Optional[bytes]:
    """A 32x18 grey frame from the middle of a clip, to know footage by sight.

    Stock sites hold the same footage under more than one id. Pexels 853987
    (Coverr) and 4671883 are one ten-second phone clip, identical frame for
    frame, from two uploaders, and a build that only checked ids played both
    in one scene sixteen seconds apart. The frame is kept beside the download,
    so a rebuild does not decode it again.
    """
    if not out.exists():
        try:
            ff.run(["-ss", f"{length / 2 if math.isfinite(length) else 0.0:.3f}",
                    "-i", str(path), "-frames:v", "1", "-update", "1",
                    "-vf", "scale=32:18,format=gray", str(out)])
        except RuntimeError:
            return None
    try:
        return Image.open(out).convert("L").resize((32, 18)).tobytes()
    except OSError:
        return None


def same_footage(a: Tuple[float, bytes], b: Tuple[float, bytes]) -> bool:
    """Two clips of one length whose middle frames match to within a re-encode."""
    (len_a, frame_a), (len_b, frame_b) = a, b
    if not (math.isfinite(len_a) and math.isfinite(len_b)) or abs(len_a - len_b) > 0.1:
        return False
    if not frame_a or len(frame_a) != len(frame_b):
        return False
    return sum(abs(x - y) for x, y in zip(frame_a, frame_b)) / len(frame_a) < 4


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
    with manifest.timed("stock", "downloads"), \
            requests.get(url, headers=headers or {}, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
                manifest.note("stock", "downloads", bytes=len(chunk))
    return out


# Pixabay's API terms require a result to be cached for a day rather than
# re-requested, and Pexels bills a monthly quota that two similar scripts would
# otherwise spend twice. A day is the number their terms name; the searches this
# caches are the ones a paid instance repeats most.
SEARCH_TTL = 24 * 3600


def _search_cache_dir() -> Path:
    return Path(os.environ.get(
        "VIDSMITH_SEARCH_CACHE",
        Path(__file__).resolve().parent.parent / ".cache" / "searches",
    ))


def search_cache_path(provider: str, parts: Sequence[Any],
                      cache_dir: Optional[Path] = None) -> Path:
    """Where a search's results are cached. One definition, because the rerank
    benchmark finds past searches by this name and a second copy of the hash
    would drift from it in silence. `cache_dir` lets it read another checkout's
    cache; the default is this one's."""
    slug = hashlib.sha1(
        "\x1f".join([provider, *(str(p) for p in parts)]).encode("utf-8")
    ).hexdigest()[:20]
    return (cache_dir or _search_cache_dir()) / f"{provider}_{slug}.json"


def _cached_search(provider: str, parts: Sequence[Any], fetch) -> List[Dict]:
    """Serve a repeated stock search from disk for `SEARCH_TTL` seconds.

    The key is the query and its parameters, never the API key: the cache is
    shared across projects and jobs, so a key in the filename would be a secret
    sitting in a directory nobody thinks of as sensitive. A cache that cannot be
    read or written is not an error either, because a search that still works is
    better than a build that stops.
    """
    path = search_cache_path(provider, parts)
    try:
        if path.exists() and time.time() - path.stat().st_mtime < SEARCH_TTL:
            results = json.loads(path.read_text(encoding="utf-8"))
            # the build manifest counts these apart from `calls`, because the
            # Pexels quota is requests an hour and a cached answer spends none
            manifest.note("stock", provider, cached=1)
            return results
    except (OSError, ValueError):
        pass

    with manifest.timed("stock", provider):
        results = fetch()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results), encoding="utf-8")
    except OSError:
        pass
    return results


def preview_still(url: str) -> Optional[bytes]:
    """A candidate's still, shrunk to what the reranker is shown.

    Module level so the rerank benchmark judges exactly these bytes. A copy of
    the resize and the JPEG quality in the benchmark would measure the model
    against pictures production never sends.
    """
    try:
        with manifest.timed("stock", "previews"):
            r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
        img.thumbnail((320, 320), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=72)
        return buf.getvalue()
    except Exception:
        return None


def pexels_search(query: str, key: str, orientation: str, want_h: int) -> List[Dict]:
    return _cached_search("pexels_video", (query, orientation, want_h),
                          lambda: _pexels_video_fetch(query, key, orientation, want_h))


def _pexels_video_fetch(query: str, key: str, orientation: str,
                        want_h: int) -> List[Dict]:
    r = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "per_page": SEARCH_RESULTS, "orientation": orientation,
                "size": "medium"},
        headers={"Authorization": key},
        timeout=TIMEOUT,
    )
    # the monthly allowance comes back on every response, spent or not
    usage.stock_headers("pexels", getattr(r, "headers", None), getattr(r, "status_code", 0))
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


def pexels_photos(query: str, key: str, orientation: str = "landscape",
                  count: int = 12) -> List[Dict]:
    """Stock photographs, for thumbnails.

    A thumbnail wants a photograph, not a frame lifted out of compressed b-roll:
    stills are shot and graded to be looked at on their own.

    `large2x` is a fixed-height render, so that resolution promise only holds
    one way round. Measured: a landscape result arrives 1880x1253 and crops to
    1280x720 with pixels to spare, while a portrait one arrives 731x1300 - over
    YouTube's 640px minimum and under its 1280px recommendation, which uploads
    without complaint and can look soft. `original` would be larger if that ever
    matters; it is not worth the download until someone says a portrait
    thumbnail looked bad.
    """
    return _cached_search("pexels_photo", (query, orientation, count),
                          lambda: _pexels_photo_fetch(query, key, orientation, count))


def _pexels_photo_fetch(query: str, key: str, orientation: str,
                        count: int) -> List[Dict]:
    r = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": count, "orientation": orientation},
        headers={"Authorization": key},
        timeout=TIMEOUT,
    )
    # the monthly allowance comes back on every response, spent or not
    usage.stock_headers("pexels", getattr(r, "headers", None), getattr(r, "status_code", 0))
    r.raise_for_status()
    out = []
    for p in r.json().get("photos", []):
        src = p.get("src") or {}
        if not src.get("large2x"):
            continue
        out.append({
            "id": str(p["id"]),
            "url": src["large2x"],
            "preview": src.get("medium") or src.get("small") or src["large2x"],
            "author": p.get("photographer", ""),
            "page": p.get("url", ""),
            "alt": (p.get("alt") or "").strip(),
        })
    return out


def pixabay_search(query: str, key: str, want_h: int) -> List[Dict]:
    return _cached_search("pixabay", (query, want_h),
                          lambda: _pixabay_fetch(query, key))


def _pixabay_fetch(query: str, key: str) -> List[Dict]:
    params = {"key": key, "q": query, "per_page": SEARCH_RESULTS, "safesearch": "true"}
    r = requests.get("https://pixabay.com/api/videos/", params=params, timeout=TIMEOUT)
    usage.stock_headers("pixabay", getattr(r, "headers", None), getattr(r, "status_code", 0))
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
                 lead_in: float = 0.25,
                 caption_cfg: Optional[CaptionConfig] = None,
                 project_root: Optional[Path] = None):
        self.cfg = cfg
        self.theme = theme or resolve_theme()
        self.theme_cfg = theme_cfg or ThemeConfig()
        self.total_scenes = total_scenes
        self.lead_in = lead_in
        # diagrams must not be drawn under wherever the captions will land
        self.caption_clear = cap.caption_top(size, caption_cfg or CaptionConfig())
        self.size = size
        self.fps = fps
        self.workdir = workdir
        self.cache = workdir / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.keys = keys
        self.log = log
        self.used: set = set()
        # a middle frame per downloaded clip, to catch one footage under two ids
        self._prints: Dict[str, Tuple[float, bytes]] = {}
        # each scene's beats and their searches, written once for the whole video
        self._beats: Dict[int, List[Dict[str, Any]]] = {}
        # A creator's clips in one scene are usually one shoot: the same dancer
        # in the same library aisle four times, the same keyboard three times in
        # ten seconds. One clip per creator a scene, and not the creator the
        # previous scene ended on.
        self._scene_creators: set = set()
        self._last_creator = ""
        # what fraction of the last scene's candidates showed the wrong
        # subject - a near-total rejection means there is no footage to find
        self._reject_ratio = 0.0
        self._filmable = True
        self._local: List[Path] = []
        if cfg.provider == "local":
            self._local = self._find_local(project_root)

    def _find_local(self, project_root: Optional[Path]) -> List[Path]:
        """Every usable file under `local_dir`, and a loud line when there is none.

        A relative `local_dir` belongs to the project, because that is where
        `config.yaml` lives and every one of them writes `assets/clips`.
        Resolving it against the process's current directory found nothing
        whenever vidsmith ran from anywhere else, and nothing is not an error
        here: every scene quietly became a generated card.
        """
        root = Path(self.cfg.local_dir)
        if not root.is_absolute() and project_root is not None:
            root = Path(project_root) / root
        found = sorted(
            p for p in root.rglob("*")
            if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT
        ) if root.is_dir() else []
        if not found:
            why = "does not exist" if not root.is_dir() else "holds no video or image files"
            self.log(f"    local: {root} {why}; every scene will be a generated card")
            stray = Path(self.cfg.local_dir)
            if (not stray.is_absolute() and project_root is not None
                    and stray.resolve() != root.resolve() and stray.is_dir()):
                self.log(f"    local: there is a {self.cfg.local_dir} under the "
                         f"current directory ({stray.resolve()}); a relative "
                         f"local_dir is read from the project, so move it there "
                         f"or give an absolute path")
        return found

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
            # the clip and the search it came from, so a finished render can
            # offer that shot's other candidates without searching blind
            ledger[f"{scene.index}:{j}"] = {
                "credit": shot.get("credit", ""), "url": shot.get("credit_url", ""),
                "id": shot.get("clip", ""), "query": shot.get("query", ""),
            }
        self._ledger_path().write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    # -- diagrams ------------------------------------------------------------ #
    def _decision_path(self) -> Path:
        # Whether a scene is drawn is an editorial decision about the narration,
        # not about the frame - so it is made once and both cuts obey it. The
        # model's filmability verdict is not stable across runs, and letting each
        # aspect ask again produced a landscape cut and a Shorts cut that showed
        # different things.
        return self.workdir.parent / "diagram_scenes.json"

    def _decisions(self) -> Dict[str, bool]:
        path = self._decision_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _decide(self, scene: Scene, drawn: bool) -> None:
        decisions = self._decisions()
        decisions[str(scene.index)] = drawn
        self._decision_path().write_text(json.dumps(decisions, indent=2),
                                         encoding="utf-8")

    def _diagram_cache_path(self) -> Path:
        # A diagram spec describes the idea, not the frame, so it is the same for
        # every aspect - it lives beside the narration rather than in the
        # per-aspect visuals directory, and a second cut costs no extra calls.
        return self.workdir.parent / "diagrams.json"

    def _diagram_spec(self, scene: Scene, brief: str) -> Optional[diagram.Spec]:
        """The drawn alternative for a line no footage can illustrate."""
        key = self.keys.get("gemini", "")
        if not key:
            return None

        cache: Dict[str, Any] = {}
        path = self._diagram_cache_path()
        if path.exists():
            try:
                cache = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cache = {}

        raw = cache.get(str(scene.index))
        if not raw:
            try:
                raw = llm.design_diagram(scene.text, brief, key, log=self.log)
            except Exception as exc:
                self.log(f"    diagram skipped ({exc})")
                return None
            cache[str(scene.index)] = raw
            path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

        spec = diagram.Spec.from_dict(raw)
        if not spec.is_drawable():
            self.log("    diagram spec was too thin to draw; keeping footage")
            return None
        return spec

    # -- vision reranking ---------------------------------------------------- #
    def _preview(self, url: str) -> Optional[bytes]:
        return preview_still(url)

    def _rerank(self, hits: List[Dict], scene: Scene, query: str,
                want: int = 1, text: Optional[str] = None,
                key: Optional[str] = None) -> List[Dict]:
        """Reorder search results by what the stills actually show.

        Stock search ranks by popularity, not by whether the clip depicts the
        line - "calendar pages turning" returns a book. Judging the preview
        stills costs one Gemini call per `rerank_pool` candidates and no video
        downloads.

        Only judged candidates are eligible, so a scene the model mostly
        rejects is short of clips, and a short scene used to be held on three
        clips for thirty seconds. So while fewer than `want` unused clips
        survive, the next candidates in search order are judged as well, up to
        `RERANK_ROUNDS` calls. The rounds are counted in the cache, so a rebuild
        never spends more than a first build did.

        `text` is the passage the clip sits under and `key` names its verdict,
        for a scene cut into beats. A verdict records the search it judged, and
        one made for another search is not reused: its clips are not these.
        """
        api_key = self.keys.get("gemini", "")
        if not (self.cfg.rerank and api_key) or len(hits) < 2:
            return hits

        line = text or scene.text
        slot = key or str(scene.index)
        self._reject_ratio = 0.0
        self._filmable = True
        by_id = {h["id"]: h for h in hits}
        cache = self._rank_cache()
        cached = cache.get(slot)
        order: List[str] = []
        reject: set = set()
        filmable, rounds = True, 0
        genre = self.cfg.genre or "any"
        # a verdict judged in another style ordered the clips for that style
        if (isinstance(cached, dict) and cached.get("order")
                and cached.get("query", query) == query
                and cached.get("genre", "any") == genre):
            order = [i for i in cached["order"] if i in by_id]
            reject = set(cached.get("reject") or [])
            filmable = cached.get("filmable", True)
            rounds = int(cached.get("rounds") or 1)
            if not order:
                rounds = 0

        blocked = {a for a in self._scene_creators | {self._last_creator} if a}
        # too dark to read: never shown to the model and never picked
        dark: set = set()

        while rounds < RERANK_ROUNDS:
            # usable means a clip a pick could actually take: not rejected, not
            # already in the video, and one per creator
            fresh = {by_id[i].get("author") or i for i in order
                     if i not in reject and i not in self.used
                     and by_id[i].get("author", "") not in blocked}
            if order and len(fresh) >= want:
                break
            images: List[bytes] = []
            keep: List[Dict] = []
            before = len(dark)
            for hit in [h for h in hits if h["id"] not in order and h["id"] not in dark]:
                if len(keep) >= max(2, self.cfg.rerank_pool):
                    break
                blob = self._preview(hit.get("preview", "")) if hit.get("preview") else None
                if blob and too_dark(blob):
                    dark.add(hit["id"])
                elif blob:
                    images.append(blob)
                    keep.append(hit)
            if len(dark) > before:
                self.log(f"    rerank: passed over {len(dark) - before} too dark to read")
            if len(images) < 2:
                break
            if order:
                self.log(f"    rerank: {len(fresh)} usable of {want} shots; "
                         f"judging {len(keep)} more")

            try:
                verdict = llm.rank_clips(line, query, images, api_key, log=self.log,
                                         genre=genre)
                ranked, rejected, judged_filmable = verdict
            except Exception as exc:
                self.log(f"    rerank skipped ({exc})")
                break

            # Only the judged candidates are eligible. Letting the unjudged tail
            # of the result list backfill would quietly reinstate exactly the
            # wrong subjects the reject pass just removed.
            batch = [keep[i]["id"] for i in ranked]
            if not order:
                filmable = judged_filmable
                if not filmable:
                    self.log("    rerank: no camera can point at this idea")
                if batch[0] != hits[0]["id"]:
                    self.log(f"    rerank: picked #{hits.index(by_id[batch[0]])} "
                             f"over the top result")
            dropped = {keep[i]["id"] for i in rejected}
            if dropped:
                self.log(f"    rerank: rejected {len(dropped)} of {len(keep)} "
                         f"as the wrong subject")
            order += batch
            reject |= dropped
            rounds += 1
            # the search and the line go in too, so a verdict is never reused for
            # another search, and the rerank benchmark can rebuild the case
            cache[slot] = {"order": order, "reject": sorted(reject),
                           "filmable": filmable, "rounds": rounds,
                           "query": query, "line": line}
            if genre != "any":
                cache[slot]["genre"] = genre
            self._rank_cache_path().write_text(json.dumps(cache, indent=2),
                                               encoding="utf-8")

        if not order:
            return [h for h in hits if h["id"] not in dark] or hits
        self._reject_ratio = len(reject) / len(order)
        self._filmable = filmable
        keepers = [by_id[i] for i in order if i not in reject]
        if not keepers:
            self.log("    rerank: everything was rejected; keeping the best of a bad set")
            keepers = [by_id[order[0]]]
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
    def _stock_batch(self, query: str, count: int, scene: Scene,
                     text: Optional[str] = None, key: Optional[str] = None,
                     need: Optional[float] = None) -> List[Dict]:
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

        hits = self._rerank(hits, scene, query, want=count, text=text, key=key)
        need = scene.duration if need is None else need
        picked: List[Dict] = []
        passed: Dict[str, int] = {}
        for hit in hits:
            # one clip per shot, and past that only while the clips taken are
            # too short between them to cover the scene without slowing down
            if (len(picked) >= count
                    and sum(p["length"] for p in picked) >= need):
                break
            if hit["id"] in self.used:
                continue
            author = hit.get("author", "")
            if author and (author in self._scene_creators or author == self._last_creator
                           or any(p["author"] == author for p in picked)):
                passed[author] = passed.get(author, 0) + 1
                continue
            dest = self.cache / f"{provider}_{hit['id']}.mp4"
            try:
                if not dest.exists():
                    _download(hit["url"], dest)
                length = ff.duration(dest)  # also rejects truncated downloads
            except Exception as exc:
                self.log(f"    download failed ({exc}); trying next result")
                dest.unlink(missing_ok=True)
                continue
            twin = self._twin(provider, hit["id"], dest, length or math.inf)
            if twin:
                self.log(f"    skipping {provider} {hit['id']}: the same footage as "
                         f"{twin}, uploaded twice")
                continue
            self.used.add(hit["id"])
            picked.append({"id": hit["id"], "path": dest, "length": length or math.inf,
                           "author": author, "page": hit.get("page", ""),
                           "query": query})
        if passed and len(picked) < count:
            self.log("    one clip per creator a scene; passed over "
                     + ", ".join(f"{n} from {a}" for a, n in passed.items()))
        return picked

    def _local_batch(self, scene: Scene, query: str, count: int) -> List[Dict]:
        """Up to `count` distinct files, all of them as good a match as the best.

        Only files tied with the best filename score are eligible, for every
        shot including the first. A lower score never wins over reusing the
        best match, because the score counts shared words, not meaning: with
        one image per pose, `byte-celebrating` scores one below `byte-pointing`
        on "[visual: byte pointing]" by sharing "byte", which is exactly as
        close as the wrong pose gets. Taking it cut a pointing scene to
        celebrating and then thinking, and it looked like a bug because it
        was one. A near miss and a contradiction score the same, so neither is
        taken; an author who wants variety adds equally named variants
        (`byte-pointing-2.png`), which tie and are used.

        Within that tier `self.used` still orders the pick, unused first, so
        equally good files spread across scenes the way stock results do. When
        the tier is smaller than `count` this returns fewer files rather than
        repeating one, and the shot plan collapses to match: a repeated still
        restarts its Ken Burns move at every cut and a repeated video replays
        its opening, both of which read as a glitch where one longer shot does
        not.

        When nothing matches at all, the whole folder ties at zero and the
        pick is as arbitrary as it always was; that is logged, because it is
        a script asking for something the folder does not have.
        """
        if not self._local:
            return []
        terms = set(keywords(query + " " + scene.text, limit=8))
        scores = {p: sum(1 for t in terms if t in p.stem.lower()) for p in self._local}
        best = max(scores.values())
        if best == 0:
            self.log(f"    local: no file name matches '{query[:40]}'; "
                     f"using whatever is least used")
        tier = sorted((p for p in self._local if scores[p] == best),
                      key=lambda p: (p.stem in self.used, p.stem))
        picked = tier[:max(1, count)]
        self.used.update(p.stem for p in picked)
        return [{"path": p, "author": "", "page": ""} for p in picked]

    # -- beats --------------------------------------------------------------- #
    def _plan(self, scene: Scene) -> List[float]:
        multi = (self.cfg.cut_on_sentences
                 and self.cfg.provider in MULTI_SHOT_PROVIDERS)
        return (plan_shots(scene, self.lead_in, self.cfg.min_shot_seconds,
                           self.cfg.max_shot_seconds)
                if multi else [scene.duration])

    def _beat_cache_path(self) -> Path:
        # Shape-independent, like the diagram decisions: every cut of a video
        # searches for the same things, so a second cut spends no request here.
        return self.workdir.parent / "beats.json"

    def _beat_key(self, scene: Scene, text: str) -> str:
        # Keyed by the words rather than by position, so a redraft cannot hand
        # one scene's search to another. A genre is part of the key, or choosing
        # one on a rebuild would be served the searches written without it;
        # `any` adds nothing, so searches cached before genres are still found.
        parts = [scene.heading or "", text]
        if self.cfg.genre not in ("", "any"):
            parts.append(self.cfg.genre)
        raw = "\x1f".join(parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _passages(self, scene: Scene, plan: Sequence[float]) -> List[Dict[str, Any]]:
        """A scene's beats, each with the words spoken across it and its search.

        A beat with no search written for it yet uses the scene's own, which is
        exactly what a build did before beats existed.
        """
        stock = self.cfg.provider in ("pexels", "pixabay")
        if not (stock and self.cfg.beat_seconds > 0):
            return [{"lo": 0, "hi": len(plan), "text": scene.text,
                     "query": scene_query(scene)}]
        ranges = plan_beats(plan, self.cfg.beat_seconds)
        words = cap.attach_punctuation(scene.words, scene.text) if scene.words else []
        edges = [0.0]
        for d in plan:
            edges.append(edges[-1] + d)
        cache = self._read_json(self._beat_cache_path())
        beats = []
        for n, (lo, hi) in enumerate(ranges):
            last = n == len(ranges) - 1
            text = " ".join(w["text"] for w in words
                            if edges[lo] <= self.lead_in + w["start"]
                            and (last or self.lead_in + w["start"] < edges[hi]))
            # a scene that is one beat is judged against its whole text, as it
            # always was, so its verdicts and benchmark cases keep their shape
            text = scene.text if len(ranges) == 1 or not text else text
            key = self._beat_key(scene, text)
            beats.append({"lo": lo, "hi": hi, "text": text, "key": key,
                          "query": (cache.get(key) or {}).get("query") or scene_query(scene)})
        return beats

    def prepare_beats(self, scenes: Sequence[Scene]) -> None:
        """Write a search for every beat of every stock scene, in one request.

        One search per scene held a thirty-second scene on a single subject
        while the narration moved through four ideas, and the subject was often
        a metaphor the narration never stated: a forest trail under a tree data
        structure, a ring of keys under an index. A viewer called the footage
        mostly unrelated. So a scene longer than `beat_seconds` is cut into
        beats, and each beat's search is written from the words spoken during
        it, with the scene's own search passed along as the writer's intent.
        """
        if self.cfg.provider not in ("pexels", "pixabay") or self.cfg.beat_seconds <= 0:
            return
        path = self._beat_cache_path()
        cache = self._read_json(path)
        pending: List[Tuple[Scene, Dict[str, Any]]] = []
        planned = []
        for scene in scenes:
            if not scene.words:
                continue
            beats = self._passages(scene, self._plan(scene))
            planned.append((scene, beats))
            asked = {b["key"] for _, b in pending}
            pending += [(scene, b) for b in beats
                        if b.get("key") and b["key"] not in cache and b["key"] not in asked]

        key = self.keys.get("gemini", "")
        if pending and key:
            try:
                queries = llm.beat_queries(
                    [{"text": b["text"], "heading": s.heading}
                     for s, b in pending], key, log=self.log,
                    genre=self.cfg.genre)
            except llm.LLMUnavailable as exc:
                self.log(f"    beat searches unavailable ({exc}); "
                         f"each scene keeps its own search")
                queries = []
            for (scene, beat), q in zip(pending, queries):
                if q:
                    cache[beat["key"]] = {"text": beat["text"], "query": q}
            if queries:
                path.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                                encoding="utf-8")
                self.log(f"    wrote {len(queries)} searches for the beats of "
                         f"{len({s.index for s, _ in pending})} scenes")

        for scene, beats in planned:
            for beat in beats:
                if beat.get("key") in cache:
                    beat["query"] = cache[beat["key"]]["query"]
            self._beats[scene.index] = beats

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _fit(self, scene: Scene, plan: Sequence[float],
             sources: List[Dict]) -> Tuple[List[float], List[Dict]]:
        """Cut `plan` to the clips in `sources`, and give back any left over."""
        lengths = [self._length(s) for s in sources]
        fitted = fit_shots(plan, lengths, self.cfg.min_shot_seconds)
        placed = [sources[i] for _, i in fitted]
        # a clip taken for coverage and then not needed goes back for a later scene
        for s in sources:
            if s not in placed and s.get("id"):
                self.used.discard(s["id"])
        if any(d > lengths[i] + 0.05 for d, i in fitted):
            self.log(f"    only {sum(lengths[i] for _, i in fitted):.1f}s of usable "
                     f"footage for {sum(plan):.1f}s of scene {scene.index}; slowing it "
                     f"to fit rather than looping")
        return [d for d, _ in fitted], placed

    def _twin(self, provider: str, clip_id: str, path: Path,
              length: float) -> Optional[str]:
        """The id of a clip this build already uses that is this footage again."""
        frame = footage_print(path, length, self.cache / f"{provider}_{clip_id}.print.png")
        if frame is None:
            return None
        mine = (length, frame)
        self._prints[clip_id] = mine
        for other, theirs in self._prints.items():
            if other != clip_id and other in self.used and same_footage(mine, theirs):
                return other
        return None

    @staticmethod
    def _length(source: Dict) -> float:
        """Seconds of footage a source holds; a still holds whatever it is asked for."""
        if source.get("length"):
            return source["length"]
        path = Path(source["path"])
        if path.suffix.lower() not in VIDEO_EXT:
            return math.inf
        try:
            return ff.duration(path) or math.inf
        except Exception:
            return math.inf

    # -- public -------------------------------------------------------------- #
    def _shot_paths(self, scene: Scene, n: int) -> List[Path]:
        return [self.workdir / f"scene_{scene.index:03d}_{j:02d}.mp4"
                for j in range(n)]

    def build(self, scene: Scene, force: bool = False) -> List[str]:
        plan = self._plan(scene)
        query = scene_query(scene)

        # ---- reuse whatever this aspect already rendered -------------------- #
        if not force:
            ledger = self._load_ledger()
            # Deterministic order, and the clips on disk must actually add up to
            # the narration slot. Trusting the filenames alone let a stale set
            # from a different plan through, and the picture ran short of the
            # speech - every cut after it drifted. Any count is accepted, because
            # a scene is cut to the clips it got rather than to its plan.
            on_disk = sorted(self.workdir.glob(f"scene_{scene.index:03d}_*.mp4"))
            paths = self._shot_paths(scene, len(on_disk))
            got = [ff.duration(p) for p in paths] if on_disk == paths else []
            if got and abs(sum(got) - scene.duration) <= 0.15:
                entries = [ledger.get(f"{scene.index}:{j}", {}) for j in range(len(paths))]
                scene.shots = [
                    {"path": str(p), "duration": d,
                     "credit": entry.get("credit", ""), "credit_url": entry.get("url", ""),
                     "query": entry.get("query", ""), "clip": entry.get("id", "")}
                    for p, d, entry in zip(paths, got, entries)
                ]
                scene.visual = scene.shots[0]["path"]
                return [s["path"] for s in scene.shots]

        # ---- source the footage --------------------------------------------- #
        spec: Optional[diagram.Spec] = None
        decided = self._decisions().get(str(scene.index))
        wants_drawing = self.cfg.diagrams and (bool(scene.diagram) or decided is True)
        if scene.diagram and not self.cfg.diagrams:
            # Said out loud, because otherwise a directive the author wrote
            # would vanish in silence and the scene would just be footage.
            self.log(f"    the script asked for a diagram but visuals.diagrams "
                     f"is off; using footage for '{query[:40]}'")

        if wants_drawing:
            # a decided or explicit diagram skips the search entirely - no point
            # paying for downloads that are going to be thrown away
            spec = self._diagram_spec(scene, scene.diagram or query)
            # Say so either way. This branch used to report only when the model
            # had decided, so a `[diagram:]` the script asked for was drawn in
            # silence and, when it could not be, fell back to footage in the
            # same silence - a log that looked identical whether the feature
            # worked or vanished.
            asked = "the script" if scene.diagram else "the model"
            if spec is None:
                self.log(f"    {asked} asked for a diagram and none could be "
                         f"drawn; using footage")
            else:
                self.log(f"    drawing a {spec.kind} diagram ({asked} asked)")

        if spec is not None:
            # An explicit [diagram:] is a decision the script already made, so
            # it is deliberately not written to diagram_scenes.json - that file
            # records what the model decided, and the script is re-read every
            # build. _drawn_ranges() therefore reads `or s.diagram` as well, so
            # a thumbnail can still consider these scenes.
            sources = []
        elif self.cfg.provider in ("pexels", "pixabay"):
            self._scene_creators = set()
            beats = self._beats.get(scene.index) or self._passages(scene, plan)
            cut: List[float] = []
            sources = []
            for b, beat in enumerate(beats):
                part = plan[beat["lo"]:beat["hi"]]
                key = str(scene.index) if len(beats) == 1 else f"{scene.index}.{b}"
                batch = self._stock_batch(beat["query"], len(part), scene,
                                          text=beat["text"], key=key, need=sum(part))
                if b == 0:
                    hopeless = (not self._filmable
                                or self._reject_ratio >= self.cfg.diagram_on_reject)
                    if self.cfg.diagrams and decided is None and hopeless:
                        # Two signals, and the first matters more: candidates can
                        # all look related to a bad query ("branching tree
                        # diagram" returns trees) while none illustrate the idea.
                        spec = self._diagram_spec(scene, query)
                        if spec is not None:
                            why = ("not filmable" if not self._filmable
                                   else f"{self._reject_ratio:.0%} rejected")
                            self.log(f"    {why}; drawing a {spec.kind} diagram")
                            for s in batch:
                                self.used.discard(s["id"])
                            break
                if not batch and beat["query"] != query:
                    # the passage's own search found nothing usable; the scene's
                    # is a subject the writer chose, which beats a card
                    batch = self._stock_batch(query, len(part), scene, text=beat["text"],
                                              key=f"{key}~", need=sum(part))
                if batch:
                    part, batch = self._fit(scene, part, batch)
                    self._scene_creators.update(s["author"] for s in batch if s["author"])
                    cut += part
                    sources += batch
                elif sources and sources[-1] is None:
                    cut[-1] += sum(part)          # one card, not a card restarting
                else:
                    cut.append(sum(part))
                    sources.append(None)
            if spec is not None:
                sources = []
            elif any(sources):
                plan = cut
                self._last_creator = next(
                    (s["author"] for s in reversed(sources) if s), "")
            else:
                sources = []
            if decided is None:
                self._decide(scene, spec is not None)
        elif self.cfg.provider == "local":
            sources = self._local_batch(scene, query, len(plan))
            if sources:
                plan, sources = self._fit(scene, plan, sources)
        else:
            sources = []

        if not sources and spec is None:
            plan = [scene.duration]

        outs = self._shot_paths(scene, len(plan))
        # stale clips from a previous, longer plan would be concatenated too
        for old in self.workdir.glob(f"scene_{scene.index:03d}_*.mp4"):
            if old not in outs:
                old.unlink(missing_ok=True)

        reveals = diagram.reveal_steps(len(plan)) if spec is not None else []
        scene.shots = []
        for j, (out, duration) in enumerate(zip(outs, plan)):
            src = sources[j] if j < len(sources) else None
            path = src["path"] if src else None

            if spec is not None:
                # the diagram builds across the scene's shots as it is explained
                frame = diagram.render(
                    spec, self.cache / f"diagram_{scene.index:03d}_{j:02d}.png",
                    self.size, self.theme, reveals[j],
                    clear_below=self.caption_clear)
                # a diagram must not drift under the viewer while they read it
                normalise_still(frame, out, duration, self.size, self.fps,
                                ken_burns=False)
            elif path and path.suffix.lower() in VIDEO_EXT:
                head = 1.0 if self._length(src) > duration + 2 else 0.0
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
                "query": src.get("query", "") if src else "",
                "clip": src.get("id", "") if src else "",
            })

        scene.visual = scene.shots[0]["path"]
        self._remember(scene)
        return [s["path"] for s in scene.shots]




def long_shot_warnings(scene: Scene, cfg: VisualConfig) -> List[str]:
    """Say, during the build, that a scene is going to sit on one clip.

    `vidsmith check` reports this too, but only after the encode, which is the
    expensive half: a 2:25 video costs about four minutes of ffmpeg. The build
    already knows - it has just logged "rejected 15 of 15 as the wrong subject"
    a line earlier - and saying nothing until the delivery is checked wastes the
    render. Two real cuts shipped this way, one of them published.

    A drawn scene is exempt. `[diagram: ...]` is one frame for the whole scene
    by design, however long it is held.

    So is a build that never went looking for footage. `cards` generates one
    frame per scene and `local` matches whatever is on disk, so "one shot for
    the whole scene" is the correct output there rather than a fault. The first
    run of this warning fired on five scenes of `projects/gil`, which is a cards
    build, and every one of them was right to be a single frame.
    """
    if scene.diagram or not scene.shots:
        return []
    if cfg.provider in ("cards", "local"):
        return []
    ceiling = max(cfg.max_shot_seconds, 0.1) * LONG_SHOT_FACTOR
    longest = max(s["duration"] for s in scene.shots)
    if longest <= ceiling:
        return []
    return [f"    warning: scene {scene.index} holds one shot for "
            f"{longest:.1f}s, past {cfg.max_shot_seconds:.1f}s; too few usable "
            f"clips came back. Split the scene or reword its [visual: ...]"]


def build_all(scenes: Sequence[Scene], cfg: VisualConfig, size: Tuple[int, int],
              fps: int, workdir: Path, keys: Dict[str, str], force: bool = False,
              log=print, theme: Optional[Theme] = None,
              theme_cfg: Optional[ThemeConfig] = None,
              lead_in: float = 0.25,
              caption_cfg: Optional[CaptionConfig] = None,
              project_root: Optional[Path] = None) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    builder = VisualBuilder(cfg, size, fps, workdir, keys, log, theme, theme_cfg,
                            total_scenes=len(scenes), lead_in=lead_in,
                            caption_cfg=caption_cfg, project_root=project_root)
    if not force:
        # Scenes whose clips are reused never pass through a search, so nothing
        # marks their footage as taken, and a scene rebuilt beside them - after
        # an edit to its words or its directive - could pick the same clip.
        builder.used |= {entry.get("id") for entry in builder._load_ledger().values()
                         if isinstance(entry, dict) and entry.get("id")}
    builder.prepare_beats(scenes)
    for scene in scenes:
        builder.build(scene, force=force)
        cuts = "+".join(f"{s['duration']:.1f}" for s in scene.shots)
        searched = " / ".join(dict.fromkeys(
            s["query"] for s in scene.shots if s.get("query"))) or scene_query(scene)
        log(f"  visual  scene {scene.index:>3}  {len(scene.shots)} shot"
            f"{'s' if len(scene.shots) != 1 else ' '}  {cuts:<22} "
            f"{searched[:90]}")
        for line in long_shot_warnings(scene, cfg):
            log(line)
