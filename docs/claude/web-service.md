# Web service

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
Every build now does that itself: `build/manifest{tag}.json` carries seconds per
stage, ffmpeg time within each, and a `share` line. The first real one, a
20-second local-footage build, put ffmpeg at 75%, which agrees with the table.

Jobs live in memory under `jobs/<id>/`. A finished render is kept for
`VIDSMITH_KEEP_DAYS` within `VIDSMITH_KEEP_GB`, survives a restart through its
`job.json`, and is listed by `GET /api/jobs` for the page's Past Renders card; a
failed one is swept after an hour. `VIDSMITH_TOKEN` gates the
API when set; `/healthz` stays open and reports ffmpeg and bundled fonts.
**`keys` is behind the token**, deliberately: it inventories which credentials
the box holds, and a stranger who found the URL has no business reading it.

**One shot of a finished render can be changed without building it again.**
The footage is the part most often wrong, and the only fix was a whole new
build in which every other shot was free to change too. `vidsmith/retake.py`
offers the shot's own search results (or a typed search), the reranker's
verdicts shown beside them rather than obeyed, encodes the clip picked to
exactly the slot the old one filled, and runs `pipeline.build(retake=True)`.
Four rules hold it together:

- **A retake is the same build, never a copy of the render stage**, because a
  second writer is how credits go missing here. `retake=True` blanks the model
  key so nothing optional is spent, keeps the thumbnail and its credit line,
  and rewrites `description.txt` from `youtube.json` on disk so it names the new
  creator.
- **A clip comes only from a search result, never a URL the caller sends**,
  because the server downloads it. A clip already in the video is refused, and
  the replacement is encoded as `retake_*.mp4`, never `scene_*`, because the
  reuse path cuts in every file matching that name.
- **A failed or stopped retake hands back the video it started from, still
  `done`.** Marking it failed would give a finished video to the one-hour sweep.
  `out/` and the touched files are copied to the job's `.backup/` first, and a
  copy still there at startup means the process died mid-master:
  `_adopt` calls `snapshot.restore()` before it reads the record.
- **It takes the render slot and waits in the line**, because the master pass is
  most of an encode. The job keeps its id, and the page follows it like the first
  build. A render already on YouTube is refused: a changed video would be a second
  upload.

**One scene's words can be rewritten too.** `vidsmith/rewrite.py` replaces that
scene's narration lines in `script.md` in place, keeping notes and every other
line, and refuses words that would change anything else once parsed (a
heading, a directive, a split scene). Then `pipeline.build(edit=True)` rebuilds
only that scene, per the scoped invalidation in [incidents.md](incidents.md) (stale caches), keeps the thumbnail, and
writes the description again because its chapter times moved. When the model
cannot write it, the old description is kept **without** its chapters rather
than publishing times that now point at the wrong moment. Both kinds of change
back up through `vidsmith/snapshot.py` into the job's `.backup/`, restored on
failure and at startup. The page's inline script is parsed by `node --check` in
`test_page_script.py`, because a duplicate `let` once took the whole page down
while every text-reading page test passed.

**A finished render can gain a Shorts version.** `vidsmith/cuts.py` runs
`pipeline.build(overrides={"aspect": "9:16"}, cut=True)`: narration, timings and
beat searches are shared, so it costs vertical footage and an encode. `cut`
keeps `youtube.json` and writes `description-9x16.txt` from it with the Short's
own credits, and picks a thumbnail for the new shape only when it has none.
Two sharing traps came with it. `scenes.json` holds the shots of whichever cut
was built last, so each cut now records its own in `visuals{tag}/shots.json`
and `retake.Build` reads that. And the page used to play the first `mp4` in the
file list, which is the Short, because `a-9x16.mp4` sorts before `a.mp4`, so
`Job.public()` serves `cuts` main first and the page names the cut it plays. A
scene edit rebuilds every other cut too, or the Short would keep the old words.
`/api/jobs/{id}/cuts` refuses a 9:16 cut past `SHORTS_SECONDS` (180).

**The thumbnail can be chosen by hand too, and it is not a render.**
`vidsmith/cover.py` offers the photographs from the search the build used
(recorded in `build/thumbnail{tag}.json`) or a typed one, and the middle frame of
any shot. It titles the pick with `thumbs.titled` exactly as the build does,
then corrects the thumbnail credit line - a photograph owes its photographer, a
frame of the footage owes nothing extra - and rewrites the description from
`youtube.json` through `write_metadata`. It runs in the request, because there is
no encode, but `Job.retitling` keeps it and a shot change from overlapping: a
failed change restores its copy of `out/`, which would put the old thumbnail back.

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
