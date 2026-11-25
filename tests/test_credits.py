"""Attribution.

Pexels' API terms require crediting the creator and linking back, and this has
already broken twice: once when a second aspect overwrote the first's credits
file, and once when a cached rebuild lost the credit entirely.
"""
from __future__ import annotations

from vidsmith.pipeline import all_credits, credits_block


def _with_shots(scene, *credits):
    scene.shots = [{"path": f"s{i}.mp4", "duration": 2.0, "credit": name,
                    "credit_url": f"http://x.test/{i}"}
                   for i, name in enumerate(credits)]
    return scene


def test_every_shot_creator_is_listed(scenes):
    _with_shots(scenes[0], "Ada", "Grace")
    _with_shots(scenes[1], "Katherine")
    _with_shots(scenes[2], "Annie")
    block = credits_block(scenes, "pexels")
    for name in ("Ada", "Grace", "Katherine", "Annie"):
        assert name in block


def test_a_creator_used_twice_is_listed_once(scenes):
    _with_shots(scenes[0], "Ada", "Ada")
    _with_shots(scenes[1], "Ada")
    _with_shots(scenes[2], "Grace")
    assert credits_block(scenes, "pexels").count("Ada") == 1


def test_every_clip_is_linked_even_when_its_creator_already_is(scenes):
    """A studio shooting a series supplies several clips to one video. Skipping
    a creator already listed dropped their other links: a two-minute build
    linked 24 of its 33 clips."""
    _with_shots(scenes[0], "Ada", "Ada")
    _with_shots(scenes[1], "Ada")
    block = credits_block(scenes, "pexels")
    for i in range(2):
        assert f"http://x.test/{i}" in block
    ada = next(line for line in block.splitlines() if line.startswith("Ada"))
    assert ada == "Ada - http://x.test/0, http://x.test/1"


def test_a_clip_used_twice_is_linked_once(scenes):
    scenes[0].shots = [{"path": f"s{i}.mp4", "duration": 2.0, "credit": "Ada",
                        "credit_url": "http://x.test/same"} for i in range(2)]
    assert credits_block(scenes[:1], "pexels").count("http://x.test/same") == 1


def test_a_description_over_youtubes_limit_loses_prose_not_credits():
    from vidsmith.llm import MAX_DESCRIPTION
    from vidsmith.pipeline import description_box

    credits = "Footage from Pexels (https://www.pexels.com)\n" + "\n".join(
        f"Creator {i} - https://www.pexels.com/video/a-long-descriptive-slug-{i}/"
        for i in range(60))
    meta = {"description": "word " * 600,
            "chapters": [{"time": "0:00", "label": "Start"},
                         {"time": "1:00", "label": "Middle"}]}

    box = description_box(meta, credits)

    assert len(box.strip()) <= MAX_DESCRIPTION
    assert credits.strip() in box, "the credits were cut"
    assert "0:00 Start\n1:00 Middle" in box
    assert box.startswith("word")


def test_a_short_description_is_left_whole():
    from vidsmith.pipeline import description_box

    meta = {"description": "A short one.", "chapters": []}
    assert description_box(meta, "Footage from Pexels\nAda - http://x") == \
        "A short one.\n\nFootage from Pexels\nAda - http://x\n"


def test_the_provider_is_named_and_linked(scenes):
    _with_shots(scenes[0], "Ada")
    block = credits_block(scenes, "pexels")
    assert "Pexels" in block and "https://www.pexels.com" in block


def test_generated_cards_need_no_credits(scenes):
    for s in scenes:
        s.shots = [{"path": "c.mp4", "duration": 2.0, "credit": "", "credit_url": ""}]
    assert credits_block(scenes, "cards") == ""


def test_scenes_without_shots_fall_back_to_the_legacy_fields(scenes):
    """scenes.json written before shots existed still has to credit correctly."""
    scenes[0].shots = []
    scenes[0].credit = "Ada"
    scenes[0].credit_url = "http://x.test/ada"
    for s in scenes[1:]:
        s.shots = []
    assert "Ada" in credits_block(scenes, "pexels")


def test_each_aspect_keeps_its_own_credits(tmp_path):
    (tmp_path / "credits.txt").write_text("Footage from Pexels\nAda - http://x.test/a\n",
                                          encoding="utf-8")
    (tmp_path / "credits-9x16.txt").write_text("Footage from Pexels\nGrace - http://x.test/g\n",
                                               encoding="utf-8")
    merged = all_credits(tmp_path)
    assert "[16:9]" in merged and "[9:16]" in merged
    assert "Ada" in merged and "Grace" in merged


def test_merged_credits_are_empty_when_nothing_was_sourced(tmp_path):
    assert all_credits(tmp_path) == ""


# --------------------------------------------------------------------------- #
# a Pexels video link at its id
# --------------------------------------------------------------------------- #
import pytest  # noqa: E402

from vidsmith.pipeline import short_link  # noqa: E402


@pytest.mark.parametrize("url,expected", [
    ("https://www.pexels.com/video/seo-web-design-search-google-4549682/",
     "https://www.pexels.com/video/4549682/"),
    ("https://www.pexels.com/video/4549682/", "https://www.pexels.com/video/4549682/"),
    # a slug with digits of its own: the id is the last number, not the first
    ("https://www.pexels.com/video/blue-8-bit-12345678/",
     "https://www.pexels.com/video/12345678/"),
    ("https://pexels.com/video/a-clip-555/", "https://pexels.com/video/555/"),
    # photos were not checked against Pexels, and Pixabay's are already short
    ("https://www.pexels.com/photo/a-receipt-1234567/",
     "https://www.pexels.com/photo/a-receipt-1234567/"),
    ("https://pixabay.com/videos/id-35350051/", "https://pixabay.com/videos/id-35350051/"),
    ("", ""),
])
def test_a_pexels_video_link_is_shortened_to_its_id(url, expected):
    """btrees2 linked 74 clips in 5,656 of its 6,779 description characters,
    over YouTube's 5000; the slug is the clip's title spelled out."""
    assert short_link(url) == expected


def test_the_credits_block_carries_the_short_link_and_still_names_everyone(scenes):
    scenes[0].shots = [
        {"path": "a.mp4", "duration": 2.0, "credit": "Ada",
         "credit_url": "https://www.pexels.com/video/seo-web-design-search-google-4549682/"},
        {"path": "b.mp4", "duration": 2.0, "credit": "Ada",
         "credit_url": "https://www.pexels.com/video/4549682/"},   # the same clip
    ]
    block = credits_block(scenes[:1], "pexels")
    assert "Ada - https://www.pexels.com/video/4549682/" in block
    assert block.count("4549682") == 1, "one clip in two spellings is linked once"
    assert "seo-web-design" not in block
