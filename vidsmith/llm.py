"""Optional Gemini calls: b-roll search terms, and the YouTube upload metadata.

Everything here is optional. Without a key the pipeline still runs - scene
queries fall back to keyword extraction and the metadata step is skipped.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import requests

from . import genres
from . import manifest
from . import usage
from .config import VoiceConfig
from .script_parser import DIRECTIVE, HEADING, NOTE, WPS, Scene

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Pinned, not an alias. `gemini-flash-lite-latest` worked, which is the problem:
# an alias repoints to whatever is newest, and newest carries the smallest
# free-tier allowance. The sibling recut repo lost a run to exactly this when
# `gemini-flash-latest` became a model capped at 20 requests per day, and it
# resolves silently, so the first sign is a build failing on quota it should
# have had.
#
# It also decides who this competes with. Free-tier quota is per model, and this
# machine runs two other projects against Gemini: recut on gemini-3.1-flash-lite
# and gemini-3-flash-preview, reruns on gemini-3.7-flash, -3.6 and -3.5. An alias
# can repoint onto any of them without a commit. gemini-3.5-flash-lite is what
# the alias resolved to on 2026-09-08 and belongs to nothing else here.
#
# gemini-2.5-flash 404s on free keys, which is why the alias was used originally.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMUnavailable(RuntimeError):
    pass


def _quota_violation(r) -> Dict[str, Any]:
    """The quota this 429 actually broke, empty if the body does not say.

    Google returns the specifics in a QuotaFailure detail: a quotaId naming the
    window, the model it applies to, and the ceiling. That is worth reading -
    "out of quota" and "out of quota until the next minute" call for opposite
    responses, and the status code is identical for both.
    """
    try:
        details = r.json()["error"]["details"]
    except Exception:                      # not JSON, not an error body, no .json
        return {}
    for detail in details:
        if str(detail.get("@type", "")).endswith("QuotaFailure"):
            violations = detail.get("violations") or [{}]
            return violations[0]
    return {}


def _spent_message(violation: Dict[str, Any]) -> str:
    quota_id = violation.get("quotaId", "")
    limit = violation.get("quotaValue")
    model = (violation.get("quotaDimensions") or {}).get("model")
    if not (limit and model):
        return "the Gemini free tier is out of quota for now; it resets daily"
    unit = "requests" if "Requests" in quota_id else "tokens"
    # naming the number and the model is the difference between "wait" and
    # "switch models": the budget is per model, so another one may be untouched
    return (f"the Gemini free tier is spent: {limit} {unit} a day for {model}. "
            "It resets at midnight Pacific.")


def _retry_after(r) -> float:
    """The wait Google advertises, clamped so a bad value cannot hang a build.

    Worth reading only once the quotaId says the wait can help: the loop's own
    backoff tops out at eight seconds, which is not enough for a limit measured
    per minute.
    """
    try:
        details = r.json()["error"]["details"]
    except Exception:
        return 0.0
    for detail in details:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            try:
                return min(75.0, float(str(detail["retryDelay"]).rstrip("s")))
            except (KeyError, TypeError, ValueError):
                return 0.0
    return 0.0


def _refuse_if_spent(r) -> float:
    """Refuse a daily cap. Let a per-minute one be retried, and say how long.

    Not every RESOURCE_EXHAUSTED is the day. The same status covers the
    per-minute burst limit, which really does clear in seconds, and the daily
    request cap, which only tomorrow clears. Treating both as fatal kills a
    build over a blip; retrying both spends what little is left of a budget
    that is already gone.

    The body says which, in the quotaId. The RetryInfo next to it does not: on
    a spent daily cap it advertised 8s, then 56s, then 56s, then 52s, and every
    one of those waits was honoured and still met a 429.

    This lives outside the request loops because it was first added to the text
    path and not the vision one, and the vision path went on retrying three
    times per thumbnail.
    """
    if r.status_code != 429 or "RESOURCE_EXHAUSTED" not in r.text:
        return 0.0
    violation = _quota_violation(r)
    quota_id = violation.get("quotaId", "")
    if "PerMinute" in quota_id or "PerSecond" in quota_id:
        return _retry_after(r)             # RETRY_STATUS then backs off and retries
    raise QuotaExhausted(_spent_message(violation))


class QuotaExhausted(LLMUnavailable):
    """The key is fine, the day's allowance is not. Distinct because it is the
    one failure that waiting fixes, and the only sensible advice differs."""


class GaveUp(LLMUnavailable):
    """Every retry met a failure that might have cleared: a dropped connection,
    a 5xx, a per-minute limit. The model never judged anything.

    Callers that degrade still catch it as LLMUnavailable. It is distinct for
    the rerank benchmark, which must not score the network as the model's
    answer. Matching on the message text would be a second copy of this
    wording that nothing keeps in step.
    """


def _network_failure(exc: Exception, api_key: str) -> str:
    """A connection reset, DNS failure or timeout, fit to repeat back.

    Every optional feature degrades by catching LLMUnavailable, so a requests
    exception escaping the loop skips all of them: a bench run died on a reset
    after 122 good calls. The message has to lose the key on the way out,
    because the key travels in the query string and urllib3 quotes the whole
    URL - a DNS failure reads "Max retries exceeded with url: ...?key=AIza...",
    and this text goes into a 502 body, the build log and bench results.
    """
    said = f"the network failed: {type(exc).__name__}: {exc}"
    return said.replace(api_key, "<key>") if api_key else said


# Which helper a request belongs to, so a build manifest says "rank_clips made 14
# requests" rather than "Gemini made 40". Read off the calling frame rather than
# passed in, so none of the seven helpers can forget to pass it.
_CALLER: ContextVar[str] = ContextVar("vidsmith_llm_caller", default="unknown")


@contextmanager
def _counted(caller: str, model: str) -> Iterator[None]:
    """Time one model call for the build manifest, and count it if it fails."""
    token = _CALLER.set(caller)
    manifest.collect("models", model)
    try:
        with manifest.timed("model", caller):
            yield
    except LLMUnavailable as exc:
        manifest.note("model", caller, failed=1, gave_up=int(isinstance(exc, GaveUp)))
        raise
    finally:
        _CALLER.reset(token)


def generate(prompt: str, api_key: str, model: str = DEFAULT_MODEL,
             temperature: float = 0.4, retries: int = 4, log=None) -> str:
    with _counted(sys._getframe(1).f_code.co_name, model):
        return _generate(prompt, api_key, model, temperature, retries, log)


def _generate(prompt: str, api_key: str, model: str, temperature: float,
              retries: int, log) -> str:
    if not api_key:
        raise LLMUnavailable("no GEMINI_API_KEY")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096},
    }
    last = ""
    for attempt in range(retries):
        manifest.note("model", _CALLER.get(), requests=1)
        # every attempt spends the day's budget, so every attempt is counted
        usage.gemini_request(model)
        try:
            r = requests.post(
                ENDPOINT.format(model=model),
                params={"key": api_key},
                json=body,
                timeout=120,
            )
        except requests.RequestException as exc:
            # as transient as a 503, so it gets the same backoff
            last = _network_failure(exc, api_key)
            manifest.note("model", _CALLER.get(), retries=1, waited_seconds=2 ** attempt)
            time.sleep(2 ** attempt)
            continue
        try:
            pause = _refuse_if_spent(r)
        except QuotaExhausted as exc:
            usage.gemini_spent(model, str(exc), _quota_violation(r).get("quotaValue"))
            raise
        if r.status_code in RETRY_STATUS:
            last = f"HTTP {r.status_code}: {r.text[:180]}"
            wait = max(pause, 2 ** attempt)
            # a minute of silence is indistinguishable from a hang, and this one
            # is deliberate, so say whose limit is being waited out
            if log and wait > 8:
                log(f"    waiting {wait:.0f}s: {model} is over its rate limit")
            manifest.note("model", _CALLER.get(), retries=1, waited_seconds=wait)
            time.sleep(wait)
            continue
        if r.status_code != 200:
            raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            raise LLMUnavailable(f"unexpected response: {json.dumps(data)[:300]}")
        return "".join(p.get("text", "") for p in parts).strip()
    raise GaveUp(f"gave up after {retries} attempts - {last}")


def generate_vision(prompt: str, images: Sequence[bytes], api_key: str,
                    model: str = DEFAULT_MODEL, temperature: float = 0.1,
                    retries: int = 3, log=None) -> str:
    """Same call as generate(), with JPEG stills attached before the prompt."""
    with _counted(sys._getframe(1).f_code.co_name, model):
        return _generate_vision(prompt, images, api_key, model, temperature, retries, log)


def _generate_vision(prompt: str, images: Sequence[bytes], api_key: str, model: str,
                     temperature: float, retries: int, log) -> str:
    if not api_key:
        raise LLMUnavailable("no GEMINI_API_KEY")
    parts: List[Dict[str, Any]] = []
    for blob in images:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(blob).decode("ascii"),
            }
        })
    parts.append({"text": prompt})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 512},
    }
    last = ""
    for attempt in range(retries):
        manifest.note("model", _CALLER.get(), requests=1)
        # every attempt spends the day's budget, so every attempt is counted
        usage.gemini_request(model)
        try:
            r = requests.post(ENDPOINT.format(model=model), params={"key": api_key},
                              json=body, timeout=180)
        except requests.RequestException as exc:
            # as transient as a 503, so it gets the same backoff
            last = _network_failure(exc, api_key)
            manifest.note("model", _CALLER.get(), retries=1, waited_seconds=2 ** attempt)
            time.sleep(2 ** attempt)
            continue
        try:
            pause = _refuse_if_spent(r)
        except QuotaExhausted as exc:
            usage.gemini_spent(model, str(exc), _quota_violation(r).get("quotaValue"))
            raise
        if r.status_code in RETRY_STATUS:
            last = f"HTTP {r.status_code}: {r.text[:180]}"
            wait = max(pause, 2 ** attempt)
            # a minute of silence is indistinguishable from a hang, and this one
            # is deliberate, so say whose limit is being waited out
            if log and wait > 8:
                log(f"    waiting {wait:.0f}s: {model} is over its rate limit")
            manifest.note("model", _CALLER.get(), retries=1, waited_seconds=wait)
            time.sleep(wait)
            continue
        if r.status_code != 200:
            raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            out = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            raise LLMUnavailable(f"unexpected response: {json.dumps(data)[:300]}")
        return "".join(p.get("text", "") for p in out).strip()
    raise GaveUp(f"gave up after {retries} attempts - {last}")


RERANK_PROMPT = """You are choosing stock B-roll to sit behind one line of narration.

