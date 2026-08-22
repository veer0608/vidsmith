# vidsmith

Script in, narrated and captioned YouTube video out.

You write a markdown script. vidsmith speaks it in a neural voice, finds a shot
for every scene, burns word-timed captions, mixes music under the narration, and
encodes a delivery-ready mp4 — plus an `.srt` and a draft title/description/chapters.

```powershell
cd ~/claude/vidsmith; .\vidsmith.cmd build demo
```

```
script   4 scenes, ~35s estimated
voice    en-US-AndrewNeural at +8%
visuals  provider=cards 1920x1080
captions captions.ass + captions.srt
render   28.1s of picture, mixing and encoding
done     out/why-your-bank-statement-lies.mp4  (29.1s, 4.2 MB, 22s to build)
```

## Why the captions are exact

Most tools generate speech, then run Whisper over that speech to find out where
the words landed. vidsmith never does that. Edge's TTS returns a `WordBoundary`
event for every word it speaks, so the timings come from the engine that made
the audio. There is nothing to drift, no model to download, and no transcription
step to be wrong.

Punctuation is the one thing those events drop, so it is stitched back on from
the source script before captions are grouped — otherwise nothing ever breaks on
a full stop.

## Install

This is a PowerShell 5.1 machine: `&&` is a parser error there, so the commands
below chain with `;`.

```powershell
cd ~/claude/vidsmith; python -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt
```

ffmpeg is the only non-Python dependency:

```bash
winget install Gyan.FFmpeg
```

Check everything at once:

```powershell
cd ~/claude/vidsmith; .\vidsmith.cmd doctor
```

## Writing a script

Paragraphs are scenes. Headings start a new scene. `[visual: ...]` sets the
stock-footage search for that scene, `[hold: 4.0]` forces a minimum duration,
and lines starting with `>` are production notes that never get spoken.

```markdown
# Why Your Bank Statement Lies

## The hook
[visual: paper bank statement on a desk]
Your bank statement is not a record of what you spent. It is a record of what
your bank found convenient to store.

## Merchant names
[visual: card terminal in a small shop]
The merchant name on a transaction is typed by the payment processor, not the
shop. That is why a coffee costs money at a company you have never heard of.
```

Or have Gemini draft one:

```powershell
cd ~/claude/vidsmith; .\vidsmith.cmd new gil --topic "why Python's GIL still matters" --minutes 3
```

## Commands

| command | what it does |
| --- | --- |
| `vidsmith new NAME [--topic ... --minutes N]` | create a project, optionally drafting the script |
| `vidsmith build NAME` | render to mp4 |
| `vidsmith voices --lang en-IN` | list narration voices |
| `vidsmith meta NAME` | regenerate YouTube title/description/chapters |
| `vidsmith thumbs NAME [--count 6]` | rank thumbnail frames, compose a titled one |
| `vidsmith doctor` | check ffmpeg, edge-tts and keys |

Useful `build` flags:

```
--aspect 9:16              vertical cut for Shorts (16:9, 9:16, 1:1, 4:5)
--provider pexels          real stock footage instead of generated cards
--voice en-IN-PrabhatNeural
--music path/to/bed.mp3    ducked under the narration automatically
--captions block           no karaoke highlight
--theme ink                colour and type preset
--accent "#FF7A59"         accent override
--watermark "@handle"      channel mark, bottom-right
--no-cards                 skip the title and end cards
--force voice,visuals      redo cached stages
--stop-after voice         stop early to check timings before rendering
```

Each aspect gets its own picture, captions and output file, so a vertical cut
never overwrites the landscape one. Narration is shared between them — changing
aspect does not re-synthesize speech.

## Cut rhythm

A scene is not a shot. Narration runs six to eight seconds, and one unbroken
take that long is what makes generated video look generated - so each scene is
split into shots at the sentence boundaries the TTS already reported, and the
picture changes exactly where the speaker lands a full stop.

```
visual  scene   0  2 shots  2.7+3.6   paper bank statement on a desk
visual  scene   1  2 shots  4.3+4.1   card terminal in a small shop
```

