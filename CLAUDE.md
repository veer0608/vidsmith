# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This is a **PowerShell 5.1** machine. `&&` is a parser error there; chain with `;`.
`.\vidsmith.cmd` wraps `.venv\Scripts\python.exe -m vidsmith`.

```powershell
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest          # 179 tests, ~40s
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest -m "not slow"
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest tests/test_shot_plan.py::test_plan_sums_to_the_narration_slot
cd ~/claude/vidsmith; .\vidsmith.cmd doctor                       # ffmpeg, edge-tts, which keys resolve
cd ~/claude/vidsmith; .\vidsmith.cmd new demo --topic "how b-trees work"
cd ~/claude/vidsmith; .\vidsmith.cmd build demo --provider pexels
cd ~/claude/vidsmith; .\vidsmith.cmd thumbs demo                  # rank frames from a finished build
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m uvicorn web.app:app --port 8077
```

`-m slow` tests shell out to ffmpeg and encode real video. Everything else is
pure and fast. `--stop-after <stage>` halts a build after any of
`parse queries voice visuals captions render meta`; `--force voice,visuals,render`
redoes cached stages.

## The script

`projects/<name>/script.md` is the input contract, parsed by `script_parser.py`:

```markdown
# Why Your Bank Statement Lies

## The hook
[visual: paper bank statement on a desk]
Your bank statement is not a record of what you spent.
```

A scene breaks on a `##` heading **or** a blank line between paragraphs, so an
innocent-looking reflow silently re-cuts the video. `[visual: ...]` sets that
scene's stock-footage query and also answers to `b-roll`, `broll`, `footage` and
`shot`; `[diagram: ...]` forces a drawn scene, `[image: ...]` a still, and
`[hold: 3.5]` puts a floor under the on-screen duration. Lines opening with `>`,
`<!--` or `//` are production notes and never reach the narration. `WPS = 2.6`
is only a pre-flight estimate of scene length; the real timings come from the
voice.

**The drafting prompt is under test, not just under review.** `vidsmith new
--topic` has Gemini write the script, and `tests/test_script_prompt.py` asserts
what the prompt must still demand: a word budget stated per scene as well as in
total, derived from the speaking rate; both directives taught; diagrams
described as diagrams rather than pictures; no invented facts; varied sentence
rhythm; distinct headings; and a hook-through-takeaway shape. Rewording that
prompt without reading the tests will quietly drop one of them.

## Architecture

`pipeline.build()` is the spine: parse → b-roll queries → narration → visuals →
captions → render → metadata. Every stage writes into `projects/<name>/build/`
and is skipped when its output is already there.

**Word timings are the backbone.** edge-tts returns a `WordBoundary` event per
spoken word, but only when `Communicate(..., boundary="WordBoundary")` is passed;
the default is one `SentenceBoundary` per utterance. Those timings drive caption
timing and the edit: `visuals.plan_shots()` cuts each scene into shots at the
sentence boundaries the speaker actually lands. Nothing transcribes anything.

**The narration slot is authoritative.** `scene.duration` is the contract: each
scene's clips must sum to exactly it, or the picture drifts against the voice for
the rest of the video. Any floor on clip length is applied to the slot upstream,
never to the clip. `collapse()` merges a shot plan when fewer clips are available
than shots, preserving the total.

**Per-aspect vs shared artifacts.** Narration, scene timings, diagram specs and
the drawn-scene decision are shape-independent and live in `build/`. Picture,
captions, scrim and the delivery file depend on frame size and are suffixed
(`picture-9x16.mp4`). A second cut costs footage and an encode, not speech or
model calls. Anything asked of a model once must be cached where both cuts see
it, or the two cuts disagree about what the video contains.

**Gemini is used four times, all optional and all degrading to something.**
b-roll search queries, reranking stock candidates by their preview stills,
designing diagram specs, and writing YouTube metadata plus the thumbnail search.
Without `GEMINI_API_KEY` each falls back (keyword extraction, provider order,
no diagram, no metadata). `llm.generate_vision()` sends downscaled JPEGs inline.

**Diagrams exist because some scenes are unfilmable.** "branching tree diagram"
returns photographs of trees. A scene is drawn when the script says
`[diagram: ...]` or when reranking rejects nearly every candidate. `diagram.py`
renders a JSON spec (`flow`, `tree`, `stack`, `compare`) in the project theme.

**The music bed is synthesised, not sourced.** There is no free API for licensed
music and an unlicensed track is a copyright strike, so `music.py` builds the bed
in ffmpeg itself: detuned sine triads over a four-chord progression (`calm`,
`warm`, `tense`), low-passed and smeared with an echo until it reads as
atmosphere rather than as notes. `--music auto` generates it once per mood into
`build/music-<mood>.wav`, `--music none` drops it, and a path uses that file.
`render.py` mixes it under the voice through `sidechaincompress` keyed off the
narration, so it ducks whenever anyone speaks.

**`theme.py` is the single source of colour and type.** Cards, diagrams, captions,
progress bar and thumbnail all read from one `Theme`, which is why the output
looks designed rather than assembled.

## Configuration

The dataclasses in `config.py` are the schema, and `projects/<name>/config.yaml`
overrides them. `vidsmith new` writes that file fully expanded, so it lists every
key rather than only the interesting ones. `_merge()` sets a key only when the
dataclass already has it, which means **a misspelled or invented key is silently
ignored**: no error, no warning, and the default quietly stands.

