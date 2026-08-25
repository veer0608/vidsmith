"""Optional Gemini calls: b-roll search terms, and the YouTube upload metadata.

Everything here is optional. Without a key the pipeline still runs - scene
queries fall back to keyword extraction and the metadata step is skipped.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from .script_parser import Scene

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# gemini-2.5-flash 404s on free keys; the -latest aliases keep working.
DEFAULT_MODEL = "gemini-flash-lite-latest"
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMUnavailable(RuntimeError):
    pass


def generate(prompt: str, api_key: str, model: str = DEFAULT_MODEL,
             temperature: float = 0.4, retries: int = 4) -> str:
    if not api_key:
        raise LLMUnavailable("no GEMINI_API_KEY")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096},
    }
    last = ""
    for attempt in range(retries):
        r = requests.post(
            ENDPOINT.format(model=model),
            params={"key": api_key},
            json=body,
            timeout=120,
        )
        if r.status_code in RETRY_STATUS:
            last = f"HTTP {r.status_code}: {r.text[:180]}"
            time.sleep(2 ** attempt)
            continue
        if r.status_code != 200:
            raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            raise LLMUnavailable(f"unexpected response: {json.dumps(data)[:300]}")
        return "".join(p.get("text", "") for p in parts).strip()
    raise LLMUnavailable(f"gave up after {retries} attempts - {last}")


def generate_vision(prompt: str, images: Sequence[bytes], api_key: str,
                    model: str = DEFAULT_MODEL, temperature: float = 0.1,
                    retries: int = 3) -> str:
    """Same call as generate(), with JPEG stills attached before the prompt."""
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
        r = requests.post(ENDPOINT.format(model=model), params={"key": api_key},
                          json=body, timeout=180)
        if r.status_code in RETRY_STATUS:
            last = f"HTTP {r.status_code}: {r.text[:180]}"
            time.sleep(2 ** attempt)
            continue
        if r.status_code != 200:
            raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            out = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            raise LLMUnavailable(f"unexpected response: {json.dumps(data)[:300]}")
        return "".join(p.get("text", "") for p in out).strip()
    raise LLMUnavailable(f"gave up after {retries} attempts - {last}")


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

Then decide which are unusable. A clip is unusable when it shows the wrong
subject - not merely a weaker version of the right one. A book is not a
calendar; a laptop is not a card terminal. Be strict about subject and lenient
about style: an unremarkable shot of the right thing beats a beautiful shot of
the wrong thing.

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
               model: str = DEFAULT_MODEL) -> Tuple[List[int], List[int], bool]:
    """(order, rejected, filmable) over `images`.

    `filmable` is False when no camera can point at the idea - that is the cue to
    draw a diagram rather than keep searching for footage that does not exist.
    """
    if not images or len(images) < 2:
        return [], [], True
    prompt = RERANK_PROMPT.format(n=len(images), last=len(images) - 1,
                                  line=line.strip(), query=query.strip())
    raw = generate_vision(prompt, images, api_key, model)
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

Return ONLY a JSON array of strings, one per line, in order.

NARRATION:
{lines}
"""


def pick_thumbnail(title: str, hook: str, images: Sequence[bytes], api_key: str,
                   model: str = DEFAULT_MODEL,
                   drawn: Sequence[int] = (),
                   notes: str = "",
                   kind: str = "frame") -> Tuple[int, str]:
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
    verdict = _json_block(generate_vision(prompt, images, api_key, model))
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
                    model: str = DEFAULT_MODEL) -> str:
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
                   api_key, model, temperature=0.85)
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
                    model: str = DEFAULT_MODEL, log=print) -> int:
    """Fill in the b-roll query for scenes that have no [visual:] directive."""
    pending = [s for s in scenes if not (s.query and s.query.strip())
               or s.query.strip() == s.heading.strip()]
    if not pending:
        return 0
    lines = "\n".join(f"{i + 1}. {s.text}" for i, s in enumerate(pending))
    try:
        raw = generate(QUERY_PROMPT.format(lines=lines), api_key, model, temperature=0.6)
        queries = _json_block(raw)
    except (LLMUnavailable, ValueError) as exc:
        log(f"  b-roll queries: falling back to keywords ({exc})")
        return 0

    filled = 0
    for scene, q in zip(pending, queries):
        if isinstance(q, str) and q.strip():
            scene.query = q.strip()
            filled += 1
    return filled


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