One search serves every shot in a scene, so the shots stay on the same subject
and it still costs one API call. A sentence longer than `max_shot_seconds` is
broken at its last comma; if the provider cannot supply enough distinct clips,
the plan collapses back to fewer, longer shots rather than cutting to the same
footage twice.

```yaml
visuals:
  cut_on_sentences: true
  min_shot_seconds: 2.4
  max_shot_seconds: 5.5
```

## Footage that matches the line

Stock search ranks by popularity, not by whether a clip depicts what is being
said - "calendar pages turning" returns a book. With `GEMINI_API_KEY` set, the
preview stills of the top candidates are shown to Gemini alongside the narration
line, and the results are reordered by what is actually in frame.

```
visual  scene   2  2 shots  3.6+3.2   calendar pages turning
  rerank: picked #2 over the top result
```

The model also marks candidates that show the wrong subject outright, and those
are never used. Only judged candidates are eligible - letting the unjudged tail
of the result list backfill would quietly reinstate the clips the reject pass
just removed. When that leaves fewer usable clips than the scene has shots, the
shot plan collapses:

```
visual  scene   2  1 shot   6.8   calendar pages turning
  rerank: rejected 7 of 8 as the wrong subject
```

Holding one correct shot for 6.8s beats cutting to a book halfway through a line
about calendars.

It judges stills, not video, so it costs one call per scene and no extra
downloads. The ordering is cached in `build/visuals*/rerank.json`, so rebuilds
do not re-ask. Without a Gemini key, or if the call fails, the provider's own
order is used and the build carries on.

```yaml
visuals:
  rerank: true
  rerank_pool: 8      # candidates shown to the model
```

## Music

There is no free API for licensed music, and an unlicensed track is a copyright
strike waiting to happen - so the bed is synthesised. It is a slow chord pad:
detuned sines per chord with soft attack and release, low-passed and smeared
with an echo until it reads as atmosphere rather than as notes.

```yaml
audio:
  music: auto        # "auto", "" for none, or a path to your own file
  mood: calm         # calm | warm | tense
  music_gain_db: -18
  duck: true
```

The bed is loudness-normalised when generated, so `music_gain_db` means "this
far under the voice" rather than "this far under whatever amplitude the
synthesis happened to land on". Measured on the demo: the bed sits around
-34 dB, and ducks 8 dB whenever anyone is speaking.

```
--music auto --mood tense     # generated bed
--music path/to/track.mp3     # your own
--music none                  # silence
```

## Diagrams for what cannot be filmed

Some ideas have no footage anywhere. A script about B-trees asks for "branching
tree diagram" and every stock library returns a photograph of a tree - the
reranker cannot fix that, because the footage does not exist.

Those scenes get drawn instead. Gemini writes a small JSON spec (text, not image
generation - the free tier has no image quota) and vidsmith draws it in the
project's theme, so a diagram frame sits beside the cards and the footage
without looking pasted in. Four layouts: `flow`, `tree`, `stack`, `compare`.

Two things trigger one. An explicit directive in the script:

```markdown
## How B-trees work
[diagram: a root node branching down to leaves]
Most databases build these shortcuts using tree structures.
```

Or the reranker deciding for itself. It already looks at every candidate still,
so it also answers whether a camera can point at the idea at all - and that
verdict matters more than the rejection count, because candidates can all look
related to a bad query while none of them illustrate anything:

```
visual  scene   2  3 shots  3.5+3.4+3.8   complex branching tree diagram graphic
  rerank: no camera can point at this idea
  not filmable; drawing a tree diagram
```

The space a diagram may use is derived from the caption settings, not assumed:
`captions.caption_top()` computes where the caption box will reach from the same
numbers that build the ASS styles, and the layout stops above it. Raise
`captions.size` or `margin_v` and the diagram moves up; switch captions off and
it takes the whole frame.

On a multi-shot scene the diagram builds as it is explained - one element
revealed per shot, the rest ghosted in place so the layout never jumps. Diagrams
never get Ken Burns: a frame someone is reading must not drift under them.

