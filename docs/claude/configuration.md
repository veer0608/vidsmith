# Configuration

The dataclasses in `config.py` are the schema, and `projects/<name>/config.yaml`
overrides them. `vidsmith new` writes that file fully expanded, so it lists every
key rather than only the interesting ones. `_merge()` sets a key only when the
dataclass already has it, which means **a misspelled or invented key is silently
ignored**: no error, no warning, and the default quietly stands.

`render.aspect` picks from `ASPECTS` (`16:9`, `9:16`, `1:1`, `4:5`) and `cfg.size`
derives the pixel frame from it. Pixel values in the config are stated against a
1920-wide frame (`captions.size: 62`, `margin_v: 150`) and scaled by width at
render time, which is the same rule as the WIDTH bullet in [incidents.md](incidents.md).

**Footage comes from a provider, and a missing key is not an error.**
`visuals.provider` defaults to `pexels`; `pixabay` is the same shape against a
different library, `cards` needs no key at all, and `local` matches your own
clips in `assets/clips` on filename. When the key for a provider is missing the
lookup raises, `visuals` logs `falling back to a card`, and the scene gets a
generated card instead. Nothing fails, so nobody notices until the video is a
slideshow. That silence is why `/api/options` reports which providers this
instance can actually reach and the page disables the rest, and why the CLI
default was moved off `cards`: a deck of cards is not a video.

**`local` had both halves of that silence, and each shipped.** A relative
`visuals.local_dir` - `assets/clips`, which every `config.yaml` writes - was read
from the process's current directory, so running from anywhere but the project
found no files and every scene became a card. `pipeline` now hands the builder
`proj.root`, relative paths resolve against it, and an empty or missing folder
is logged with the absolute path it looked in (and a pointer to any
`assets/clips` left under the current directory). The other half is which file a
shot gets. A scene cut into three shots took three *different* files, so with
one image per pose "[visual: byte pointing]" showed pointing, then celebrating,
then thinking: those two score one below pointing by sharing "byte". Only files
**tied with the best filename score** are eligible now, for every shot; a lower
score never wins over reusing the best, because a near miss and a contradiction
score the same. Fewer tied files than shots means fewer shots, not repeats, and
the plan collapses. Variety comes from equally named variants
(`byte-pointing-2.png`), which tie and are spread across scenes by `self.used`.

**`visuals.genre` steers the searches, it does not filter the results.**
Pexels has no genre parameter, so `genres.py` hands a style direction to the
two prompts that write stock searches, `beat_queries` and `suggest_queries`,
with the passage's subject still ranked above the style. The reranker is told the genre too and
puts on-style clips first among the right subjects; its verdicts record the
genre and one judged in another style is not reused. The first version only
told the search writer to add a style word "where it helps" and moved a nature
build by one word; demanding it in every search moved it visibly, and also
started trading away a passage's setting ("the mug on your desk" became a mug
held up outdoors). It needs a Gemini key to do anything. The genre is part of
the beat cache key, except `any`, which adds nothing, so caches written before
genres existed are still found.

