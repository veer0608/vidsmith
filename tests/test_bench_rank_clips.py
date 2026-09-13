"""The rerank benchmark's own arithmetic.

A benchmark that miscounts is worse than none: it produces a number that gets
quoted. These pin the scoring rules on cases small enough to check by hand, and
the two behaviours a long run depends on - finding past searches under the name
production cached them, and stopping on a spent quota without losing the calls
already paid for.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from bench import rank_clips as rc
from vidsmith import llm
from vidsmith.visuals import search_cache_path

IDS = ["a", "b", "c", "d"]


def _case(case_id="p/16x9/0", ids=IDS):
    return {"id": case_id, "line": "a line", "query": "a shot",
            "candidates": [{"id": i, "sha1": ""} for i in ids],
            "production": {"order": list(ids), "reject": [], "same_pool": True}}


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def test_the_pick_is_the_best_clip_not_rejected():
    assert rc.first_pick(["a", "b", "c"], ["a"]) == "b"


def test_when_everything_is_rejected_the_best_of_a_bad_set_is_kept():
    """visuals._rerank keeps ranked[:1] rather than cutting to nothing."""
    assert rc.first_pick(["c", "a"], ["a", "c"]) == "c"


def test_search_order_scores_its_first_result():
    labels = {"a": "wrong", "b": "right", "c": "wrong", "d": "wrong"}
    s = rc.case_scores(_case(), labels, [{"order": IDS, "reject": []}])
    assert s["top"] == 0.0


def test_a_right_pick_after_rejecting_the_first_result_scores():
    labels = {"a": "wrong", "b": "right", "c": "wrong", "d": "right"}
    s = rc.case_scores(_case(), labels, [{"order": ["a", "b", "c", "d"], "reject": ["a"]}])
    assert s["top"] == 1.0


def test_repeats_are_averaged_and_agreement_is_reported():
    labels = {"a": "right", "b": "wrong", "c": "wrong", "d": "wrong"}
    runs = [{"order": ["a", "b", "c", "d"], "reject": []},
            {"order": ["b", "a", "c", "d"], "reject": []},
            {"order": ["a", "b", "c", "d"], "reject": []}]
    s = rc.case_scores(_case(), labels, runs)
    assert s["top"] == pytest.approx(2 / 3)
    assert s["agree"] is False


def test_an_unsure_pick_counts_neither_way():
    labels = {"a": "unsure", "b": "right", "c": "wrong", "d": "wrong"}
    s = rc.case_scores(_case(), labels, [{"order": IDS, "reject": []}])
    assert s["top"] is None


def test_reject_precision_and_recall_count_by_hand():
    labels = {"a": "wrong", "b": "right", "c": "wrong", "d": "wrong"}
    # rejects a (wrong, correct), b (right, a mistake); misses c and d
    s = rc.summarise([_case()], {"p/16x9/0": labels},
                     {"p/16x9/0": [{"order": IDS, "reject": ["a", "b"]}]})
    assert s["reject_precision"] == pytest.approx(1 / 2)
    assert s["reject_recall"] == pytest.approx(1 / 3)


def test_a_case_with_nothing_usable_is_scored_on_abstaining_not_on_its_pick():
    labels = {i: "wrong" for i in IDS}
    rejected_all = rc.case_scores(_case(), labels, [{"order": IDS, "reject": IDS}])
    kept_one = rc.case_scores(_case(), labels, [{"order": IDS, "reject": ["a"]}])
    unfilmable = rc.case_scores(_case(), labels,
                                [{"order": IDS, "reject": [], "filmable": False}])
    assert rejected_all["top"] is None
    assert (rejected_all["abstain"], kept_one["abstain"], unfilmable["abstain"]) == (1.0, 0.0, 1.0)


def test_a_partly_labelled_case_is_left_out():
    runs = {"p/16x9/0": [{"order": IDS, "reject": []}]}
    s = rc.summarise([_case()], {"p/16x9/0": {"a": "right"}}, runs)
    assert s["cases"] == 0


def test_the_kept_share_difference_counts_by_hand():
    """Model keeps 2 usable of 2; search order keeps 2 usable of 4, per case."""
    model = {str(i): {"kept_right": 2, "kept_judged": 2} for i in range(10)}
    base = {str(i): {"kept_right": 2, "kept_judged": 4} for i in range(10)}
    mean, lo, hi = rc.kept_interval(model, base)
    assert mean == lo == hi == pytest.approx(0.5)
    assert rc.kept_interval({"0": model["0"]}, {"0": base["0"]}) is None


def test_the_interval_is_paired_over_shared_cases():
    a = {str(i): {"top": 1.0} for i in range(20)}
    b = {str(i): {"top": 0.0} for i in range(20)}
    b["only-in-b"] = {"top": 1.0}
    mean, lo, hi = rc.paired_interval(a, b)
    assert mean == lo == hi == 1.0


def test_a_failed_call_is_scored_as_the_search_order(tmp_path):
    case = _case()
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "m.jsonl").write_text(
        json.dumps({"case": case["id"], "repeat": 0, "model": "m", "error": "HTTP 500"}) + "\n",
        encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    runs = rc.systems([case], tmp_path)["m"][case["id"]]
    assert runs[0]["order"] == IDS and runs[0]["reject"] == []


def test_the_report_names_every_system_that_has_scores(tmp_path):
    case = _case()
    (tmp_path / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    (tmp_path / "labels.json").write_text(json.dumps(
        {case["id"]: {"a": "wrong", "b": "right", "c": "wrong", "d": "wrong"}}),
        encoding="utf-8")
    report = rc.score(tmp_path)
    assert "| search order | 1 | 0% (n=1)" in report
    assert "| past builds | 1 |" in report


# --------------------------------------------------------------------------- #
# labels
# --------------------------------------------------------------------------- #
def test_labels_are_saved_and_a_bad_value_refused(tmp_path):
    rc.save_label(tmp_path, "p/16x9/0", "a", "right")
    rc.save_label(tmp_path, "p/16x9/0", "b", "wrong")
    assert rc.load_labels(tmp_path) == {"p/16x9/0": {"a": "right", "b": "wrong"}}
    with pytest.raises(ValueError):
        rc.save_label(tmp_path, "p/16x9/0", "a", "maybe")


def test_the_label_page_does_not_show_search_order():
    """Shown first, the top result would get the benefit of every doubt - and
    the top result is the baseline."""
    ids = [str(i) for i in range(8)]
    page = rc._page([_case(ids=ids)], {})
    shown = json.loads(page.split("const CASES = ")[1].split(";\n")[0])[0]["shown"]
    assert [s["id"] for s in shown] != ids
    assert sorted(s["id"] for s in shown) == sorted(ids)


# --------------------------------------------------------------------------- #
# model-written labels, checked against a human sample
# --------------------------------------------------------------------------- #
def test_kappa_by_hand():
    """4 agree right, 4 agree wrong, 1 each way: observed .8, chance .5, kappa .6."""
    ids = [str(i) for i in range(10)]
    case = _case(ids=ids)
    human = {case["id"]: dict(zip(ids, ["right"] * 5 + ["wrong"] * 5))}
    model = {case["id"]: dict(zip(ids, ["right"] * 4 + ["wrong"] + ["right"] + ["wrong"] * 4))}
    a = rc.agreement([case], human, model)
    assert a["agree"] == pytest.approx(0.8)
    assert a["kappa"] == pytest.approx(0.6)
    assert a["matrix"] == {"right/right": 4, "right/wrong": 1, "wrong/right": 1, "wrong/wrong": 4}


def test_the_kappa_interval_resamples_cases_and_needs_more_than_one():
    ids = [str(i) for i in range(4)]
    cases = [_case(f"p/16x9/{k}", ids) for k in range(6)]
    same = {c["id"]: {"0": "right", "1": "right", "2": "wrong", "3": "wrong"} for c in cases}
    assert rc.kappa_interval(cases, same, same) == (1.0, 1.0)
    assert rc.kappa_interval(cases[:1], same, same) is None

    # half the cases disagree on one still each: the interval must widen below 1
    noisy = {k: dict(v) for k, v in same.items()}
    for c in cases[:3]:
        noisy[c["id"]]["0"] = "wrong"
    lo, hi = rc.kappa_interval(cases, same, noisy)
    assert lo < rc.agreement(cases, same, noisy)["kappa"] <= hi <= 1.0


def test_agreement_leaves_out_unsure_and_unlabelled_stills():
    case = _case()
    human = {case["id"]: {"a": "right", "b": "unsure", "c": "wrong"}}
    model = {case["id"]: {"a": "right", "b": "right", "c": "wrong", "d": "wrong"}}
    a = rc.agreement([case], human, model)
    assert (a["stills"], a["judged"], a["unsure"]) == (3, 2, 1)


def test_the_label_file_notes_are_not_a_case(tmp_path):
    (tmp_path / "labels-ai.json").write_text(json.dumps(
        {"_about": "a model", "p/16x9/0": {"a": "right"}}), encoding="utf-8")
    assert rc.load_labels(tmp_path, rc.MODEL) == {"p/16x9/0": {"a": "right"}}


def test_the_sample_keeps_started_cases_and_is_fixed_once_chosen(tmp_path):
    cases = [_case(f"p/16x9/{i}") for i in range(30)]
    rc.save_label(tmp_path, "p/16x9/7", "a", "right")      # labelled by hand already
    first = rc.sample_ids(cases, tmp_path, size=20)
    assert len(first) == 20 and first[0] == "p/16x9/7"
    assert first != [f"p/16x9/{i}" for i in range(20)], "drawn at random, not page order"
    rc.save_label(tmp_path, "p/16x9/29", "a", "right")     # later labels change nothing
    assert rc.sample_ids(cases, tmp_path, size=20) == first


def test_scores_against_model_labels_always_carry_the_check(tmp_path):
    case = _case()
    (tmp_path / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    (tmp_path / "labels-ai.json").write_text(json.dumps(
        {"_about": "claude", case["id"]: {"a": "wrong", "b": "right", "c": "wrong", "d": "wrong"}}),
        encoding="utf-8")
    report = rc.score(tmp_path, labels_name=rc.MODEL)
    assert "Scored against labels written by claude" in report
    assert "Do the model's labels match a person's?" in report


# --------------------------------------------------------------------------- #
# collect and run
# --------------------------------------------------------------------------- #
def _project(root, name, aspect_dir, query, verdict):
    build = root / "projects" / name / "build"
    (build / aspect_dir).mkdir(parents=True)
    (build / "scenes.json").write_text(json.dumps(
        [{"index": 0, "text": "The line.", "query": query}]), encoding="utf-8")
    (build / aspect_dir / "rerank.json").write_text(json.dumps({"0": verdict}),
                                                   encoding="utf-8")


def test_collect_finds_the_search_production_cached(tmp_path, monkeypatch):
    """In the ROOT's cache, not the package's. The first real collect ran from a
    worktree, read the worktree's empty cache, and reported 0 cases."""
    monkeypatch.delenv("VIDSMITH_SEARCH_CACHE", raising=False)
    monkeypatch.setattr("vidsmith.visuals.preview_still", lambda url: url.encode())
    hits = [{"id": str(i), "preview": f"https://x/{i}.jpg"} for i in range(10)]
    path = search_cache_path("pexels_video", ("desk calendar", "portrait", 1080),
                             tmp_path / ".cache" / "searches")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(hits), encoding="utf-8")
    # The same query was also searched for a wide cut, with different clips.
    # The first real collect paired the vertical verdict with these.
    wide = search_cache_path("pexels_video", ("desk calendar", "landscape", 1080),
                             tmp_path / ".cache" / "searches")
    wide.write_text(json.dumps([{"id": f"w{i}", "preview": f"https://x/w{i}.jpg"}
                                for i in range(10)]), encoding="utf-8")
    verdict = {"order": [str(i) for i in range(8)], "reject": ["3"], "filmable": True}
    _project(tmp_path, "p", "visuals-9x16", "desk calendar", verdict)
    _project(tmp_path, "p", "visuals-4x5", "desk calendar", verdict)   # same search again

    cases = rc.collect(tmp_path, tmp_path / "data", log=lambda *a: None)

    assert len(cases) == 1, "the same scene at a second aspect is one case"
    case = cases[0]
    assert case["orientation"] == "portrait"
    assert [c["id"] for c in case["candidates"]] == [str(i) for i in range(8)]
    assert case["production"]["same_pool"] and case["production"]["reject"] == ["3"]
    assert case["candidates"][0]["sha1"] == hashlib.sha1(b"https://x/0.jpg").hexdigest()


