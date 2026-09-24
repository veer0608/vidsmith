# Uploading

`upload.py` is the other half of `check`. `check` was written to notice the
faults that land on the upload form after the fact; this fills that form from
the files the build already wrote, so there is no pasting step left to get
wrong. No SDK, for the same reason `llm.py` has none: `requests` against
documented HTTP, so an auth failure reads as a status code.

**Three endpoints, and the third is the one that matters.** `videos.insert`
takes the mp4 with the title, description and tags, `thumbnails.set` takes the
jpg, and `captions.insert` takes the srt. Leave that last call out and YouTube
transcribes the audio itself, which is exactly the fault `published.py` found on
a live video: a caption track with `kind: "asr"` on a video whose exact word
timings were sitting in `out/`. `sync` is sent as `false` deliberately, because
`true` asks YouTube to re-time the text against the audio, which is the
transcription step this whole project exists to avoid.

**Every file is resolved by the same aspect tag.** The cut, its
`description<tag>.txt`, its `captions<tag>.srt` and its thumbnail. This is the
empty-tag family again, and the one that hurts here is the description:
publishing the widescreen one under a Shorts cut names photographers whose clips
are not in it, which is a licence problem rather than a cosmetic one.
`published.record()` now takes the tag too, because a receipt witnessing
`description.txt` after a 9:16 upload is a promise about a file nobody
published.

**A cut is published, not a project, so every artifact of publishing is per
cut too.** `published<tag>.json` witnesses one video: there used to be a single
`published.json`, and uploading a Short beside a widescreen video overwrote it,
so the project forgot the widescreen video had been verified and checked its
drift against the Short's files. `check.publish_drift()` reads every receipt it
finds. `check --published ID --aspect 9:16` and `publish --aspect 9:16` name the
cut, because the two cuts of a real build shared no footage at all: checking a
Short against `credits.txt` asks a vertical video to name 27 creators whose
clips are not in it.

**The cuts are checked against each other, not only against their own
files.** `check.cuts_agree()` reads the opposite direction from
`credits_published()`: a credit line in `description<tag>.txt` that is not in
that cut's ledger is a photographer whose clip is not in the video, which is
what pasting the widescreen description under a Short does and what shipped
twice. It also reads every chapter label into every cut's description, not just
`description.txt`, and compares the prose of each description with the
widescreen one, so a cut rebuilt without the other is visible. A Shorts-only
project has no reference and is not asked for one.

**`vidsmith published` reads the receipts back.** Test uploads accumulate
on a channel quietly - two private ones were there within a day of the upload
path working, and nothing in the tool could say so. `published.receipts()`
returns one row per `published<tag>.json` with the video, the cut and any file
that has moved since it was verified, offline. `--live` adds what YouTube says
each one is now through `privacy_of()`, which batches fifty ids per request
because videos.list costs a unit per call rather than per video, and reports a
video that does not come back as gone from the channel: a receipt outlives the
video it names.

**`--box` adds what the live instance published.** Its receipts live in
its own job directories, so a video it uploaded is invisible to a listing of
this checkout - the first one was. `deploy.remote_api()` reads `/api/jobs` from
the box over loopback, because every job route is behind the token and the
token lives there; a laptop never gets a copy of it to answer a question about
it. `jobs.renders()` carries each render's `youtube` record for the same
reason: a render that was uploaded and one that was not looked identical, on
the page as well.

**A name keeps its owner's spelling.** The howto title is "How To Use
vidsmith And How To Buy It"; the model drafting the upload title-cased the brand
and it went public as "How To Use Vidsmith", with "use Vidsmith to turn" in the
description, beside earlier uploads that said vidsmith. `write_metadata()`, the
one writer, now respells the title, description, tags and chapter labels with
`llm.spell_names()` before any file is written, so a youtube.json from before
the rule is corrected on its next pass as well. The names come from the
project's config, not from each caller: `llm.title_names()` takes the words the
title spells against its own case (a lower-case word in a title-cased title, or
a capital inside a word like iPhone), and top-level `names:` adds any the title
does not carry. A sentence-case title yields no lower-case names, since it
cannot say which of its lower-case words are brands.

