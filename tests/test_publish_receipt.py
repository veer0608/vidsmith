"""Noticing that the copy on YouTube is stale, without asking YouTube.

`check --published` reads the public watch page, so it cannot see a private
draft - and a draft being pasted into the upload form is exactly when a
description is most likely to be wrong. This is the half that works then.

The case it was written for happened: a video was uploaded and its description
pasted, the build was rerun to replace one scene, and the replacement footage
carried two photographers the pasted description does not name. Every local file
agreed with every other, so `check` reported the delivery consistent. It was.
The wrong copy was on YouTube, and nothing had any way to say so.
"""
from __future__ import annotations

import json

import pytest

from vidsmith.check import publish_drift
from vidsmith.published import RECEIPT, digest, record


@pytest.fixture
def out(tmp_path):
    (tmp_path / "description.txt").write_text(
        "A description naming everyone.\n", encoding="utf-8")
    (tmp_path / "credits.txt").write_text(
        "Footage from Pexels (https://www.pexels.com)\n"
        "Kelly Lacy - https://www.pexels.com/video/1/\n", encoding="utf-8")
    return tmp_path


def test_a_delivery_never_published_says_nothing(out):
    """Most projects. A check that talks about publishing before anything has
    been published is noise."""
    assert publish_drift(out) == []


def test_an_untouched_delivery_says_nothing(out):
    record(out, "0PkBP0dk4Lw")
    assert publish_drift(out) == []


def test_a_rebuilt_description_is_reported(out):
    record(out, "0PkBP0dk4Lw")
    (out / "description.txt").write_text("Rewritten.\n", encoding="utf-8")

    problems = publish_drift(out)
    assert len(problems) == 1
    assert "description.txt" in problems[0]
    assert "0PkBP0dk4Lw" in problems[0]


def test_changed_credits_are_reported(out):
    """The licence-bearing half, and the one that actually moved: a rebuild
    pulled different footage and added two photographers."""
    record(out, "0PkBP0dk4Lw")
    (out / "credits.txt").write_text(
        "Footage from Pexels (https://www.pexels.com)\n"
        "Kelly Lacy - https://www.pexels.com/video/1/\n"
        "Nothing Ahead - https://www.pexels.com/video/2/\n", encoding="utf-8")

    problems = publish_drift(out)
    assert len(problems) == 1
    assert "credits.txt" in problems[0]


def test_both_files_are_named_in_one_finding(out):
    record(out, "0PkBP0dk4Lw")
    (out / "description.txt").write_text("Rewritten.\n", encoding="utf-8")
    (out / "credits.txt").write_text("Different.\n", encoding="utf-8")

    problems = publish_drift(out)
    assert len(problems) == 1, "one delivery, one thing to do about it"
    assert "description.txt" in problems[0] and "credits.txt" in problems[0]


def test_a_deleted_file_is_reported(out):
    record(out, "0PkBP0dk4Lw")
    (out / "description.txt").unlink()
    assert any("missing" in p for p in publish_drift(out))


def test_it_says_what_to_do(out):
    record(out, "0PkBP0dk4Lw")
    (out / "description.txt").write_text("Rewritten.\n", encoding="utf-8")
    assert "check --published" in publish_drift(out)[0]


def test_the_receipt_records_the_url_shape_that_was_pasted(out):
    """Somebody will paste a URL rather than an id, and the receipt has to hold
    the id either way or the message it prints is not clickable."""
    record(out, "https://www.youtube.com/watch?v=0PkBP0dk4Lw&t=9s")
    body = json.loads((out / RECEIPT).read_text(encoding="utf-8"))
    assert body["video_id"] == "0PkBP0dk4Lw"


def test_a_malformed_receipt_is_ignored_rather_than_raised(out):
    """A checker that starts crashing on its own metadata is a checker that
    stops running on the deliveries most likely to be wrong."""
    (out / RECEIPT).write_text("{not json", encoding="utf-8")
    assert publish_drift(out) == []

    (out / RECEIPT).write_text('["a list"]', encoding="utf-8")
    assert publish_drift(out) == []


def test_a_receipt_from_before_a_file_existed_is_not_a_finding(out):
    """An empty recorded digest means the file was not there to witness, which
    is not the same as it having changed."""
    (out / RECEIPT).write_text(json.dumps({
        "video_id": "0PkBP0dk4Lw", "checked": "2026-09-04T00:00:00+00:00",
        "files": {"description.txt": "", "credits.txt": digest(out / "credits.txt")},
    }), encoding="utf-8")
    assert publish_drift(out) == []


def test_check_reports_it_alongside_the_offline_faults(out, tmp_path):
    """It has to reach `check` itself, not only be callable."""
    from vidsmith import check as check_mod

    record(out, "0PkBP0dk4Lw")
    (out / "credits.txt").write_text("Different.\n", encoding="utf-8")
    problems = check_mod.check(out)

    assert any("changed since this delivery was checked" in p for p in problems), problems


# --------------------------------------------------------------------------- #
# a stale description and a new cut get opposite advice
# --------------------------------------------------------------------------- #
@pytest.fixture
def delivered(out):
    (out / "a-video.mp4").write_bytes(b"the published cut")
    (out / "a-video-9x16.mp4").write_bytes(b"the short")
    return out


def test_the_receipt_records_the_cut(delivered):
    record(delivered, "0PkBP0dk4Lw")

    body = json.loads((delivered / RECEIPT).read_text(encoding="utf-8"))
    assert body["cut"] == {"name": "a-video.mp4",
                           "digest": digest(delivered / "a-video.mp4")}


def test_a_short_receipt_records_the_short(delivered):
    record(delivered, "0PkBP0dk4Lw", tag="-9x16")

    body = json.loads((delivered / RECEIPT).read_text(encoding="utf-8"))
    assert body["cut"]["name"] == "a-video-9x16.mp4"


def test_a_rebuilt_cut_is_a_new_cut_not_a_stale_description(delivered):
    """howto was rebuilt and deliberately not uploaded; check told a person to
    re-paste the new description onto the old video, which would have credited
    eighteen photographers whose footage is not in it."""
    record(delivered, "eNhDPX7s_Xs")
    (delivered / "a-video.mp4").write_bytes(b"a rebuilt cut")
    (delivered / "credits.txt").write_text("New footage, new people.\n", encoding="utf-8")

    [problem] = publish_drift(delivered)
    assert "this is a new cut" in problem
    assert "nothing public is wrong" in problem
    assert "never paste this description onto the old video" in problem
    assert "re-paste it" not in problem


def test_a_description_rebuilt_over_the_same_cut_says_re_paste(delivered):
    record(delivered, "0PkBP0dk4Lw")
    (delivered / "description.txt").write_text("Rewritten.\n", encoding="utf-8")

    [problem] = publish_drift(delivered)
    assert "is stale; re-paste it" in problem
    assert "new cut" not in problem


def test_an_old_receipt_with_no_cut_gives_both_answers(out):
    """Receipts written before the cut was recorded cannot tell which it is,
    so they say so rather than guessing the dangerous one."""
    (out / RECEIPT).write_text(json.dumps({
        "video_id": "eNhDPX7s_Xs", "checked": "2026-09-04T08:13:35+00:00",
        "files": {"description.txt": digest(out / "description.txt"),
                  "credits.txt": "0" * 12}}), encoding="utf-8")

    [problem] = publish_drift(out)
    assert "cannot tell" in problem
    assert "never paste this description onto the old video" in problem
