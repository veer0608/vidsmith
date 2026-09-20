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
