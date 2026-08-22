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
