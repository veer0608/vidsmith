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

Return ONLY a JSON object:
{{"ranked": [image numbers, best first, every number once],
  "reject": [image numbers showing the wrong subject, may be empty]}}"""


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
               model: str = DEFAULT_MODEL) -> Tuple[List[int], List[int]]:
    """(order, rejected) over `images`. Rejected clips show the wrong subject."""
    if not images or len(images) < 2:
        return [], []
    prompt = RERANK_PROMPT.format(n=len(images), last=len(images) - 1,
                                  line=line.strip(), query=query.strip())
    raw = generate_vision(prompt, images, api_key, model)
    verdict = _json_block(raw)

    if isinstance(verdict, list):          # tolerate a bare ranking
        verdict = {"ranked": verdict, "reject": []}
    if not isinstance(verdict, dict):
        raise ValueError("model did not return a ranking")

    order = _indices(verdict.get("ranked"), len(images))
    # anything the model left out keeps its original relative position
    order += [i for i in range(len(images)) if i not in order]
    rejected = _indices(verdict.get("reject"), len(images))
    return order, rejected


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


SCRIPT_PROMPT = """Write a narration script for a YouTube video.

Topic: {topic}
Target runtime: about {minutes} minutes (roughly {words} words of narration).

Format the output as markdown exactly like this, and output nothing else:

# <video title>

## <scene heading>
[visual: <2-5 word stock footage search query, concrete and filmable>]
<narration for this scene: 2 to 4 spoken sentences>

Rules:
- Open with a hook that states the payoff in the first two sentences.
- Write for the ear: short sentences, no lists, no headings inside narration,
  no markdown emphasis, no numbers longer than four digits, no URLs.
- 8 to 16 scenes. Each [visual:] must show what the camera sees, not an idea.
- End with one clear takeaway. No "like and subscribe".
"""


def draft_script(topic: str, minutes: float, api_key: str,
                 model: str = DEFAULT_MODEL) -> str:
    words = int(minutes * 150)
    text = generate(
        SCRIPT_PROMPT.format(topic=topic, minutes=minutes, words=words),
        api_key, model, temperature=0.8,
    )
    return re.sub(r"^```(?:markdown)?|```$", "", text.strip(),
                  flags=re.MULTILINE).strip() + "\n"