The {n} images above are preview stills from {n} candidate clips, in order,
numbered 0 to {last}.

NARRATION: {line}
INTENDED SHOT: {query}

Rank the clips best-first for this line. Judge only what is visible:
- Does the still literally show the intended subject? A clip of the wrong object
  is useless no matter how attractive it is.
- Is the subject clear and prominent rather than incidental?
- Would it read at a glance, at speed, behind captions?
{style}
Then decide which are unusable. A clip is unusable when it shows the wrong
subject - not merely a weaker version of the right one. A book is not a
calendar; a laptop is not a card terminal. Be strict about subject and lenient
about style: an unremarkable shot of the right thing beats a beautiful shot of
the wrong thing. A screen that is plain green or blue, left blank for a picture
to be keyed in later, is unusable too: it looks unfinished behind captions.
So is a clip whose main content is words or a logo - a title card, a "subscribe"
or "free" graphic, an advert - because it puts somebody else's message on
screen under the narration.

Finally, judge whether stock footage can depict this line at all. Some ideas
have no footage anywhere - a B-tree, a hash collision, an API contract. A
literal photograph of a tree does not illustrate a tree data structure. If the
line is about an abstract or technical construct that no camera can point at,
say so, even when the candidates look superficially related.

Return ONLY a JSON object:
{{"ranked": [image numbers, best first, every number once],
  "reject": [image numbers showing the wrong subject, may be empty],
  "filmable": true or false}}"""


def _indices(values: Any, limit: int) -> List[int]:
    out: List[int] = []
    for value in values if isinstance(values, list) else []:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < limit and idx not in out:
            out.append(idx)
    return out


def rank_clips(line: str, query: str, images: Sequence[bytes], api_key: str,
               model: str = DEFAULT_MODEL, log=None,
               genre: str = "any") -> Tuple[List[int], List[int], bool]:
    """(order, rejected, filmable) over `images`.

    `filmable` is False when no camera can point at the idea - that is the cue to
    draw a diagram rather than keep searching for footage that does not exist.
    """
    if not images or len(images) < 2:
        return [], [], True
    prompt = RERANK_PROMPT.format(n=len(images), last=len(images) - 1,
                                  line=line.strip(), query=query.strip(),
                                  style=genres.rerank_block(genre, line))
    raw = generate_vision(prompt, images, api_key, model, log=log)
    verdict = _json_block(raw)

    if isinstance(verdict, list):          # tolerate a bare ranking
        verdict = {"ranked": verdict, "reject": [], "filmable": True}
    if not isinstance(verdict, dict):
        raise ValueError("model did not return a ranking")

    order = _indices(verdict.get("ranked"), len(images))
    # anything the model left out keeps its original relative position
    order += [i for i in range(len(images)) if i not in order]
    rejected = _indices(verdict.get("reject"), len(images))
    return order, rejected, verdict.get("filmable", True) is not False


DASHES = "—–"


def undash(text: str) -> str:
    """Strip em and en dashes out of anything a model wrote.

    They are a tell, and the narration one is worse than cosmetic: the voice
    reads a dash as a pause the script did not ask for, and the caption grouper
    treats it as a clause break. Instructing the model is not enough on its own,
    so the output is repaired as well.
    """
    # A range between digits wants a word, not a comma, because a comma there is
    # heard as a thousands separator. It keys off the dash itself and runs first:
    # repairing a digit-comma afterwards cannot tell a comma this function just
    # made from one the writer typed, and it turned "20,000 requests" into
    # "20 to 000 requests" in every description that quoted a round number.
    out = re.sub(r"(\d)\s*[" + DASHES + r"]\s*(?=\d)", r"\1 to ", text)
    out = re.sub(r"\s*[" + DASHES + r"]\s*", ", ", out)
    out = re.sub(r",\s*([,.;:!?])", r"\1", out)  # a dash before a full stop
    return re.sub(r"\s{2,}", " ", out)


def _json_block(text: str) -> Any:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON found in model output")
    depth, opener = 0, text[start]
    closer = "]" if opener == "[" else "}"
    for i in range(start, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON in model output")


# The two ranking jobs are not the same question. Frames come out of the video,
# so a drawn diagram is on the subject by construction and beats a stock shot.
# Stock photographs are the opposite case: none of them is from the video, and
# telling a model to look for the mechanism among them invites it to settle for
# a metaphor. The shared tail is passed in as a value, so its braces are never
# re-formatted and the JSON example needs no doubling.
THUMBNAIL_TAIL = """- One clear focal point beats a busy or empty picture.
- It has to read at the size of a phone thumbnail.

