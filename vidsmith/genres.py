"""The kind of footage a video should be cut from.

Pexels has no genre filter, so a genre is mostly a direction handed to the model
that writes the stock searches: the searches themselves then land in the style.
Pixabay does filter one thing, animation against film, and that is passed
through as a real API parameter.

`any` is the default and adds nothing to a prompt or a cache key, so a build
that never chose a genre searches exactly as it did before genres existed.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple


class Genre(NamedTuple):
    label: str
    # finishes the sentence "The footage for this video should be ..."
    direction: str
    # Pixabay's `video_type`: all | film | animation
    pixabay_type: str = "all"


GENRES: Dict[str, Genre] = {
    "any": Genre("Any", ""),
    "cinematic": Genre("Cinematic",
                       "cinematic: slow motion, shallow depth of field, dramatic "
                       "light and smooth camera moves"),
    "documentary": Genre("Documentary",
                         "documentary: real people in real places, handheld camera, "
                         "natural light"),
    "business": Genre("Business",
                      "business: offices, meetings, professionals at work, clean "
                      "modern workplaces"),
    "technology": Genre("Technology",
                        "technology: screens, code, devices, data centres and people "
                        "using software"),
    "nature": Genre("Nature",
                    "nature: landscapes, wildlife, water, forests, sky and the "
                    "outdoors"),
    "city": Genre("City",
                  "urban: streets, buildings, traffic, crowds and city life by day "
                  "and night"),
    "animation": Genre("Animation",
                       "animated: motion graphics, 3D renders and animated "
                       "illustrations rather than filmed footage", "animation"),
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
            "Write searches that land in that style, adding a style word where it "
            "helps, but the literal subject of the passage always comes first: never "
            "swap the subject for something that only fits the style.\n")


def options() -> List[Dict[str, str]]:
    return [{"name": name, "label": g.label} for name, g in GENRES.items()]
