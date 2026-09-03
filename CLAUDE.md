# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start here

Find the row for what you are about to do, read that section, and note the trap
before writing anything. The traps are the reason this file exists.

| About to | Read | What bites |
| --- | --- | --- |
| Edit a script | The script | A blank line starts a new scene |
| Write a test | Tests | Build scenes with `make_scene()`, never by hand |
| Add or change a model call | Architecture, `LLMUnavailable` | Raise `LLMUnavailable` or the fallbacks stop working |
| Touch shot lengths or timing | Architecture, narration slot | Clips must sum to `scene.duration` exactly |
| Move captions or diagrams | Architecture, karaoke; Things that have actually broken here, layers and ASS | Never hardcode a caption fraction; the ASS `Format:` line is positional |
| Change ffmpeg or the encode | Architecture, three passes | Do not collapse the passes into one |
| Hand a path to ffmpeg | Things that have actually broken here, escaping | Two escapes, two parsers; never share one helper between them |
| Pass an optional file between stages | Things that have actually broken here, `Path("")` | `Path("")` is truthy and exists; guard on `is None` |
| Add a config key | Configuration | A misspelled key is ignored in silence; a closed-set value is refused on load |
| Reach for a build's output file | Things that have actually broken here, an empty tag | 16:9 has no suffix, so `*{tag}.mp4` matches every other cut |
| Write an artifact a second way | Things that have actually broken here, two writers | Call the one writer; a second copy is how credits go missing |
| Change the footage source | Configuration | A provider with no key falls back to cards without failing |
| Reword the drafting prompt | The script | `test_script_prompt.py` says what it must still demand |
| Repair text a model wrote | Things that have actually broken here, dashes | A repair pass cannot tell its own output from the input |
| Redraft an existing script | Things that have actually broken here, stale caches | Scene-indexed caches must be invalidated |
| Publish a video anywhere | Things that have actually broken here, attribution and chapters | Crediting is a licence condition; YouTube drops a chapter list rather than the bad line |
| Edit the web page | Web service | Ask the server for what it knows; do not hardcode a second copy |
| Touch the web queue | Web service; Things that have actually broken here, the render slot | Claim the slot and you own giving it back on every path out |
| Show it to someone | Deploying | The tunnel beats both hosts |
| Commit anything | Working in this repo | `main` is protected; every change is a branch and a PR |
| Publish a build | `vidsmith check <name>` | Run it first; it compares delivered files against each other |
| Debug an ffmpeg filter error | Things that have actually broken here, a missing filter | "No option name near" can mean the filter does not exist |
| Touch `serve-public.ps1` | Things that have actually broken here, PowerShell unrolling | An `if` that returns an array hands back a string |
| Handle a model 429 | Architecture, `LLMUnavailable` | Read the `quotaId`: `PerDay` refuses, `PerMinute` waits |
| Deploy or update the live box | Deploying | `ssh host "commands"`, never a session; check which machine it ran on |
| Write a test that reloads or spawns | Tests | Module state and threads outlive their test and disarm the next file |
| Speed the service up | Web service | Concurrency is settled and measured; 78% of a build is ffmpeg |
| Change the voice provider | Architecture, two providers | Polly's marks are ms with starts and no durations, billed twice |
| Reach for a version of ffmpeg | Tests | Three are in play and they disagree about libass |
| Change the price or the buy link | `test_selling_links.py` | Both docs must agree; a `PASTE_GUMROAD_*` token holds CI red on purpose |

## Working in this repo

**`main` is protected and requires all three CI checks.** `test`, `windows`
and `macos` are all required as of 2026-08-28. macOS was advisory until
then, which meant the one runner that has found a platform-specific fault
here could go red without blocking a merge. A direct push is rejected
with `GH006`, so every change is a branch, a PR, and a wait for ubuntu, windows
and macos to go green before `gh pr merge --squash --delete-branch`. Budget for
the round trip: it is a few minutes per change, which is the argument for
batching a fix and its test into one PR rather than two.

Two habits this repo keeps punishing:

- **Do not pipe a command you gate on into `tail`.** The pipeline's exit status
  is `tail`'s, so `pytest ... | tail && git commit` commits over failing tests.
  Use `set -o pipefail` or check `${PIPESTATUS[0]}`.
- **Make the failure legible before theorising about it.** Every long detour in
  this file was reasoning that felt sufficient and was never checked against the
  failing thing: an escaping theory that survived two rounds because nobody ran
  `ffmpeg -filters`, a token bug "fixed" once before it was found, a quota guard
  added to one of two call sites. The move that works is cheap and boring -
  print the value with delimiters and a length, run the real script's own lines
  rather than a retyped copy, ask the binary what it can do.

## Commands

This is a **PowerShell 5.1** machine. `&&` is a parser error there; chain with `;`.
`.\vidsmith.cmd` wraps `.venv\Scripts\python.exe -m vidsmith`.

```powershell
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest          # 455 tests, ~22s
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest -m "not slow"
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest tests/test_shot_plan.py::test_plan_sums_to_the_narration_slot
cd ~/claude/vidsmith; .\vidsmith.cmd doctor                       # ffmpeg, edge-tts, which keys resolve
cd ~/claude/vidsmith; .\vidsmith.cmd new demo --topic "how b-trees work"
cd ~/claude/vidsmith; .\vidsmith.cmd build demo --provider pexels
cd ~/claude/vidsmith; .\vidsmith.cmd thumbs demo                  # rank frames from a finished build
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m uvicorn web.app:app --port 8077
```

