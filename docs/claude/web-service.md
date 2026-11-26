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

## Your own photos and videos

The page's "Your Photos and Videos" card sends each file to `POST /api/uploads?name=...`
as the raw request body (no multipart dependency). `web/uploads.py` refuses at the
door what the encode would choke on minutes later: the extension must be one of
`VIDEO_EXT | IMAGE_EXT`, an image must open in Pillow, a video must have a duration
ffmpeg can read, and the size is counted as it streams (15 MB an image, 150 MB a
video, 20 files and 400 MB a render). Files wait in `<jobs>/_uploads/<id>/`; the
leading underscore is what keeps `sweep_orphans` from treating it as a dead render.

`POST /api/jobs` takes `media: [ids]`. `Jobs.submit` copies them into the job's
`assets/mine` (a repeated phone name like `IMG_0001.jpg` is numbered, not
overwritten), so a render never depends on an upload a later sweep could remove.

The script reaches them through `[media: a.jpg, b.mp4]` above a paragraph
(`Scene.media`, part of `picture_key`, so editing it rebuilds that shot). With
no `[media:]` line anywhere, `uploads.spread` writes them in for you: scene *i*
gets files `i*n//T .. (i+1)*n//T`, or the one it lands on when there are fewer
files than scenes. A script that names any file is left alone.

`VisualBuilder._mine` resolves names by file name only, case-insensitively, inside
`assets/mine`, so `../` cannot leave it. It runs before the provider branch, so it
works under every provider and beats a model-decided diagram; a missing name is
logged and the scene falls back to the provider. A user's own file has no author, so
nothing reaches `credits.txt`.

**The thumbnail takes their photo first.** `thumbs.from_mine` ranks the images a
`[media:]` line names (at least 640 wide, never a video) with the same sharpness-and-colour
score a lifted frame gets, and a build or `thumbs --refresh` uses the winner before any
stock search, so no search or model call is spent and no credit is owed. A refresh that
swaps a stock photo for theirs must drop the stock credit line: `set_thumbnail_credit`
used to leave the file alone when that was the only line, so it kept naming a
photographer whose photo was gone. It now deletes the file when nothing is owed.

## Registering for access

`/register` is open to anyone and spends nothing: it writes one row to
`<jobs>/_accounts.json` (`web/accounts.py`). The owner approves at `/admin`, which is
only a shell; every call it makes needs the owner token the page already holds. Approval
mints a member token, and the person collects it once from their status link
(`GET /api/register/{secret}`), after which only its sha256 is kept. There is no email:
the page polls, and the secret lives in that browser's storage.

Three rules hold it together:

- **`guard` stays the owner's alone.** A member uses `identity` and `own_job`, on
  exactly these routes: create, uploads, draft, whoami, and a job's status, files,
  archive, description and cancel. YouTube login, usage, retakes, rewrites, covers, cuts
  and the admin list stay on `guard`, because a member who could reach the YouTube login
  could publish to the owner's channel. A new route is the owner's unless it is added to
  that list. **`guard` refuses a member with 403, never 401**: the page answers a 401 by
  forgetting the token, and a member was handed theirs once, so a 401 on one stray click
  locked them out for good. The page also asks `/api/whoami` and hides every
  `owner-only` card for a member; a new owner-only card needs that class.
- **A member sees only their own renders**: `owners` maps job id to member, and another
  member's job is a 404, not a 403, so ids cannot be probed.
- **The cap is per member per UTC day** (`VIDSMITH_DAILY_RENDERS`, default 3), spent
  before the submit and given back if the submit is refused. The owner is not counted.
  Topic drafts have their own cap (`VIDSMITH_DAILY_DRAFTS`, default 5), because each is
  a Gemini call and asking again is free; a draft the model fails is given back.
  Revoking drops the hash, so the token stops working at once. **New Login Link** in
  `/admin` mints a replacement for a member who lost theirs, shows it to the owner once
  as a `?t=` link, and leaves it on their status link too; the old token stops.

**Remove** deletes a request from `/admin`, and only a denied one: an approved member
holds a working token and a pending one is a person waiting, so either is denied first
and nobody loses access without the owner seeing it happen. Removing also drops the
record of which renders were theirs, and the same address can ask again.

The owner's page shows "N waiting for access", linked to `/admin`, when requests are
pending, and `web/alerts.py` emails the owner on each new request once mail is set
up: `VIDSMITH_ALERT_TO`, `VIDSMITH_SMTP_HOST`, `VIDSMITH_SMTP_USER` and
`VIDSMITH_SMTP_PASSWORD` in the instance's `.env` (port 587 STARTTLS unless
`VIDSMITH_SMTP_PORT` says 465; for Gmail the password is an app password, never
the account's). Missing any of them and alerts are simply off; `/admin` says which.
Three rules hold it: the mail goes on a thread after the row is written, and a
mail server that refuses is logged, never in the person's way; the requester's
name is flattened to one line before it reaches the Subject, because a newline
there is a header injection; and the settings are read per request through
`_alert_settings()`, the one place tests stub, while `conftest` clears them so no
test can email whoever the machine alerts. A default argument cannot stand in for
that stub: `notify(sender=send)` bound the real `send` when the module loaded, and
the first test of the stubbed path made a real DNS lookup.

**A push to the owner's phone is the alert that needs no password.** The email
setup took four rounds on 2026-11-26 and never reached the box: an SSH line that
never connected, a terminal tab nobody typed into, a "Saved" from a window that
was not this box. `VIDSMITH_NTFY_TOPIC` is the whole setup for the other channel:
`alerts.notify_phone()` POSTs to `https://ntfy.sh/<topic>` and the ntfy app on a
subscribed phone shows it, tap through to `/admin`. A topic is readable by anyone
who knows its name, so the name is long and random and the push says only how
many requests wait, never who asked: the name, email and note stay on the box.
Both channels run side by side; `/admin` says which are on.
