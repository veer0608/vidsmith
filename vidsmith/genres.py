"""The kind of footage a video should be cut from.

Pexels has no genre filter, so a genre works through the two model calls that
decide the footage. The search writer puts a style word into every search, and
the reranker, looking at the stills, puts the clips in that style first among
those showing the right subject. Pixabay does filter one thing, animation
against film, and that is passed through as a real API parameter.

A first nature build on a coffee script moved its searches by one word at most
("coffee cherries on branch" became "on hillside bush", the mug on the desk did
not move), because the search writer was only told to add a style word "where
it helps" and the reranker was never told a style existed. Both now carry it.

`any` is the default and adds nothing to a prompt or a cache key, so a build
that never chose a genre searches and ranks exactly as it did before genres.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple


class Genre(NamedTuple):
    label: str
    # finishes the sentence "The footage for this video should be ..."
    direction: str
    # words a stock library answers to for this look, offered to the search
    # writer as the style half of a search
    words: str = ""
    # Pixabay's `video_type`: all | film | animation
    pixabay_type: str = "all"


GENRES: Dict[str, Genre] = {
    "any": Genre("Any", ""),
    "cinematic": Genre("Cinematic",
                       "cinematic: slow motion, shallow depth of field, dramatic "
                       "light and smooth camera moves",
                       "cinematic, slow motion, close up, golden hour, moody light"),
    "documentary": Genre("Documentary",
                         "documentary: real people in real places, handheld camera, "
                         "natural light",
                         "real, candid, handheld, worker, local, natural light"),
    "business": Genre("Business",
                      "business: offices, meetings, professionals at work, clean "
                      "modern workplaces",
                      "office, professional, meeting, modern workplace, team"),
    "technology": Genre("Technology",
                        "technology: screens, code, devices, data centres and people "
                        "using software",
                        "screen, digital, laptop, futuristic, neon, data"),
    "nature": Genre("Nature",
                    "nature: landscapes, wildlife, water, forests, sky and the "
                    "outdoors",
                    "outdoors, sunlight, field, forest, mountain, river, green"),
    "city": Genre("City",
                  "urban: streets, buildings, traffic, crowds and city life by day "
                  "and night",
                  "city, street, urban, downtown, night lights, crowd"),
    "animation": Genre("Animation",
                       "animated: motion graphics, 3D renders and animated "
                       "illustrations rather than filmed footage",
                       "animation, 3D render, motion graphics, cartoon, illustration",
                       "animation"),
}


def get(name: str) -> Genre:
    return GENRES.get(name or "any", GENRES["any"])


def prompt_block(name: str) -> str:
    """The style paragraph for a search-writing prompt, or nothing for `any`.

    The subject still comes first. A genre that outranked the passage would
    bring back the fault beats were built to fix: footage that suits the
    channel and has nothing to do with what is being said.
    """
    genre = get(name)
    if not genre.direction:
        return ""
    return (f"\nSTYLE: the footage for this video should be {genre.direction}. "
            "Every search must carry that style: name the subject, then place it or "
            f"describe it in the style, using words like: {genre.words}. "
            "\"Coffee mug on desk\" in a nature video is \"coffee mug outdoors in "
            "sunlight\", still within the word limit. But the literal subject of the passage always "
            "comes first: never swap the subject for something that only fits the "
            "style, and never drop the subject to make room for a style word.\n")


def rerank_block(name: str) -> str:
    """The style paragraph for the clip reranker, or nothing for `any`.

    Style orders the clips that already show the right subject. It never
    rescues a wrong one and is never a reason to reject, or a scene short of
    on-style footage would lose the right footage too.
    """
    genre = get(name)
    if not genre.direction:
        return ""
    return (f"\nSTYLE: this video's footage should be {genre.direction}. Among the "
            "clips that show the right subject, rank the ones in that style above "
            "the ones that are not. Style never makes up for the wrong subject, and "
            "a clip is never rejected for being off style.\n")


def options() -> List[Dict[str, str]]:
    return [{"name": name, "label": g.label} for name, g in GENRES.items()]
