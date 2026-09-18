# Keys and hosts

`pipeline.find_keys()` resolves `GEMINI_API_KEY`, `PEXELS_API_KEY` and
`PIXABAY_API_KEY` from the environment, then `.env` at the project, projects
parent and repo root, then two sibling projects' `.env` files. Nothing key-driven
is required: with no keys at all the build still produces narrated, captioned
video over generated cards.

**The page shows what is left before a render spends it.** `vidsmith/usage.py`
keeps `.cache/usage.json` (`VIDSMITH_USAGE` moves it; `conftest` points every
test at its own). The two services report differently, so they are recorded
differently. Pexels sends `X-Ratelimit-Limit`, `-Remaining` and `-Reset` for the
monthly allowance on every response, so the last one is kept as fact. Gemini
sends nothing until it refuses, so each request attempt, retries included, is
counted per model per **Pacific** day, and a daily refusal is recorded with the
limit it names. That count is this server's only: the laptop spending the same
key is invisible to it. Windows Python has no tz data, so `usage.pacific()`
applies the US daylight-saving rule itself rather than calling `zoneinfo`.
`/api/usage` is behind the token.

**Unsetting `GEMINI_API_KEY` in the shell does not stop a build calling Gemini.**
The sibling `.env` files still resolve, so a build "with the key unset" spent a
`pick_thumbnail` request anyway, which nothing reported until the build manifest
counted it. To see what a build actually spent, read `totals.model` in
`build/manifest{tag}.json` rather than assuming from the environment.

**The manifest is written by `pipeline.build()` on the way out, never inside
the build.** The body is `_build()`, which returns early at every `--stop-after`
stage and can fail anywhere; one writer in the wrapper is what catches a failed
or cancelled run. Recording is a context variable, so a call made outside a
build records nothing, and `asyncio.to_thread` workers (Polly) still report into
the build that started them. Counts sit under `totals`, never beside the facts:
the first real manifest had its `voice` fact overwritten by the `voice` counts.
Source-inspecting tests that look for build logic read `_build`, not `build`.

`ffmpeg_util` resolves ffmpeg from `FFMPEG_BINARY`, then `bin/`, then PATH, then
the winget package directory. A host with no package manager fetches a static
build at deploy time via `scripts/fetch-runtime-deps.sh`. The themes name Windows
font families, so `assets/fonts` is handed to the `subtitles` filter as
`fontsdir`; without it libass silently substitutes a different face.