```yaml
visuals:
  diagrams: true
  diagram_on_reject: 0.7    # rejected fraction that also triggers one
```

Which scenes get drawn is decided **once**, in `build/diagram_scenes.json`, and
every aspect obeys it. The model's filmability verdict is not stable between
runs - on the same script the landscape pass called three scenes unfilmable and
the portrait pass called none of them unfilmable - so asking again per aspect
gave a 16:9 cut and a Shorts cut that showed different things. The spec lives in
`build/diagrams.json` for the same reason: a diagram describes the idea, not the
frame, so the second cut costs no extra calls.

## Thumbnails

```powershell
cd ~/claude/vidsmith; .\vidsmith.cmd thumbs demo
```

Frames are sampled from the picture track, not the delivery file, so no
captions, watermark or progress bar end up in a thumbnail. Candidates are
ranked on edge detail and colour spread, penalised for crushed or blown
exposure, and spaced at least 2.5s apart so six candidates are not six frames of
one shot. The best frame is also composed into `titled.jpg` at 1280x720 with the
video title in the project's theme.

## The look

Every on-screen element reads from one `theme`, so a video looks designed rather
than assembled — the title card rule, the caption highlight, the kicker and the
progress bar are the same accent by construction.

```yaml
theme:
  preset: midnight      # midnight | ink | sunset | forest | paper | mono
  accent: ""            # "#RRGGBB" to override just the accent
  watermark: "@veer0608"
  title_card: true      # opening frame with the video title
  end_card: true        # closing frame with the last takeaway
  lower_thirds: false   # scene-heading chip, top-left
  progress_bar: true
  scrim: true           # bottom gradient so captions read over any footage
  scene_counter: true   # "02 / 04", bottom-left
```

What that buys you per frame:

- **Title and end cards** are real clips in the timeline, not overlays, so
  narration and captions stay in sync behind them.
- **Scene cards** are laid out editorially — accent rule, letterspaced kicker,
  two-line headline clamped so it can never reach into the caption zone.
- **Captions** fade in and out, scale up slightly on entry, and keep identical
  glyph widths as the highlight moves, so nothing reflows mid-line.
- **The scrim** is the reason captions stay legible once real footage replaces
  the cards — a bottom gradient burned under everything.

Override any of it per build:

```
--theme ink --accent "#FF7A59" --watermark "@yourhandle" --no-cards
```

## Visual providers

| provider | key needed | what you get |
| --- | --- | --- |
| `cards` (default) | none | generated gradient cards with Ken Burns motion |
| `pexels` | free `PEXELS_API_KEY` | real stock video, one clip per scene, no repeats |
| `pixabay` | free `PIXABAY_API_KEY` | same, different library |
| `local` | none | your own clips in `assets/clips`, matched on filename |

Without a stock key the build does not fail — it logs the fallback and renders
cards. With `GEMINI_API_KEY` set, the search query for each scene is written by
Gemini from the narration ("hands counting cash", not "personal finance");
without it, queries fall back to keyword extraction from the sentence.

**AI image generation is not wired in on purpose.** Gemini's image models are
listed on a free key but return `RESOURCE_EXHAUSTED` on the first call — the
free tier has no image quota at all. Adding billing to the Google key is the
only way to turn that on, so cards and stock footage are the honest options.

## Keys

Put them in `.env` next to this README:

```
PEXELS_API_KEY=...
GEMINI_API_KEY=...
```

`GEMINI_API_KEY` is also picked up from `~/claude/schemablind/.env` if it is not
set here. Everything key-dependent is optional; narration and captions need no
key at all.

## How a build is staged

```
parse   -> scenes.json          script split into scenes
queries -> scenes.json          Gemini writes a b-roll search per scene
voice   -> build/audio/*.mp3    edge-tts, plus word timings
visuals -> build/visuals/*.mp4  one normalised clip per scene
captions-> build/captions.ass   karaoke ASS + a plain .srt for YouTube
render  -> out/*.mp4            three ffmpeg passes: narration, picture, master
meta    -> out/youtube.txt      title, description, chapters, tags
```

