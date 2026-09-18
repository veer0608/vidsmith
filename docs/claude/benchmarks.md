# Benchmarks

`bench/rank_clips` measures `llm.rank_clips` against the search's own order,
which is what a build keeps when the call fails or there is no key. Four steps,
each `python -m bench.rank_clips <step> --root C:/Users/veera/claude/vidsmith`:
`collect` turns past reranks into `cases.json`, `label` serves a page on port
8078 that writes `labels.json` on every click, `run` judges each case three
times into `results/<model>.jsonl`, and `score --write` produces `REPORT.md`.

- **`--root` is the checkout that did the building, not the one running the
  code.** Searches and verdicts live in its gitignored `.cache/` and
  `projects/*/build/`. The first collect ran from a worktree, read the
  worktree's empty cache through the package-relative default, and reported 0
  cases without complaint.
- **One query is often cached at two orientations**, one per cut, with
  different clips. Taking the first found paired vertical verdicts with
  landscape results. `collect` now prefers the search that holds every clip the
  verdict judged.
- **`labels.json` is the answer key, so only a person writes it.** Testing the
  page means clicking a label, and that label must then be removed: a guessed
  label silently makes the benchmark agree with whoever guessed.
- **Model-written labels live in `labels-ai.json` and are never quoted alone.**
  736 stills is a lot of clicking, so all 92 cases were also labelled by Claude,
  judged blind to `labels.json` and to past verdicts. `label --sample 20` serves
  a fixed random sample (`sample.json`, chosen once) for a person to label, and
  `agree` reports Cohen's kappa between the two. Raw agreement is not enough:
  when most stills are right, two labellers who never look agree most of the
  time. `score --labels ai` always appends that check. Do not show agreement
  numbers to the person while the sample is still being labelled.
- **Stills are not committed and are pinned by hash.** `run` refuses a still
  whose bytes changed, since a different picture is a different test.
- **It runs on the free tier's 500 requests a day.** Every call is appended as
  it returns, a spent quota stops the run, and the next run resumes. A failed
  call is scored as the search order, because that is what a build does with it.
- **But a call the model never answered is not scored.** Spent retries on a
  dropped connection, a 5xx or a per-minute limit raise `llm.GaveUp`, a subclass
  of `LLMUnavailable`, and `run` stops on it the way it stops on a spent quota.
  The first real run lost connection three times. Scored, those would have
  counted the network against the model. Tell the two apart by class, never by
  the message text.
- **Scores are paired over shared cases, with a bootstrap interval.** A few
  dozen cases vary far more between themselves than two systems do. Quote the
  interval with the number, or the number means nothing.
