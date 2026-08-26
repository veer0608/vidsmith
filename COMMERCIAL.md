# Commercial use

`LICENSE.md` (PolyForm Noncommercial 1.0.0) covers personal use, study, hobby
projects and non-profits. It does not cover using vidsmith to make money —
channels that carry ads or sponsorship, client work, or anything you resell or
run as a service.

For that, a commercial licence is available. Open an issue or email the address
on <https://github.com/veer0608>.

## What you are buying

A perpetual licence to run vidsmith commercially on your own machines, for one
person or one company. You keep the source and every video you make with it.
There is no per-video fee and nothing phones home, because there is nowhere for
it to phone: the render happens entirely on your hardware.

## Third-party terms you are still responsible for

A licence to this code is not a licence to the services it calls. Whoever runs
vidsmith commercially has to satisfy these themselves, and two of them matter
more than they look.

**Narration — read this before selling anything made with it.** `voice.py`
speaks through `edge-tts`, an unofficial client for the text-to-speech service
behind Microsoft Edge's Read Aloud. Microsoft publishes no terms granting
commercial use of that endpoint, and their own support answers point commercial
users at Azure Speech instead. Personal use is uncontroversial; commercial use
is not settled, and the risk is yours. Azure Speech is the supported path and
the intended home for a `voice.py` provider that does not exist yet — see
[Microsoft's answer on the question](https://learn.microsoft.com/en-us/answers/questions/2088770/are-opensource-edge-tts-free-for-commercial-use).

**Footage.** Pexels and Pixabay both permit commercial use of API results and
both require you to credit the creator and link back. vidsmith already builds
that block for you, in `credits.txt` and the YouTube description, and it is a
licence condition rather than a courtesy. Pixabay additionally requires search
results to be cached for 24 hours rather than re-requested, which
`visuals._cached_search()` does. Do not remove either.

**Model calls.** Gemini writes the b-roll queries, diagram specs, metadata and
drafted scripts. Check the current Google AI terms for the tier your key is on;
free tiers and paid tiers differ on what may be done with the output.

**Music.** The bed is synthesised by `music.py` in ffmpeg, so there is no
third-party rights holder and nothing to clear. That was the point of building
it rather than sourcing it.

**Fonts.** The themes name Windows font families. Shipping or embedding a font
is a separate licence from using one that is already installed on the machine
doing the render.

## Not legal advice

This file describes the intent of the licence and flags the third-party terms
that apply. It is not legal advice, and none of the people who wrote it are
lawyers. If real money depends on the edge-tts question in particular, get an
opinion from someone qualified before you rely on it.