**`check` runs first and refuses.** Everything it looks for is worse once
public, and taking a video down does not unpublish it. `--force` exists for the
operator who has read the problems and disagrees; it prints them either way.
Uploads are `private` by default, so the listing can be read before anyone else
sees it, and the command prints the `check --published` line to run once it is
public.

**`vidsmith publish` is the other half, and it changes one field.**
`set_privacy()` reads the video's `status` and sends it back with only
`privacyStatus` changed, because videos.update replaces the whole part: a
writable field left out, `selfDeclaredMadeForKids` among them, is reset without
a word. `publishAt` is deliberately not carried, since it schedules a private
video. The command runs the offline `check` first and leaves the video as it is
on a fault, then reads the visible copy back through `_check_live()`, the same
helper `check --published` uses. The first public video, `3NuA_RVbO10`, was
flipped by hand this way before the command existed.

**`publish` also manages a video that is already live.** Replacing howto
on 2026-09-24 needed three hand-written calls, and each is a flag now.
`--privacy private` takes a video down and checks nothing, because nobody can
see it afterwards and the video being retired is usually an old one whose files
are gone. `--meta` puts this cut's title, description and tags on the video it
was uploaded as, through `upload.set_metadata()`, which reads the snippet and
sends `categoryId` and the languages back as they were, since videos.update
replaces the snippet whole just as it does the status. It is refused unless the
receipt names that video and records a cut whose digest still matches the one in
out/: a new description on a rebuilt cut credits footage the live video does not
contain. The stale-description drift for that receipt is the one check finding
it answers; any other still stops it without `--force`.

**A receipt remembers the video it replaced.** The new howto upload wrote over
`published.json`, and the old video, still on the channel with 37 views,
dropped out of `vidsmith published`. `record()` now moves a different video id
it is about to overwrite into a `replaced` list, and `published` lists each one
as "replaced by <id>", with `--live` adding whether it is still up.

**A clean `check --published` answers the drift it resolved.** The run that
confirmed a re-pasted description rewrote the receipt and still failed on "the
description published there is stale", because the offline half had judged
the old receipt first; a second run passed. The offline check is run again
after a clean live comparison now.

**The OAuth flow is the standard loopback one and its only security decision is
the `state`.** The redirect port is open to anything else on this machine, so
`redirect_result()` refuses a code that does not carry the state we generated;
it is a module-level function rather than a branch inside the handler precisely
so it can be tested without a socket. `access_type=offline` with
`prompt=consent` is what makes Google return a refresh token, and the save path
never copies a refresh response wholesale: Google omits the refresh token from
every response after the first, so that would erase it and ask for consent
again on the next run. The token lands in `.youtube-token.json`, gitignored.

**Quota is the limit nobody meets until they do.** `videos.insert` costs about
1600 units against a default 10,000 a day, so it is roughly six uploads and then
a wait until Pacific midnight. Same shape as the Gemini ceiling: it is not in a
header, and the failure arrives after the render is already paid for.

**The page uploads too, and consent is the only part that differs.** A render
made on the live instance was downloaded and its description typed into YouTube
Studio by hand, which is the pasting step this module exists to remove, because
the CLI's consent needs a browser on the machine holding the login. `web/youtube.py`
sends Google's redirect to `/api/youtube/callback` on the app instead, so the
server's `YOUTUBE_CLIENT_ID` must be a **Web application** OAuth client listing
that exact URL (`GET /api/youtube` reports it); a Desktop client's loopback
redirect cannot reach a server. Three rules hold it together. The callback is
outside the token gate, since it is Google's redirect arriving, so a **single-use
state issued by the gated `/connect` route, valid fifteen minutes**, is what
authorises it. `access_token(interactive=False)` raises `NotConnected` instead of
waiting on a consent screen nobody can see. And the video id is captured from the
log the moment `publish()` places the video, so a thumbnail or caption failure
after it records the render as uploaded with a warning rather than failed: a
retry would have made a second video. The upload status lives on the job and in
its `job.json`, so a restart mid-upload reports it as failed rather than stuck.