The title is composited over the lower third afterwards, so the picture does not
need to carry words, and anything important should not sit at the very bottom.

Return ONLY a JSON object: {"pick": <number>, "why": "<six words>"}"""


THUMBNAIL_PROMPT = """You are choosing the thumbnail for a YouTube video.

The {n} images above are frames taken from the finished video, numbered 0 to
{last}. They have already been filtered for sharpness and contrast, so judge
them on meaning, not on technical quality.

TITLE: {title}
IT OPENS: {hook}
{drawn}
{notes}

Pick the frame that best represents what the video is about.

Rule out first, then choose. A frame is WRONG if what is actually visible in it
belongs to a different subject. A screen full of trading charts, a spreadsheet,
a game or an unrelated app is wrong for a video about something else, however
well the person in front of it matches the mood. Judge what is on the screen and
in the frame, not the emotion you infer from a posture.

Among what is left:
- A frame that shows the mechanism the video explains beats a stock shot, and
  beats a metaphor for the mechanism. A gear is not an index. Any frame listed
  above as drawn for this video is on the subject by construction; prefer one
  unless a photographic frame shows the actual subject more clearly.
{tail}"""


THUMBNAIL_STOCK_PROMPT = """You are choosing the thumbnail for a YouTube video.

The {n} images above are stock photographs found for this video, numbered 0 to
{last}. None of them is a frame from the video, so do not look for one. Judge
which photograph is most plainly about the subject below.

TITLE: {title}
IT SHOWS: {hook}
{drawn}
{notes}

Pick the photograph that best represents what the video is about.

Rule out first, then choose. A photograph is WRONG if what is actually visible
in it belongs to a different subject. A generic office, an unrelated app on a
screen, or a person looking stressed at a laptop is wrong for a video about
something else, however well the mood matches. Judge what is depicted, not the
emotion you infer from a posture.

Among what is left:
- A photograph of the thing the video is actually about beats a metaphor for it.
  A gear is not an index, and a worried face is not a subject.
{tail}"""


QUERY_PROMPT = """You are a video editor sourcing stock B-roll for a narrated video.

For each numbered line of narration below, write ONE stock-footage search query.

Rules:
- 2 to 5 words, concrete and filmable: objects, places, actions, textures.
- Describe what the CAMERA sees, never an abstract idea. "server racks blinking"
  not "data infrastructure". "hands counting cash" not "personal finance".
- No proper nouns, no text-on-screen, no people's names, no numbers.
- Consecutive lines must not repeat the same subject.
{style}
Return ONLY a JSON array of strings, one per line, in order.