def _ready(tmp_path, n_cases=2):
    stills = rc.stills_dir(tmp_path)
    stills.mkdir(parents=True)
    cases = []
    for k in range(n_cases):
        cands = []
        for i in IDS:
            blob = f"{k}{i}".encode()
            (stills / f"{k}{i}.jpg").write_bytes(blob)
            cands.append({"id": f"{k}{i}", "sha1": hashlib.sha1(blob).hexdigest()})
        cases.append({"id": f"p/16x9/{k}", "line": "l", "query": "q", "candidates": cands})
    (tmp_path / "cases.json").write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return cases


def test_a_spent_quota_stops_the_run_and_keeps_what_was_paid_for(tmp_path, monkeypatch):
    _ready(tmp_path)
    calls = []

    def fake(line, query, images, key, model, log):
        calls.append(1)
        if len(calls) == 3:
            raise llm.QuotaExhausted("500 requests a day")
        return [1, 0, 2, 3], [2], True

    monkeypatch.setattr(llm, "rank_clips", fake)
    code = rc.run(tmp_path, "key", "m", repeats=2, data_dir=tmp_path, log=lambda *a: None)
    rows = rc.read_results(rc.results_path(tmp_path, "m"))
    assert code == 2
    assert len(rows) == 2
    assert rows[0]["order"] == ["0b", "0a", "0c", "0d"] and rows[0]["reject"] == ["0c"]