`.gitattributes` normalises the tree to LF, so git prints a CRLF notice on almost
every commit made here. It is the setting working, not a problem to fix.

`-m slow` tests shell out to ffmpeg and encode real video. Everything else is
pure and fast. `--stop-after <stage>` halts a build after any of
`parse queries voice visuals captions render meta`; `--force voice,visuals,render`
redoes cached stages.

## Tests

`pytest.ini` sets `pythonpath = . tests` and defines the one marker, `slow`, for
the tests that shell out to a real ffmpeg and encode video. GitHub Actions runs
the whole suite, encodes included, on **ubuntu, windows and macos** for every
push to main and every PR (`.github/workflows/tests.yml`); it installs ffmpeg
and a real font, because without one Pillow falls back to a bitmap default and
the card tests measure text nobody would ship.

Each platform earns its minutes. Windows is the only runner that hands ffmpeg a
drive letter, which is what the path escaping exists for. macOS was added
because the README invites a mac user to clone this, and it found a real fault
within one run. It also carries `--timeout=120`, because it once sat in the
suite for twenty-five minutes and reported nothing: a hang produces no output,
and a per-test timeout turns it into a failing test with a traceback.

**Three ffmpegs are in play and they do not agree.** Ubuntu 6.1.1 on the
instance, winget 9.0 on this machine, Homebrew 8.1.2 on the macOS runner - and
that last one is built without libass, so it has no `subtitles` filter at all.
Never assume a capability from a version number: `ffmpeg_util.filters()` asks
the binary and `require_filter()` names what is missing and what it costs. A CI
job's installer can also succeed while installing nothing, so every job now runs
`ffmpeg -version` after installing rather than trusting the exit status.

**`test_lint.py` gates on undefined names.** pyflakes, narrowed to the faults
that ship broken behaviour: a name that does not resolve, or a `nonlocal` that
is never bound. It exists because a thumbnail ranking ran through an undefined
variable for months behind a bare `except`. It deliberately ignores unused
imports, so the signal stays worth reading, and it carries a test proving the
gate itself can still fail.

**Build scenes with `make_scene()` from `tests/conftest.py`, never by hand.**
Word timings drive the edit, the captions and the mix, so a test only means
something against words shaped the way edge-tts reports them: punctuation
stripped, times in seconds from the start of the speech, `duration` agreeing with
the words it holds. A hand-written `words` list passes against input the TTS
could never produce. The `scene` and `scenes` fixtures wrap it.

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
`shot`. It answers to `image` as well, which is an alias too rather than a "use
a still" switch, whatever the name suggests: a still only enters through the
`local` provider matching an image file on disk. `[diagram: ...]` forces a drawn
scene, and `[hold: 3.5]` puts a floor under the on-screen duration. Lines opening with `>`,
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

**Per-aspect vs shared artifacts.** Narration, scene timings, diagram specs and
the drawn-scene decision are shape-independent and live in `build/`. Picture,
captions, scrim and the delivery file depend on frame size and are suffixed
(`picture-9x16.mp4`). A second cut costs footage and an encode, not speech or
model calls. Anything asked of a model once must be cached where both cuts see
it, or the two cuts disagree about what the video contains.

**Gemini is used seven times, all optional and all degrading to something.**
`suggest_queries` writes a b-roll search per scene, `rank_clips` reranks stock
candidates by their preview stills, `design_diagram` writes a diagram spec,
`upload_metadata` writes the YouTube title, description and chapters,
`thumbnail_query` writes the thumbnail search, `pick_thumbnail` ranks the
candidates it returns, and `draft_script` writes a whole script from a topic for
`vidsmith new --topic` and the web page's topic tab. Without `GEMINI_API_KEY`
each falls back: keyword extraction for both search-writing calls, provider
order for reranking, the top search result for the thumbnail pick, no diagram
and no metadata. Only drafting refuses outright, because there is nothing to
degrade to: the CLI stops and the web page answers 503. `rank_clips` and
`pick_thumbnail` go through `llm.generate_vision()`, which sends downscaled
JPEGs inline.

**`LLMUnavailable` is what makes that degradation real.** `llm.py` calls
`v1beta/generateContent` over plain `requests`, with no SDK. The default model is
the floating alias `gemini-flash-lite-latest` rather than a pinned id, because
pinned ids get retired out from under a key. A call retries four times with
exponential backoff on `{429, 500, 502, 503, 504}`, and raises `LLMUnavailable`
on anything else, including a missing key. Every optional feature above is
optional because it catches that one exception; a new model call must raise it
too, not invent its own failure mode.

**Diagrams exist because some scenes are unfilmable.** "branching tree diagram"
returns photographs of trees. A scene is drawn when the script says
`[diagram: ...]` or when reranking rejects nearly every candidate. `diagram.py`
renders a JSON spec (`flow`, `tree`, `stack`, `compare`) in the project theme.

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

