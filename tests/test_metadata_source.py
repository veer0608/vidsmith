"""The source a video was built from, credited in the file people paste.

A video made out of someone else's writing should say whose. vidsmith is the
only thing that writes the description, and until now nothing told it where a
script came from, so a generated video credited its stock photographers and not
its source.
"""

import json

from vidsmith.config import Config
from vidsmith.pipeline import description_box, source_credit, write_metadata

META = {
    "title": "A video",
    "description": "Three short paragraphs about the thing.",
    "chapters": [{"time": "0:00", "label": "Open"}],
}


def test_a_source_becomes_an_attribution_line():
    assert source_credit("https://example.com/post") == "Adapted from: https://example.com/post"


def test_no_source_credits_nothing():
    assert source_credit("") == ""
    assert source_credit("   ") == ""


def test_the_description_people_paste_carries_it(tmp_path):
    write_metadata(tmp_path, META, source="https://example.com/post")
    pasted = (tmp_path / "description.txt").read_text(encoding="utf-8")
    assert "Adapted from: https://example.com/post" in pasted


def test_it_survives_alongside_footage_credits(tmp_path):
    (tmp_path / "credits-9x16.txt").write_text("Photo by Someone", encoding="utf-8")
    write_metadata(tmp_path, META, source="https://example.com/post")
    pasted = (tmp_path / "description-9x16.txt").read_text(encoding="utf-8")
    # Two different debts, and neither may evict the other.
    assert "Adapted from: https://example.com/post" in pasted
    assert "Photo by Someone" in pasted


def test_a_build_with_no_source_is_unchanged(tmp_path):
    write_metadata(tmp_path, META, source="")
    pasted = (tmp_path / "description.txt").read_text(encoding="utf-8")
    assert "Adapted from" not in pasted
    assert pasted.startswith("Three short paragraphs")


def test_the_readable_file_lists_it_too(tmp_path):
    write_metadata(tmp_path, META, source="https://example.com/post")
    assert "SOURCE" in (tmp_path / "youtube.txt").read_text(encoding="utf-8")


def test_the_attribution_is_never_model_written(tmp_path):
    # The description is generated; this line is not. A paraphrased attribution
    # is not an attribution, so it is assembled after the model has finished.
    write_metadata(tmp_path, META, source="https://example.com/a?b=1&c=2")
    pasted = (tmp_path / "description.txt").read_text(encoding="utf-8")
    assert "https://example.com/a?b=1&c=2" in pasted


def test_config_carries_a_source_and_defaults_to_none():
    assert Config().source == ""
    assert Config(source="x").source == "x"


def test_the_json_is_untouched_by_it(tmp_path):
    # youtube.json is the model's output. Attribution is ours and belongs in the
    # rendered files, not folded back into what the model said.
    write_metadata(tmp_path, META, source="https://example.com/post")
    assert json.loads((tmp_path / "youtube.json").read_text(encoding="utf-8")) == META


def test_a_source_in_the_file_reaches_the_config(tmp_path):
    """The gap the unit tests left open.

    `Config(source=...)` worked and `write_metadata(source=...)` worked, and
    nothing exercised the path between them. load_config reads top-level
    scalars one at a time, so a field added to Config is invisible there until
    it is named, and a real video was generated crediting its photographers and
    not the article it was built from while every test passed.
    """
    from pathlib import Path

    from vidsmith.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "title: A video\nsource: https://example.com/post\nrender:\n  aspect: '9:16'\n",
        encoding="utf-8",
    )
    assert load_config(Path(path)).source == "https://example.com/post"


def test_a_config_without_a_source_still_loads(tmp_path):
    from pathlib import Path

    from vidsmith.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text("title: A video\n", encoding="utf-8")
    assert load_config(Path(path)).source == ""


def test_the_round_trip_ends_in_the_pasted_description(tmp_path):
    # config on disk -> load_config -> write_metadata -> description.txt.
    # Every step in one test, because the fault lived between two of them.
    from pathlib import Path

    from vidsmith.config import load_config
    from vidsmith.pipeline import write_metadata

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("title: A video\nsource: https://example.com/post\n", encoding="utf-8")
    cfg = load_config(Path(cfg_path))
    out = tmp_path / "out"
    write_metadata(out, META, source=cfg.source)
    assert "Adapted from: https://example.com/post" in (out / "description.txt").read_text(
        encoding="utf-8"
    )
