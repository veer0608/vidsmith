# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It is kept short on purpose, because it is loaded into every session. The
detail, and the story of how each rule was learned, lives in `docs/claude/`.
Read the file the table points at before changing that area; it is not loaded
for you.

## Start here

Find the row for what you are about to do, read that doc, and note the trap
before writing anything. The traps are the reason this file exists.

| About to | Read | What bites |
| --- | --- | --- |
| Edit a script | [script.md](docs/claude/script.md) | A blank line starts a new scene |
| Write a test | [tests.md](docs/claude/tests.md) | Build scenes with `make_scene()`, never by hand |
| Add or change a model call | [architecture.md](docs/claude/architecture.md), `LLMUnavailable` | Raise `LLMUnavailable` or the fallbacks stop working |
| Touch shot lengths or timing | [architecture.md](docs/claude/architecture.md), narration slot | Clips must sum to `scene.duration` exactly, and no shot may outlast its clip |
| Change what footage gets searched for | [incidents.md](docs/claude/incidents.md), unrelated footage | One search per scene, or a metaphor, reads as unrelated; a beat's verdict is keyed `index.beat` |
| Move captions or diagrams | [architecture.md](docs/claude/architecture.md), karaoke; [incidents.md](docs/claude/incidents.md), layers and ASS | Never hardcode a caption fraction; the ASS `Format:` line is positional |
| Change ffmpeg or the encode | [architecture.md](docs/claude/architecture.md), three passes | Do not collapse the passes into one |
| Hand a path to ffmpeg | [incidents.md](docs/claude/incidents.md), escaping | Two escapes, two parsers; never share one helper between them |
| Pass an optional file between stages | [incidents.md](docs/claude/incidents.md), `Path("")` | `Path("")` is truthy and exists; guard on `is None` |
| Add a config key | [configuration.md](docs/claude/configuration.md) | A misspelled key is ignored in silence; a closed-set value is refused on load |
| Reach for a build's output file | [incidents.md](docs/claude/incidents.md), an empty tag | 16:9 has no suffix, so `*{tag}.mp4` matches every other cut |
| Write an artifact a second way | [incidents.md](docs/claude/incidents.md), two writers | Call the one writer; a second copy is how credits go missing |
| Change the footage source | [configuration.md](docs/claude/configuration.md) | A provider with no key falls back to cards without failing |
| Reword the drafting prompt | [script.md](docs/claude/script.md) | `test_script_prompt.py` says what it must still demand |
| Repair text a model wrote | [incidents.md](docs/claude/incidents.md), dashes | A repair pass cannot tell its own output from the input |
| Redraft an existing script | [incidents.md](docs/claude/incidents.md), stale caches | Scene-indexed caches must be invalidated |
| Publish a video anywhere | [incidents.md](docs/claude/incidents.md), attribution and chapters | Crediting is a licence condition; YouTube drops a chapter list rather than the bad line |
| Edit the web page | [web-service.md](docs/claude/web-service.md) | Ask the server for what it knows; do not hardcode a second copy |
| Touch the web queue | [web-service.md](docs/claude/web-service.md); [incidents.md](docs/claude/incidents.md), the render slot | Claim the slot and you own giving it back on every path out |
| Change how a finished render swaps a shot | [web-service.md](docs/claude/web-service.md), one shot of a finished render | A retake is `build(retake=True)`, never a second copy of the render stage |
| Show it to someone | [deploying.md](docs/claude/deploying.md) | The tunnel beats both hosts |
| Commit anything | Working in this repo, below | `main` is protected; every change is a branch and a PR |
| Publish a build | `vidsmith check <name>` | Run it first; it compares delivered files against each other |
| Judge the footage a build chose | `vidsmith sheet <name>`, [configuration.md](docs/claude/configuration.md), reading the footage | Read the frames beside the words, never the log; with no `visuals{tag}/shots.json` the times are another cut's |
| Add or change a footage style | [configuration.md](docs/claude/configuration.md), `visuals.genre` | A style word that is a noun, a place or a time becomes the subject; a rule the prompt states and the model can break is enforced in code |
| Bring a live render down | `vidsmith fetch <job>`, [deploying.md](docs/claude/deploying.md) | The token is the instance's own, not the one in a local `.env` |
| Check a video already public | `vidsmith check <name> --published <id>` | The offline half cannot see the YouTube form, where every shipped fault landed |
| Upload anything | `vidsmith upload`, [uploading.md](docs/claude/uploading.md) | Resolve every file by the same aspect tag; a caption track is not optional |
| Rebuild anything already uploaded | `check` reports publish drift | A new description over the same cut means re-paste; a new *cut* means the public pair still match, and pasting its description onto the old video breaks attribution |
| Debug an ffmpeg filter error | [incidents.md](docs/claude/incidents.md), a missing filter | "No option name near" can mean the filter does not exist |
| Touch `serve-public.ps1` | [incidents.md](docs/claude/incidents.md), PowerShell unrolling | An `if` that returns an array hands back a string |
| Change the default model | `test_docs_model_id.py` | Every Gemini id the docs name must be `llm.DEFAULT_MODEL`, or listed in `HISTORICAL` with a reason |
| Change a limit, default or threshold the docs quote | `test_docs_constants.py` | The docs' numbers are checked against the code; change both, and reword a sentence only with its case |
| Handle a model 429 | [incidents.md](docs/claude/incidents.md), quota entries | Read the `quotaId`: `PerDay` refuses, `PerMinute` waits |
| Resolve a key, or read what a build spent | [keys.md](docs/claude/keys.md) | Unsetting a key in the shell does not stop a build finding one in a sibling `.env` |
| Deploy or update the live box | [deploying.md](docs/claude/deploying.md) | `ssh host "commands"`, never a session; check which machine it ran on |
| Write a test that reloads or spawns | [tests.md](docs/claude/tests.md); [incidents.md](docs/claude/incidents.md), macOS narration hang | Module state and threads outlive their test and disarm the next file |
| Write a test that resolves a key | [tests.md](docs/claude/tests.md) | `conftest` clears every `KEY_ENV` name; a test must never read the machine's own |
| Speed the service up | [web-service.md](docs/claude/web-service.md) | Concurrency is settled and measured; 78% of a build is ffmpeg |
| Change the voice provider | [architecture.md](docs/claude/architecture.md), two providers | Polly's marks are ms with starts and no durations, billed twice |
| Reach for a version of ffmpeg | [tests.md](docs/claude/tests.md) | Three are in play and they disagree about libass |
| Change the price or the buy link | `test_selling_links.py` | Both docs must agree; a `PASTE_GUMROAD_*` token holds CI red on purpose |
| Measure a model call, or quote a number from one | [benchmarks.md](docs/claude/benchmarks.md) | `labels.json` is ground truth a person wrote; never write to it from code or a guess |