**Footage comes from a provider, and a missing key is not an error.**
`visuals.provider` defaults to `pexels`; `pixabay` is the same shape against a
different library, `cards` needs no key at all, and `local` matches your own
clips in `assets/clips` on filename. When the key for a provider is missing the
lookup raises, `visuals` logs `falling back to a card`, and the scene gets a
generated card instead. Nothing fails, so nobody notices until the video is a
slideshow. That silence is why `/api/options` reports which providers this
instance can actually reach and the page disables the rest, and why the CLI
default was moved off `cards`: a deck of cards is not a video.

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
  query returns a stressed person every time. When `vidsmith thumbs` does sample
  frames, it takes them from the picture track rather than the delivery file,
  which already has captions, watermark and progress bar burned in.
- **A broad `except` around an optional feature hides typos, not just outages.**
  `thumbs.from_stock()` ranked its stock photos through a variable that did not
  exist in that scope. The `except Exception` two lines below caught the
  `NameError`, logged "thumbnail pick skipped", and shipped whatever Pexels
  returned first. The feature was dead for every video and nothing went red.
  When a fallback exists to absorb a network failure, give it a test that proves
  the good path still runs, or the fallback becomes the only path.
- **The stock-search cache is a licence condition too, not an optimisation.**
  Pixabay's API terms require a result to be cached for 24 hours rather than
  re-requested, so `visuals._cached_search()` puts every search on disk under
  `.cache/searches` for `SEARCH_TTL`. It also protects the Pexels quota, which
  is 200 requests an hour and 20,000 a month against one key no matter how many
  people are rendering. Do not shorten the TTL to get fresher footage, and note
  the cache key deliberately excludes the API key: it is shared across jobs, so
  a key in a filename would be a secret in a directory nobody guards.
- **Attribution is a licence condition, and it has broken twice.** The Pexels and
  Pixabay API terms require naming the creator and linking back, so
  `pipeline.credits_block()` builds the block from what the search actually
  returned and `description_box()` folds it into the YouTube description.
  `visuals` keeps a per-shot ledger in `credits.json` because `scenes.json` is
  shared across aspects, and `all_credits()` labels each cut's `credits*.txt`.
  Both past failures were silent: a second aspect overwrote the first's credits
  file, and a cached rebuild kept the clip but lost the credit. Generated cards
  need no attribution, so an empty block there is correct rather than a bug.
- **The file you paste has to belong to the cut you are publishing.** There was
  one `description.txt` holding every aspect's credits stacked under `[16:9]`
  and `[9:16]` labels, and it is the file whose entire purpose is to be pasted
  into YouTube. On a real build the two cuts shared **no footage at all** - 22
  credits against 18, zero lines in common - so pasting it named twenty-odd
  photographers whose clips are not in the video, and trimming it by hand
  instead dropped ones that are. Both happened, on published videos, and the
  hand-trim was done on the strength of the Pexels *content* licence, which says
  attribution is not required. That licence does not govern here: vidsmith is an
  API consumer, and the **API Guidelines** are a different document that asks
  for a prominent Pexels link and photographer credit. Read the one that applies
  to how the asset was obtained. `write_metadata()` now writes one
  `description<tag>.txt` per ledger, the 16:9 one keeping the unsuffixed name
  because that is what `aspect_tag()` calls it. `youtube.txt` keeps the labelled
  everything, because that one is for reading rather than pasting.
- **Dashes are kept out by two different mechanisms, and neither covers the
  script.** The voice reads an em dash as a pause the writing did not ask for.
  `llm.undash()` turns em and en dashes into commas, a range between digits into
  `5 to 10` (a comma there is heard as a thousands separator), and leaves
  hyphens alone, but it is only applied to the YouTube metadata and chapter
  labels. A *drafted* script stays clean because the prompt forbids dashes, so a
  draft can still come back with one and nothing downstream will strip it. It
  must not simply be run over a hand-written script either: `captions.TRAILING`
  and `visuals.CLAUSE_END` both rely on a dash as a clause boundary. The range
  rule keys off the dash and runs *before* the general pass, deliberately: when
  it matched any digit-comma-digit instead it could not tell a comma `undash`
  had just made from one the writer typed, and shipped `20,000 requests` into
  the description as `20 to 000 requests`.
  To *count* dashes, match the codepoint, not a shell bracket expression:
  `grep '[em-dash en-dash]'` also reports every `→` in this file, because all
  three characters begin with the same `0xE2` byte and the class is matched
  bytewise. Six arrows read as six em dashes until the count was redone in
  Python.
- **`Path("")` is `Path(".")`, which is truthy and exists.** Every "is there an
  optional file here" guard has to be `is None`, never a bare truth test. The
  captions stage used `Path("")` for "no subtitle track", the guard in front of
  `render.master` let it through, and ffmpeg was handed the current directory as
  an ASS file: `--captions none` died in the master pass on every project
  without a watermark.
- **A filtergraph path needs two layers of escaping, not one.** The drive colon
  is eaten by the filter's option parser and an apostrophe is eaten by the
  filtergraph's quoting, so `ffmpeg_util.escape_filter_path()` spells one as
  `\:` and the other as `\'\''`. Every simpler spelling was tried against real
  ffmpeg and silently dropped the character, looking for `OBrien`. Note the
  original bug was invisible in review: `.replace("'", "\'")` is Python for
  replacing an apostrophe with itself.