**A style word that names a thing becomes the thing that gets filmed.** Six PRs
(#122 to #132) went into learning that on live renders of one delivery script:
Technology's "screen" put phone maps under "the box lands on your doorstep",
City's "crowd" put a crowd at a crossing there, Documentary's "daylight" put
daytime trucks under "overnight". So every style's `words` are adjectives - one
word each, hyphenated if needed, no digits, because `beat_queries` keeps six
words and strips digits ("slow motion" reached Pexels as "slow") - and a place
or time the narration names outranks the style. Business is the one exception
and is excluded from the test, since its subject really is the office.
**Every rule here that held is enforced in code, because the prompt already
stated each one and the model broke each one anyway.** `genres.scrub()` removes
device words from a Technology search when the narration names no device;
`genres.ensure_style()` puts the style's `look` in front of a search that came
back with no style word; the reranker is told for Technology to reject a clip
whose subject is a screen the narration never named. `style_search()` runs both
and logs what it changed (`style: dropped 'gps' ...`, `style: ... carried no
technology style word, so it is ...`), because silent rewrites left no way to
tell from a build whether either had fired. What is left after all of it is
Pexels lacking a clip, or the reranker choosing differently between two runs
of identical code - two renders an hour apart put a ship and then a sack under
the same line. More rules will not fix those; compare frame sheets, never one run.

**The search shown beside a frame is the shot's own, not the scene's.**
Since beat searches a scene runs several, and `shot_times()` showed
`scene.query`, the `[visual:]` directive, for every shot. A real build read
"printer rolling out paper" under clips found by searching "broken text on
monitor", so the sheet blamed the wrong search for footage the reader was
judging. It reads the per-shot `query` out of `shots.json` now, falling back
to the directive for builds from before beat searches.

**`vidsmith retake --scene N` re-films one scene and nothing else.**
`visuals.forget_beats()` drops that scene's entries from `beats.json` - keyed
by heading and passage text, so an entry is this scene's when its text is part
of the scene's - and `invalidate(only={n})` drops its clips and verdicts. The
next build writes fresh searches for those beats alone: a retake of one scene
of machine-statements rebuilt in 103s against 499s for the whole video, and
spent one scene's vision calls rather than nine.

**A build says when a search has drifted off the page.**
`genres.drifting_devices()` reports the device words a beat search names
that the words spoken over it do not, and `prepare_beats` logs it as
`drift: scene 5 searches '...' but these words name no monitor`. It is
said and never fixed: `scrub()` rewrites, and only where a genre asks,
because a scene really about software is meant to show a screen and this
cannot tell the two apart. Measured on the 22 searches of that build
before shipping: it warned on the four bad scene-5 searches, once softly
on 'data entry on computer' where the narration says retype, and on
nothing else. A plain word-overlap rule was tried first and cried wolf on
'advertisement on bank statement' for a scene about a marketing banner.

**It cannot change the subject, only the roll.** The search is written from the
narration, so scene 5 of that build came back as screens and code twice, from
"broken text on monitor" and then "database error on screen", because the words
were about databases and matching. Rewriting the words to describe the printed
page is what moved the footage to receipts and bills. A scene that comes back
wrong twice needs `rewrite`, not another retake.

**A clip that keeps coming back goes in `visuals.exclude`.** A retake writes a
near-identical search, the results overlap, and the rerank can approve the same
clip every roll: howto's scene 1 came back twice with a Matrix-rain
"hacking code" clip in the same slot, and the second roll lost the good shots
around it. An entry is what the shot sheet prints beside a frame, the clip's
Pexels or Pixabay page, or its bare id; `clip_exclusion()` reads both, and
`load_config()` refuses an entry that names no clip, since it would exclude
nothing in silence. Excluded ids are dropped from the results before the rerank,
so they take no place among the stills judged. A scene already showing one is
re-filmed on the next plain `build`, every cut of it, because a built scene
reuses its clips and never searches again; filtering the results alone would
have left the clip exactly where it was.

**Read the footage off a shot sheet.** `vidsmith sheet <name>` writes
`build/sheet<tag>/sheet.html`: a frame per shot from the picture track (no
captions burned in), its timing, the words spoken over it, the search that
found it and its creator. Every footage fault above was found this way and
none from a log, and the sheet was rebuilt by hand about fifteen times before it
was a command. Two traps it names rather than hides: `scenes.json` holds the
shots of whichever cut was built last, so a build with no
`visuals{tag}/shots.json` warns that its times belong to another cut (it names
which, from the clip folder), and a published project with no `build/` falls
back to `captions<tag>.srt` in blocks of about six seconds, labelled as caption
blocks with no searches and no credits.

**Do not add an Animation style back.** It was built, rendered from both
providers and removed. Pexels has no animation filter and returned filmed
footage; Pixabay's `video_type=animation` filter works, but its animated library
has almost nothing about concrete subjects - a SUBSCRIBE title, a FREE advert, a
hot dog under a parcel line - and once the reranker rejected text and adverts,
40 of 48 candidates were rejected and most of the 8 kept were still unrelated.
`genres.py` records the same.

The loudness chain is deliberate: narration normalises to `-14` LUFS, the bed
sits `-18` dB under it at roughly `-32` LUFS, and `loudnorm` finishes the mix at
`-14`. Raising `music_gain_db` without re-checking the mix is how the bed starts
competing with the voice.
