"""Turn a markdown script into a list of Scenes.

Script format (everything optional except the narration itself):

    # My Video Title

    ## Hook
    [visual: aerial drone shot of a city at sunrise]
    Most people think compound interest is boring. They are wrong.

    ## The setup
    Here is what actually happens to a rupee left alone for thirty years.

A scene break is either a `##` heading or a blank line between paragraphs.
`[visual: ...]` (aliases: b-roll, broll, footage, shot) sets the stock-footage
search query for that scene. `[hold: 3.5]` forces a minimum on-screen duration.
Lines starting with `>` or `<!--` are treated as production notes and dropped
from the narration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List

# Kept as data rather than buried in the pattern: the web page needs the same
# vocabulary to tell you what it is about to build, and gets it from the server
# instead of keeping a second copy that can quietly disagree.
DIRECTIVE_KINDS = ("visual", "b-?roll", "footage", "shot", "hold", "image", "diagram")
NOTE_PREFIXES = (">", "<!--", "//")

DIRECTIVE = re.compile(
    r"^\s*\[(" + "|".join(DIRECTIVE_KINDS) + r")\s*:\s*(.+?)\]\s*$",
    re.IGNORECASE,
)
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
NOTE = re.compile(r"^\s*(" + "|".join(re.escape(p) for p in NOTE_PREFIXES) + r")")
# A rough words-per-second used only to pre-flag scenes that will run long.
WPS = 2.6


@dataclass
class Scene:
    index: int
    heading: str = ""
    text: str = ""
    query: str = ""
    hold: float = 0.0
    diagram: str = ""      # "[diagram: ...]" forces a drawn frame for this scene
    # filled in by later stages
    audio: str = ""
    words: List[Dict[str, Any]] = field(default_factory=list)
    duration: float = 0.0
    start: float = 0.0
    visual: str = ""
    credit: str = ""       # legacy single-clip attribution
    credit_url: str = ""
    # one scene is cut into several shots: {path, duration, credit, credit_url}
    shots: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def est_seconds(self) -> float:
        return max(self.hold, len(self.text.split()) / WPS)


def _clean(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_script(path: Path) -> tuple[str, List[Scene]]:
    lines = path.read_text(encoding="utf-8").splitlines()

    title = ""
    scenes: List[Scene] = []
    cur_heading = ""
    cur_query = ""
    cur_hold = 0.0
    cur_diagram = ""
    buf: List[str] = []

    def flush():
        # cur_heading is read here but only ever assigned in the loop below, so
        # it needs no nonlocal. It survives a flush on purpose: a heading is a
        # section, not a label for one paragraph, so every scene under one `##`
        # keeps it, and an undirected scene is searched on it.
        nonlocal buf, cur_query, cur_hold, cur_diagram
        text = _clean(" ".join(buf))
        buf = []
        if not text:
            return
        scenes.append(
            Scene(
                index=len(scenes),
                heading=cur_heading,
                text=text,
                query=cur_query or cur_heading,
                hold=cur_hold,
                diagram=cur_diagram,
            )
        )
        cur_query = ""
        cur_hold = 0.0
        cur_diagram = ""

    for raw in lines:
        line = raw.rstrip()

        if NOTE.match(line):
            continue

        m = DIRECTIVE.match(line)
        if m:
            kind, value = m.group(1).lower(), m.group(2).strip()
            if kind == "hold":
                try:
                    cur_hold = float(value)
                except ValueError:
                    pass
            elif kind == "diagram":
                if buf:
                    flush()
                cur_diagram = value
            else:
                # visual, b-roll, footage, shot and image all mean the same
                # thing: the search that fills this scene. `image` is an alias,
                # not a "use a still" switch - a still only enters through the
                # `local` provider matching an image file on disk. The README
                # and CLAUDE.md both claimed otherwise for a while, which is a
                # directive that reads as doing something and quietly does not.
                # A directive starts a new scene if narration is already buffered.
                if buf:
                    flush()
                cur_query = value
            continue

        h = HEADING.match(line)
        if h:
            level, text = len(h.group(1)), _clean(h.group(2))
            flush()
            if level == 1 and not title:
                title = text
                cur_heading = ""
            else:
                cur_heading = text
            continue

        if not line.strip():
            flush()
            continue

        buf.append(line.strip())

    flush()
    return title or path.stem.replace("-", " ").title(), scenes


def save_scenes(scenes: List[Scene], path: Path) -> None:
    import json

    path.write_text(
        json.dumps([s.to_dict() for s in scenes], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_scenes(path: Path) -> List[Scene]:
    import json

    return [Scene(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
