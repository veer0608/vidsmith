# The script

`projects/<name>/script.md` is the input contract, parsed by `script_parser.py`:

```markdown
# Why Your Bank Statement Lies

## The hook
[visual: paper bank statement on a desk]
Your bank statement is not a record of what you spent.
```

A scene breaks on a `##` heading **or** a blank line between paragraphs, so an
innocent-looking reflow silently re-cuts the video. `[visual: ...]` sets that
scene's own stock-footage query. On a stock build that is now the fallback rather
than the search: every beat of a scene is searched for in words `beat_queries`
writes from what is spoken during it, and the directive is used when that call
is unavailable or a beat's search finds nothing (`visuals.beat_seconds: 0`
restores the directive as the search). It still feeds the thumbnail search. It
also answers to `b-roll`, `broll`, `footage` and
`shot`. It answers to `image` as well, which is an alias too rather than a "use
a still" switch, whatever the name suggests: a still only enters through the
`local` provider matching an image file on disk. `[diagram: ...]` forces a drawn
scene, and `[hold: 3.5]` puts a floor under the on-screen duration. Lines opening with `>`,
`<!--` or `//` are production notes and never reach the narration.

**`WPS` is measured, and everything that sizes a script leans on it.** The real
timings come from the voice, but before a build `script_parser.WPS` is the only
speaking rate there is: the page's runtime estimate reads it, and
`llm.WORDS_PER_MINUTE` is derived from it plus the voice config's lead-in and
gap, which sizes every draft and the web's word limit. It sat at 2.6 a second by
assumption while the default voice spoke at 3.35, measured off fourteen builds,
so drafted videos came out about a fifth short and the page promised eight
minutes for six and a half. `scripts/speaking_rate.py` reads it back off
`build/scenes.json`; re-run it after changing the default voice or its rate.

**The drafting prompt is under test, not just under review.** `vidsmith new
--topic` has Gemini write the script, and `tests/test_script_prompt.py` asserts
what the prompt must still demand: a word budget stated per scene as well as in
total, derived from the speaking rate; both directives taught; diagrams
described as diagrams rather than pictures; no invented facts; varied sentence
rhythm; distinct headings; and a hook-through-takeaway shape. Rewording that
prompt without reading the tests will quietly drop one of them.

**The prompt cannot get the length right on its own, so the draft is measured.**
At nine minutes, one-shot drafts came back between 26% and 69% of their budget,
in two different shapes: half the scenes asked for, or the right number of
scenes each far too short. `llm.lengthen()` counts the narration and sends the
short scenes back with a stated length each - its share of the words actually
missing, since a flat per-scene size overshot - and replies that lose a heading
or come back shorter change nothing. Nine real drafts landed between 90% and
109%, for one or two more requests each. A rewrite over `PARAGRAPH_WORDS` comes
back as paragraphs under their own `[visual:]` lines, because lengthening a draft
that wrote seven scenes otherwise made eighty-second scenes on one stock search.
Two things lean on the measured spread: `LENGTHEN_OVERSHOOT` is the long end of
it, and the web's `/api/draft` aims that far under the instance limit, so a
script drafted at the limit is one the limit accepts. The limit itself counts
spoken words at `llm.WORDS_PER_MINUTE`, the same rate drafting uses, and serves
`word_cap` to the page's meter rather than letting it keep its own copy.
