# How this video timed its own captions

## The hook
[visual: studio microphone close up in a dark room]
Almost every tool that captions a video transcribes it first. It synthesises the narration, then hands that audio to a second model and asks it when each word was spoken.

## The thing already known
[visual: audio waveform moving on a screen]
That is a strange question to ask, because the engine that produced the speech already knew. It reports an event for every word it says, carrying the exact offset from the start of the audio.

## Why it cannot drift
[diagram: speech engine, word timings, captions and cuts]
Those timings come out of the same pass that made the sound. Nothing estimates them and nothing transcribes them back, so they cannot disagree with the voice you are hearing.

## What it buys
[visual: hands editing video on a laptop at night]
Captions land on the word being spoken. The picture cuts where the speaker actually lands a full stop, not at a guessed position halfway through a sentence.

## The point
[visual: city skyline at sunrise]
Every caption in this video was placed that way. If a word looks early or late to you, that is the engine, not an estimate.
