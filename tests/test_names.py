"""A name keeps its owner's spelling in everything uploaded.

The howto title is "How To Use vidsmith And How To Buy It". The model drafting
the upload title-cased the brand, and it went out as "How To Use Vidsmith" with
"use Vidsmith to turn" in the description, beside an earlier upload that said
vidsmith. Asking the model is not a rule; this is.
"""
from __future__ import annotations

import json

import pytest
import yaml

from vidsmith import llm, pipeline
from vidsmith.config import load_config, write_default_config


@pytest.mark.parametrize("title, names", [
    ("How To Use vidsmith And How To Buy It", ["vidsmith"]),
    ("vidsmith Short", ["vidsmith"]),
    ("What vidsmith Does", ["vidsmith"]),
    ("Fixing macOS Audio On An iPhone", ["macOS", "iPhone"]),
    # nothing in these is spelt against the title's own case
    ("Meet Claude, Anthropic's AI Assistant", []),
    ("Why Python's GIL Still Matters", []),
    ("Why Your Captions Are Always Slightly Off", []),
    # sentence case says nothing about which lower-case words are names
    ("Understanding how indexes work", []),
    ("How to fix your query plan", []),
    ("", []),
])
def test_a_title_names_what_it_spells_on_purpose(title, names):
    assert llm.title_names(title) == names


def test_every_model_written_field_is_respelt():
    meta = {
        "title": "How To Use Vidsmith And How To Buy It",
        "description": "Use VIDSMITH to turn scripts into video. Vidsmith's "
                       "output is yours; vidsmithy is not a word.",
        "tags": ["Vidsmith", "video generation"],
        "chapters": [{"time": "0:00", "label": "Installing Vidsmith"}],
        "narration_key": "Vidsmith stays, this is not model text",
    }
    out = llm.spell_names(meta, ["vidsmith"])
    assert out["title"] == "How To Use vidsmith And How To Buy It"
    assert out["description"] == ("Use vidsmith to turn scripts into video. "
                                  "vidsmith's output is yours; vidsmithy is not a word.")
    assert out["tags"] == ["vidsmith", "video generation"]
    assert out["chapters"] == [{"time": "0:00", "label": "Installing vidsmith"}]
    assert out["narration_key"] == meta["narration_key"]
    assert meta["title"].startswith("How To Use Vidsmith"), "the input is not changed"


def test_respelling_its_own_output_changes_nothing():
    once = llm.spell_names({"title": "Vidsmith on an IPHONE"}, ["vidsmith", "iPhone"])
    assert once == {"title": "vidsmith on an iPhone"}
    assert llm.spell_names(once, ["vidsmith", "iPhone"]) == once


def test_no_names_changes_nothing():
    meta = {"title": "Why Your Backup Has Never Been Tested"}
    assert llm.spell_names(meta, []) is meta


# --------------------------------------------------------------------------- #
# through the one writer
# --------------------------------------------------------------------------- #
def _project(tmp_path, title, **top):
    root = tmp_path / "proj"
    root.mkdir()
    write_default_config(root / "config.yaml", title)
    if top:
        raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        raw.update(top)
        (root / "config.yaml").write_text(yaml.safe_dump(raw, sort_keys=False),
                                          encoding="utf-8")
    (root / "out").mkdir()
    (root / "out" / "credits.txt").write_text("", encoding="utf-8")
    return root


META = {"title": "How To Use Vidsmith And How To Buy It",
        "description": "Learn how to use Vidsmith on macos.",
        "tags": ["vidsmith"], "chapters": []}


def test_the_writer_respells_every_file_it_writes(tmp_path):
    """Including a youtube.json written before the rule, handed back unchanged."""
    root = _project(tmp_path, "How To Use vidsmith And How To Buy It")
    pipeline.write_metadata(root / "out", dict(META))
    for name in ("youtube.json", "youtube.txt", "description.txt"):
        text = (root / "out" / name).read_text(encoding="utf-8")
        assert "Vidsmith" not in text, name
        assert "vidsmith" in text, name
    saved = json.loads((root / "out" / "youtube.json").read_text(encoding="utf-8"))
    assert saved["title"] == "How To Use vidsmith And How To Buy It"


def test_a_name_the_title_does_not_carry_is_listed_in_config(tmp_path):
    root = _project(tmp_path, "How To Use vidsmith And How To Buy It", names=["macOS"])
    pipeline.write_metadata(root / "out", dict(META))
    text = (root / "out" / "description.txt").read_text(encoding="utf-8")
    assert "use vidsmith on macOS." in text


def test_one_name_without_brackets_is_still_a_list(tmp_path):
    root = _project(tmp_path, "Anything", names="macOS")
    assert load_config(root / "config.yaml").names == ["macOS"]


def test_a_new_project_lists_no_names(tmp_path):
    root = _project(tmp_path, "Anything")
    assert load_config(root / "config.yaml").names == []