`render.aspect` picks from `ASPECTS` (`16:9`, `9:16`, `1:1`, `4:5`) and `cfg.size`
derives the pixel frame from it. Pixel values in the config are stated against a
1920-wide frame (`captions.size: 62`, `margin_v: 150`) and scaled by width at
render time, which is the same rule as the WIDTH bullet below.

The loudness chain is deliberate: narration normalises to `-14` LUFS, the bed
sits `-18` dB under it at roughly `-32` LUFS, and `loudnorm` finishes the mix at
`-14`. Raising `music_gain_db` without re-checking the mix is how the bed starts
competing with the voice.

## Things that have actually broken here

- **Scene-indexed caches go stale on a redraft.** `diagram_scenes.json`,
  `rerank.json`, `credits.json` and `narration.wav` are keyed by position with
  nothing tying them to the words. `pipeline.invalidate()` drops them when the
  script changes; `build/visuals*/cache/` survives, being keyed by provider id.
  A missed entry here put the previous script's voice under a new picture.
- **Sizes must key off frame WIDTH, never height.** 15% of 1920 is not the same
  kind of quantity as 15% of 1080; keying box heights to height made portrait
  diagrams nearly square. Portrait then gets larger type deliberately.
- **Layers must not collide.** `captions.caption_top()` computes where the
  caption box reaches from the same numbers that build the ASS styles, and
  diagrams stop above it. Do not hardcode a fraction here.
- **ASS is positional.** The `Format:` line must list all ten Dialogue fields:
  a missing `MarginV` shifts every field and prepends a stray comma to the text.
- **Thumbnails come from the Pexels *photo* API, not video frames.** A frame is
  graded to sit behind captions; a diagram frame is the clearest frame in the
  video and the worst thumbnail. The search is written from the scenes' visual
  directives, not the hook. Every explainer hook is a frustration, so a hook-fed
  query returns a stressed person every time.
- **Dashes are kept out by two different mechanisms, and neither covers the
  script.** The voice reads an em dash as a pause the writing did not ask for.
  `llm.undash()` turns em and en dashes into commas, a range between digits into
  `5 to 10` (a comma there is heard as a thousands separator), and leaves
  hyphens alone, but it is only applied to the YouTube metadata and chapter
  labels. A *drafted* script stays clean because the prompt forbids dashes, so a
  draft can still come back with one and nothing downstream will strip it. It
  must not simply be run over a hand-written script either: `captions.TRAILING`
  and `visuals.CLAUSE_END` both rely on a dash as a clause boundary.
- **Don't pipe a command you gate on into `tail`.** The pipeline's exit status is
  `tail`'s, so `pytest ... | tail && git commit` commits over failing tests.
- **Heredocs mangle backslash escapes here.** Writing Python containing `\n` or
  `\1` through `bash <<'EOF'` has repeatedly produced real newlines and control
  characters mid-string. Use the Write or Edit tools for anything with escapes.

## Keys and hosts

`pipeline.find_keys()` resolves `GEMINI_API_KEY`, `PEXELS_API_KEY` and
`PIXABAY_API_KEY` from the environment, then `.env` at the project, projects
parent and repo root, then two sibling projects' `.env` files. Nothing key-driven
is required: with no keys at all the build still produces narrated, captioned
video over generated cards.

`ffmpeg_util` resolves ffmpeg from `FFMPEG_BINARY`, then `bin/`, then PATH, then
the winget package directory. A host with no package manager fetches a static
build at deploy time via `scripts/fetch-runtime-deps.sh`. The themes name Windows
font families, so `assets/fonts` is handed to the `subtitles` filter as
`fontsdir`; without it libass silently substitutes a different face.

## Web service

`web/` is FastAPI over the same pipeline. Renders run on a worker thread and the
browser polls, because a video takes minutes. **Queue depth is one**: two x264
encodes starve each other, so a second caller gets 429. Jobs live in memory under
`jobs/<id>/` and are swept an hour after finishing, so anything worth keeping is
copied into `projects/`. `VIDSMITH_TOKEN` gates the API when set; `/healthz`
stays open and reports ffmpeg, bundled fonts and which keys resolved.

`VIDSMITH_JOBS` moves the job directory, and `VIDSMITH_MAX_MINUTES` (default 4)
caps how long a submitted script may run. Both exist because the host, not the
code, is usually the constraint.

## Deploying

Two hosts are written up, and they solve the ffmpeg problem differently.

**Render** (`render.yaml`) uses the *native Python runtime rather than a
container*, because ffmpeg and the fonts are fetched in the build step by
`scripts/fetch-runtime-deps.sh`. Encoding is CPU-bound, so the free instance runs
10 to 15 minutes for a 90 second video and can exhaust memory at 1080p; the
blueprint therefore asks for `starter` and holds `VIDSMITH_MAX_MINUTES` at 2,
with jobs in `/tmp`. `autoDeploy` is off, and the keys are `sync: false` so they
are set in the dashboard and never committed.

**Hugging Face Spaces** (`deploy/huggingface.md`) is the free option and the
better machine: 2 vCPU and 16 GB, which does encode 1080p where a 512 MB instance
does not. It builds the `Dockerfile` on their side, so this stays true even
though Docker cannot run on this machine. Keys go in Space secrets, and
`/healthz` is the check that matters after a build: `fonts` should list the two
DejaVu files and `keys` should show `gemini` and `pexels` true. A free Space
sleeps after an idle stretch and takes a minute to wake.
