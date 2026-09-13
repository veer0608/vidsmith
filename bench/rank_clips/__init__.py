"""Does the clip reranker beat the stock site's own order, and by how much?

`llm.rank_clips` picks the footage behind every scene. Without a Gemini key the
build keeps the search's own order instead, so that order is the baseline the
model has to beat. It is measured here against labels a person wrote, on
searches production actually ran:

    python -m bench.rank_clips collect --root C:/Users/veera/claude/vidsmith
    python -m bench.rank_clips label   --root C:/Users/veera/claude/vidsmith
    python -m bench.rank_clips run     --root C:/Users/veera/claude/vidsmith
    python -m bench.rank_clips score   --write

`--root` is the checkout whose projects were built, because the searches and
past verdicts live in its `.cache/` and `projects/*/build/`, and neither is
committed. The stills are not committed either: `cases.json` records each
candidate's id, preview URL and a hash of the still, and `collect` fetches the
stills into `.cache/bench/`. A still that no longer matches its hash is refused
rather than judged, since a different picture is a different test case.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
POOL = 8                        # visuals.rerank_pool's default
PROVIDER = "pexels_video"
LABELS = ("right", "wrong", "unsure")
ORIENTATIONS = ("landscape", "portrait", "square")


def stills_dir(root: Path) -> Path:
    return root / ".cache" / "bench" / "rank_clips" / "stills"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _sha1(blob: bytes) -> str:
    return hashlib.sha1(blob).hexdigest()


# --------------------------------------------------------------------------- #
# collect: past searches and verdicts -> cases.json + stills on disk
# --------------------------------------------------------------------------- #
def search_cache(root: Path) -> Path:
    """The searches `root`'s builds made. Not the default cache location, which
    is relative to the installed package: run from a worktree, that is the
    worktree's empty cache, and collect found nothing at all."""
    override = os.environ.get("VIDSMITH_SEARCH_CACHE")
    return Path(override) if override else root / ".cache" / "searches"


def _find_search(query: str, cache: Path, aspect: str,
                 judged: Sequence[str]) -> Optional[Tuple[List[Dict], str]]:
    """The search a past verdict was made over, and its orientation.

    The same query is often cached at two orientations, because a project has
    a wide cut and a vertical one. Taking the first found paired vertical
    verdicts with landscape results. So prefer the search holding every clip
    the verdict judged, then the orientation a build of that aspect searches at
    (`pipeline._apply_overrides`: 9:16 and 4:5 are portrait).
    """
    from vidsmith.config import ASPECTS
    from vidsmith.visuals import search_cache_path

    found: Dict[str, List[Dict]] = {}
    heights = sorted({min(size) for size in ASPECTS.values()})
    for orientation in ORIENTATIONS:
        for want_h in heights:
            hits = _read_json(
                search_cache_path(PROVIDER, (query, orientation, want_h), cache), None)
            if isinstance(hits, list) and hits and orientation not in found:
                found[orientation] = hits

    wanted = set(judged)
    for orientation, hits in found.items():
        if wanted and wanted <= {str(h["id"]) for h in hits}:
            return hits, orientation
    likely = "portrait" if aspect in ("9x16", "4x5") else "landscape"
    for orientation in (likely, *ORIENTATIONS):
        if orientation in found:
            return found[orientation], orientation
    return None


