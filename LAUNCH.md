# Launch drafts

Two venues, same claim. Both lead with the one thing that is technically
unusual rather than with what the tool does, because "script to video" is a
crowded sentence and "the timings are not estimated" is not.

Not committed as project documentation - this is a working file. Delete it once
the posts are up.

---

## Show HN

**Title** (80 char limit, this is 76):

```
Show HN: Vidsmith – script to narrated video, timed by the TTS engine itself
```

**First comment**, posted immediately after submitting:

```
I kept hitting the same wall building video tooling: you synthesize narration,
then you transcribe it back with Whisper to find out when each word was spoken,
so you can time captions. You are asking a second model to guess something the
first model already knew exactly.

edge-tts emits a WordBoundary event per spoken word, but only if you pass
boundary="WordBoundary" - the default is one event per sentence, which is why
most wrappers never see them. Those events carry an offset in 100-nanosecond
ticks straight from the engine that produced the audio. Nothing is transcribed,
and the timings cannot drift from the speech because they came out of it.

Once you have per-word timings for free, they turn out to drive more than
captions. Vidsmith also cuts the picture on them: each scene is split into shots
at the sentence boundaries the speaker actually landed on, not at estimated
positions. Every scene's clips must sum to exactly its narration slot, or the
picture drifts against the voice for the rest of the video.

Other bits that were more interesting to build than expected:

- Some scenes are unfilmable. Search any stock library for "branching tree
  diagram" and you get photographs of trees. When vision reranking rejects
  nearly every candidate as the wrong subject, the scene gets a drawn diagram
  instead, rendered from a JSON spec in the project's theme.
- The music bed is synthesised in ffmpeg rather than sourced - detuned sine
  triads over a four-chord progression, low-passed and echo-smeared until it
  reads as atmosphere. There is no free API for licensed music and an
  unlicensed track is a copyright strike.
- Stock attribution is a licence condition, not a courtesy, so the credit block
  is built from what the search actually returned and folded into the YouTube
  description automatically. Getting this wrong silently is the single most
  recurrent bug in the project's history.

Honest limitations, because they matter more than the feature list:

- The default voice path uses edge-tts, an unofficial client for the endpoint
  behind Edge's Read Aloud. Microsoft publishes no terms permitting commercial
  use of it, so there is an Amazon Polly provider for anyone who needs to be
  licensed. Polly reports word timings too, which almost nothing else does, but
  not in the same shape: they arrive as speech marks in milliseconds carrying
  starts and no durations, so the ends are reconstructed, and the audio and the
  marks are separate billed requests, so a video costs its script length twice.
  Nothing about the cut changes either way.
- It is source-available under PolyForm Noncommercial, not open source. Free
  for personal use, study and non-profits; commercial use needs a licence.
- Output quality is bounded by free tiers. It will not beat a funded tool using
  ElevenLabs and Veo. It costs nothing per video and runs entirely on your
  machine, which is a different trade rather than a better one.

Python, ffmpeg, no GPU. 336 tests, CI on Linux and Windows - Windows because
three of the last sixteen bugs were Windows-shaped and ubuntu could not see any
of them.

https://github.com/veer0608/vidsmith
```

---

## r/Python

**Title:**

```
I built a script-to-video tool that never transcribes anything - the caption
timings come out of the TTS engine
```

**Body:**

```
Most script-to-video pipelines synthesize narration and then run Whisper over
the result to find out when each word was spoken. That always struck me as
backwards: the TTS engine already knew.

edge-tts emits a WordBoundary event per word if you pass
boundary="WordBoundary" (the default is one per sentence, which is why it is
easy to miss). Each event carries an offset in 100-nanosecond ticks. So you get
exact per-word timings for free, from the same engine that produced the audio.

Vidsmith uses them for captions and for the edit - scenes are cut into shots at
the sentence boundaries the speaker actually landed on. Input is a markdown
file; output is an mp4 with karaoke captions, stock b-roll, a synthesised music
bed and a YouTube description with the stock attribution already folded in.

Stack: Python, ffmpeg, Pillow, FastAPI for the web front. edge-tts for voice by
default, with Amazon Polly as a licensed alternative for anyone with revenue
attached, since edge-tts is an unofficial client Microsoft grants no commercial
use of. Pexels/Pixabay for footage, Gemini for the b-roll searches and metadata.
No GPU and no paid API needed to run it.

Source-available under PolyForm Noncommercial - free for personal and
non-profit use, commercial needs a licence.

https://github.com/veer0608/vidsmith

Happy to go into the parts that were harder than expected: keeping clip
durations summing exactly to the narration slot, deciding when a scene is
unfilmable and should be drawn as a diagram instead, and why the karaoke
captions re-emit the whole line once per word instead of using \k tags.
```

---

## Notes on posting

**Have a video ready to link.** The first question either audience asks is
"show me the output", and a claim about timing accuracy is answered by watching
captions land on words. Upload one before posting, not after.

**Post the Show HN comment immediately**, in the same minute as the submission.
HN convention is that context comes from the author in the first comment, and a
Show HN with no author comment reads as a drive-by.

**Expect the licence to come up on HN.** PolyForm Noncommercial is not open
source and someone will say so. The answer is that this is deliberate and
stated up front, which is why it is in the post rather than only in the repo.

**Do not post both on the same day.** If HN goes badly, r/Python is a second
attempt with a different framing rather than an echo.
