# Architecture

`pipeline.build()` is the spine: parse → b-roll queries → narration → visuals →
captions → render → metadata. Every stage writes into `projects/<name>/build/`
and is skipped when its output is already there.

**Word timings are the backbone.** edge-tts returns a `WordBoundary` event per
spoken word, but only when `Communicate(..., boundary="WordBoundary")` is passed;
the default is one `SentenceBoundary` per utterance. Those timings drive caption
timing and the edit: `visuals.plan_shots()` cuts each scene into shots at the
sentence boundaries the speaker actually lands. Nothing transcribes anything.

**There are two voice providers, and they report timings in different shapes.**
`voice.provider` is `edge` or `polly`, and `tests/test_voice_polly.py` is forty
tests because the second one is not a drop-in. edge-tts is free, needs no key,
and is an unofficial client for the endpoint behind Edge's Read Aloud, which
Microsoft grants no commercial use of - so Polly is the licensed path and
`COMMERCIAL.md` sells against it. Polly reports word timings too, which almost
nothing else does, but as speech marks in **milliseconds carrying starts and no
durations**, so the ends are reconstructed rather than read. The audio and the
marks are **separately billed requests**, so a video costs its script length
twice. `engine: generative` is deliberately absent from the closed set: it is
the one engine that returns no speech marks at all, so it cannot time captions
or the cut, which is the whole design. Nothing about the edit changes between
providers, and that is the point of normalising both into the same word list.

  **Those forty tests all stub the SDK, so until 2026-08-31 the code had never
  met the service.** It was run once against real Polly then, and it works:
  `Matthew`, `neural`, `us-east-1`, one sentence, audio and marks back, ten
  words in the same `text/start/end` shape edge-tts produces, starts ascending,
  every word carrying an end.

  The case worth keeping is the last word, because the reconstruction has
  nothing after it to bound against and a mistake there lands on the frame as a
  caption outliving its audio:

  ```
  {'text': 'said', 'start': 1.507, 'end': 1.716}
  {'text': 'each', 'start': 1.716, 'end': 1.889}
  {'text': 'word', 'start': 1.889, 'end': 2.4}     audio is 2.400s
  ```

  Exact, to the millisecond the audio ends. Re-run it after touching
  `voice_polly.py`: the tests cannot catch a change in what Polly actually
  sends, only a change in what we think it sends, and the whole commercial
  story rests on this path working.

**The narration slot is authoritative.** `scene.duration` is the contract: each
scene's clips must sum to exactly it, or the picture drifts against the voice for
the rest of the video. Any floor on clip length is applied to the slot upstream,
never to the clip. `collapse()` merges a shot plan when fewer clips are available
than shots, preserving the total.

**And no shot may ask a clip for more than it holds.** `fit_shots()` pairs the
longest clip with the longest shot and moves a planned cut only as far as a clip's
real length forces it, so a slot is covered by distinct footage. When every clip
a scene has cannot cover it between them, each plays in full, slowed by the same
factor, and the build says so. `normalise_video` used to `-stream_loop` a short
clip instead, which put identical frames on screen a few seconds apart - see the
entry on looped footage in [incidents.md](incidents.md).

**Per-aspect vs shared artifacts.** Narration, scene timings, diagram specs and
the drawn-scene decision are shape-independent and live in `build/`. Picture,
captions, scrim and the delivery file depend on frame size and are suffixed
(`picture-9x16.mp4`). A second cut costs footage and an encode, not speech or
model calls. Anything asked of a model once must be cached where both cuts see
it, or the two cuts disagree about what the video contains.

**Gemini is used eight times, all optional and all degrading to something.**
`suggest_queries` writes a b-roll search per scene, `beat_queries` writes one per
beat of a long scene from the words spoken during it, `rank_clips` reranks stock
candidates by their preview stills, `design_diagram` writes a diagram spec,
`upload_metadata` writes the YouTube title, description and chapters,
`thumbnail_query` writes the thumbnail search, `pick_thumbnail` ranks the
candidates it returns, and `draft_script` writes a whole script from a topic for
`vidsmith new --topic` and the web page's topic tab. Without `GEMINI_API_KEY`
each falls back: keyword extraction for both scene-search calls, the scene's
own search for every beat, provider
order for reranking, the top search result for the thumbnail pick, no diagram
and no metadata. Only drafting refuses outright, because there is nothing to
degrade to: the CLI stops and the web page answers 503. `rank_clips` and
`pick_thumbnail` go through `llm.generate_vision()`, which sends downscaled
JPEGs inline.