def collect(root: Path, data_dir: Path = HERE, pool: int = POOL, log=print) -> List[Dict]:
    """Every past rerank whose search is still on disk becomes a case.

    The candidates are the first `pool` results in search order, which is the
    order production shows the model and the order the baseline keeps. The past
    verdict is kept alongside, marked `same_pool` only when production judged
    exactly these candidates, because a verdict over fifteen clips is not a
    verdict over the first eight.
    """
    from vidsmith.visuals import preview_still

    out = stills_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    cache = search_cache(root)
    cases: List[Dict] = []
    seen = set()
    for verdict_file in sorted(root.glob("projects/*/build/visuals*/rerank.json")):
        project = verdict_file.parents[2]
        aspect = verdict_file.parent.name.partition("-")[2] or "16x9"
        scenes = {str(s.get("index")): s for s in
                  _read_json(project / "build" / "scenes.json", [])}
        verdicts = _read_json(verdict_file, {})
        for idx in sorted(verdicts, key=lambda k: int(k) if k.isdigit() else 0):
            scene, verdict = scenes.get(idx), verdicts[idx]
            if not scene or not isinstance(verdict, dict) or not scene.get("query"):
                continue
            judged = [str(i) for i in verdict.get("order") or []]
            found = _find_search(scene["query"], cache, aspect, judged)
            if found is None:
                continue
            hits, orientation = found
            key = (scene["text"].strip(), scene["query"].strip(), orientation)
            if key in seen:
                continue            # the same scene built again at another aspect

            candidates = []
            for hit in hits[:pool]:
                if not hit.get("preview"):
                    continue
                still = out / f"{hit['id']}.jpg"
                if still.exists():
                    blob = still.read_bytes()
                else:
                    blob = preview_still(hit["preview"])
                    if not blob:
                        continue    # production drops a still it cannot fetch too
                    still.write_bytes(blob)
                candidates.append({"id": str(hit["id"]), "preview": hit["preview"],
                                   "page": hit.get("page", ""),
                                   "author": hit.get("author", ""), "sha1": _sha1(blob)})
            if len(candidates) < 2:
                continue            # rank_clips does not judge fewer than two

            ids = [c["id"] for c in candidates]
            seen.add(key)
            cases.append({
                "id": f"{project.name}/{aspect}/{idx}",
                "line": scene["text"].strip(),
                "query": scene["query"].strip(),
                "orientation": orientation,
                "candidates": candidates,
                "production": {
                    "order": [i for i in judged if i in ids],
                    "reject": [str(i) for i in verdict.get("reject") or [] if str(i) in ids],
                    "filmable": verdict.get("filmable", True) is not False,
                    "same_pool": sorted(judged) == sorted(ids),
                },
            })
            log(f"  {cases[-1]['id']:<32} {len(candidates)} stills  {scene['query'][:50]}")

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "cases.json").write_text(
        json.dumps({"pool": pool, "provider": PROVIDER, "cases": cases}, indent=1),
        encoding="utf-8")
    log(f"{len(cases)} cases, {sum(len(c['candidates']) for c in cases)} stills to label")
    return cases


def load_cases(data_dir: Path = HERE) -> List[Dict]:
    return _read_json(data_dir / "cases.json", {}).get("cases", [])


HUMAN = "labels.json"
MODEL = "labels-ai.json"


def load_labels(data_dir: Path = HERE, name: str = HUMAN) -> Dict[str, Dict[str, str]]:
    """{case id: {candidate id: label}}. Keys starting with `_` are notes about
    the file - who or what wrote it - and never a case."""
    raw = _read_json(data_dir / name, {})
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# the human sample the model's labels are checked against
# --------------------------------------------------------------------------- #
SAMPLE_SEED = "rank_clips-sample-v1"


def sample_ids(cases: Sequence[Dict], data_dir: Path = HERE, size: int = 20) -> List[str]:
    """The cases a person labels to check the model's labels, fixed once chosen.

    Cases already started by hand are kept in it, because they were labelled
    blind and throwing them away wastes that work. The rest are drawn at random
    rather than taken from the top of the page, which is ordered by project.
    Written to sample.json on first use, so a later call cannot quietly choose a
    different sample after some of the answers are known.
    """
    path = data_dir / "sample.json"
    fixed = _read_json(path, None)
    known = {c["id"] for c in cases}
    if isinstance(fixed, list) and fixed:
        return [i for i in fixed if i in known]
    started = [c["id"] for c in cases if c["id"] in load_labels(data_dir)]
    rest = [c["id"] for c in cases if c["id"] not in started]
    random.Random(SAMPLE_SEED).shuffle(rest)
    chosen = started + rest[:max(0, size - len(started))]
    path.write_text(json.dumps(chosen, indent=1), encoding="utf-8")
    return chosen


