"""Rewrite one scene's narration in a finished build, and rebuild only that scene.

A line that reads badly aloud used to cost the whole video: every scene voiced
again, searched again, judged again and encoded again. The build compares each
scene against the script it last made, so a scene whose words changed is voiced
and filmed again and every other scene keeps its voice and its clips. This is
the part that changes the words: in the script itself, in place, and only if
nothing else about the script moves with them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import pipeline, snapshot
from .retake import RetakeRefused
from .script_parser import _clean, narration_words, parse_text, replace_scene_text

MAX_SCENE_CHARS = 2000


def scenes(root: Path) -> Dict[str, Any]:
    """Every scene of the script, as it will be read."""
    script = Path(root) / "script.md"
    try:
        text = script.read_text(encoding="utf-8")
    except OSError:
        raise RetakeRefused("this render has no script to edit")
    _, parsed = parse_text(text)
    return {"words": narration_words(text),
            "scenes": [{"index": s.index, "heading": s.heading, "text": s.text,
                        "words": len(s.text.split())} for s in parsed]}


def check(root: Path, index: int, new_text: str,
          word_cap: Optional[int] = None) -> str:
    """The rewritten script, or a refusal naming what is wrong with the edit.

    The new words are parsed as part of the script before anything is written,
    and refused if they would change more than that scene's narration: a line
    that opens with `##` or reads as a `[visual:]` directive would re-cut the
    video rather than re-word one scene, which is a different request.
    """
    root = Path(root)
    if not (root / "build" / "scenes.json").exists():
        raise RetakeRefused("this render kept no working files, so a scene cannot "
                            "be rebuilt on its own; render the script again")
    if len(new_text) > MAX_SCENE_CHARS:
        raise RetakeRefused(f"a scene is limited to {MAX_SCENE_CHARS} characters")
    old = (root / "script.md").read_text(encoding="utf-8")
    try:
        new = replace_scene_text(old, index, new_text)
    except ValueError as exc:
        raise RetakeRefused(str(exc)) from exc

    _, before = parse_text(old)
    _, after = parse_text(new)
    if len(after) != len(before):
        raise RetakeRefused("those words would change how many scenes the script has")
    for was, now in zip(before, after):
        if was.picture_key() != now.picture_key() or (
                now.index != index and was.narration_key() != now.narration_key()):
            raise RetakeRefused("those words would change more of the script than "
                                "this scene's narration")
    if after[index].text != _clean(" ".join(new_text.split())):
        raise RetakeRefused("those words read as a heading, a directive or a note, "
                            "not as narration")
    if after[index].text == before[index].text:
        raise RetakeRefused("that is what the scene already says")
    if word_cap is not None and narration_words(new) > word_cap:
        raise RetakeRefused(f"the script would be {narration_words(new)} spoken words, "
                            f"over this instance's limit of {word_cap}")
    return new


def touched() -> List[str]:
    """What an edit can rewrite: the script, every artifact and the delivery."""
    return ["script.md", "build", "out"]


def apply(root: Path, index: int, new_text: str, word_cap: Optional[int] = None,
          log: Callable[[str], None] = print) -> Path:
    """Write the new words and deliver the video again. Returns the new mp4.

    The build decides what the edit costs, by comparing the script against the
    scenes it last built. The script, `build/` and `out/` are copied aside first
    and put back on any way out that is not success, so a failed or stopped
    edit leaves the finished video, and the script that made it, as they were.
    """
    root = Path(root)
    new = check(root, index, new_text, word_cap)
    log(f"edit     rewriting scene {index} and rebuilding only what it changes")
    snapshot.take(root, touched())
    try:
        (root / "script.md").write_text(new, encoding="utf-8")
        final = pipeline.build(root, log=log, edit=True)
    except BaseException:
        snapshot.restore(root)
        raise
    snapshot.discard(root)
    return final