Every stage skips work it already has on disk, so a failed encode never costs
you the narration again. `--force` names the stages to redo.

## Output

```
projects/demo/out/
  why-your-bank-statement-lies.mp4    delivery file, faststart, AAC 192k, -14 LUFS
  why-your-bank-statement-lies.jpg    thumbnail frame
  captions.srt                        upload as a caption track
  youtube.txt / youtube.json          title, description, chapters, tags
  credits.txt                         creator attribution when stock footage was used
```

## Tests

```powershell
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m pytest
```

53 fast tests run in under a second; 12 more marked `slow` encode real video with
ffmpeg. `-m "not slow"` skips those.

They exist because the same class of bug kept shipping: cache and timing
invariants that look fine until you watch the whole video. The suite pins the
ones that actually broke - a shot plan must sum to its narration slot, caption
lines must never overlap, an ASS Format row must match its Dialogue fields, a
cached clip is only reused when its real duration fits the slot, and every clip
used has to end up in the credits.

Writing them immediately found another: a sentence longer than `max_shot_seconds`
with no comma in the usable window fell through and held one 12-second shot -
exactly what the cutting is there to prevent. It now falls back to a word gap,
and to an arithmetic split if there is not even one of those.

## Running it as a web service

`web/` is a small FastAPI front: paste a script, watch the pipeline log stream,
download the mp4. Locally:

```powershell
cd ~/claude/vidsmith; .venv\Scripts\python.exe -m uvicorn web.app:app --port 8077
```

Renders happen on a worker thread, not in the request, because a video takes
minutes. The browser polls `/api/jobs/{id}` and the progress bar tracks real
pipeline stages rather than a timer. **The queue depth is one** - two concurrent
x264 encodes starve each other on a small box, so a second caller gets a 429.

| route | what it does |
| --- | --- |
| `POST /api/jobs` | start a render, returns a job id |
| `GET /api/jobs/{id}` | status, progress, log tail, output list |
| `GET /api/jobs/{id}/files/{name}` | download one output |
| `GET /healthz` | ffmpeg found, and whether a render is running |
| `GET /api/docs` | generated OpenAPI docs |

### Deploying free

Hugging Face Spaces is the one free tier that can actually encode video: 2 vCPU
and 16 GB on the free CPU plan, against 0.1 vCPU and 512 MB on a free Render
instance. `Dockerfile` targets it, and the Space builds the image itself - no
Docker on your machine. Steps and the caveats are in
[deploy/huggingface.md](deploy/huggingface.md).

**A public Space is a public renderer**: anyone with the URL spends your Pexels
and Gemini quota. Keep it private unless you put auth in front of it.

### Deploying on Render

`render.yaml` is a Render blueprint. It uses the native Python runtime, not a
container: `scripts/fetch-runtime-deps.sh` pulls a static ffmpeg into `bin/` and
the DejaVu fonts into `assets/fonts/` at build time, both of which the code
already looks in. Nothing needs Docker.

Two things that bite on a host:

- **ffmpeg is not there.** `FFMPEG_BINARY`/`FFPROBE_BINARY`, then `bin/`, then
  `PATH` - the fetch script covers the second.
- **Neither are the fonts.** The themes name Windows families, and libass will
  silently substitute something else, so `assets/fonts` is handed to the
  `subtitles` filter as `fontsdir`.

Set `PEXELS_API_KEY` and `GEMINI_API_KEY` in the dashboard, and keep
`VIDSMITH_MAX_MINUTES` honest for the instance size - encoding is CPU-bound and
a free instance is roughly ten times slower than a laptop.

## Known limits

- Edge voices are a free, undocumented Microsoft endpoint. Occasional connection
  failures are normal; each scene retries three times.
- Crossfades (`transition: fade`) re-encode the whole picture track and are much
  slower than the default hard cuts.
- `local` provider matching is filename keyword overlap, not content matching.
