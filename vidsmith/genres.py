"""The kind of footage a video should be cut from.

Pexels has no genre filter, so a genre works through the two model calls that
decide the footage. The search writer puts a style word into every search, and
the reranker, looking at the stills, puts the clips in that style first among
those showing the right subject.

There is no Animation style, deliberately. It was built and tested on a
delivery script: Pexels has no animation filter and returned five filmed shots
and one cartoon; Pixabay's `video_type=animation` filter worked, but its
animated library held a SUBSCRIBE title, a FREE advert and a hot dog under a
parcel line, and after the reranker was taught to reject those, 40 of 48
candidates were rejected and most of the 8 kept were still unrelated. A stock
library cannot supply animation about a concrete subject, so it was removed.

A first nature build on a coffee script moved its searches by one word at most
("coffee cherries on branch" became "on hillside bush", the mug on the desk did
not move), because the search writer was only told to add a style word "where
it helps" and the reranker was never told a style existed. Both now carry it.

`any` is the default and adds nothing to a prompt or a cache key, so a build
that never chose a genre searches and ranks exactly as it did before genres.
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple


class Genre(NamedTuple):
    label: str
    # finishes the sentence "The footage for this video should be ..."
    direction: str
    # words a stock library answers to for this look, offered to the search
    # writer as the style half of a search. Each is ONE word, hyphenated if it
    # has to be: `beat_queries` keeps six words, and "slow motion" and "close
    # up" reached Pexels as "slow" and "close". No digits either, because the
    # same clean-up strips them and "3D" arrives as "D".
    words: str = ""
    # strip device words from a search whose narration names no device
    devices_only_if_said: bool = False


# Words that put a screen in the shot. The second set is narration that makes a
# screen the literal subject: "you tap buy" is a phone, "the app" is a screen.
DEVICE_WORDS = frozenset(
    "screen screens phone phones smartphone smartphones gps app apps laptop laptops "
    "tablet tablets monitor monitors computer computers digital data display "
    "displays dashboard interface map maps software code website online "
    "navigation navigator directions satnav".split())
DEVICE_CUES = DEVICE_WORDS | frozenset(
    "tap taps tapped click clicks clicked type typed typing email emails "
    "internet web browser keyboard device devices".split())


GENRES: Dict[str, Genre] = {
    "any": Genre("Any", ""),
    "cinematic": Genre("Cinematic",
                       "cinematic: slow motion, shallow depth of field, dramatic "
                       "light and smooth camera moves",
                       "cinematic, slow-motion, closeup, sunset, moody, dramatic"),
    "documentary": Genre("Documentary",
                         "documentary: real people in real places, handheld camera, "
                         "natural light",
                         "real, candid, handheld, worker, local, daylight"),
    "business": Genre("Business",
                      "business: offices, meetings, professionals at work, clean "
                      "modern workplaces",
                      "office, professional, meeting, corporate, team"),
    # Its words used to be nouns - screen, laptop, data - and a style word that
    # is a noun becomes the subject: a delivery video turned into phone maps,
    # with "the box lands on your doorstep" shown as a hand holding a phone.
    # They describe a look now, and screens only come from the narration. The
    # prompt alone did not hold: on the live site it wrote "modern delivery
    # driver handheld gps" and the doorstep line was a phone on a dashboard
    # again, so `scrub()` removes a device word the narration never said.
    "technology": Genre("Technology",
                        "technology: modern, high-tech and automated settings with "
                        "clean, cool lighting; screens and devices only where the "
                        "narration is about software or a device",
                        "modern, high-tech, automated, sleek, blue-lit",
                        devices_only_if_said=True),
    "nature": Genre("Nature",
                    "nature: landscapes, wildlife, water, forests, sky and the "
                    "outdoors",
                    "outdoors, sunlight, field, forest, mountain, river, green"),
    "city": Genre("City",
                  "urban: streets, buildings, traffic, crowds and city life by day "
                  "and night",
                  "city, street, urban, downtown, nightlife, crowd"),
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
            "Every search must include one of these style words: "
            f"{genre.words}. A search with no style word is wrong. Count the style "
            "word inside the word limit, because a longer search is cut off at the "
            "end: make room by dropping filler such as \"at\", \"the\" or \"a\", "
            "never by leaving the style word out. The literal subject of the passage "
            "always comes first: never swap the subject for something that only fits the style, and never "
            "drop the subject to make room for a style word. Any place or setting the "
            "passage names is part of the subject too, so keep it and add the style "
            "as light, look or detail: \"the mug on your desk\" in a nature video is "
            "\"coffee mug on sunlit desk\", never a mug outdoors, and \"shipped across "
            "the ocean\" keeps its ship and its ocean. Only when the passage names no "
            "place may the style choose one. A style word describes the shot and never "
            "becomes its subject: a passage about a truck is still a truck, not a "
            "screen or a phone in one.\n")


def scrub(name: str, query: str, narration: str) -> str:
    """Take device words out of a search when the narration names no device.

    Enforced in code because the prompt already forbids it and the model did it
    anyway. A search left with nothing is returned empty, and the callers keep
    the scene's own search instead.
    """
    if not get(name).devices_only_if_said:
        return query
    if names_a_device(narration):
        return query
    return " ".join(w for w in query.split()
                    if not set(re.findall(r"[a-z]+", w.lower())) & DEVICE_WORDS)


def names_a_device(narration: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", narration.lower())) & DEVICE_CUES)


def rerank_block(name: str, line: str = "") -> str:
    """The style paragraph for the clip reranker, or nothing for `any`.

    Style orders the clips that already show the right subject. It never
    rescues a wrong one and is never a reason to reject, or a scene short of
    on-style footage would lose the right footage too.

    The one exception is a device under narration that names none. `scrub()`
    works on words, and after it removed "gps" the search came back "sleek van
    navigation" and Pexels answered with a phone map on a dashboard, under "the
    box lands on your doorstep". The reranker sees the picture, so it catches a
    screen whatever word fetched it.
    """
    genre = get(name)
    if not genre.direction:
        return ""
    devices = ""
    if genre.devices_only_if_said and not names_a_device(line):
        devices = (" This narration names no phone, screen or computer, so a clip "
                   "whose main subject is a phone, a screen, a map or navigation "
                   "display, a tablet, a monitor or a laptop shows the wrong subject: "
                   "reject it.")
    return (f"\nSTYLE: this video's footage should be {genre.direction}. Among the "
            "clips that show the right subject, rank the ones in that style above "
            "the ones that are not. Style never makes up for the wrong subject, and "
            f"a clip is never rejected for being off style.{devices}\n")


def options() -> List[Dict[str, str]]:
    return [{"name": name, "label": g.label} for name, g in GENRES.items()]