**`LLMUnavailable` is what makes that degradation real.** `llm.py` calls
`v1beta/generateContent` over plain `requests`, with no SDK. The default model is
`DEFAULT_MODEL`, a pinned id (`gemini-3.5-flash-lite`), never a `-latest` alias:
an alias repoints to whatever is newest, and newest can carry a far smaller
free-tier allowance. The comment above it in `llm.py` has the history. A call retries four times with
exponential backoff on `{429, 500, 502, 503, 504}`, and raises `LLMUnavailable`
on anything else, including a missing key. Every optional feature above is
optional because it catches that one exception; a new model call must raise it
too, not invent its own failure mode.

**Diagrams exist because some scenes are unfilmable.** "branching tree diagram"
returns photographs of trees. A scene is drawn when the script says
`[diagram: ...]` or when reranking rejects nearly every candidate. `diagram.py`
renders a JSON spec (`flow`, `tree`, `stack`, `compare`) in the project theme.

**And they are off by default, because the boxes read as slides.** Published
videos kept cutting to "MARKDOWN TO SCENES" and "LICENSING OPTIONS" frames that
explained the subject the way a deck would. `visuals.diagrams` now defaults to
`false`, which gates all three triggers: an explicit directive, a stored model
decision, and the on-reject substitution. Existing projects keep what their
fully expanded `config.yaml` says, so an old project rebuilt still draws.

Two layers keep a *drafted* script away from them, in the same shape as dashes.
`SCRIPT_PROMPT` offers only `[visual:]` and teaches the hard case, an idea with no
obvious subject, with worked examples that name the real subject or a person
doing the thing - never a metaphor, see the entry on unrelated footage in [incidents.md](incidents.md); its
output template no longer shows `[diagram:]`, because a
template is followed more faithfully than any instruction above it. Then
`llm.strip_diagrams()` removes any `[diagram:]` line the model writes anyway.
The line is removed, not renamed to `[visual:]`: a model's diagram description
describes a graphic, which is the worst possible stock search, and a scene with
no directive gets a query Gemini writes from its narration. First real draft on
the subject that produced those frames came back with zero diagram lines. A
hand-written `[diagram:]` with drawing off is logged, never silently dropped.

**The music bed is synthesised, not sourced.** There is no free API for licensed
music and an unlicensed track is a copyright strike, so `music.py` builds it in
ffmpeg: detuned sine triads over a four-chord progression (`calm`, `warm`,
`tense`), low-passed and echo-smeared until it reads as atmosphere rather than
notes. `--music auto` generates one per mood into `build/music-<mood>.wav`,
`--music none` drops it, a path uses that file. `render.py` mixes it under the
voice with `sidechaincompress` keyed off the narration, so it ducks whenever
anyone speaks.

**The render is three ffmpeg passes on purpose.** Narration mix (every scene mp3
delayed to its start time), then the picture cut, then the final master (scrim,
progress bar, captions, ducked music, loudnorm). The split is diagnostic: a
failure names the stage that broke instead of dumping one enormous filtergraph.
`transition: cut` lets pass two stream-copy the clips with the concat demuxer,
and `fade` swaps in `xfade` and a re-encode. Do not collapse these into a single
invocation for speed; the encode dominates either way and you lose the bisect.

**Karaoke captions re-emit the whole line once per word.** More events than `\k`
tags need, and deliberate: it renders identically in every libass build, survives
re-timing, and fixed glyph widths mean nothing reflows as the highlight moves.
Motion lives on the caption group instead, a short scale-up on entry and a fade
either side. Tidying this into `\k` tags is a regression, not a simplification.

**`theme.py` is the single source of colour and type.** Cards, diagrams, captions,
progress bar and thumbnail all read from one `Theme`, which is why the output
looks designed rather than assembled.