NARRATION:
{lines}
"""


def pick_thumbnail(title: str, hook: str, images: Sequence[bytes], api_key: str,
                   model: str = DEFAULT_MODEL,
                   drawn: Sequence[int] = (),
                   notes: str = "",
                   kind: str = "frame", log=None) -> Tuple[int, str]:
    """Which candidate actually represents the video.

    Sharpness and colour find a striking frame, which is not the same thing as a
    relevant one - the sharpest frame in a video about locks is often a stock
    close-up of a keyboard.

    `kind` says what the candidates are. "frame" means they came out of the
    video; "photo" means they are stock photographs, where the advice to prefer
    the mechanism over a stock shot would be advice to prefer nothing at all.
    """
    if not images:
        return 0, ""
    if len(images) == 1:
        return 0, ""
    # which candidates are diagrams is known, not something to make the model
    # squint at: it read a rusty gear as "matching the indexing mechanism"
    note = (f"DRAWN FOR THIS VIDEO: images {', '.join(str(i) for i in drawn)}"
            if drawn else "")
    # `hook` is clamped because a scene of narration is long and only its
    # opening is useful. `notes` is not: it carries a line per candidate, and
    # truncating it would silently drop the ones at the end of the list.
    template = THUMBNAIL_STOCK_PROMPT if kind == "photo" else THUMBNAIL_PROMPT
    prompt = template.format(n=len(images), last=len(images) - 1,
                             title=title.strip(), hook=hook.strip()[:220],
                             drawn=note, notes=notes.strip(),
                             tail=THUMBNAIL_TAIL)
    verdict = _json_block(generate_vision(prompt, images, api_key, model, log=log))
    if not isinstance(verdict, dict):
        raise ValueError("no pick returned")
    try:
        pick = int(verdict.get("pick", 0))
    except (TypeError, ValueError):
        pick = 0
    return (pick if 0 <= pick < len(images) else 0), str(verdict.get("why", ""))


THUMB_QUERY_PROMPT = """Choose the image for a YouTube thumbnail.

TITLE: {title}
WHAT THE VIDEO SHOWS: {subjects}

Decide what KIND of image it should be, then write the stock photo search for
it. The kinds, roughly in order of how well they work:

  object    one thing at the centre of the topic, close and hard-lit
  contrast  two things in one frame: full and empty, one and many, stopped and moving
  action    a person mid-task, doing the thing, hands visible
  place     a location that carries the idea: a server hall, a sorting office
  reaction  a person feeling something about it

"reaction" is the obvious answer and almost always the wrong one. Nearly every
explainer opens by describing a frustration, and writing the search from that
produces one more photo of somebody holding their head, which is what every
other thumbnail on the platform already is. Choose it only if nothing else fits.

The search itself must be two to four concrete words naming things a camera can
see. It must not contain any of the words above, and no proper nouns, no company
names, and nothing that exists only inside software.

Return ONLY a JSON object: {{"kind": "<one of the five>", "search": "<the words>"}}"""


def thumbnail_query(title: str, subjects: str, api_key: str,
                    model: str = DEFAULT_MODEL, log=None) -> str:
    """A photo search for the thumbnail, written from what the video shows.

    Deliberately not given the hook. The hook is where the frustration lives,
    and a model handed it returns "stressed developer" for every video ever made
    about anything going wrong.

    The kind and the search come back as separate JSON fields for a reason: when
    the menu of kinds sat in a prompt that asked for prose, the model wrote the
    menu back out and the search became "Kind two things contrasted in".
    """
    raw = generate(THUMB_QUERY_PROMPT.format(title=title.strip(),
                                             subjects=subjects.strip()[:400]),
                   api_key, model, temperature=0.85, log=log)
    verdict = _json_block(raw)
    if not isinstance(verdict, dict):
        raise ValueError("no search returned")
    words = re.sub(r"[^A-Za-z \-]", " ", str(verdict.get("search", ""))).split()
    banned = {"kind", "object", "contrast", "action", "place", "reaction"}
    words = [w for w in words if w.lower() not in banned]
    query = " ".join(words[:4]).strip()
    if len(query.split()) < 2:
        raise ValueError(f"unusable search: {verdict!r}")
    return query


def suggest_queries(scenes: Sequence[Scene], api_key: str,
                    model: str = DEFAULT_MODEL, log=print, genre: str = "any") -> int:
    """Fill in the b-roll query for scenes that have no [visual:] directive."""
    pending = [s for s in scenes if not (s.query and s.query.strip())
               or s.query.strip() == s.heading.strip()]
    if not pending:
        return 0
    lines = "\n".join(f"{i + 1}. {s.text}" for i, s in enumerate(pending))
    try:
        raw = generate(QUERY_PROMPT.format(lines=lines,
                                           style=genres.prompt_block(genre)),
                       api_key, model,
                       temperature=0.6, log=log)
        queries = _json_block(raw)
    except (LLMUnavailable, ValueError) as exc:
        log(f"  b-roll queries: falling back to keywords ({exc})")
        return 0

    filled = 0
    for scene, q in zip(pending, queries):
        q = (genres.style_search(genre, q.strip(), scene.text, log=log)
             if isinstance(q, str) else "")
        if q:
            scene.query = q
            filled += 1
    return filled


BEAT_QUERY_PROMPT = """You are a video editor sourcing stock footage for a narrated
explainer video. The picture changes every few seconds, and each numbered
passage below is what is being said during one stretch of it.

For each passage, write ONE stock video search for what should be on screen
while it is spoken. A viewer hears the passage and sees the shot at the same
moment, and should see why that shot is there without being told.

Rules:
- 2 to 5 words a stock library understands.
- Search for the most literal thing the passage names or does. A passage about
  email replies gets an email inbox on a laptop. Source code gets code on a
  monitor. A chart gets a chart on a tablet. A slow disk gets a hard drive.
  Screens showing an app, a chat, code or a spreadsheet are good shots.
- Only when the passage names nothing filmable, show a person doing what it
  describes: a developer typing in a terminal, a team reviewing a document.
- NEVER a metaphor. A forest path is not a tree structure, a ring of keys is not
  an index, and a laptop lid closing is not a chatbot. The comparison is never
  said out loud, so the viewer just sees unrelated footage.
