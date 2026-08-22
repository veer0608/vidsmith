# vidsmith

Script in, narrated and captioned YouTube video out.

You write a markdown script. vidsmith speaks it in a neural voice, finds a shot
for every scene, burns word-timed captions, mixes music under the narration, and
encodes a delivery-ready mp4 — plus an `.srt` and a draft title/description/chapters.

```bash
python -m vidsmith build demo
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

```bash
cd ~/claude/vidsmith && python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
```

ffmpeg is the only non-Python dependency:

```bash
winget install Gyan.FFmpeg
```

Check everything at once:

```bash
cd ~/claude/vidsmith && .venv/Scripts/python.exe -m vidsmith doctor
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

```bash
cd ~/claude/vidsmith && .venv/Scripts/python.exe -m vidsmith new gil --topic "why Python's GIL still matters" --minutes 3
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

## Thumbnails

```bash
cd ~/claude/vidsmith && .venv/Scripts/python.exe -m vidsmith thumbs demo
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

## Known limits

- Edge voices are a free, undocumented Microsoft endpoint. Occasional connection
  failures are normal; each scene retries three times.
- Crossfades (`transition: fade`) re-encode the whole picture track and are much
  slower than the default hard cuts.
- `local` provider matching is filename keyword overlap, not content matching.
