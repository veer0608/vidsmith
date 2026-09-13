# rank_clips benchmark, 2026-09-13

Scored against labels written by a person.

10 of 92 cases fully labelled; 10 have at least one usable still. Picking a still at random from those would be usable 71% of the time.

| system | cases | top pick usable | vs search order (95% CI) | rejects that were wrong subjects | wrong subjects rejected | kept stills usable | vs search order (95% CI) | same pick every repeat | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| search order | 10 | 89% (n=9) | - | - | 0% | 68% | - | - | 0 |
| past builds | 8 | 80% (n=5) | -20 pts (-60 to +0) | 64% | 78% | 87% | +13 pts (+4 to +25) | - | 0 |
| gemini-3.5-flash-lite | 10 | 88% (n=8) | -12 pts (-38 to +0) | 65% | 83% | 90% | +20 pts (+6 to +37) | 50% | 0 |

*Top pick usable* is what a build would put on screen: the best clip not rejected. It is scored only on cases with at least one usable still, and a pick labelled unsure counts neither way. *Past builds* are the verdicts saved by real builds, included only where they judged exactly these candidates. A failed call is scored as the search order, because that is what a build falls back to.
