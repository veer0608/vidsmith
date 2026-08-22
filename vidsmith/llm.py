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
  "description" - 3 short paragraphs, plain text, no markdown
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
    return _json_block(raw)


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

# <title, under sixty characters, no colon, states the payoff>

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