- No generic filler that would fit any passage: "person thinking", "person typing
  on computer", "stressed worker". If the same search would suit a different
  passage just as well, it is not specific enough.
- It must be something stock libraries really hold footage of. "Chat app on a
  phone" exists; a particular feature of a particular product, like "side panel
  in a web app", does not.
- No diagrams or infographics, no proper nouns, no brand or product names, no
  numbers.
- Passages from the same scene must not repeat a subject, and no main subject
  appears more than twice in the whole video.
{style}
Return ONLY a JSON array of strings, one per passage, in order.

PASSAGES:
{lines}
"""


def beat_queries(passages: Sequence[Dict[str, str]], api_key: str,
                 model: str = DEFAULT_MODEL, log=print,
                 genre: str = "any") -> List[str]:
    """A stock search per passage of narration, in order.

    Each passage is a dict with `text` and optionally the scene's `heading`. One
    request for the whole video, so it costs what `suggest_queries` does no
    matter how many beats a build has. Raises `LLMUnavailable` like every other
    call here; the builder falls back to the scene's own search.

    The scene's `[visual:]` is deliberately not sent. Offered as the writer's
    plan, it was kept where it did not fit: "person closing laptop" survived
    under "you might think Claude is just another chatbot", where the same
    passage without it came back "chatbot interface on screen".
    """
    if not passages:
        return []
    lines = []
    for i, p in enumerate(passages):
        prefix = f"[scene: {p['heading']}] " if p.get("heading") else ""
        lines.append(f"{i + 1}. {prefix}{p['text']}")
    raw = generate(BEAT_QUERY_PROMPT.format(lines="\n".join(lines),
                                            style=genres.prompt_block(genre)),
                   api_key, model,
                   temperature=0.5, log=log)
    try:
        queries = _json_block(raw)
    except ValueError as exc:
        raise LLMUnavailable(f"beat searches came back unreadable ({exc})")
    if not isinstance(queries, list) or len(queries) != len(passages):
        raise LLMUnavailable(f"asked for {len(passages)} searches and got "
                             f"{len(queries) if isinstance(queries, list) else 'none'}")
    out = []
    for q, p in zip(queries, passages):
        words = re.sub(r"[^A-Za-z' \-]", " ", str(q)).split()
        out.append(genres.style_search(genre, " ".join(words[:6]), p["text"], log=log))
    return out


DIAGRAM_PROMPT = """You are designing a simple diagram to illustrate one line of
narration in an explainer video. Stock footage cannot show this idea, so it is
being drawn instead.

NARRATION: {line}
THE SHOT THAT WAS WANTED: {query}

Pick the layout that fits the idea:
- "flow"    a sequence of steps or a pipeline, 3 to 5 stages
- "tree"    one thing branching into several, a root and 2 to 4 children
- "stack"   layers sitting on each other, 3 to 4, base first
- "compare" two sides set against each other, 2 to 4 items each

Rules:
- Labels are 1 to 3 words. They are read at a glance, not studied.
- No sentences, no punctuation, no numbers longer than four digits.
- The diagram must carry the idea in the narration, not decorate it.
- "title" is at most five words, or an empty string if the layout speaks alone.
- A compare diagram lights one side and mutes the other, so it takes a side.
  Set "accent": true on exactly one group: the one the narration favours,
  recommends or argues for. Set "accent": false on the other. The order of the
  groups does not decide the emphasis; the flag does.

Return ONLY JSON, one of:
{{"kind": "flow"|"tree"|"stack", "title": "...", "nodes": ["...", "..."]}}
{{"kind": "compare", "title": "...",
  "groups": [{{"label": "...", "items": ["..."], "accent": true|false}},
             {{"label": "...", "items": ["..."], "accent": true|false}}]}}"""


def design_diagram(line: str, query: str, api_key: str,
                   model: str = DEFAULT_MODEL, log=None) -> Dict[str, Any]:
    """A diagram spec for a line stock footage cannot illustrate."""
    raw = generate(DIAGRAM_PROMPT.format(line=line.strip(), query=query.strip()),
                   api_key, model, temperature=0.3, log=log)
    spec = _json_block(raw)
    if not isinstance(spec, dict):
        raise ValueError("model did not return a diagram spec")
    return spec


META_PROMPT = """Write YouTube upload metadata for this video.

Return ONLY JSON with these keys:
  "title"       - under 70 characters, specific, no clickbait punctuation
  "description" - 3 short paragraphs, plain text, no markdown, no dashes
  "tags"        - 12 lowercase strings
  "chapters"    - array of {{"time": "M:SS", "label": "..."}}, first time is "0:00"

Video title from the script: {title}
Total runtime: {runtime}

