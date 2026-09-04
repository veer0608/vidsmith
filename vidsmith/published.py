"""Check a video that is already public against the delivery it came from.

`check.py` compares delivered files with each other and deliberately touches no
network, which is what makes it usable on a day the quota is gone. This is the
other half, and it is separate for that reason: every fault this project has
actually shipped landed *at YouTube*, in the gap between a correct `out/` and
what was pasted into the form.

Three real ones, all from published videos:

* a description that silently did not save, because a published video's Details
  page does not autosave the way the upload wizard does;
* photographer credits trimmed out of a description by hand, on the strength of
  the Pexels *content* licence, which is not the document that governs an API
  consumer;
* a caption track that was only YouTube's own transcription, so the exact
  edge-tts word timings the whole pipeline exists to produce were not the ones
  a viewer read.

None of those are visible from `out/`. All three are visible in one
unauthenticated GET of the watch page, so this needs no API key, no OAuth and no
quota - the same property that makes `check` worth running.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
_PLAYER = re.compile(r"ytInitialPlayerResponse\s*=\s*(\{.*?\});", re.S)
# a chapter line as YouTube parses one: a timestamp at the start of a line,
# then a label. `1:05` and `1:02:03` are both legal.
_STAMP = re.compile(r"^[ \t]*((?:\d+:)?\d{1,2}:\d{2})[ \t]+(\S.*)$", re.M)


def _seconds(stamp: str) -> float:
    parts = [float(x) for x in stamp.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]

# Where a credit line points decides what crediting it needs, and the line says
# so itself. Pexels' API Guidelines ask for the photographer by name and a link
# back; Pixabay's ask for neither, only that you do not pass the content off as
# your own. Reading this off the URL rather than off `build.json` means it still
# works on a delivery built before that file existed - which is most of them.
_PEXELS = "pexels.com"
_PIXABAY = "pixabay.com"


class Unreachable(RuntimeError):
    """The watch page could not be read. Not a finding about the video."""


def video_id(value: str) -> str:
    """Accept a bare id or any of the URL shapes people actually paste."""
    value = (value or "").strip()
    m = _ID.search(value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    raise ValueError(f"not a YouTube video id or url: {value!r}")


def fetch(vid: str, timeout: float = 25.0) -> Dict:
    """Title, description, tags and caption tracks, from the public page.

    Everything comes out of `ytInitialPlayerResponse`, the JSON the player is
    handed inline. `captionTracks` is the part worth having: it distinguishes a
    track somebody uploaded from `kind: "asr"`, which is YouTube guessing.
    """
    import requests

    try:
        r = requests.get(
            f"https://www.youtube.com/watch?v={vid}",
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout)
        r.raise_for_status()
    except Exception as exc:                       # network, DNS, 404, blocked
        raise Unreachable(f"could not read the watch page for {vid}: {exc}")

    m = _PLAYER.search(r.text)
    if not m:
        raise Unreachable(
            f"the page for {vid} carried no player data; YouTube may have served "
            "a consent or bot check rather than the video")
    try:
        data = json.loads(m.group(1))
    except ValueError as exc:
        raise Unreachable(f"the player data for {vid} did not parse: {exc}")

    details = data.get("videoDetails") or {}
    tracks = ((data.get("captions") or {})
              .get("playerCaptionsTracklistRenderer") or {}).get("captionTracks") or []
    return {
        "id": vid,
        "title": details.get("title") or "",
        "description": details.get("shortDescription") or "",
        "tags": list(details.get("keywords") or []),
        # a track with no `kind` was uploaded; `asr` is YouTube's transcription
        "uploaded_captions": [t.get("languageCode") for t in tracks
                              if t.get("kind") != "asr"],
        "asr_captions": [t.get("languageCode") for t in tracks
                         if t.get("kind") == "asr"],
    }


def _credit_lines(out: Path) -> List[str]:
    """The widescreen ledger, which is the cut that gets published."""
    ledger = out / "credits.txt"
    if not ledger.exists():
        others = sorted(out.glob("credits*.txt"))
        if not others:
            return []
        ledger = others[0]
    return [ln.strip() for ln in ledger.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _creator(line: str) -> str:
    """`Engin_Akyurt - https://...` and `Thumbnail: Bakr Magrabi - https://...`."""
    name = line.split(" - ")[0].strip()
    for prefix in ("Thumbnail:", "Footage from"):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
    return name


def attribution(live_description: str, out: Path) -> List[str]:
    """Credits owed by the delivery, against the description actually published.

    A licence condition is met by the text a viewer can see, not by a file in
    `out/`, and this is the check `vidsmith check` cannot make. The rule differs
    per source, so it is applied per line rather than in bulk:

    * a Pexels line needs the photographer named and a link back to pexels.com;
    * a Pixabay line needs neither, so long as Pixabay is named as the source.

    Getting this wrong in the lenient direction is how eleven and thirteen
    photographers were trimmed out of two published descriptions.
    """
    problems: List[str] = []
    lines = _credit_lines(out)
    if not lines:
        return problems
    lowered = live_description.lower()

    wants_pexels = any(_PEXELS in ln.lower() for ln in lines)
    wants_pixabay = any(_PIXABAY in ln.lower() for ln in lines)
    if wants_pexels and _PEXELS not in lowered:
        problems.append(
            "the published description has no link back to pexels.com, which the "
            "Pexels API guidelines ask for")
    if wants_pixabay and "pixabay" not in lowered:
        problems.append(
            "the published description does not name Pixabay as the footage source")

    for line in lines:
        low = line.lower()
        if _PEXELS not in low:
            continue                  # Pixabay lines: the source line is enough
        if " - " not in line:
            # `Footage from Pexels (https://www.pexels.com)` is the header, not a
            # person. Demanding it by name reported a contributor called
            # "Pexels (https://www.pexels.com)" that no description would ever
            # carry, and it would have fired on every Pexels build.
            continue
        name = _creator(line)
        if name and name.lower() not in lowered:
            problems.append(
                f"the Pexels contributor '{name}' is credited in credits.txt but "
                "not in the published description; the API guidelines ask for the "
                "photographer by name")
    return problems


def chapters(live_description: str, built: int) -> List[str]:
    """Whether the published chapter list will work, not whether it matches.

    The first rule here asked that every label the build wrote appear in the
    published description. Run across four real videos it reported six missing
    chapters and every one was wrong: the labels had been reworded by hand
    ("Runs on Your Hardware" became "Local Hardware") and the chapters were
    working perfectly. Rewording is the normal thing to do to a chapter label,
    and a check that fires on it stops being read - which is worse than not
    having it, because the credit findings beside it are real.

    What YouTube actually requires is structural, so that is what is checked:
    at least three entries, the first at 0:00, and ascending. Break one of those
    and the video shows no chapters at all, with no error anywhere.
    """
    if built < 1:
        return []
    found = _STAMP.findall(live_description)
    if not found:
        return [f"the build wrote {built} chapters but the published description "
                "has no chapter list at all"]

    times = [_seconds(stamp) for stamp, _label in found]
    problems: List[str] = []
    if times[0] != 0:
        problems.append(
            f"the published chapter list starts at {found[0][0]} rather than "
            "0:00, so YouTube will ignore every chapter")
    if len(times) < 3:
        problems.append(
            f"the published description has {len(times)} chapter line(s); "
            "YouTube needs at least three or it shows none")
    if times != sorted(times):
        problems.append("the published chapters are out of order, so YouTube "
                        "will ignore every one of them")
    return problems


def check_published(out_dir: Path, vid: str,
                    live: Optional[Dict] = None) -> List[str]:
    """Everything wrong with the published video, as plain sentences.

    `live` is injectable so this is testable without the network, which matters:
    a checker whose tests need YouTube to be up is a checker that gets skipped.
    """
    out = Path(out_dir)
    vid = video_id(vid)
    live = live if live is not None else fetch(vid)
    problems: List[str] = []

    if not (live.get("description") or "").strip():
        # the exact shape of the fault that already happened once: title saved,
        # description did not, and nothing said so
        problems.append("the published video has no description at all")
        return problems

    problems.extend(attribution(live["description"], out))

    meta_path = out / "youtube.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            meta = {}
        title = (meta.get("title") or "").strip()
        # a hand-edited title is normal - this one had " made with vidsmith"
        # appended after upload - so only a title that dropped the build's
        # entirely is worth reporting
        if title and title.lower() not in (live.get("title") or "").lower():
            problems.append(
                f"the published title is '{live.get('title')}', which does not "
                f"contain the built title '{title}'")
        problems.extend(chapters(live["description"],
                                 len(meta.get("chapters") or [])))
        if (meta.get("tags") or []) and not live.get("tags"):
            problems.append("the build wrote tags but the published video has none")

    # the whole pipeline exists to produce exact word timings; shipping only
    # YouTube's transcription throws that away without saying so
    if not live.get("uploaded_captions"):
        if live.get("asr_captions"):
            problems.append(
                "the only caption track is YouTube's automatic one; the exact "
                "timings in captions.srt were never uploaded")
        else:
            problems.append("the published video has no caption track")

    return problems


# --------------------------------------------------------------------------- #
# the receipt
# --------------------------------------------------------------------------- #
RECEIPT = "published.json"

# What a publish is actually a promise about. The description is the file that
# gets pasted, and the credits are the licence condition inside it; if either
# has moved since the video was verified, the copy on YouTube is stale.
WITNESSED = ("description.txt", "credits.txt")


def digest(path: Path) -> str:
    """Twelve hex characters of the file, or "" when it is not there."""
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def record(out_dir: Path, vid: str) -> Path:
    """Write down that this delivery was verified against this video.

    `check --published` can only read a *public* video, so it cannot help while
    a draft is private - which is exactly when the description is being pasted
    and is easiest to get wrong. This is the offline half of that: what was
    verified, and what the files looked like at the time.
    """
    out = Path(out_dir)
    body = {
        "video_id": video_id(vid),
        "checked": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": {name: digest(out / name) for name in WITNESSED},
    }
    path = out / RECEIPT
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path