## Rules that hold everywhere

One line each. The reason behind every one is in the linked doc.

- `pipeline.build()` is the spine: parse → b-roll queries → narration → visuals →
  captions → render → metadata, each stage cached in `projects/<name>/build/`.
- Word timings from the voice drive the cut, the captions and the mix. Nothing
  transcribes anything.
- Every Gemini call is optional and degrades on `LLMUnavailable`, except
  drafting, which refuses. The model is a pinned id, never a `-latest` alias.
- Sizes key off frame **width**, never height.
- Changing anything under `theme` or `render` means rebuilding every aspect
  that exists, not the default one.
- An unsuffixed default is not a name: ask `config.aspect_tag()` what a shape is
  called, never derive it from a filename.
- A broad `except` around an optional feature needs a test proving the good path
  still runs, or the fallback becomes the only path.
- Attribution is a licence condition, owed per source, and every past loss was a
  second code path writing the same artifact.
- Nothing may `-stream_loop` a clip; `fit_shots()` never asks a clip for more
  than it holds.
- Do not shorten the stock-search cache TTL; Pixabay's terms require it.
- Do not propose running two renders at once, or an Animation style. Both were
  built, measured and removed.
- Heredocs mangle backslash escapes here. Use Write or Edit for anything with
  escapes.

## Working in this repo

**`main` is protected and requires all three CI checks.** `test`, `windows`
and `macos` are all required as of 2026-08-28. A direct push is rejected with
`GH006`, so every change is a branch, a PR, and a wait for ubuntu, windows and
macos to go green before `gh pr merge --squash --delete-branch`. Budget for the
round trip: it is a few minutes per change, which is the argument for batching
a fix and its test into one PR rather than two.

Two habits this repo keeps punishing:

- **Do not pipe a command you gate on into `tail`.** The pipeline's exit status
  is `tail`'s, so `pytest ... | tail && git commit` commits over failing tests.
  Use `set -o pipefail` or check `${PIPESTATUS[0]}`.
- **Make the failure legible before theorising about it.** Every long detour in
  [incidents.md](docs/claude/incidents.md) was reasoning that felt sufficient
  and was never checked against the failing thing: an escaping theory that
  survived two rounds because nobody ran `ffmpeg -filters`, a token bug "fixed"
  once before it was found, a quota guard added to one of two call sites. The
  move that works is cheap and boring - print the value with delimiters and a
  length, run the real script's own lines rather than a retyped copy, ask the
  binary what it can do.

When a new trap is learned, write the story into the right `docs/claude/` file
and add a row or a one-line rule here. Do not grow this file back into the docs:
`test_claude_md.py` holds it to 200 lines and fails on any `docs/claude/` file
the docs map below does not link.

## Commands

This is a **PowerShell 5.1** machine. `&&` is a parser error there; chain with `;`.
`.\vidsmith.cmd` wraps `.venv\Scripts\python.exe -m vidsmith`.

```powershell
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest
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

## Docs map

| File | Covers |
| --- | --- |
| [tests.md](docs/claude/tests.md) | CI matrix, the three ffmpegs, lint gate, credential isolation, `make_scene()` |
| [benchmarks.md](docs/claude/benchmarks.md) | `bench/rank_clips`, labels, kappa, bootstrap intervals |
| [script.md](docs/claude/script.md) | `script.md` format, directives, `WPS`, the drafting prompt and `lengthen()` |
| [architecture.md](docs/claude/architecture.md) | Pipeline stages, voice providers, narration slot, Gemini calls, diagrams, music, render passes, captions, theme |
| [configuration.md](docs/claude/configuration.md) | Config schema, providers, `local`, `visuals.genre`, shot sheets, loudness |
| [incidents.md](docs/claude/incidents.md) | Things that have actually broken here, one entry per fault |
| [keys.md](docs/claude/keys.md) | Key resolution, usage tracking, the build manifest, ffmpeg resolution |
| [uploading.md](docs/claude/uploading.md) | `upload.py`, OAuth, YouTube quota, uploading from the page |
| [web-service.md](docs/claude/web-service.md) | Render queue, jobs, retakes, rewrites, Shorts cuts, covers, cancellation |
| [deploying.md](docs/claude/deploying.md) | The EC2 instance, `vidsmith deploy`, `fetch`, tunnel, Render, Spaces |
