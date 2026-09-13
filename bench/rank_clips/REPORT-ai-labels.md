# rank_clips benchmark, 2026-09-13

Scored against labels written by Claude (claude-opus-5), 2026-09-13: one sheet per case, stills in the label page's shuffled order, same right/wrong/unsure rule, without seeing labels.json or past build verdicts.

92 of 92 cases fully labelled; 87 have at least one usable still. Picking a still at random from those would be usable 86% of the time.

| system | cases | top pick usable | vs search order (95% CI) | rejects that were wrong subjects | wrong subjects rejected | kept stills usable | vs search order (95% CI) | same pick every repeat | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| search order | 92 | 94% (n=69) | - | - | 0% | 82% | - | - | 0 |
| past builds | 47 | 92% (n=40) | -3 pts (-9 to +0) | 60% | 83% | 94% | +9 pts (+4 to +15) | - | 0 |
| gemini-3.5-flash-lite | 92 | 99% (n=80) | +4 pts (-1 to +10) | 52% | 83% | 96% | +10 pts (+6 to +15) | 74% | 0 |

*Top pick usable* is what a build would put on screen: the best clip not rejected. It is scored only on cases with at least one usable still, and a pick labelled unsure counts neither way. *Past builds* are the verdicts saved by real builds, included only where they judged exactly these candidates. A failed call is scored as the search order, because that is what a build falls back to.

## Do the model's labels match a person's?

Model labels: Claude (claude-opus-5), 2026-09-13: one sheet per case, stills in the label page's shuffled order, same right/wrong/unsure rule, without seeing labels.json or past build verdicts. Checked against 10 of 20 sample cases a person labelled without seeing them.

- Stills compared: 51 (29 more left out because one side was unsure)
- Same label: 88%
- Cohen's kappa: 0.68 (95% CI 0.32 to 0.88, resampling the 10 cases)
- Person right, model wrong: 0; person wrong, model right: 6
- Every disagreement has the model calling a still usable that the person did not, so scores against the model's labels run high.