Return ONLY JSON, one of:
{{"kind": "flow"|"tree"|"stack", "title": "...", "nodes": ["...", "..."]}}
{{"kind": "compare", "title": "...",
  "groups": [{{"label": "...", "items": ["..."]}}, {{"label": "...", "items": ["..."]}}]}}"""


def design_diagram(line: str, query: str, api_key: str,
                   model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """A diagram spec for a line stock footage cannot illustrate."""
    raw = generate(DIAGRAM_PROMPT.format(line=line.strip(), query=query.strip()),
                   api_key, model, temperature=0.3)
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


def upload_metadata(title: str, scenes: Sequence[Scene], api_key: str,
                    model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    def stamp(t: float) -> str:
        return f"{int(t // 60)}:{int(t % 60):02d}"

    body = "\n".join(f"[{stamp(s.start)}] {s.heading + ': ' if s.heading else ''}{s.text}"
                     for s in scenes)
    runtime = stamp(sum(s.duration for s in scenes))
    raw = generate(META_PROMPT.format(title=title, runtime=runtime, body=body),
                   api_key, model, temperature=0.5)
    meta = _json_block(raw)
    for key in ("title", "description"):
        if isinstance(meta.get(key), str):
            meta[key] = undash(meta[key])
    for chapter in meta.get("chapters") or []:
        if isinstance(chapter.get("label"), str):
            chapter["label"] = undash(chapter["label"])
    return meta


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

EVERY SCENE GETS ONE VISUAL DIRECTIVE:

  [visual: 2-5 words, something a camera can point at]
      Use when the scene has a physical subject: hands, objects, places,
      machinery, people working. This is searched against a stock library, so
      it must be a thing that exists on film.

  [diagram: what the diagram itself shows]
      Use only when the idea has no photographable subject at all - a data
      structure, a protocol, a sequence of states, a tradeoff.

      Describe the DIAGRAM, not a picture. It is drawn as boxes and arrows, so
      say what the boxes are:
        good  [diagram: the four stages a write passes through]
        good  [diagram: a root node branching down to leaf nodes]
        good  [diagram: read speed set against write cost]
        bad   [diagram: magnifying glass over a book]
        bad   [diagram: person closing a laptop]
        bad   [diagram: red gear stuck in machinery]
      The bad ones name objects you could photograph. Those are [visual:].

THE TEST: if you can imagine pointing a camera at it, it is [visual:]. Most
scenes are. Even a technical script usually has only two or three [diagram:]
scenes, and a script where most scenes are diagrams is wrong - it means
photographable images were filed as diagrams.

OUTPUT exactly this markdown and nothing else:

# <title, under sixty characters, no colon, states the payoff. Keep
#  apostrophes where they belong: "Python's GIL", not "Pythons GIL" -
#  the title is burned onto the opening card>

## <scene heading, two or three words, different from every other heading>
[visual: ...]  or  [diagram: ...]
<narration>
"""


# edge-tts at the default +8% rate speaks about 155 words a minute
WORDS_PER_MINUTE = 155
WORDS_PER_SCENE = 42


def draft_script(topic: str, minutes: float, api_key: str,
                 model: str = DEFAULT_MODEL) -> str:
    """Draft a script sized to an actual runtime.

    The budget is spelled out per scene as well as in total, because a lone
    total is consistently undershot - measured at about two thirds of the
    requested length.
    """
    words = int(minutes * WORDS_PER_MINUTE)
    scenes = max(5, min(18, round(words / WORDS_PER_SCENE)))
    per_scene = words / scenes
    text = generate(
        SCRIPT_PROMPT.format(topic=topic, words=words, scenes=scenes,
                             lo=int(per_scene * 0.8), hi=int(per_scene * 1.25)),
        api_key, model, temperature=0.8,
    )
    return re.sub(r"^```(?:markdown)?|```$", "", text.strip(),
                  flags=re.MULTILINE).strip() + "\n"
