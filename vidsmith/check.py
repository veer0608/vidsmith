"""Check a finished build before it is published.

Everything here compares one delivered file against another. That is deliberate:
the faults this catches were all found by reading outputs after a run, never by
reading the code that wrote them. A thumbnail was refreshed and the description
beside it went on crediting the photographer that had been dropped; a refresh
wrote `untitled.jpg` next to correctly named files and reported success. Both
looked right in isolation and wrong side by side.

Nothing here calls a model or the network, so it costs nothing and works on a
day the quota is gone.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from . import ffmpeg_util as ff
from .config import ASPECTS, LONG_SHOT_FACTOR, aspect_tag

_END = re.compile(r"--> (\d+:\d+:\d+[,.]\d+)")

# A shot this long was not chosen, it was survived. `max_shot_seconds` defaults
# to 5.5, so anything near double that means `plan_shots` had fewer usable clips
# than the slot needed and `collapse()` merged them to keep the total exact.
# Measured case: a 9:16 cut shipped one 16.8s shot after the reranker logged
# "rejected 15 of 15 as the wrong subject", and check called the delivery
# consistent because nothing here looked at the edit.
LONG_SHOT_SECONDS = 9.0


def settings(out: Path) -> dict:
    """What the build said about itself, or {} when it did not say.

    Written by `pipeline.write_build_info()`. Everything here is optional: a
    delivery from before this existed, or one assembled by hand, is still
    checked on the inference that predates it.
    """
    path = out / "build.json"
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return body if isinstance(body, dict) else {}


def frozen_shots(build: Path, aspect: str, scenes: List[dict],
                 cfg: Optional[dict] = None) -> List[str]:
    """Scenes whose picture sits on one clip for far too long.

    This is the one check that reads `build/` rather than `out/`, because the
    fault is invisible in the delivered file: Ken Burns is still panning and the
    karaoke captions still change every word, so neither `freezedetect` nor scene
    detection sees anything wrong with sixteen seconds of the same clip.

    Shot counts come from the per-aspect `credits.json` and durations from the
    shared `scenes.json`, which is safe in that direction: a scene's length is
    narration, so it is shape-independent, while its shot list is not. Both are
    already on disk, so this costs no ffprobe calls and stays usable on a day the
    quota is gone.
    """
    cfg = cfg or {}
    # When the build said which provider it used, believe it rather than reading
    # the ledger's tea leaves. `cards` and `local` never search, so one frame per
    # scene is their correct output.
    if cfg.get("provider") in ("cards", "local"):
        return []
    ceiling = float(cfg.get("max_shot_seconds") or 0.0) * LONG_SHOT_FACTOR \
        or LONG_SHOT_SECONDS

    ledger = build / f"visuals{aspect_tag(aspect)}" / "credits.json"
    if not ledger.exists():
        return []                     # nothing went looking for footage
    try:
        credits = json.loads(ledger.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []

    # Every entry is a shot, credited or not, because that is what the count has
    # to mean. What decides whether a scene is judged at all is whether anything
    # in it was footage: a cards build writes the ledger too, with an empty
    # credit per scene, and one generated frame held for a whole scene is the
    # correct output rather than a fault. Reading counts off it reported every
    # scene of `projects/gil` as frozen.
    #
    # Skipping the uncredited *entries* instead was the first fix and it was
    # wrong in the other direction: `projects/indexes` mixes a card and a clip
    # inside one scene, so dropping the card left a two-shot scene looking like
    # a single 10.1s hold. Count the shots, filter the scenes.
    counts: dict = {}
    footage: set = set()
    for key, entry in credits.items():
        scene_index = str(key).split(":")[0]
        counts[scene_index] = counts.get(scene_index, 0) + 1
        if (entry or {}).get("credit"):
            footage.add(scene_index)

    problems = []
    for scene in scenes:
        # a drawn scene is one frame on purpose, however long it is held
        if scene.get("diagram"):
            continue
        index = str(scene.get("index"))
        if index not in footage:
            continue                  # generated frames, not a search that failed
        shots = counts.get(index, 0)
        duration = float(scene.get("duration") or 0.0)
        if not shots or not duration:
            continue
        longest = duration / shots
        if longest > ceiling:
            heading = scene.get("heading") or scene.get("text", "")[:40]
            problems.append(
                f"the {aspect} cut holds one shot for {longest:.1f}s on scene "
                f"{scene.get('index')} ('{heading}'); the reranker probably left "
                f"too few usable clips")
    return problems


def seconds(stamp: str) -> float:
    """Accept both a chapter's `1:23` and an SRT's `00:01:23,400`."""
    parts = [float(x) for x in stamp.replace(",", ".").split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def delivered(out: Path) -> List[tuple]:
    """Every delivered cut as `(aspect, path)`, widescreen first.

    Each file is matched against `aspect_tag()` rather than found by sorting
    names, because 16:9 carries no suffix at all. Taking the first name that
    was not a short handed back `a-1x1.mp4`, which sorts before `a.mp4`, so the
    square cut was checked as though it were the widescreen one and the real
    16:9 cut was checked by nothing: not its runtime, not its captions, not its
    thumbnail. Same empty-tag fault that once had `vidsmith thumbs` sampling
    the wrong cut, and the same fix, which is to ask `config` what a shape is
    called instead of spelling it here.
    """
    suffixed = [(a, aspect_tag(a)) for a in ASPECTS if aspect_tag(a)]
    cuts = []
    for mp4 in sorted(out.glob("*.mp4")):
        aspect = next((a for a, tag in suffixed if mp4.stem.endswith(tag)), "16:9")
        cuts.append((aspect, mp4))
    cuts.sort(key=lambda pair: aspect_tag(pair[0]) != "")
    return cuts


def credits_published(out: Path) -> List[str]:
    """Every cut's attribution, against the description that cut will publish.

    Attribution is a licence condition and `description<tag>.txt` is the file
    that gets pasted, so a credit living only in `credits<tag>.txt` has not been
    given. Each ledger is read against its own description: checking all of them
    against one file let a 9:16 credit pass because the 16:9 description happened
    to name the same photographer.

    The "Footage from ..." line counts. It is the prominent link back that the
    Pexels API guidelines ask for, not a heading over the real credits.
    """
    problems: List[str] = []
    for ledger in sorted(out.glob("credits*.txt")):
        tag = ledger.stem[len("credits"):]
        desc_file = out / f"description{tag}.txt"
        if not desc_file.exists():
            problems.append(f"{ledger.name} has no {desc_file.name} beside it, "
                            "so its credits would not be published")
            continue
        published = desc_file.read_text(encoding="utf-8")
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and line not in published:
                problems.append(
                    f"a credit in {ledger.name} is not in {desc_file.name}, so "
                    f"it would not be published: {line[:60]}")
                break                      # one per cut is enough to act on
    return problems


# A scene the model swapped for a drawing is not automatically wrong - one
# unfilmable idea in a video is ordinary. Two is a pattern, and a pattern usually
# means the queries are bad rather than the subjects being unfilmable. The share
# matters too, so a long video is not judged by the same count as a short one.
SUBSTITUTION_FLOOR = 2
SUBSTITUTION_SHARE = 0.2


def substituted_scenes(cfg: dict) -> List[str]:
    """Scenes the model swapped for a drawing when footage was asked for.

    Only the model's substitutions count. A `[diagram: ...]` in the script is a
    decision the writer already made and reporting it would be noise - which is
    the difference between this and the first version of the rule, which counted
    every generated frame and stayed silent on the build that prompted it.

    That build is the case to keep in mind: a promo opened on fifteen seconds of
    a static card because scene 0 was substituted, and the only record was a
    build log in a terminal that had been closed. `check` passed it.

    `cards` and `local` are exempt, as with the frozen-shot rule: every scene is
    a generated frame there and that is what they are for. A dead provider key is
    exempt for the same reason and is visible anyway - the build falls back to
    `cards` and `build.json` says so.
    """
    if cfg.get("provider") in ("cards", "local"):
        return []
    total = int(cfg.get("scenes") or 0)
    swapped = list(cfg.get("substituted") or [])
    if total < 1 or len(swapped) < SUBSTITUTION_FLOOR:
        return []
    if len(swapped) < total * SUBSTITUTION_SHARE:
        return []
    return [f"the model replaced {len(swapped)} of {total} scenes with drawings "
            f"rather than footage (scenes {', '.join(str(i) for i in swapped)}); "
            f"the searches for those are probably not filmable. Rebuild with "
            f"--force diagrams,visuals,render to judge them again"]


def publish_drift(out: Path) -> List[str]:
    """The delivery was rebuilt after it was verified against a live video.

    `check --published` reads the watch page, so it can only see a *public*
    video - and the moment a stale description is most likely is while a draft
    is still private and being pasted into the form. This is the half that works
    then, and offline: `published.py` leaves a receipt naming the video and what
    the files looked like, and this notices when they have moved since.

    The case it was written for: a video was uploaded and its description
    pasted, the build was then rerun to replace one scene, and the new footage
    carried two photographers the pasted description does not name. Every local
    file agreed with every other, so `check` said the delivery was consistent -
    correctly, and uselessly, because the wrong copy was on YouTube.

    Reads only files already on disk, so it costs nothing and keeps this module
    free of the network.
    """
    path = out / "published.json"
    if not path.is_file():
        return []                     # never published from here; nothing to say
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(body, dict):
        return []

    from .published import digest

    vid = body.get("video_id") or "the published video"
    when = (body.get("checked") or "")[:10]
    moved = []
    for name, was in (body.get("files") or {}).items():
        now = digest(out / name)
        if was and now and now != was:
            moved.append(name)
        elif was and not now:
            moved.append(f"{name} (now missing)")

    if not moved:
        return []
    return [f"{', '.join(moved)} changed since this delivery was checked against "
            f"https://youtu.be/{vid}{' on ' + when if when else ''}, so the "
            f"description published there is probably stale; re-paste it and run "
            f"check --published {vid}"]


def check(out_dir: Path) -> List[str]:
    """Everything wrong with this delivery, as plain sentences."""
    out = Path(out_dir)
    problems: List[str] = []

    # first, because it is the one finding that does not depend on the delivery
    # existing: a receipt outlives the files it witnessed, and "the description
    # on YouTube is stale" stays true even when out/ has been emptied
    problems.extend(publish_drift(out))

    cuts = delivered(out)
    if not cuts:
        return problems + ["no mp4 in out/; nothing has been delivered"]

    # `delivered()` sorts widescreen first, so this is the 16:9 cut whenever one
    # exists. It is only the *reference* for runtime and chapters, not a
    # requirement: a Shorts-only project is a real thing - `projects/promo-short`
    # is one - and demanding a landscape cut meant every check of it returned
    # "nothing has been delivered" while a finished vertical video sat in out/.
    # Its captions, thumbnail, credits and chapters went unexamined for as long
    # as it existed, which is the same shape of fault as the empty tag below:
    # a shape assumption dressed up as a delivery check.
    reference_aspect, reference = cuts[0]
    runtime = ff.duration(reference)

    # every cut is the same edit at a different size, so any disagreement here
    # means one of them was rebuilt and the others were not
    for aspect, cut in cuts[1:]:
        if abs(runtime - ff.duration(cut)) > 1.0:
            problems.append(
                f"the two cuts disagree on length: {runtime:.0f}s and "
                f"{ff.duration(cut):.0f}s ({cut.name})")

    # the edit itself: a scene that ran out of footage and sat on one clip
    build = out.parent / "build"
    scenes_json = build / "scenes.json"
    if scenes_json.exists():
        try:
            scenes = json.loads(scenes_json.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            scenes = []
        cfg = settings(out)
        for aspect, _cut in cuts:
            problems.extend(frozen_shots(build, aspect, scenes, cfg))
        problems.extend(substituted_scenes(cfg))

    meta_path = out / "youtube.json"
    desc_path = out / "description.txt"
    desc = desc_path.read_text(encoding="utf-8") if desc_path.exists() else ""
    if not desc:
        problems.append("description.txt is missing; run vidsmith meta")

    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        chapters = meta.get("chapters") or []
        # YouTube drops the entire list rather than the offending line
        if chapters and chapters[0].get("time") != "0:00":
            problems.append("the first chapter is not at 0:00, so YouTube will "
                            "ignore every chapter")
        for c in chapters:
            if seconds(c.get("time", "0:00")) >= runtime:
                problems.append(f"chapter '{c.get('label')}' is at {c.get('time')}, "
                                f"past the {runtime:.0f}s runtime")
            if desc and c.get("label") and c["label"] not in desc:
                problems.append(f"chapter '{c['label']}' is missing from "
                                "description.txt")

    for aspect, cut in cuts:
        srt = out / f"captions{aspect_tag(aspect)}.srt"
        if not srt.exists():
            problems.append(f"{srt.name} is missing")
            continue
        ends = _END.findall(srt.read_text(encoding="utf-8"))
        if ends and seconds(ends[-1]) > ff.duration(cut) + 0.5:
            problems.append(f"{srt.name} runs {seconds(ends[-1]):.1f}s, past the "
                            f"{ff.duration(cut):.1f}s of {cut.name}")

    for aspect, video in cuts:
        frame_w, frame_h = ASPECTS[aspect]
        portrait = frame_h > frame_w          # 1:1 is neither, and wants a wide still
        jpg = out / f"{video.stem}.jpg"
        if not jpg.exists():
            problems.append(f"{jpg.name} is missing, so {video.name} has no thumbnail")
            continue
        try:
            from PIL import Image

            w, h = Image.open(jpg).size
        except Exception as exc:                  # a corrupt jpg is the finding
            problems.append(f"{jpg.name} could not be read ({exc})")
            continue
        if portrait and w >= h:
            problems.append(f"{jpg.name} is {w}x{h}, landscape, but names a "
                            "vertical cut")
        if not portrait and h > w:
            problems.append(f"{jpg.name} is {w}x{h}, portrait, but names the "
                            f"{aspect} cut")

    # attribution is a licence condition, and description.txt is what gets
    # published: a credit that lives only in credits.txt has not been given
    problems.extend(credits_published(out))

    # a thumbnail nothing delivers, left by a refresh that resolved the wrong name
    named = {p.stem for _, p in cuts}
    for jpg in sorted(out.glob("*.jpg")):
        if jpg.stem not in named:
            problems.append(f"{jpg.name} matches no delivered cut; a refresh "
                            "probably wrote it under the wrong title")

    return problems