- **The concat demuxer list needs a *different* escape, and only one layer.**
  `_concat_copy` writes `file '<path>'` and the demuxer reads that list itself,
  with no filter-option parser underneath, so the drive colon is safe and the
  apostrophe is spelled `'\''`. Two escapes, two parsers, deliberately not
  shared: `escape_concat_path()` is not `escape_filter_path()` and a test holds
  them apart. Unescaped, the stream-copy path - the default, since
  `transition: cut` - died on any machine whose user folder has an apostrophe.
- **Attribution is owed per source, not per build.** The thumbnail's
  photographer was credited only when the *footage* block was already
  non-empty, so an empty block short-circuited the condition. A `cards` or
  `local` build owes no footage credit but still pulls a real Pexels
  photograph for its thumbnail, and that build named nobody and wrote no
  credits file at all. Third failure in this family, and the same shape as the
  first two: silent, licence-bearing, and only visible by building the
  combination nobody builds.
- **The registry is memory and the directories are not.** `_sweep` walks
  `self._jobs`, so every `jobs/<id>/` still on disk when the process ends
  becomes unreachable: nothing holds a reference and nothing ever deletes it.
  The live instance was holding 2.5 GB across five orphans on an 18 GB disk,
  gaining a generation on every restart and reported by nothing. A render needs
  room to write, so it would eventually have failed a build with a message about
  disk rather than about jobs. `sweep_orphans()` runs from the constructor,
  where `_jobs` is empty by definition, so anything present belongs to a process
  that has gone and age never needs consulting. Do not move that call anywhere
  else: run at any other moment it deletes the render in flight, and it looks
  exactly like a tidy-up.
- **Claim the render slot and you own giving it back.** `Jobs.submit` sets
  `_active` under the lock, but only `_run` clears it, so anything between the
  two that can raise has to release it itself. Writing the job directory did
  not, and an unwritable `VIDSMITH_JOBS` wedged the instance: 429 for every
  later caller, for a render that had never started, until the process
  restarted.
- **A closed-set value outside its set used to fall back in silence.** Survivable
  when the fallback is visible: an unknown `theme.preset` is obvious the moment
  you look at the video. Not survivable for `render.aspect`, where `cfg.size`
  fell back to 16:9 while the *filename* was built from the string you typed, so
  `aspect: 9x16` rendered a landscape video into `-9x16.mp4` and nothing said
  otherwise. `9x16` is the likely typo precisely because that is the suffix
  convention. `config._check()` now refuses every closed set on load, because
  argparse and the web's `_validate` already did and `config.yaml` - the surface
  `vidsmith new` writes out in full - validated nothing. Contrast
  `music.ensure_bed`, which gets it right: normalise the mood, *then* name the
  file, so the name cannot lie about the contents.
- **Two writers for one artifact is how attribution goes missing.** `vidsmith
  meta` kept its own copy of the metadata write that omitted the credits block
  and left `description.txt` stale, so regenerating a description stripped the
  attribution out of the exact file you paste into YouTube. There is now one
  `pipeline.write_metadata()` and the CLI calls it. Fourth failure in this
  family; every one of them was a second code path, never the first.
- **An empty tag makes `*{tag}.mp4` match everything.** 16:9 is the unsuffixed
  default, so the pattern collapsed to `*.mp4`, and `demo-1x1.mp4` sorts before
  `demo.mp4` - asking for widescreen thumbnails sampled the square cut without
  a word. Reachable once `build/picture.mp4` is gone, which `invalidate()` does
  on every redraft. `aspect_tag()` is one definition in `config.py` now, because
  the same expression living in two modules is what let this drift.
  **Then `check.py` did the same thing, which is the checker written to catch
  this family failing to it.** It knew two shapes, `wide` and `9x16`, while
  `ASPECTS` has four, and resolved the widescreen cut as the first `*.mp4` that
  was not a short. `demo-1x1.mp4` sorts before `demo.mp4` again - `-` is 0x2D
  and `.` is 0x2E - so the square cut was checked as the widescreen one. The
  loud half was a false alarm on two thumbnails that were correct. The half
  that mattered was silent: the real 16:9 cut was neither the resolved wide cut
  nor a short, so it fell out of every loop and its runtime, captions and
  thumbnail went unexamined, and `runtime` for the chapter checks was read off
  the square cut. 4:5 is vertical and was not a short either, so it was checked
  as though it were landscape. `check.delivered()` now matches every file
  against `aspect_tag()` and returns `(aspect, path)` with the widescreen cut
  first. The lesson is the one this file keeps writing down: an unsuffixed
  default is not a name, and anything deriving a shape from a filename has to
  ask `config` what that shape is called.
- **YouTube drops a whole chapter list rather than the bad line.** The first
  must be at 0:00, there must be at least three, and none may be shorter than
  ten seconds - break one rule and the video has no chapters at all, with no
  error anywhere. A real 60.8s build emitted a six-second chapter and would have
  published a description whose entire list was ignored. `llm.usable_chapters()`
  folds a short chapter into the one before it, measures the last against the
  runtime, and returns nothing when fewer than three survive, because a
  `youtube.txt` promising chapters that will never appear is worse than one
  admitting the video has none. Filtered once in `upload_metadata`, so all three
  written files agree.
