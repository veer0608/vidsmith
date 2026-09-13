# What the rank_clips benchmark found

`llm.rank_clips` looks at a scene's candidate stock clips, ranks them, and
rejects the ones showing the wrong subject. Without it, a build keeps the stock
site's own search order. This measures the model (`gemini-3.5-flash-lite`)
against that baseline on 92 real searches from past builds, 8 candidate stills
each, 3 runs per case. Raw numbers: [REPORT.md](REPORT.md) (scored against a
person's labels) and [REPORT-ai-labels.md](REPORT-ai-labels.md) (against
Claude's). Run on 2026-09-13.

## Findings

**1. Rejection works: it raises the share of usable clips a build draws from.**
A build cuts each scene into several shots from the clips it kept, so a
wrong subject left in the pool reaches the screen even when the first pick was
fine. The model rejected 83% of wrong-subject stills, and the share of kept
stills that are usable rose by:

| labels | cases | search order | model | difference (95% CI) |
| --- | --- | --- | --- | --- |
| Claude's | 92 | 82% | 96% | +10 pts (+6 to +15) |
| a person's | 10 | 68% | 90% | +20 pts (+6 to +37) |

Both intervals exclude zero, under both sets of labels. The verdicts saved by
earlier real builds show the same effect (+9 pts, +4 to +15, over 47 cases).

**2. It does not measurably improve the first pick, because there is little
room to.** The search's own top result was already usable 94% of the time
(Claude's labels). The model scored +4 pts, with an interval of -1 to +10 that
includes no change. On the person's 10 cases the estimate is -12 pts (-38 to
+0), from only 8 scoreable cases. Neither result supports a claim either way.

**3. It over-rejects.** Only 52% of the stills it rejected were wrong subjects
by Claude's labels (65% by the person's), so roughly a third to a half of
rejections threw away usable footage. That costs something real: fewer kept
clips collapse a scene's shot plan, and in a project with diagrams switched on,
a high rejection ratio is one of the two signals that replaces the footage with
a drawn diagram (`diagram_on_reject`).

**4. It is not stable across runs.** At temperature 0.1, its first pick was
the same in all three runs for only 74% of cases. A build caches the verdict
in `rerank.json`, so one build is consistent, but a rebuild that clears the
cache can pick different footage for about a quarter of scenes.

## How far to trust the labels

Labelling 736 stills by hand was the bottleneck, so every case was labelled by
Claude (a different model family from the one under test), without seeing the
person's labels. A person then labelled a random sample without seeing
Claude's. The sample was stopped at 10 of 20 planned cases.

- Agreement over those 10 cases: Cohen's kappa **0.68** (95% CI 0.32 to 0.88),
  88% raw agreement on 51 stills both sides labelled.
- **Every one of the 6 disagreements went the same way**: Claude called a still
  usable that the person did not. The pattern was the right kind of thing
  missing the shot's specific detail: a calendar with no circled date, a
  network switch for a server rack.
- So scores against Claude's labels run high. That inflates the absolute
  percentages (compare the 82% and 68% baselines above) more than the
  model-versus-baseline differences, which point the same way under both.
- 29 of the 80 sampled stills were marked unsure by one side or both, and are
  left out of every figure above.

## Limits

- 92 cases from 58 distinct narration lines, all from this project's own
  scripts, and many built at two orientations, so the cases are not fully
  independent.
- One model, one prompt, one pool size (8).
- The human sample is 10 cases. Its intervals are wide, and it checks
  Claude's labels more than it measures the model.

## What it suggests changing

The reranker earns its call through rejection, not ranking. The next
experiment is the over-rejection in finding 3: a prompt that asks for a reject
only when the subject is clearly wrong, measured on these same cases against
the kept-usable number and reject precision together, so a gain in one is not
paid for in the other.
