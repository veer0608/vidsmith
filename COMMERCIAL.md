# Commercial use

`LICENSE.md` (PolyForm Noncommercial 1.0.0) covers personal use, study, hobby
projects and non-profits. It does not cover using vidsmith to make money:
channels that carry ads or sponsorship, client work, or anything you resell or
run as a service.

For that, a commercial licence is available.

## Price

| | | | |
| --- | --- | --- | --- |
| **Solo** | **$49** once | One named person, making videos for themselves or their own channel. | [Buy](PASTE_GUMROAD_SOLO_LINK) |
| **Company** | **$299** once | One company, any number of people in it, for that company's own videos. | [Buy](PASTE_GUMROAD_COMPANY_LINK) |
| **Agency or client work** | quote | Making videos *for other people* under contract, or reselling the output as a service. Say roughly what you do and you will get a number. | by email |

All three are perpetual. You pay once, keep the source, and keep every video you
make with it, including after the licence is bought. There is no per-video fee,
no seat count to maintain and no renewal, and nothing phones home because there
is nowhere for it to phone: the render happens entirely on your hardware.

For scale, the hosted tools that do this job run about $19 to $48 a month
depending on the plan and whether you pay yearly, and they bill for as long as
you use them. Two months of one costs more than the solo licence here costs
once, and it keeps costing.

Being straight about the other end of the market: there are permissively
licensed projects that do a similar job for nothing, MoneyPrinterTurbo among
them. If a free MIT licence is what you need, take one of those. What is sold
here is a different thing: timings taken from the speech engine rather than
transcribed back, a delivery checker that reads the output files against each
other before you publish, attribution handled as the licence condition it is,
and a documented Amazon Polly path so the narration itself is licensed.

Both fixed tiers check out on Gumroad and the licence arrives by email, naming
whoever you give at checkout: [Solo, $49](PASTE_GUMROAD_SOLO_LINK) and
[Company, $299](PASTE_GUMROAD_COMPANY_LINK). Nothing here is a subscription, so
there is no account to keep and nothing to cancel later.

An agency case is quoted rather than fixed, because the range is wide. Say
roughly what you do, by email to the address on
<https://github.com/veer0608>, and you will get a number. Email rather than an
issue: what you are describing is your business, and an issue is public.

## First thing to do after buying: switch the voice to Polly

The default narration path is not cleared for commercial use, so this is the
first change a paying user makes rather than something to read about further
down. The reasoning is under Narration below. The steps are here because a
buyer needs them on the way in, not on the way through.

```
pip install -r requirements-polly.txt
```

Set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_REGION` in the
environment or in any `.env` the build reads, then in `projects/<name>/config.yaml`:

```yaml
voice:
  provider: polly
  name: Matthew        # a Polly VoiceId, not an edge-tts name
  engine: neural
  pitch: +0Hz
```

That exact combination has been run against the real service and works. Four
things bite, and all four are refusals rather than bad output:

- **`name` is a Polly VoiceId.** Leaving the edge-tts default in place is the
  most likely mistake, because `provider` is the only key that obviously needs
  changing and the file already has a name in it.
- **`engine: generative` is refused on purpose.** It is the one engine that
  returns no speech marks, so it cannot time captions or the cut, which is the
  whole design.
- **`pitch` must be `+0Hz` on `neural` and `long-form`.** Those engines do not
  support prosody pitch, so a leftover value is refused rather than ignored.
  `standard` honours it.
- **A video costs its script length twice.** Polly bills the audio and the
  speech marks as separate requests. That is Amazon's charge, not a fee here.

Nothing about the edit changes. Polly reports word timings too, so the captions,
the shot plan and the mix come out identical. You are changing who licenses you,
not how the video is cut.

## What you are buying

A perpetual licence to run vidsmith commercially on your own machines, at the
tier you bought. You keep the source and every video you make with it.

## Third-party terms you are still responsible for

A licence to this code is not a licence to the services it calls. Whoever runs
vidsmith commercially has to satisfy these themselves, and two of them matter
more than they look.

**Narration, and read this before selling anything made with it.** `voice.py`
speaks through `edge-tts`, an unofficial client for the text-to-speech service
behind Microsoft Edge's Read Aloud. Microsoft publishes no terms granting
commercial use of that endpoint, and their own support answers point commercial
users at Azure Speech instead. Personal use is uncontroversial; commercial use
is not settled, and the risk is yours. See
[Microsoft's answer on the question](https://learn.microsoft.com/en-us/answers/questions/2088770/are-opensource-edge-tts-free-for-commercial-use).

Amazon Polly is the supported path, and vidsmith speaks it. The setup is at the
top of this file, under "First thing to do after buying", rather than repeated
here: a buyer needs it before they start, and a second copy of it is how the
two drift apart.

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