- **The upload form's caps are limits; the prompt is a request.** `META_PROMPT`
  asks for a title under 70 characters and twelve tags, and the model has
  complied every time so far. YouTube refuses a title over 100 characters or
  tags totalling over 500, at upload, after the render is paid for.
  `llm.within_youtube_limits()` trims title and description at a word boundary
  and drops whole tags from the end - half a tag is not a tag, and the model
  writes them most relevant first. Unlike the chapter rule this guards a risk
  nothing here has been seen to hit; it is in because the failure lands at the
  destination, which is where this project keeps getting bitten.
- **"No option name near <path>" can mean the filter does not exist.** The macOS
  runner failed on a subtitle path and the message read exactly like an escaping
  fault. It was not: Homebrew's ffmpeg 8.1.2 is built without libass, so there is
  no `subtitles` filter at all, and that is what the parser says when it cannot
  find the filter it was asked for. Two rounds went into escaping rules and one
  into copying the file somewhere with a plainer name, and none of it could have
  worked. Ask the binary first: `ffmpeg_util.filters()` reads the list from
  `-filters` and `require_filter()` names what is missing and what it costs.
  `doctor` reports it, and the caption tests skip on a build that cannot run
  them rather than failing as though the code were wrong.
- **A setting that changes the picture has to be applied per aspect.**
  `--force visuals,render` rebuilds the cut you asked for and no other, so
  turning the title card off left every 9:16 cut 2.4s longer than its 16:9 pair
  and still opening on a card. All four projects were in that state at once.
  `check` caught it four times over ("the two cuts disagree on length: 45s and
  47s") within minutes of being written, which is the whole argument for it: the
  wide cut was correct, the vertical cut was correct, and only the pair was
  wrong. Changing anything under `theme` or `render` means rebuilding every
  aspect that exists, not the default one.
- **`vidsmith check <name>` exists because reading the output beat reading the
  source three times in one day.** It compares delivered files against each
  other rather than against the code that wrote them: every credit in
  `credits<tag>.txt` against `description<tag>.txt`, its own cut's, because
  checking them all against one description let a 9:16 credit pass whenever the
  16:9 description happened to name the same photographer; each thumbnail's
  orientation against the cut it names, caption and chapter timings against the
  runtime, and any jpg that matches no delivered cut. Every check in it is a
  fault that actually shipped, and each one looked correct in isolation. It
  calls no model and no network, so it works on a spent day, which is exactly
  when a hurried refresh gets published. Run it before uploading anything.
- **Replacing a thumbnail invalidates `description.txt`, which is the file that
  gets published.** `description.txt` and `youtube.txt` are composed from the
  `credits*.txt` files, so `thumbs --refresh` corrected the credits and left the
  description beside them naming the photographer it had just dropped. Four
  videos sat upload-ready in exactly that state, and the credits file that
  looked right was not the one anybody pastes into YouTube. The refresh now
  rebuilds the metadata from `youtube.json` already on disk, which costs no
  model call, so honest attribution never depends on having quota left. When
  changing anything a credits file feeds, ask what else is derived from it.
- **"Untitled" is a sentinel, and every entry point has to resolve it.**
  `build()` fills an empty or `Untitled` config title from the script heading and
  writes it back; `thumbs --refresh` read `cfg.title` raw, slugged it to
  `untitled`, and wrote a pair of orphan jpgs beside the real thumbnails while
  leaving those stale. Nothing looked wrong: it reported two thumbnails
  rewritten, and two files had genuinely been written. Only the mtimes gave it
  away. `pipeline.resolve_title()` is now the one resolver and both callers use
  it, because the same divergence had already happened once between the CLI and
  the web job. Anything slugging `cfg.title` straight into a filename is the bug.
- **The macOS narration hang is not fixed, and the `-t` did not fix it.**
  `build_narration` hung again inside `ff.run` after that change, so the reading
  of the filtergraph that produced it was wrong. It is intermittent: many green
  runs either side. What is fixed is the reporting. A `TimeoutExpired` carries
  whatever the process printed before it was killed, and that was being thrown
  away, so both occurrences were reported as a stack trace through `subprocess`
  with nothing from ffmpeg in it. `VIDSMITH_FFMPEG_TIMEOUT` is now set to 45s in
  CI, under the 120s pytest limit, so our own guard fires first and prints what
  ffmpeg said; at the 900s default pytest always won the race and the guard
  never spoke. All three jobs carry both limits now, since a hang on ubuntu or
  windows was every bit as opaque. Next occurrence, read that output before
  theorising again.
  **And the guard still did not fire on the third occurrence, for a reason that
  had nothing to do with ffmpeg.** `TIMEOUT` was read once at import, so the
  only way to test it was `importlib.reload`, which monkeypatch does not undo.
  The two tests for it reloaded to read the value and reloaded again to restore
  it, but the restoring reload ran *while monkeypatch was still in force*, so it
  restored the module against the patched environment and left it holding 900.
  `test_filter_paths` sorts before `test_integration`, so every later file ran
  at 900 whatever the job had set, and pytest won the race every time. Setting
  45 in all three jobs had changed nothing at all. It is `timeout_limit()`
  reading the environment per call now, so there is no module state to go stale.
  Proven by measurement before it was fixed: with the variable set to 45, a test
  running after that file saw 900.0. The lesson is not about timeouts. **A test
  that mutates module state can silently disarm a safety net three files away**,
  and the second time this happened in one day was threads outliving their test
  and appending to the next test's list.
  **Fourth occurrence, 2026-09-02, and the guard finally fired. It told us
  nothing, and the reason is our own log level.** Same test,
  `test_each_scene_speaks_at_its_own_start`, on the first CI run of PR #58,
  with ubuntu and windows green and a re-run of the same job passing in 65s.
  The 45s from the job environment was honoured, so `timeout_limit()` reading
  per call is working. Both bounds the last round added were present:
  `atrim=0:22.746` in the graph and `-t 22.746` on the command. It sat the full
  45 seconds on about a second of work and was killed. What it said before it
  was killed: nothing at all.
  That empty capture reads like a finding and is not one. `ff.run` takes
  `quiet=True` by default and no caller overrides it, so every ffmpeg call in
  this project runs at `-loglevel error`, and at that level a **healthy** ffmpeg
  also prints nothing. So the capture cannot tell a process that hung before it
  started from one that stopped halfway, which is the single thing worth
  knowing here. The reporting fix from the last round answers a question it is
  not equipped to answer, and reading its output as evidence about the
  filtergraph is a fifth round of the same mistake this file keeps recording.
  **That is now fixed, and the fifth occurrence should be readable.** Every
  `ff.run` carries `-progress pipe:1`, which writes `out_time` to stdout
  regardless of the log level, and the timeout report reads the last one back:
  "it reached out_time=00:00:13.000000 before it was killed", or "it never
  reported any progress, so it had not begun encoding". Those are different
  faults and the previous three hangs could not tell them apart. Verified
  against real ffmpeg rather than only mocked: a `veryslow` 720p encode killed
  at 4s reported `out_time=00:00:08.000000`.
  Two details worth keeping. Silence on stderr is now reported as the
  non-finding it is, in those words, so nobody reads it as evidence a fifth
  time. And progress lines are stripped out of ordinary failure messages by
  `_without_progress()`, because a broken filtergraph buried under half a
  second of counters is a worse message than the one we had.
  **Sixth occurrence, 2026-09-03, on PR #69, and the progress line paid for
  itself.** Same test, ubuntu and windows green, and a re-run of the same job
  passing. What it said this time:

  ```
  it reached out_time=00:00:21.342000 before it was killed
  ```

  21.342 of a 22.746s output. So it is **not** failing to start and the graph is
  **not** failing to produce - it gets to within a second and a half of the end
  and then sits for the whole 45s. Five rounds of theorising had no way to tell
  those apart; one line of output did.
  The tail is the region a bare `apad` owns, so `build_narration` now uses
  `apad=whole_dur=<total>` and the graph holds nothing that generates forever.
  A bare apad pads until something downstream stops asking, and `atrim` drops
  the frames past the end **without propagating EOF upstream**, so apad went on
  producing silence for atrim to throw away. Measured against real ffmpeg on the
  same three-input graph before changing it: identical 22.746s output either
  way, and less work bounded, 0.18s against 0.30s.
  **This is a narrowing, not a proven fix, and the next person should not read
  it as one.** The hang is intermittent and does not reproduce off macOS, so
  nothing here has watched it stop happening. What is true is that the one
  unbounded element is gone and the evidence points at the region it owned. If
  it happens a seventh time, that reading is wrong and the pad is not the
  culprit: look at `amix` with `dropout_transition=0`, which is the other filter
  in the tail, and read the new `out_time` before theorising again.
- **An ffmpeg call with no timeout can hang forever, and one did.** `apad` is
  infinite by definition, so `build_narration` left `atrim` as the only thing
  ending its output; `master()` had always passed `-t` as well, and this one did
  not. macOS CI hung inside exactly that call. Both are fixed: the narration
  encode is bounded by `-t` as well as the graph, and `ff.run()` takes a
  `VIDSMITH_FFMPEG_TIMEOUT` (900s) so a stuck encode raises instead of sitting.
  The timeout matters well beyond CI. The web service holds one render slot and
  gives it back on the way out of the job, and a subprocess that never returns
  takes no way out, so the instance stops accepting work permanently. When
  bounding an encode, keep the limit generous: it is a bound on forever, not a
  performance budget, and killing an honest long encode is worse than the hang.
- **PowerShell unrolls a single-element array on its way out of a statement.**
  `$live = if (...) { @(...) } else { @() }` hands back a *String* when the array
  holds one item, so `$live[-1]` indexes the string and yields its last
  character. `serve-public.ps1` printed a 24-character access token as `c`, and
  `.Count` is 1 either way so nothing looked wrong. Assign in two statements, and
  read the last element with `Select-Object -Last 1`, which behaves the same on
  a scalar and an array. Testing the same lines with a direct assignment - the
  form that keeps the array - passes every time and proves nothing.
- **A spent model quota is not a retryable failure.** Gemini answers `429
  RESOURCE_EXHAUSTED` when the free allowance is gone, and the generic retry
  loop spent four more requests on a number only the next day restores.
  `llm.QuotaExhausted` is raised immediately instead, and `_refuse_if_spent()`
  sits outside *both* request loops: the guard was added to `generate()` and not
  `generate_vision()`, so every thumbnail pick went on retrying for another day.
  The web layer maps it to **429, not 502** - a 5xx invites a tunnel or proxy to
  substitute its own HTML error page, and the page then reports `Unexpected
  token '<'` instead of what happened. The daily window is Pacific-aligned, so it
  does not roll over at local midnight.
- **Not every `RESOURCE_EXHAUSTED` is the day, and the body says which.** The
  same 429 covers the per-minute burst limit, which clears on its own, and the
  daily cap, which does not. Refusing both kills a build over a blip; retrying
  both spends what is left of a budget already gone. The `QuotaFailure` detail
  carries a `quotaId` that names the window
  (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), the model, and the
  ceiling, so `_refuse_if_spent()` refuses on `PerDay` and returns a wait on
  `PerMinute`. The number is worth repeating back: "500 requests a day for
  gemini-3.5-flash-lite" tells you to switch models, "out of quota" does not.
- **The `RetryInfo` beside it is not a promise.** Against a spent daily cap it
  advertised 8s, then 56s, then 56s, then 52s. All four waits were honoured and
  all four met another 429. It is trusted only once the `quotaId` says waiting
  can help, and clamped so a bad value cannot hang a build. A wait longer than
  the ordinary backoff is announced through the build log, because reranking
  runs once per scene and several silent minute-long pauses inside one build are
  indistinguishable from the hang described under Tests. `rank_clips()` takes
  `log` for exactly that reason; calling it without one makes the pause silent
  again. The rule is enforced, not remembered: every helper in `llm.py` that
  issues a request must accept a `log` and hand it down, and
  `test_every_request_helper_can_announce_a_wait` fails by name on any that does
  not. That test exists because the same mistake had already been made twice:
  the quota guard went into `generate()` and not `generate_vision()`, then the
  wait announcement went into `rank_clips()` and not `design_diagram()`, which
  runs just as often. Both were caught by reading, after shipping.
- **The free ceiling here is requests, not tokens.** 500 generate calls a day
  per model. That is unlike the Groq trap noted in the global `CLAUDE.md`, where
  the binding limit is tokens per day and appears in no header; Gemini prints
  its metric, its ceiling and the model in the error body. Do not carry the
  Groq assumption across. Note also that the last few requests trickle rather
  than stopping cleanly, so one probe succeeding does not mean the day is open.
- **A deliberate refresh should refuse where a build degrades.**
  `thumbs.from_stock()` falls back to a keyword search when the model is
  unavailable, because a render must never fail over a thumbnail. `vidsmith
  thumbs --refresh` passes `strict=True` and refuses instead: writing the same
  fallback over an existing thumbnail is worse than leaving it alone.
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
browser polls, because a video takes minutes. **One render at a time, and a
line behind it.** Two x264 encodes starve each other, so exactly one runs; a
second submission waits rather than being refused, because the box could always
have taken the work, only not that minute. `VIDSMITH_MAX_QUEUE` bounds the line
at three, and a full line is a 429 again: an unbounded queue tells the tenth
caller "queued" and makes them wait half an hour, which is a worse answer than
a refusal with a reason. `snapshot()` carries `position` and `waiting`, and the
position lives on the queue rather than on the job, because a copy kept on the
job goes stale the moment anything ahead of it finishes or is cancelled.

Cancelling a *queued* job is a separate path from cancelling a running one: the
running one is cooperative and reads a flag in the log callback, and a job that
has not started has no log callback to read anything, so it is dropped from the
line instead.

**Do not propose running two renders at once. It was measured and it does not
work.** The obvious idea is that most of a build is waiting on somebody else's
network, so jobs could overlap and only the encode need serialise. On the
2 vCPU instance, for a 41 second video taking 167s end to end:

| stage | share | what it is actually doing |
| --- | --- | --- |
| visuals | 48% | **71% ffmpeg**, 20% network |
| render | 47% | one ffmpeg call |
| everything else | 5% | |

**78% of a build is ffmpeg**, and 17% is network. `visuals` is not waiting on
Pexels, it is running eleven encodes to scale, crop, pan and trim each shot.
Overlapping jobs would interleave the same CPU work on the same two cores: the
ceiling is 167/131 = **1.29x**, assuming perfect overlap and no contention, in
exchange for gating inside the log callback, a second cancellation path and
unbounded disk. The lever is fewer pixels or more cores, and the cheap encoder
win is already taken - both the per-shot encodes and the master pass run
`veryfast`, and `crf` is what holds quality. Measure before reopening this; the
numbers above came from wrapping `ff.run`, `ff.probe` and
`requests.Session.request` and attributing each call to the running stage.

Jobs live in memory under `jobs/<id>/` and are swept an hour after finishing, so
anything worth keeping is copied into `projects/`. `VIDSMITH_TOKEN` gates the
API when set; `/healthz` stays open and reports ffmpeg and bundled fonts.
**`keys` is behind the token**, deliberately: it inventories which credentials
the box holds, and a stranger who found the URL has no business reading it.

`VIDSMITH_JOBS` moves the job directory, and `VIDSMITH_MAX_MINUTES` (default 4)
caps how long a submitted script may run. Both exist because the host, not the
code, is usually the constraint.

**Stopping a render is cooperative.** `POST /api/jobs/{id}/cancel` sets a flag
that is read inside the log callback, so the run ends at the next stage boundary
rather than mid-encode, and the status becomes `cancelled`. `web.jobs.Cancelled`
derives from `BaseException` on purpose: the pipeline has broad `except
Exception` handlers that would otherwise swallow the cancellation and finish
rendering a video nobody is waiting for. `GET /api/busy` is the unguarded
read-out the idle page polls, so a second visitor learns the slot is taken
before writing a script instead of collecting a 429 afterwards.

**What the page needs to know, the server tells it.** `/api/options` serves
`stages` from `jobs.stage_sequence()`, `script` from `script_parser`'s own
`DIRECTIVE_KINDS`, `NOTE_PREFIXES` and `WPS`, and `providers` with a `ready`
flag per source computed from the keys that actually resolve. So a pipeline
stage, a script directive or a footage source reaches the page without anyone
editing it. Follow this whenever the page needs something the server knows.

What is left in JavaScript is the scene-splitting rule itself, in `analyse()`:
it counts scenes and estimates runtime as you type, and it is reimplemented
rather than served because asking the server per keystroke would be absurd. It
is not authoritative and the server still decides, but if the scene-break rule
changes, `analyse()` changes with it. The regex literals beside it are fallbacks
for a failed options fetch, not a second source of truth.

## Deploying

**There is a live instance, and it is the AWS one.** `vidsmith.duckdns.org`,
an EC2 Ubuntu 24.04 box with 2 vCPU and 2 GB, uvicorn on loopback behind Caddy
for TLS, run by a systemd unit called `vidsmith`. `deploy/aws.md` is the whole
of it, including how to get a shell, which is the part that used to be missing.

Three things about that box are worth knowing before touching it.

**The app is at `/home/ubuntu/vidsmith`**, which is `APP_DIR` in
`cloud-init.sh`. Not `/opt`. An afternoon went into a deploy against `/opt`
because nobody checked the doc that already said so.

**Drive it with `ssh host "commands"`, never an interactive session.** With a
session in one window and a local shell in another, commands meant for the
server get typed into the local one, which answers plausibly: a missing
directory, an unknown command, a commit hash from the wrong machine. Four
rounds of "ran it, here is the output" once came from a laptop while the server
was never touched. `hostname` first if working interactively, and **when a
remote fix reports success and the symptom does not move, check which machine
it ran on before theorising about the code.**

**The public endpoints are the honest witness.** `/healthz` should list the two
DejaVu faces and `/api/busy` should carry `waiting`. Both are one request, both
are unauthenticated, and between them they have caught every deploy here that
reported success and had changed nothing.

Updating is one line, and the restart is what sweeps orphaned job directories,
so it cleans up on the way in:

```bash
ssh -t -i ~/.ssh/vidsmith-key.pem ubuntu@vidsmith.duckdns.org "cd vidsmith; git pull --ff-only; bash scripts/fetch-runtime-deps.sh --fonts-only; sudo systemctl daemon-reload; sudo systemctl restart vidsmith"
```

SSH is restricted to one address and a home connection's address changes on its
own, so a timeout is the security group needing your current IP rather than a
dead box. A refused connection is a different fault, and so is a key error.

The other three ways out, and the first is usually right for showing someone.

**A Cloudflare quick tunnel** (`scripts/serve-public.ps1`) puts the local server
on a public URL: free, no account, no domain, and `cloudflared` from winget. The
render happens on this machine, so it runs at full local speed instead of a
hosted instance's fraction of a CPU. The URL lasts only as long as the window,
which is the point when the audience is one person for ten minutes.

The two real hosts solve the ffmpeg problem differently.

**Render** (`render.yaml`) uses the *native Python runtime rather than a
container*, because ffmpeg and the fonts are fetched in the build step by
`scripts/fetch-runtime-deps.sh`. Encoding is CPU-bound, so the free instance runs
10 to 15 minutes for a 90 second video and can exhaust memory at 1080p; the
blueprint therefore asks for `starter` and holds `VIDSMITH_MAX_MINUTES` at 2,
with jobs in `/tmp`. `autoDeploy` is off, and the keys are `sync: false` so they
are set in the dashboard and never committed.

**Hugging Face Spaces** (`deploy/huggingface.md`) is the better machine but no
longer free: since 2026-08-25 a Docker Space on free cpu-basic is refused with
`402 Payment Required` and needs PRO. Its 2 vCPU and 16 GB do encode 1080p where
a 512 MB instance does not. It builds the `Dockerfile` on their side, so this stays true even
though Docker cannot run on this machine. Keys go in Space secrets, and
`/healthz` is the check that matters after a build: `fonts` should list the two
DejaVu files, and `keys`, which needs the token, should show `gemini` and
`pexels` true. A free Space
sleeps after an idle stretch and takes a minute to wake.
