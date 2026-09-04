# r/selfhosted draft

**Flair:** Release

**Title**

```
Vidsmith: self hosted markdown to narrated captioned video. No per video fee, no subscription, runs on your hardware.
```

---

**Body**

I got tired of video tools that charge per render and keep charging after you
stop using them, so I wrote one that runs on my own box.

You write a markdown file. It speaks it in a neural voice, finds a stock clip for
every scene, burns captions, mixes a music bed under the narration, and encodes a
finished mp4 with an SRT and a WebVTT beside it. The render happens locally.
Nothing is metered.

**What it needs**

- Python 3.11 and ffmpeg. ffmpeg is the only non Python dependency.
- No account and no API key for a basic render. The narration voice is a free
  Microsoft endpoint, and with no keys at all it still produces narrated,
  captioned video using generated cards instead of stock footage.
- A free Pexels or Pixabay key if you want real footage.
- Optional Gemini key for b roll search terms, diagrams and a draft YouTube
  description. Every one of those degrades to something rather than failing.

**Why the captions are the point**

Most tools synthesise speech and then transcribe that speech to find out where
the words landed. Edge's TTS already reports a boundary event for every word it
speaks, so vidsmith keeps those numbers and uses them directly. Nothing is
transcribed, so there is nothing to drift. The same timings decide where the
picture cuts, so a shot changes where the speaker lands a full stop.

**It checks its own output**

`vidsmith check` reads the delivered files against each other before you publish:
thumbnail orientation against the cut it names, captions against the runtime, the
chapter list against what YouTube will actually accept, and every stock
contributor in the credits ledger against the description that gets pasted. It
calls no model and no network, so it costs nothing.

There is also a mode that reads a published YouTube video back and compares it to
what was built. It found 24 uncredited photographers across two of my own videos.
Attribution is a licence condition for stock APIs, so that is not a cosmetic
finding.

**Web UI**

There is a small FastAPI service if you want to run it as a box on your network:
one render at a time with a bounded queue, cancellable jobs, and a health
endpoint. It is what runs at the demo link below on a 2 vCPU instance.

**Hardware note**

About 78% of a build is ffmpeg, so this is CPU bound. On 2 vCPU a 90 second video
takes several minutes. On a normal desktop it is fast. More cores or fewer pixels
is the only lever, and I measured that before saying it.

**Licence**

Free for personal, study and non profit use. Commercial use is a one off licence
rather than a subscription, and the terms are in the repo. Being upfront since
this sub rightly asks: the code is public and readable, and the paid part only
applies if you are making money with it.

One caveat worth knowing: the default voice uses an unofficial client for a
Microsoft endpoint, which is fine for personal use but is not licensed for
commercial work. Amazon Polly is supported for that, and it also reports word
timings, so nothing about the design changes.

Source: https://github.com/veer0608/vidsmith
Try it without installing: https://vidsmith.duckdns.org

Happy to answer anything about the setup.
