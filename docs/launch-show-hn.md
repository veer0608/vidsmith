# Show HN draft

**Title** (73 chars, under HN's 80 limit)

```
Show HN: Vidsmith, markdown in, narrated captioned video out, runs locally
```

**URL:** https://github.com/veer0608/vidsmith

---

**First comment** (post this yourself immediately after submitting)

I write a markdown file, and vidsmith speaks it, finds a stock clip for every
scene, burns captions that land on the word being said, and encodes a finished
mp4. It runs on my own machine, so there is no per video charge and nothing to
subscribe to.

The part I actually care about is the captions.

Most tools generate speech and then run Whisper over that speech to work out
where the words landed. That is a transcription of audio you just synthesised,
and it is approximately right. Edge's TTS already returns a WordBoundary event
for every word it speaks, so the timings are available for free and exact.
vidsmith keeps them. Nothing transcribes anything, so there is no guess to be
slightly wrong.

Those same timings decide the edit. Each scene is cut into shots at the sentence
boundaries the speaker actually lands, rather than at a round number, so the
picture changes where a full stop happens.

The other half is a checker, and it has been the more interesting one to build.
`vidsmith check` reads the delivered files against each other rather than against
the code that wrote them: a thumbnail whose orientation does not match the cut it
names, captions running past the end, a chapter list YouTube will silently drop,
a photographer credited in the ledger but missing from the description that
actually gets pasted. Every rule in it is a fault that shipped once.

Yesterday I added `vidsmith check --published <video-id>`, which reads the public
watch page and compares it to what was built. It found 24 uncredited
photographers across two of my own published videos, and one video whose only
caption track was YouTube's transcription rather than the timings the whole
pipeline exists to produce. It needs no API key: everything comes out of
ytInitialPlayerResponse in one unauthenticated GET.

Honest limitations:

- edge-tts is an unofficial client for the endpoint behind Edge's Read Aloud,
  and Microsoft grants no commercial use of it. Amazon Polly is supported as the
  licensed path, and it is one of the very few services that reports word
  timings at all, so the design survives the swap. Polly bills audio and speech
  marks separately, so a video costs its script length twice.
- Stock footage needs a free Pexels or Pixabay key. Without one it still renders,
  using generated cards, which looks like a slideshow. That fallback is silent
  and I have made the delivery checker report it.
- Some scenes are unfilmable. "Branching tree diagram" returns photographs of
  trees. Those get drawn instead, from a spec the model writes.
- ffmpeg is the only non Python dependency. About 78% of a build is ffmpeg, so it
  is CPU bound and a small cloud instance is slow.

Free for personal, study and non profit use. Commercial use is a one off licence
rather than a subscription.

Source: https://github.com/veer0608/vidsmith
Run one in the browser without installing anything: https://vidsmith.duckdns.org

Happy to go into the caption timing or the checker in more detail.