def agreement(cases: Sequence[Dict], a: Dict[str, Dict[str, str]],
              b: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """How far two sets of labels agree, over stills both labelled.

    Cohen's kappa over right and wrong, because raw agreement flatters a set
    where nearly everything is one label: if nine stills in ten are right, two
    labellers who never look agree most of the time. A still either side
    marked unsure is left out of kappa and counted separately.
    """
    both = rr = rw = wr = ww = unsure = 0
    for case in cases:
        la, lb = a.get(case["id"]) or {}, b.get(case["id"]) or {}
        for cand in case["candidates"]:
            va, vb = la.get(cand["id"]), lb.get(cand["id"])
            if va is None or vb is None:
                continue
            both += 1
            if "unsure" in (va, vb):
                unsure += 1
            elif va == vb == "right":
                rr += 1
            elif va == vb == "wrong":
                ww += 1
            elif va == "right":
                rw += 1
            else:
                wr += 1
    n = rr + rw + wr + ww
    if not n:
        return {"stills": both, "judged": 0, "unsure": unsure, "agree": None, "kappa": None,
                "matrix": {"right/right": 0, "right/wrong": 0, "wrong/right": 0, "wrong/wrong": 0}}
    observed = (rr + ww) / n
    expected = ((rr + rw) * (rr + wr) + (wr + ww) * (rw + ww)) / (n * n)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else None
    return {"stills": both, "judged": n, "unsure": unsure, "agree": observed, "kappa": kappa,
            "matrix": {"right/right": rr, "right/wrong": rw, "wrong/right": wr, "wrong/wrong": ww}}


def kappa_interval(cases: Sequence[Dict], a: Dict[str, Dict[str, str]],
                   b: Dict[str, Dict[str, str]], resamples: int = 2000,
                   seed: int = 0) -> Optional[Tuple[float, float]]:
    """95% bootstrap interval for kappa, resampling whole cases.

    Cases, not stills, because the eight stills of one case share a query and
    a labeller's reading of it: resampling stills would treat 80 correlated
    judgements as 80 independent ones and report an interval far too tight.
    """
    if len(cases) < 2:
        return None
    rng = random.Random(seed)
    kappas = sorted(k for k in (
        agreement([rng.choice(cases) for _ in cases], a, b)["kappa"]
        for _ in range(resamples)) if k is not None)
    if len(kappas) < resamples // 2:
        return None
    return kappas[int(len(kappas) * 0.025)], kappas[int(len(kappas) * 0.975) - 1]


# --------------------------------------------------------------------------- #
# label: a local page that writes labels.json on every click
# --------------------------------------------------------------------------- #
PAGE = """<!doctype html><meta charset="utf-8"><title>Label rank_clips</title>
<style>
 body{font:15px system-ui;margin:0;background:#101422;color:#e8ebf2}
 header{position:sticky;top:0;background:#1a2036;padding:12px 20px;z-index:2}
 .case{padding:18px 20px;border-bottom:1px solid #2a3150}
 .line{font-size:16px;max-width:900px} .query{color:#ffc24b;margin:6px 0 12px}
 .grid{display:flex;flex-wrap:wrap;gap:12px}
 .c{width:220px;background:#1a2036;border:3px solid #2a3150;border-radius:8px;padding:6px}
 .c img{width:100%;height:150px;object-fit:contain;background:#000;border-radius:4px}
 .c.right{border-color:#5ee6a8} .c.wrong{border-color:#ff7a59} .c.unsure{border-color:#8c93a8}
 button{font:inherit;margin:4px 2px 0;padding:3px 10px;border-radius:6px;border:1px solid #444;
   background:#232c52;color:#e8ebf2;cursor:pointer}
 .done{color:#5ee6a8}
</style>
<header><b>Right subject</b> = the still literally shows what the intended shot
describes, usable behind this line. Judge the subject, not how good the photo is.
Every click saves. <span id="progress"></span></header>
<main id="cases"></main>
<script>
const CASES = __CASES__;
let LABELS = __LABELS__;
function progress(){
  let n=0, t=0, done=0;
  for (const c of CASES){ let all=true;
    for (const k of c.shown){ t++; if ((LABELS[c.id]||{})[k.id]) n++; else all=false; }
    if (all) done++; }
  document.getElementById('progress').textContent = `${n} of ${t} stills, ${done} of ${CASES.length} cases complete`;
}
async function mark(caseId, candId, value, el){
  LABELS[caseId] = LABELS[caseId] || {}; LABELS[caseId][candId] = value;
  el.className = 'c ' + value; progress();
  await fetch('/label', {method:'POST', body: JSON.stringify({case: caseId, candidate: candId, value})});
}
const root = document.getElementById('cases');
for (const c of CASES){
  const div = document.createElement('div'); div.className='case';
  div.innerHTML = `<div class="line"></div><div class="query"></div><div class="grid"></div>`;
  div.querySelector('.line').textContent = c.line;
  div.querySelector('.query').textContent = 'intended shot: ' + c.query;
  const grid = div.querySelector('.grid');
  for (const k of c.shown){
    const cell = document.createElement('div');
    cell.className = 'c ' + ((LABELS[c.id]||{})[k.id] || '');
    cell.innerHTML = `<img loading="lazy" src="/stills/${k.id}.jpg">`;
    for (const v of ['right','wrong','unsure']){
      const b = document.createElement('button'); b.textContent = v;
      b.onclick = () => mark(c.id, k.id, v, cell); cell.appendChild(b);
    }
    grid.appendChild(cell);
  }
  root.appendChild(div);
}
progress();
</script>"""


def _page(cases: Sequence[Dict], labels: Dict) -> str:
    shown = []
    for case in cases:
        # Shuffled for display, seeded by the case so a reload keeps its place:
        # shown in search order, the first still would get the benefit of every
        # doubt, and that is the baseline being measured.
        order = list(case["candidates"])
        random.Random(case["id"]).shuffle(order)
        shown.append({"id": case["id"], "line": case["line"], "query": case["query"],
                      "shown": [{"id": c["id"]} for c in order]})
    blob = json.dumps(shown).replace("</", "<\\/")
    return PAGE.replace("__CASES__", blob).replace(
        "__LABELS__", json.dumps(labels).replace("</", "<\\/"))


def save_label(data_dir: Path, case_id: str, candidate: str, value: str) -> None:
    if value not in LABELS:
        raise ValueError(f"label must be one of {LABELS}")
    labels = load_labels(data_dir)
    labels.setdefault(case_id, {})[candidate] = value
    tmp = data_dir / "labels.json.tmp"
    tmp.write_text(json.dumps(labels, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(data_dir / "labels.json")     # a killed server never leaves half a file


def serve_labels(root: Path, data_dir: Path = HERE, port: int = 8078,
                 only: Optional[Sequence[str]] = None) -> None:
    """Serve the labelling page. It only ever reads and writes the human labels,
    so a person checking the model's labels cannot see them while they work."""
    cases = load_cases(data_dir)
    if only is not None:
        order = {case_id: i for i, case_id in enumerate(only)}
        cases = sorted((c for c in cases if c["id"] in order), key=lambda c: order[c["id"]])
    known = {(c["id"], k["id"]) for c in cases for k in c["candidates"]}
    stills = stills_dir(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                page = _page(cases, load_labels(data_dir)).encode("utf-8")
                return self._send(200, page, "text/html; charset=utf-8")
            match = re.fullmatch(r"/stills/(\w+)\.jpg", self.path)
            if match and (stills / f"{match.group(1)}.jpg").exists():
                blob = (stills / f"{match.group(1)}.jpg").read_bytes()
                return self._send(200, blob, "image/jpeg")
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if (body["case"], body["candidate"]) not in known:
                    raise ValueError("unknown case or candidate")
                save_label(data_dir, body["case"], body["candidate"], body["value"])
            except (KeyError, TypeError, ValueError) as exc:
                return self._send(400, str(exc).encode("utf-8"), "text/plain")
            self._send(204, b"", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"labelling {len(cases)} cases at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------------------- #
# run: the model over every case, checkpointed per call
# --------------------------------------------------------------------------- #
def results_path(data_dir: Path, model: str) -> Path:
    return data_dir / "results" / f"{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.jsonl"


def read_results(path: Path) -> List[Dict]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except OSError:
        pass
    return rows


def run(root: Path, api_key: str, model: str, repeats: int = 3,
        data_dir: Path = HERE, limit: int = 0, log=print) -> int:
    """Judge every case `repeats` times, appending one line per call.

    A spent daily quota stops the run with everything so far on disk, and the
    next run picks up where it stopped: the free tier is 500 requests a day per
    model, and an evaluation that loses its progress to that is one nobody
    finishes. An error that is not the quota is recorded and scored as the
    search order, because that is exactly what a build does when the call fails.
    """
    import requests

    from vidsmith import llm

    cases = load_cases(data_dir)[:limit or None]
    path = results_path(data_dir, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["case"], r["repeat"]) for r in read_results(path)}
    stills = stills_dir(root)
    calls = 0
    for case in cases:
        todo = [r for r in range(repeats) if (case["id"], r) not in done]
        if not todo:
            continue
        images = []
        for cand in case["candidates"]:
            still = stills / f"{cand['id']}.jpg"
            blob = still.read_bytes() if still.exists() else b""
            if _sha1(blob) != cand["sha1"]:
                log(f"refusing {case['id']}: still {cand['id']} is missing or has "
                    f"changed since collect; run collect again")
                return 1
            images.append(blob)
        ids = [c["id"] for c in case["candidates"]]

        for repeat in todo:
            started = time.time()
            try:
                order, rejected, filmable = llm.rank_clips(
                    case["line"], case["query"], images, api_key, model=model, log=log)
                row = {"order": [ids[i] for i in order], "reject": [ids[i] for i in rejected],
                       "filmable": filmable}
            except llm.QuotaExhausted as exc:
                log(f"stopped after {calls} calls: {exc}")
                return 2
            except requests.RequestException as exc:
                # The network, not the model: recorded as an error it would be
                # scored as the search order and count against the model. Stop,
                # and let the next run retry this call.
                log(f"stopped after {calls} calls on a network failure, everything "
                    f"so far is saved; run again to resume ({type(exc).__name__})")
                return 3
            except (llm.LLMUnavailable, ValueError) as exc:
                row = {"error": str(exc)[:300]}
            row.update({"case": case["id"], "repeat": repeat, "model": model,
                        "seconds": round(time.time() - started, 2)})
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            calls += 1
            log(f"  {case['id']:<32} repeat {repeat}  "
                f"{'error' if 'error' in row else 'ok'}  {row['seconds']}s")
    log(f"done: {calls} calls this run, results in {path}")
    return 0


# --------------------------------------------------------------------------- #
# score
# --------------------------------------------------------------------------- #
def first_pick(order: Sequence[str], reject: Sequence[str]) -> Optional[str]:
    """What a build would put on screen: the best clip not rejected, or the best
    of a bad set when everything was (visuals._rerank does the same)."""
    keepers = [i for i in order if i not in set(reject)]
    return (keepers or list(order) or [None])[0]


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def systems(cases: Sequence[Dict], data_dir: Path = HERE) -> Dict[str, Dict[str, List[Dict]]]:
    """Every way of choosing, as {system: {case id: [verdict per repeat]}}."""
    out: Dict[str, Dict[str, List[Dict]]] = {"search order": {}, "past builds": {}}
    by_id = {c["id"]: c for c in cases}
    for case in cases:
        ids = [c["id"] for c in case["candidates"]]
        out["search order"][case["id"]] = [{"order": ids, "reject": []}]
        prod = case.get("production") or {}
        if prod.get("same_pool") and prod.get("order"):
            out["past builds"][case["id"]] = [prod]
    for path in sorted((data_dir / "results").glob("*.jsonl")):
        runs: Dict[str, List[Dict]] = defaultdict(list)
        name = ""
        for row in read_results(path):
            case = by_id.get(row.get("case"))
            if case is None:
                continue
            name = row.get("model") or path.stem
            if "error" in row:
                # a failed call leaves a build on the search order
                row = {"order": [c["id"] for c in case["candidates"]], "reject": [], "error": True}
            runs[case["id"]].append(row)
        if name:
            out[name] = dict(runs)
    return out


def case_scores(case: Dict, labels: Dict[str, str], verdicts: Sequence[Dict]) -> Dict[str, Any]:
    """One system on one case, averaged over its repeats."""
    right = {k for k, v in labels.items() if v == "right"}
    wrong = {k for k, v in labels.items() if v == "wrong"}
    top, abstain, tp, fp, fn, kept_right, kept_judged = [], [], 0, 0, 0, 0, 0
    picks = []
    for verdict in verdicts:
        reject = set(verdict.get("reject") or [])
        pick = first_pick(verdict["order"], list(reject))
        picks.append(pick)
        if right:
            if pick in right:
                top.append(1.0)
            elif pick in wrong:
                top.append(0.0)            # an unsure pick scores neither way
        else:
            everything = reject >= wrong or verdict.get("filmable") is False
            abstain.append(1.0 if everything else 0.0)
        tp += len(reject & wrong)
        fp += len(reject & right)
        fn += len(wrong - reject)
        kept = [k for k in verdict["order"] if k not in reject]
        kept_right += sum(1 for k in kept if k in right)
        kept_judged += sum(1 for k in kept if k in right or k in wrong)
    return {"top": _mean(top), "abstain": _mean(abstain), "tp": tp, "fp": fp, "fn": fn,
            "kept_right": kept_right, "kept_judged": kept_judged,
            "agree": (len(set(picks)) == 1) if len(picks) > 1 else None,
            "errors": sum(1 for v in verdicts if v.get("error"))}


def complete_labels(case: Dict, labels: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    mine = labels.get(case["id"]) or {}
    if all(c["id"] in mine for c in case["candidates"]):
        return mine
    return None


def summarise(cases: Sequence[Dict], labels: Dict[str, Dict[str, str]],
              runs: Dict[str, List[Dict]]) -> Dict[str, Any]:
    per_case: Dict[str, Dict] = {}
    for case in cases:
        mine = complete_labels(case, labels)
        if mine is None or case["id"] not in runs:
            continue
        per_case[case["id"]] = case_scores(case, mine, runs[case["id"]])
    s = per_case.values()
    tops = [c["top"] for c in s if c["top"] is not None]
    tp, fp, fn = (sum(c[k] for c in s) for k in ("tp", "fp", "fn"))
    judged = sum(c["kept_judged"] for c in s)
    agree = [1.0 if c["agree"] else 0.0 for c in s if c["agree"] is not None]
    return {
        "cases": len(per_case),
        "top": _mean(tops),
        "top_n": len(tops),
        "abstain": _mean([c["abstain"] for c in s if c["abstain"] is not None]),
        "reject_precision": tp / (tp + fp) if tp + fp else None,
        "reject_recall": tp / (tp + fn) if tp + fn else None,
        "kept_usable": sum(c["kept_right"] for c in s) / judged if judged else None,
        "agree": _mean(agree),
        "errors": sum(c["errors"] for c in s),
        "per_case": per_case,
    }


def paired_interval(a: Dict[str, Dict], b: Dict[str, Dict], resamples: int = 2000,
                    seed: int = 0) -> Optional[Tuple[float, float, float]]:
    """Mean difference in top-pick usability (a minus b) with a 95% bootstrap
    interval, over the cases both systems were scored on. Paired, because the
    cases differ far more from each other than the systems do."""
    shared = [k for k in a if k in b and a[k]["top"] is not None and b[k]["top"] is not None]
    if len(shared) < 2:
        return None
    diffs = [a[k]["top"] - b[k]["top"] for k in shared]
    rng = random.Random(seed)
    means = sorted(_mean([rng.choice(diffs) for _ in diffs]) for _ in range(resamples))
    return _mean(diffs), means[int(resamples * 0.025)], means[int(resamples * 0.975) - 1]


def kept_interval(a: Dict[str, Dict], b: Dict[str, Dict], resamples: int = 2000,
                  seed: int = 0) -> Optional[Tuple[float, float, float]]:
    """Difference in the share of kept stills that are usable (a minus b), with
    a 95% interval from resampling cases, over cases both were scored on.

    This is the number rejection moves. A build cuts a scene into several shots
    from what was kept, so a wrong subject left in the pool reaches the screen
    even when the top pick was fine.
    """
    shared = [k for k in a if k in b and a[k]["kept_judged"] and b[k]["kept_judged"]]
    if len(shared) < 2:
        return None

    def diff(ids):
        share = lambda s: (sum(s[k]["kept_right"] for k in ids)
                           / max(1, sum(s[k]["kept_judged"] for k in ids)))
        return share(a) - share(b)

    rng = random.Random(seed)
    draws = sorted(diff([rng.choice(shared) for _ in shared]) for _ in range(resamples))
    return diff(shared), draws[int(resamples * 0.025)], draws[int(resamples * 0.975) - 1]


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.0%}"


def agreement_report(data_dir: Path = HERE) -> str:
    """The model's labels against the human sample, or why there is nothing yet."""
    cases = load_cases(data_dir)
    human, model = load_labels(data_dir, HUMAN), load_labels(data_dir, MODEL)
    about = _read_json(data_dir / MODEL, {}).get("_about", "")
    sample = set(_read_json(data_dir / "sample.json", []) or [])
    checked = [c for c in cases if c["id"] in sample
               and complete_labels(c, human) is not None
               and complete_labels(c, model) is not None]
    a = agreement(checked, human, model)
    lines = [
        "## Do the model's labels match a person's?",
        "",
        f"Model labels: {about or MODEL}. Checked against {len(checked)} of "
        f"{len(sample)} sample cases a person labelled without seeing them.",
        "",
    ]
    if not a["judged"]:
        return "\n".join(lines + ["Nothing to compare yet."]) + "\n"
    m = a["matrix"]
    interval = kappa_interval(checked, human, model)
    spread = (f" (95% CI {interval[0]:.2f} to {interval[1]:.2f}, resampling the "
              f"{len(checked)} cases)" if interval else "")
    lines += [
        f"- Stills compared: {a['judged']} ({a['unsure']} more left out because one "
        f"side was unsure)",
        f"- Same label: {_pct(a['agree'])}",
        f"- Cohen's kappa: {a['kappa']:.2f}{spread}" if a["kappa"] is not None
        else "- Cohen's kappa: undefined (both sides used one label only)",
        f"- Person right, model wrong: {m['right/wrong']}; person wrong, model right: "
        f"{m['wrong/right']}",
    ]
    lenient, strict = m["wrong/right"], m["right/wrong"]
    if lenient and not strict:
        lines.append("- Every disagreement has the model calling a still usable that the "
                     "person did not, so scores against the model's labels run high.")
    elif strict and not lenient:
        lines.append("- Every disagreement has the model rejecting a still the person "
                     "accepted, so scores against the model's labels run low.")
    return "\n".join(lines) + "\n"


def score(data_dir: Path = HERE, labels_name: str = HUMAN) -> str:
    cases = load_cases(data_dir)
    labels = load_labels(data_dir, labels_name)
    labelled = [c for c in cases if complete_labels(c, labels) is not None]
    usable = [c for c in labelled
              if any(v == "right" for v in labels[c["id"]].values())]
    chance = _mean([
        sum(v == "right" for v in labels[c["id"]].values())
        / max(1, sum(v in ("right", "wrong") for v in labels[c["id"]].values()))
        for c in usable])

    everything = systems(cases, data_dir)
    summaries = {name: summarise(cases, labels, runs) for name, runs in everything.items()}
    base = summaries["search order"]["per_case"]

    who = ("a person" if labels_name == HUMAN else
           _read_json(data_dir / labels_name, {}).get("_about") or labels_name)
    lines = [
        f"# rank_clips benchmark, {date.today().isoformat()}",
        "",
        f"Scored against labels written by {who}.",
        "",
        f"{len(labelled)} of {len(cases)} cases fully labelled; {len(usable)} have at "
        f"least one usable still. Picking a still at random from those would be "
        f"usable {_pct(chance)} of the time.",
        "",
        "| system | cases | top pick usable | vs search order (95% CI) | rejects that were wrong subjects | wrong subjects rejected | kept stills usable | vs search order (95% CI) | same pick every repeat | errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def spread(interval) -> str:
        # a difference between two shares is in percentage points, not percent
        pts = lambda x: f"{x * 100:+.0f}"
        return (f"{pts(interval[0])} pts ({pts(interval[1])} to {pts(interval[2])})"
                if interval else "-")

    for name, s in summaries.items():
        if not s["cases"]:
            continue
        is_base = name == "search order"
        top_diff = "-" if is_base else spread(paired_interval(s["per_case"], base))
        kept_diff = "-" if is_base else spread(kept_interval(s["per_case"], base))
        lines.append(
            f"| {name} | {s['cases']} | {_pct(s['top'])} (n={s['top_n']}) | {top_diff} | "
            f"{_pct(s['reject_precision'])} | {_pct(s['reject_recall'])} | "
            f"{_pct(s['kept_usable'])} | {kept_diff} | {_pct(s['agree'])} | {s['errors']} |")
    lines += [
        "",
        "*Top pick usable* is what a build would put on screen: the best clip not "
        "rejected. It is scored only on cases with at least one usable still, and a "
        "pick labelled unsure counts neither way. *Past builds* are the verdicts "
        "saved by real builds, included only where they judged exactly these "
        "candidates. A failed call is scored as the search order, because that is "
        "what a build falls back to.",
    ]
    if labels_name != HUMAN:
        # never publish scores against model labels without how well they held up
        lines += ["", agreement_report(data_dir).rstrip()]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.rank_clips", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "label", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--root", type=Path, default=REPO,
                         help="the checkout whose projects were built")
    sub.choices["label"].add_argument("--port", type=int, default=8078)
    sub.choices["label"].add_argument(
        "--sample", type=int, default=0,
        help="show only the fixed sample of this many cases that checks the model's labels")
    sub.choices["run"].add_argument("--model", default="")
    sub.choices["run"].add_argument("--repeats", type=int, default=3)
    sub.choices["run"].add_argument("--limit", type=int, default=0)
    scoring = sub.add_parser("score")
    scoring.add_argument("--write", action="store_true", help="also write REPORT.md")
    scoring.add_argument("--labels", choices=("human", "ai"), default="human",
                         help="score against labels.json or labels-ai.json")
    sub.add_parser("agree", help="how well the model's labels match the human sample")
    args = p.parse_args(argv)

    if args.cmd == "collect":
        collect(args.root.resolve())
    elif args.cmd == "label":
        only = sample_ids(load_cases(), size=args.sample) if args.sample else None
        serve_labels(args.root.resolve(), port=args.port, only=only)
    elif args.cmd == "agree":
        print(agreement_report())
    elif args.cmd == "run":
        from vidsmith import llm
        from vidsmith.pipeline import find_keys
        key = find_keys(args.root.resolve()).get("gemini", "")
        if not key:
            print("no GEMINI_API_KEY found")
            return 1
        return run(args.root.resolve(), key, args.model or llm.DEFAULT_MODEL, args.repeats,
                   limit=args.limit)
    elif args.cmd == "score":
        name = HUMAN if args.labels == "human" else MODEL
        report = score(labels_name=name)
        print(report)
        if args.write:
            out = "REPORT.md" if name == HUMAN else "REPORT-ai-labels.md"
            (HERE / out).write_text(report, encoding="utf-8")
    return 0