SCRIPT WITH SCENE START TIMES:
{body}
"""


# YouTube's rules for description chapters, and it enforces them by silently
# ignoring the entire list rather than the offending line: the first must be at
# 0:00, there must be at least three, and none may be shorter than ten seconds.
# So a video with one six-second chapter gets no chapters at all, and nothing
# anywhere says why - you find out by looking at the published video.
MIN_CHAPTER_SECONDS = 10.0
MIN_CHAPTERS = 3


def _seconds(stamp: str) -> Optional[float]:
    parts = str(stamp).strip().split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    seconds = 0.0
    for v in values:
        seconds = seconds * 60 + v
    return seconds


def usable_chapters(chapters: Sequence[Dict[str, Any]],
                    runtime: float) -> List[Dict[str, Any]]:
    """The chapters YouTube will actually render, or none at all.

    A short chapter is folded into the one before it rather than dropped on its
    own: the earlier label still describes the span, and the alternative is a
    gap. The last one is measured against the runtime, because a final chapter
    six seconds from the end breaks the list exactly as an interior one does.

    Returning nothing when fewer than three survive is deliberate. YouTube would
    discard the list anyway, and a `youtube.txt` showing chapters that will never
    appear is worse than one that admits the video has none.
    """
    parsed: List[Dict[str, Any]] = []
    for chapter in chapters or []:
        at = _seconds(chapter.get("time", ""))
        if at is not None:
            parsed.append({**chapter, "_at": at})
    parsed.sort(key=lambda c: c["_at"])
    if not parsed or parsed[0]["_at"] > 0:
        return []

    kept: List[Dict[str, Any]] = []
    for chapter in parsed:
        if not kept or chapter["_at"] - kept[-1]["_at"] >= MIN_CHAPTER_SECONDS:
            kept.append(chapter)
    while len(kept) > 1 and runtime - kept[-1]["_at"] < MIN_CHAPTER_SECONDS:
        kept.pop()

    if len(kept) < MIN_CHAPTERS:
        return []
    return [{k: v for k, v in c.items() if k != "_at"} for c in kept]


# YouTube's hard caps on the upload form. The prompt asks for a title under 70
# characters and twelve tags, but a prompt is a request and these are limits: a
# title over 100 characters or tags totalling over 500 are refused at upload,
# after the render is already paid for.
#
# Unlike the chapter rule above, no build here has been seen to exceed these -
# the model has complied every time. This is a guard on an unobserved risk, not
# a fix for an observed failure, and it is cheap enough to be worth having
# anyway: the failure it prevents happens at the destination, where this project
# has been bitten repeatedly.
MAX_TITLE = 100
MAX_DESCRIPTION = 5000
MAX_TAGS_TOTAL = 500


def _clip_words(text: str, limit: int) -> str:
    """Cut to `limit`, at a word boundary when there is one to cut at."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return cut or text[:limit]