def test_a_dropped_connection_stops_the_run_instead_of_scoring_it(tmp_path, monkeypatch):
    """A real run died on ConnectionResetError after 122 calls. Recorded as an
    error it would be scored as the search order, counting the network against
    the model. Driven through the real llm retry loop, because llm now turns
    the reset into an LLMUnavailable, and a plain one is what gets scored."""
    import requests

    _ready(tmp_path)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def reset(*a, **k):
        raise requests.exceptions.ConnectionError("Connection aborted.")

    monkeypatch.setattr(llm.requests, "post", reset)
    code = rc.run(tmp_path, "key", "m", repeats=2, data_dir=tmp_path, log=lambda *a: None)
    assert code == 3
    assert rc.read_results(rc.results_path(tmp_path, "m")) == []


def test_an_unusable_answer_is_still_scored_not_retried(tmp_path, monkeypatch):
    """The model answering with something unparseable is the model's failure,
    and a build falls back to the search order for it, so that is what counts."""
    _ready(tmp_path, n_cases=1)

    def junk(*a, **k):
        raise ValueError("model did not return a ranking")

    monkeypatch.setattr(llm, "rank_clips", junk)
    assert rc.run(tmp_path, "key", "m", repeats=1, data_dir=tmp_path, log=lambda *a: None) == 0
    rows = rc.read_results(rc.results_path(tmp_path, "m"))
    assert len(rows) == 1 and "error" in rows[0]


def test_a_second_run_resumes_instead_of_repaying(tmp_path, monkeypatch):
    _ready(tmp_path)
    monkeypatch.setattr(llm, "rank_clips",
                        lambda *a, **k: ([0, 1, 2, 3], [], True))
    rc.run(tmp_path, "key", "m", repeats=2, data_dir=tmp_path, limit=1, log=lambda *a: None)
    seen = []
    monkeypatch.setattr(llm, "rank_clips",
                        lambda *a, **k: seen.append(1) or ([0, 1, 2, 3], [], True))
    rc.run(tmp_path, "key", "m", repeats=2, data_dir=tmp_path, log=lambda *a: None)
    assert len(seen) == 2, "only the second case's two repeats were still owed"
    assert len(rc.read_results(rc.results_path(tmp_path, "m"))) == 4


def test_a_changed_still_is_refused_not_judged(tmp_path, monkeypatch):
    _ready(tmp_path, n_cases=1)
    (rc.stills_dir(tmp_path) / "0b.jpg").write_bytes(b"a different picture")
    monkeypatch.setattr(llm, "rank_clips", lambda *a, **k: pytest.fail("judged a changed still"))
    assert rc.run(tmp_path, "key", "m", data_dir=tmp_path, log=lambda *a: None) == 1
