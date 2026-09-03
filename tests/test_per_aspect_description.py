"""One description per cut, each carrying only that cut's credits.

`description.txt` is the file whose whole purpose is to be pasted into YouTube,
and it used to hold every aspect's credits stacked under [16:9] and [9:16]
labels. Pasting it named photographers whose clips are not in the video being
published; trimming it by hand instead dropped ones that are. Both happened on
real uploads, and attribution is a licence condition rather than a courtesy.
"""
from __future__ import annotations

from pathlib import Path

from vidsmith import pipeline

META = {
    "title": "A Title",
    "description": "Some prose about the video.",
    "chapters": [{"time": "0:00", "label": "Start"},
                 {"time": "0:20", "label": "Middle"}],
    "tags": ["one", "two"],
}


def _out(tmp_path: Path, **ledgers: str) -> Path:
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    for tag, body in ledgers.items():
        name = "credits.txt" if tag == "wide" else f"credits-{tag}.txt"
        (out / name).write_text(body, encoding="utf-8")
    return out


def test_each_cut_gets_its_own_description(tmp_path):
    out = _out(tmp_path,
               wide="Footage from Pexels\nWide Creator - https://w\n",
               **{"9x16": "Footage from Pexels\nTall Creator - https://t\n"})
    pipeline.write_metadata(out, META)

    wide = (out / "description.txt").read_text(encoding="utf-8")
    tall = (out / "description-9x16.txt").read_text(encoding="utf-8")

    assert "Wide Creator" in wide and "Tall Creator" not in wide
    assert "Tall Creator" in tall and "Wide Creator" not in tall


def test_neither_carries_the_aspect_labels(tmp_path):
    """`[16:9]` in the middle of a YouTube description is scaffolding that got
    pasted, which is what the labelled block was for and why it was wrong here."""
    out = _out(tmp_path,
               wide="Wide Creator - https://w\n",
               **{"9x16": "Tall Creator - https://t\n"})
    pipeline.write_metadata(out, META)

    for name in ("description.txt", "description-9x16.txt"):
        body = (out / name).read_text(encoding="utf-8")
        assert "[16:9]" not in body and "[9:16]" not in body


def test_the_widescreen_file_keeps_the_unsuffixed_name(tmp_path):
    """16:9 carries no suffix anywhere else, and a second naming convention is
    how the `*{tag}.mp4` family of faults keeps happening."""
    out = _out(tmp_path, wide="Wide Creator - https://w\n")
    pipeline.write_metadata(out, META)

    assert (out / "description.txt").exists()
    assert not (out / "description-16x9.txt").exists()


def test_youtube_txt_still_carries_everything_labelled(tmp_path):
    """That one is for reading, not pasting, so it keeps the whole picture."""
    out = _out(tmp_path,
               wide="Wide Creator - https://w\n",
               **{"9x16": "Tall Creator - https://t\n"})
    pipeline.write_metadata(out, META)

    body = (out / "youtube.txt").read_text(encoding="utf-8")
    assert "Wide Creator" in body and "Tall Creator" in body
    assert "[16:9]" in body and "[9:16]" in body


def test_a_build_owing_no_credits_still_gets_a_description(tmp_path):
    """A cards or local build writes no ledger, and the description is still the
    file you paste."""
    out = tmp_path / "out"
    out.mkdir(parents=True)
    pipeline.write_metadata(out, META)

    body = (out / "description.txt").read_text(encoding="utf-8")
    assert "Some prose" in body and "0:00 Start" in body


def test_check_wants_a_description_beside_every_ledger(tmp_path):
    from vidsmith.check import credits_published

    out = _out(tmp_path, wide="Wide Creator - https://w\n",
               **{"9x16": "Tall Creator - https://t\n"})
    pipeline.write_metadata(out, META)
    (out / "description-9x16.txt").unlink()

    problems = credits_published(out)
    assert any("credits-9x16.txt" in p and "description-9x16.txt" in p
               for p in problems), problems


def test_check_catches_a_credit_that_did_not_reach_its_own_description(tmp_path):
    """The fault this change exists for, in the direction that used to pass:
    checking every ledger against one description let a 9:16 credit through
    because the 16:9 description happened to name the same photographer."""
    from vidsmith.check import credits_published

    out = _out(tmp_path, wide="Shared Name - https://w\n",
               **{"9x16": "Only In Tall - https://t\n"})
    pipeline.write_metadata(out, META)
    # a hand-edit that drops the tall cut's only credit
    (out / "description-9x16.txt").write_text(
        "Some prose about the video.\n\n0:00 Start\n", encoding="utf-8")

    assert any("Only In Tall" in p for p in credits_published(out))