def within_youtube_limits(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a metadata block to what the upload form will accept.

    Tags are dropped from the end rather than truncated: half a tag is not a
    tag, and the model writes them in descending relevance, so the tail is the
    right thing to lose.
    """
    out = dict(meta)
    if isinstance(out.get("title"), str):
        out["title"] = _clip_words(out["title"], MAX_TITLE)
    if isinstance(out.get("description"), str):
        out["description"] = _clip_words(out["description"], MAX_DESCRIPTION)

    tags, total, kept = out.get("tags") or [], 0, []
    for tag in tags if isinstance(tags, list) else []:
        tag = str(tag).strip()
        if not tag:
            continue
        # the separator counts against the budget for every tag after the first
        cost = len(tag) + (2 if kept else 0)
        if total + cost > MAX_TAGS_TOTAL:
            break
        kept.append(tag)
        total += cost
    out["tags"] = kept
    return out


def upload_metadata(title: str, scenes: Sequence[Scene], api_key: str,
                    model: str = DEFAULT_MODEL, log=None) -> Dict[str, Any]:
    def stamp(t: float) -> str:
        return f"{int(t // 60)}:{int(t % 60):02d}"

    body = "\n".join(f"[{stamp(s.start)}] {s.heading + ': ' if s.heading else ''}{s.text}"
                     for s in scenes)
    runtime = stamp(sum(s.duration for s in scenes))
    raw = generate(META_PROMPT.format(title=title, runtime=runtime, body=body),
                   api_key, model, temperature=0.5, log=log)
    meta = _json_block(raw)
    for key in ("title", "description"):
        if isinstance(meta.get(key), str):
            meta[key] = undash(meta[key])
    for chapter in meta.get("chapters") or []:
        if isinstance(chapter.get("label"), str):
            chapter["label"] = undash(chapter["label"])
    # Filtered here, once, so youtube.json, youtube.txt and description.txt all
    # carry the same list and none of them promises a chapter YouTube will drop.
    meta["chapters"] = usable_chapters(meta.get("chapters") or [],
                                       sum(s.duration for s in scenes))
    return within_youtube_limits(meta)


SCRIPT_PROMPT = """Write the narration for a YouTube explainer video.

TOPIC: {topic}

LENGTH: {words} words of narration across {scenes} scenes. This is a hard
budget, not a suggestion - a scene of {lo} to {hi} words is the right size, and
coming in short makes the video shorter than it was commissioned to be. Count as
you go.

SHAPE, in this order:
1. The hook. Open on something the viewer has done or believed, and the cost of
   it being wrong. Two sentences, no preamble, no "in this video".
2. Why the obvious answer is wrong. State the belief plainly, then break it.
3. The mechanism, over three to five scenes. This is the body: how the thing
   actually works, one idea per scene, each one earning the next. Give each of
   these its own heading naming that step - never repeat a heading.
4. When it bites. A concrete situation where this costs someone something.
5. What to do instead. Actionable, not abstract.
6. The takeaway. One sentence worth repeating.

WRITE FOR THE EAR:
- Second person. "Your query", not "the user's query".
- Vary the rhythm. At least two scenes are a single short sentence. At least one
  runs four sentences. A script where every scene is two sentences reads like a
  metronome and listens like one.
- Concrete nouns over abstractions. No lists, no markdown, no headings inside
  narration, no URLs, no "firstly" or "in conclusion".
- Say numbers as words a voice can speak. Nothing longer than four digits.
- No em dashes or en dashes anywhere. Use a comma or start a new sentence. A
  dash is read aloud as a pause the writing did not ask for.

DO NOT INVENT SPECIFICS. No version numbers, release dates, benchmark figures,
percentages, company announcements or named studies unless they appear in the
topic above. A confident wrong fact is the worst thing this can produce. If a
point needs a number you do not have, make the point without it.

EVERY SCENE GETS ONE VISUAL DIRECTIVE, AND IT IS ALWAYS [visual:]:

  [visual: 2-5 words, something a camera can point at]
      This is searched against a stock video library, so it must be a thing
      that exists on film: hands, objects, places, machinery, people working.

NEVER EXPLAIN AN IDEA WITH A GRAPHIC. No diagrams, charts, boxes, arrows, tables,
labels or words on screen, and never write [diagram:]. The narration carries the
explanation. The picture shows where the idea happens in the real world.

SHOW WHAT THE NARRATION IS LITERALLY ABOUT, NEVER A METAPHOR FOR IT. A viewer
hears the line and sees the shot at the same moment, and connects them only when
the shot is the thing being talked about. When the subject exists on film, name
it: a hard drive, a server rack, a search bar on a phone, a shop terminal. When
it has no physical form, show a person doing what the line describes. A forest
path is not a tree structure and a ring of keys is not an index: the narration
never says so, and the shot reads as unrelated footage.
    idea: a text file is split into scenes
        good  [visual: hands typing in a text editor]
        bad   [visual: flowchart from file to scenes]
    idea: paying once instead of a subscription
        good  [visual: card tapped on a shop terminal]
        bad   [visual: pricing comparison table]
    idea: how a database index finds a row
        good  [visual: code scrolling on a laptop screen]
        bad   [visual: tree structure diagram]
        bad   [visual: fork in a forest trail]
The first bad ones describe a graphic, which a stock library answers with generic
infographics or nothing on the subject at all. The last is a metaphor, which it
answers with exactly the wrong subject.

THE TEST: point a camera at it, and a viewer who hears the line should see why
this shot is on screen without being told. If you would have to draw it, or
explain the comparison, name the object or the person doing the thing instead.

OUTPUT exactly this markdown and nothing else:

# <title, under sixty characters, no colon, states the payoff. Keep
#  apostrophes where they belong: "Python's GIL", not "Pythons GIL" -
#  the title is burned onto the opening card>

## <scene heading, two or three words, different from every other heading>
[visual: ...]
<narration>
"""


# About sixteen seconds of speech a scene, the same length in seconds the
# drafting prompt asked for before the speaking rate was measured.
WORDS_PER_SCENE = 52
# Words of narration per minute of finished video. Derived rather than written
# down, so it cannot drift from the speaking rate again: speech at the measured
# `WPS`, plus the silence the voice config puts around every scene. It was 155,
# a guess about half a minute out on every draft. Derived it is 193, against 182
# to 195 measured across real builds and 190 for a delivered 391 second video;
# the spread is scene length, since a script of short scenes pays more pauses.
WORDS_PER_MINUTE = int(60 / (1 / WPS + (VoiceConfig.lead_in + VoiceConfig.gap)
                             / WORDS_PER_SCENE))


def draft_script(topic: str, minutes: float, api_key: str,
                 model: str = DEFAULT_MODEL, log=None) -> str:
    """Draft a script sized to an actual runtime.

    The budget is spelled out per scene as well as in total, because a lone
    total is consistently undershot - measured at about two thirds of the
    requested length. Stating it per scene was not enough on its own either, so
    the draft is then measured and its short scenes lengthened; see `lengthen`.
    """
    words = int(minutes * WORDS_PER_MINUTE)
    scenes = max(5, min(18, round(words / WORDS_PER_SCENE)))
    per_scene = words / scenes
    text = generate(
        SCRIPT_PROMPT.format(topic=topic, words=words, scenes=scenes,
                             lo=int(per_scene * 0.8), hi=int(per_scene * 1.25)),
        api_key, model, temperature=0.8, log=log,
    )
    text = re.sub(r"^```(?:markdown)?|```$", "", text.strip(),
                  flags=re.MULTILINE).strip() + "\n"
    return lengthen(strip_diagrams(text), words, api_key, model, log=log)


# A draft counts as long enough at this share of its budget. The rewrite lands
# within a few percent either side of what it is asked for, so chasing the last
# tenth would spend requests on noise.
LONG_ENOUGH = 0.9
# The longest a lengthened draft has come back, as a share of its budget: 109%,
# across six real drafts at nine minutes that landed between 90% and 109%.
# Anything sizing a draft to fit under a hard limit has to leave this much room.
LENGTHEN_OVERSHOOT = 1.10
# A scene longer than this is one stock search held on screen for over half a
# minute, which the page already warns will repeat its footage. Drafts often
# come back with half the scenes asked for, so lengthening them without a limit
# made eighty second scenes; a rewrite this long is split into paragraphs, each
# with its own [visual:] line, and the parser makes each paragraph a scene.
PARAGRAPH_WORDS = 90
# Rounds, not requests: a round is one request per chunk of scenes.
LENGTHEN_ROUNDS = 2
# Scenes asked for in one request, bounded so the reply fits `maxOutputTokens`.
# A chunk can never be asked for more than the whole missing length, and at the
# 9.5 minute limit that is at most about 1,700 words, some 2,300 tokens of the
# 4096 with its [visual:] lines.
LENGTHEN_CHUNK = 8
# The prompt asks for at least two scenes that are one short sentence. Those
# are left alone rather than inflated, up to that many.
ONE_LINER_WORDS = 12

LENGTHEN_PROMPT = """You are lengthening scenes in the narration script below. The video
was commissioned at {target} words of narration and the script has only {total}.

Rewrite ONLY these scenes, each to the length given:
{wanted}

Hit each length. A rewrite that comes in short makes the video shorter than it
was commissioned to be, so count as you go. Add substance, not padding: a
concrete example, what the step costs or saves, what happens at the edges, the
question a viewer would ask next and its answer. Keep what the scene already
says, in the same voice, and keep it leading into the scene after it without
repeating what that scene says.

Keep every rule the script was written under: second person, sentences a voice
can speak, numbers as words, no em dashes or en dashes, no lists or markdown
inside narration. DO NOT INVENT SPECIFICS: no version numbers, release dates,
benchmark figures, percentages or named studies.

A SCENE OVER {paragraph} WORDS IS WRITTEN AS PARAGRAPHS of {short} to {paragraph}
words, separated by a blank line. Every paragraph after the first opens with its
own [visual: 2-5 words] line on the line above it: something a camera can point
at, such as hands, objects, places, machinery or people working, and never a
diagram, chart or anything that would have to be drawn. Each paragraph is shown
over its own footage, so the visual should fit that paragraph.

THE SCRIPT:

{script}

OUTPUT exactly this for each scene listed above, in the same order, and nothing
else. Copy each heading exactly. No title, and no [visual:] line above the first
paragraph, which keeps the one it has:

## <heading, copied exactly>
<first paragraph>

[visual: ...]
<next paragraph, only if the scene is over {paragraph} words>
"""


def _directive(line: str) -> bool:
    return bool(DIRECTIVE.match(line) or NOTE.match(line))


def _sections(text: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """A drafted script as (lines before the first scene, one dict per `##`).

    `lead` is the directives above a scene's first words, which a rewrite never
    touches. `body` is everything after, blank lines and any later directives
    included, because a blank line is a scene break to the parser and moving one
    re-cuts the video.
    """
    head: List[str] = []
    sections: List[Dict[str, Any]] = []
    for line in text.splitlines():
        h = HEADING.match(line)
        if h and len(h.group(1)) == 2:
            sections.append({"heading": h.group(2).strip(), "lead": [], "body": []})
        elif not sections:
            head.append(line)
        elif not _spoken(sections[-1]) and (_directive(line) or not line.strip()):
            if line.strip():
                sections[-1]["lead"].append(line.strip())
        else:
            sections[-1]["body"].append(line.strip())
    return head, sections


def _spoken(section: Dict[str, Any]) -> int:
    return sum(len(line.split()) for line in section["body"]
               if line and not _directive(line))


def _same_heading(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", heading.lower()).strip()


def _assemble(head: List[str], sections: List[Dict[str, Any]]) -> str:
    out = "\n".join(head).rstrip() + "\n"
    for s in sections:
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(s["body"])).strip()
        out += f"\n## {s['heading']}\n" + "".join(d + "\n" for d in s["lead"])
        out += body + "\n"
    return out


def lengthen(script: str, target: int, api_key: str,
             model: str = DEFAULT_MODEL, log=None) -> str:
    """Rewrite a draft's short scenes until the narration reaches `target` words.

    Asking for a length was never enough. Measured on three topics at nine
    minutes, the one-shot draft came back at 69%, 44% and 57%, and it failed in
    two different ways: one draft wrote eight scenes where eighteen were asked
    for, another wrote sixteen scenes of about fifty words against a budget of
    seventy seven. A better prompt could fix one of those and not the other.
    Measuring the result fixes both, so the draft is counted and each short
    scene is sent back to be rewritten at a stated length. The same three
    drafts came out at 106%, 96% and 101%, and three fresh ones drafted end to
    end from 26%, 33% and 64% came out at 109%, 90% and 103%.

    Each scene is asked for its share of the words actually missing, not for a
    flat per-scene size. Asked for the flat size, the same drafts overshot to
    107%, which past the instance's word limit is a script its own page refuses.

    A rewrite over `PARAGRAPH_WORDS` comes back as paragraphs, each after the
    first under its own [visual:] line, so a draft that wrote half its scenes
    is lengthened into more scenes rather than into minute-long ones.

    The narration is what gets replaced. Headings, the directives above each
    scene's first words and scene order are the draft's, and a reply that loses
    a heading or comes back shorter changes nothing, so the worst this can do is
    return the draft. A model failure part way returns what has been lengthened
    so far: the draft is still a usable script, and refusing it over a top-up
    would throw away the one request that mattered.
    """
    for _ in range(LENGTHEN_ROUNDS):
        head, sections = _sections(script)
        total = sum(_spoken(s) for s in sections)
        if not sections or total >= target * LONG_ENOUGH:
            break
        one_liners = [s for s in sections if _spoken(s) <= ONE_LINER_WORDS][:2]
        rest = [s for s in sections if not any(s is o for o in one_liners)]
        if not rest:
            break
        per = (target - sum(_spoken(s) for s in one_liners)) / len(rest)
        short = [s for s in rest if _spoken(s) < per * 0.8]
        if not short:
            break
        deficit = [per - _spoken(s) for s in short]
        asks = [round(_spoken(s) + (target - total) * d / sum(deficit))
                for s, d in zip(short, deficit)]
        if log:
            log(f"    draft is {total} of {target} words; lengthening "
                f"{len(short)} scene{'s' if len(short) != 1 else ''}")
        for i in range(0, len(short), LENGTHEN_CHUNK):
            chunk = list(zip(short, asks))[i:i + LENGTHEN_CHUNK]
            wanted = "\n".join(f"- ## {s['heading']}: about {ask} words "
                               f"(it has {_spoken(s)})" for s, ask in chunk)
            try:
                reply = generate(
                    LENGTHEN_PROMPT.format(target=target, total=total,
                                           wanted=wanted, script=script,
                                           paragraph=PARAGRAPH_WORDS,
                                           short=PARAGRAPH_WORDS * 2 // 3),
                    api_key, model, temperature=0.7, log=log)
            except LLMUnavailable as exc:
                if log:
                    log(f"    lengthening stopped, keeping the draft: {exc}")
                return strip_diagrams(_assemble(head, sections))
            rewritten = {_same_heading(r["heading"]): r
                         for r in _sections(reply)[1]}
            for s, _ask in chunk:
                r = rewritten.get(_same_heading(s["heading"]))
                # the reply's body only: a [visual:] it put above the first
                # paragraph anyway would replace the draft's, which was chosen
                # alongside every other scene's
                if r and _spoken(r) > _spoken(s):
                    s["body"] = r["body"]
        script = strip_diagrams(_assemble(head, sections))
    return script


# A drafted script never explains itself in boxes. The prompt forbids it, and as
# with dashes that is not enough on its own: a model that files an idea as
# [diagram:] anyway still has to come out without one.
DIAGRAM_LINE = re.compile(r"^[ \t]*\[diagram:[^\]\n]*\][ \t]*\n?",
                          re.IGNORECASE | re.MULTILINE)


def strip_diagrams(script: str) -> str:
    """Remove every [diagram: ...] line from a drafted script.

    The line goes rather than being renamed to [visual:], because what a model
    writes after "diagram:" describes a graphic, and a graphic is exactly the
    wrong search for a stock video library. A scene left with no directive is
    searched on a query Gemini writes from its narration at build time.

    Drafts only. A hand-written [diagram:] is its author's decision, and
    `visuals.diagrams` is the switch for that.
    """
    return DIAGRAM_LINE.sub("", script)
