# Things that have actually broken here

- **Scene-indexed caches go stale on a redraft.** `diagram_scenes.json`,
  `rerank.json`, `credits.json` and `narration.wav` are keyed by position with
  nothing tying them to the words. `pipeline.invalidate()` drops them when the
  script changes; `build/visuals*/cache/` survives, being keyed by provider id.
  A missed entry here put the previous script's voice under a new picture.
  **Then dropping all of it on every edit became its own cost.** Rewording one
  `[visual: ...]` line re-voiced the script and re-ranked all seven scenes of a
  build, twice, once per aspect: a vision call each, against a Gemini budget
  that is reported in no header. A scene is now compared as two halves.
  `narration_key()` is the heading, the text and the `hold` - anything that
  moves the timings, and a change there still drops everything. `picture_key()`
  is the `[visual: ...]` and `[diagram: ...]` directives, and a change there
  calls `invalidate(only={indices})`, which prunes those scenes' entries out of
  the index-keyed caches, deletes their clips and the cut, and leaves the
  narration, the untouched scenes' footage and the cards alone. Two rules hold
  it together: `source_key()` is built from the two halves rather than listing
  the fields again, and `carry_timings()` copies the cached timings onto the
  *fresh* scenes, so the new directive is always the one that survives. An
  unedited scene keeps its cached `query` there too, because a re-parse loses a
  model-written search back to the heading fallback and asks for it again.
  **A changed `narration_key()` no longer drops everything either, unless a
  scene was added or removed.** The old reasoning was that an edited line moves
  every later scene's start, which is true and does not matter: a clip is cut
  to its own scene's slot, and starts are read fresh by the captions, the mix
  and the cut on every build. `invalidate(only=..., respoken=...)` drops the
  edited scenes' clips and entries plus `narration.wav`, `carry_timings()` copies
  nothing onto a respoken scene so it is voiced again, and `build_all` marks
  the ledger's clips as used so the re-filmed scene cannot pick footage another
  scene already shows. Only a change in scene count still drops everything,
  because that moves every index-keyed cache onto the wrong scene.
- **Sizes must key off frame WIDTH, never height.** 15% of 1920 is not the same
  kind of quantity as 15% of 1080; keying box heights to height made portrait
  diagrams nearly square. Portrait then gets larger type deliberately.
- **Layers must not collide.** `captions.caption_top()` computes where the
  caption box reaches from the same numbers that build the ASS styles, and
  diagrams stop above it. Do not hardcode a fraction here.
- **ASS is positional.** The `Format:` line must list all ten Dialogue fields:
  a missing `MarginV` shifts every field and prepends a stray comma to the text.
- **Thumbnails come from the Pexels *photo* API, not video frames.** A frame is
  graded to sit behind captions; a diagram frame is the clearest frame in the
  video and the worst thumbnail. The search is written from the scenes' visual
  directives, not the hook. Every explainer hook is a frustration, so a hook-fed
  query returns a stressed person every time. When `vidsmith thumbs` does sample
  frames, it takes them from the picture track rather than the delivery file,
  which already has captions, watermark and progress bar burned in.
- **A broad `except` around an optional feature hides typos, not just outages.**
  `thumbs.from_stock()` ranked its stock photos through a variable that did not
  exist in that scope. The `except Exception` two lines below caught the
  `NameError`, logged "thumbnail pick skipped", and shipped whatever Pexels
  returned first. The feature was dead for every video and nothing went red.
  When a fallback exists to absorb a network failure, give it a test that proves
  the good path still runs, or the fallback becomes the only path.
- **The stock-search cache is a licence condition too, not an optimisation.**
  Pixabay's API terms require a result to be cached for 24 hours rather than
  re-requested, so `visuals._cached_search()` puts every search on disk under
  `.cache/searches` for `SEARCH_TTL`. It also protects the Pexels quota, which
  is 200 requests an hour and 20,000 a month against one key no matter how many
  people are rendering. Do not shorten the TTL to get fresher footage, and note
  the cache key deliberately excludes the API key: it is shared across jobs, so
  a key in a filename would be a secret in a directory nobody guards.
- **Attribution is a licence condition, and it has broken twice.** The Pexels and
  Pixabay API terms require naming the creator and linking back, so
  `pipeline.credits_block()` builds the block from what the search actually
  returned and `description_box()` folds it into the YouTube description.
  `visuals` keeps a per-shot ledger in `credits.json` because `scenes.json` is
  shared across aspects, and `all_credits()` labels each cut's `credits*.txt`.
  Both past failures were silent: a second aspect overwrote the first's credits
  file, and a cached rebuild kept the clip but lost the credit. Generated cards
  need no attribution, so an empty block there is correct rather than a bug.
- **The file you paste has to belong to the cut you are publishing.** There was
  one `description.txt` holding every aspect's credits stacked under `[16:9]`
  and `[9:16]` labels, and it is the file whose entire purpose is to be pasted
  into YouTube. On a real build the two cuts shared **no footage at all** - 22
  credits against 18, zero lines in common - so pasting it named twenty-odd
  photographers whose clips are not in the video, and trimming it by hand
  instead dropped ones that are. Both happened, on published videos, and the
  hand-trim was done on the strength of the Pexels *content* licence, which says
  attribution is not required. That licence does not govern here: vidsmith is an
  API consumer, and the **API Guidelines** are a different document that asks
  for a prominent Pexels link and photographer credit. Read the one that applies
  to how the asset was obtained. `write_metadata()` now writes one
  `description<tag>.txt` per ledger, the 16:9 one keeping the unsuffixed name
  because that is what `aspect_tag()` calls it. `youtube.txt` keeps the labelled
  everything, because that one is for reading rather than pasting.
- **Dashes are kept out by two different mechanisms, and neither covers the
  script.** The voice reads an em dash as a pause the writing did not ask for.
  `llm.undash()` turns em and en dashes into commas, a range between digits into
  `5 to 10` (a comma there is heard as a thousands separator), and leaves
  hyphens alone, but it is only applied to the YouTube metadata and chapter
  labels. A *drafted* script stays clean because the prompt forbids dashes, so a
  draft can still come back with one and nothing downstream will strip it. It
  must not simply be run over a hand-written script either: `captions.TRAILING`
  and `visuals.CLAUSE_END` both rely on a dash as a clause boundary. The range
  rule keys off the dash and runs *before* the general pass, deliberately: when
  it matched any digit-comma-digit instead it could not tell a comma `undash`
  had just made from one the writer typed, and shipped `20,000 requests` into
  the description as `20 to 000 requests`.
  To *count* dashes, match the codepoint, not a shell bracket expression:
  `grep '[em-dash en-dash]'` also reports every `→` in these docs, because all
  three characters begin with the same `0xE2` byte and the class is matched
  bytewise. Six arrows read as six em dashes until the count was redone in
  Python.
- **`Path("")` is `Path(".")`, which is truthy and exists.** Every "is there an
  optional file here" guard has to be `is None`, never a bare truth test. The
  captions stage used `Path("")` for "no subtitle track", the guard in front of
  `render.master` let it through, and ffmpeg was handed the current directory as
  an ASS file: `--captions none` died in the master pass on every project
  without a watermark.
- **A filtergraph path needs two layers of escaping, not one.** The drive colon
  is eaten by the filter's option parser and an apostrophe is eaten by the
  filtergraph's quoting, so `ffmpeg_util.escape_filter_path()` spells one as
  `\:` and the other as `\'\''`. Every simpler spelling was tried against real
  ffmpeg and silently dropped the character, looking for `OBrien`. Note the
  original bug was invisible in review: `.replace("'", "\'")` is Python for
  replacing an apostrophe with itself.
- **The concat demuxer list needs a *different* escape, and only one layer.**
  `_concat_copy` writes `file '<path>'` and the demuxer reads that list itself,
  with no filter-option parser underneath, so the drive colon is safe and the
  apostrophe is spelled `'\''`. Two escapes, two parsers, deliberately not
  shared: `escape_concat_path()` is not `escape_filter_path()` and a test holds
  them apart. Unescaped, the stream-copy path - the default, since
  `transition: cut` - died on any machine whose user folder has an apostrophe.
- **Attribution is owed per source, not per build.** The thumbnail's
  photographer was credited only when the *footage* block was already
  non-empty, so an empty block short-circuited the condition. A `cards` or
  `local` build owes no footage credit but still pulls a real Pexels
  photograph for its thumbnail, and that build named nobody and wrote no
  credits file at all. Third failure in this family, and the same shape as the
  first two: silent, licence-bearing, and only visible by building the
  combination nobody builds.
- **The registry is memory and the directories are not.** `_sweep` walks
  `self._jobs`, so every `jobs/<id>/` still on disk when the process ends
  becomes unreachable: nothing holds a reference and nothing ever deletes it.
  The live instance was holding 2.5 GB across five orphans on an 18 GB disk,
  gaining a generation on every restart and reported by nothing. A render needs
  room to write, so it would eventually have failed a build with a message about
  disk rather than about jobs. `sweep_orphans()` runs from the constructor,
  where `_jobs` is empty by definition, so anything present belongs to a process
  that has gone. Do not move that call anywhere else: run at any other moment
  it deletes the render in flight, and it looks exactly like a tidy-up.
  **Then deleting all of them cost the videos.** Every deploy removed a render
  somebody had not downloaded yet, and one was saved only by copying it off the
  box by hand before a restart. A finished render now drops the bulk of its
  `build/` - the downloads, the cut and the mixed narration, `DISPOSABLE` in
  `web/jobs.py` - keeping only what changing a shot later needs, and writes
  `job.json` last, so a directory that
  has one is always a finished render; `sweep_orphans()` registers those again
  as done and removes everything else as before. What is held is bounded twice,
  by `VIDSMITH_KEEP_DAYS` (7) and `VIDSMITH_KEEP_GB` (4, oldest first, never the
  newest), and a failed render still goes after an hour. The record is read
  back, never trusted: its id must name its directory and `out/` must still
  hold an mp4, and the outputs are listed off the disk.
- **Claim the render slot and you own giving it back.** `Jobs.submit` sets
  `_active` under the lock, but only `_run` clears it, so anything between the
  two that can raise has to release it itself. Writing the job directory did
  not, and an unwritable `VIDSMITH_JOBS` wedged the instance: 429 for every
  later caller, for a render that had never started, until the process
  restarted.
- **A closed-set value outside its set used to fall back in silence.** Survivable
  when the fallback is visible: an unknown `theme.preset` is obvious the moment
  you look at the video. Not survivable for `render.aspect`, where `cfg.size`
  fell back to 16:9 while the *filename* was built from the string you typed, so
  `aspect: 9x16` rendered a landscape video into `-9x16.mp4` and nothing said
  otherwise. `9x16` is the likely typo precisely because that is the suffix
  convention. `config._check()` now refuses every closed set on load, because
  argparse and the web's `_validate` already did and `config.yaml` - the surface
  `vidsmith new` writes out in full - validated nothing. Contrast
  `music.ensure_bed`, which gets it right: normalise the mood, *then* name the
  file, so the name cannot lie about the contents.
- **Two writers for one artifact is how attribution goes missing.** `vidsmith
  meta` kept its own copy of the metadata write that omitted the credits block
  and left `description.txt` stale, so regenerating a description stripped the
  attribution out of the exact file you paste into YouTube. There is now one
  `pipeline.write_metadata()` and the CLI calls it. Fourth failure in this
  family; every one of them was a second code path, never the first.
- **An empty tag makes `*{tag}.mp4` match everything.** 16:9 is the unsuffixed
  default, so the pattern collapsed to `*.mp4`, and `demo-1x1.mp4` sorts before
  `demo.mp4` - asking for widescreen thumbnails sampled the square cut without
  a word. Reachable once `build/picture.mp4` is gone, which `invalidate()` does
  on every redraft. `aspect_tag()` is one definition in `config.py` now, because
  the same expression living in two modules is what let this drift.
  **Then `check.py` did the same thing, which is the checker written to catch
  this family failing to it.** It knew two shapes, `wide` and `9x16`, while
  `ASPECTS` has four, and resolved the widescreen cut as the first `*.mp4` that
  was not a short. `demo-1x1.mp4` sorts before `demo.mp4` again - `-` is 0x2D
  and `.` is 0x2E - so the square cut was checked as the widescreen one. The
  loud half was a false alarm on two thumbnails that were correct. The half
  that mattered was silent: the real 16:9 cut was neither the resolved wide cut
  nor a short, so it fell out of every loop and its runtime, captions and
  thumbnail went unexamined, and `runtime` for the chapter checks was read off
  the square cut. 4:5 is vertical and was not a short either, so it was checked
  as though it were landscape. `check.delivered()` now matches every file
  against `aspect_tag()` and returns `(aspect, path)` with the widescreen cut
  first. The lesson is the one this file keeps writing down: an unsuffixed
  default is not a name, and anything deriving a shape from a filename has to
  ask `config` what that shape is called.
- **YouTube drops a whole chapter list rather than the bad line.** The first
  must be at 0:00, there must be at least three, and none may be shorter than
  ten seconds - break one rule and the video has no chapters at all, with no
  error anywhere. A real 60.8s build emitted a six-second chapter and would have
  published a description whose entire list was ignored. `llm.usable_chapters()`
  folds a short chapter into the one before it, measures the last against the
  runtime, and returns nothing when fewer than three survive, because a
  `youtube.txt` promising chapters that will never appear is worse than one
  admitting the video has none. Filtered once in `upload_metadata`, so all three
  written files agree.
- **The upload form's caps are limits; the prompt is a request.** `META_PROMPT`
  asks for a title under 70 characters and twelve tags, and the model has
  complied every time so far. YouTube refuses a title over 100 characters or
  tags totalling over 500, at upload, after the render is paid for.
  `llm.within_youtube_limits()` trims title and description at a word boundary
  and drops whole tags from the end - half a tag is not a tag, and the model
  writes them most relevant first. Unlike the chapter rule this guards a risk
  nothing here has been seen to hit; it is in because the failure lands at the
  destination, which is where this project keeps getting bitten.
- **"No option name near <path>" can mean the filter does not exist.** The macOS
  runner failed on a subtitle path and the message read exactly like an escaping
  fault. It was not: Homebrew's ffmpeg 8.1.2 is built without libass, so there is
  no `subtitles` filter at all, and that is what the parser says when it cannot
  find the filter it was asked for. Two rounds went into escaping rules and one
  into copying the file somewhere with a plainer name, and none of it could have
  worked. Ask the binary first: `ffmpeg_util.filters()` reads the list from
  `-filters` and `require_filter()` names what is missing and what it costs.
  `doctor` reports it, and the caption tests skip on a build that cannot run
  them rather than failing as though the code were wrong.
- **A setting that changes the picture has to be applied per aspect.**
  `--force visuals,render` rebuilds the cut you asked for and no other, so
  turning the title card off left every 9:16 cut 2.4s longer than its 16:9 pair
  and still opening on a card. All four projects were in that state at once.
  `check` caught it four times over ("the two cuts disagree on length: 45s and
  47s") within minutes of being written, which is the whole argument for it: the
  wide cut was correct, the vertical cut was correct, and only the pair was
  wrong. Changing anything under `theme` or `render` means rebuilding every
  aspect that exists, not the default one.
- **A clip shorter than its shot was looped, and a viewer saw it straight
  away.** A downloaded nine-minute build showed identical frames coming back
  under different captions: a bookshelf every 3.2s, a leather press every 7.5s.
  `normalise_video` passed `-stream_loop -1` whenever a clip was shorter than
  its shot, and shots were long because the reranker keeps two to four of every
  eight candidates and `collapse()` merged a thirty-second scene's plan down to
  that many clips. Rebuilding that script's footage with a wrapper recording
  each clip's length against its shot measured it: 13 of 36 footage shots
  looped, 47s of replayed picture, and three scenes were one clip held for 30s.
  Nothing warned about the loop; `check` and the long-shot warning both see a
  long hold, and a long hold of a long clip is fine. Two changes, measured on
  the same script: `fit_shots()` never gives a clip more than it holds, and a
  scene short of clips has the next candidates judged too (`RERANK_ROUNDS`,
  over `SEARCH_RESULTS` per search). 82 footage shots, none looped, no clip
  used twice. **Then the rendered result still showed one repeat, and it was
  not a loop:** Pexels 853987 and 4671883 are the same phone clip from two
  uploaders, identical frame for frame, and `self.used` goes by id. Judging more
  candidates makes a re-upload likelier to be picked, so `_stock_batch` now
  compares each download's length and a 32x18 middle frame against the clips
  already used (`same_footage()`). Run over all 82 clips of that build, it
  matched that pair and nothing else. A scene whose subject Pexels barely has ("hard drive platter
  spinning close up" rejected 21 of 24) still holds long shots and still warns,
  which is the honest outcome. The search cache key was deliberately left
  without the result count, because `bench/rank_clips` rebuilds that key to
  find past searches; an older 15-result page is simply served until it
  expires.
- **A viewer called the footage mostly unrelated, and the searches were why.**
  A shot sheet of every cut in two finished videos - a frame per shot beside
  the words spoken over it - showed three causes, none of them visible in a log.
  Each scene ran **one search for fifteen to thirty seconds**, so the picture
  stayed on one subject while the narration moved through four ideas: 27s of
  forest trails under tree nodes, balancing and disk reads; six keyboards in a
  row. The searches were **metaphors the narration never states**, because
  `SCRIPT_PROMPT` taught one ("librarian pulling a book from shelves" for an
  index): "fork in a wooded trail" for a tree, "man holding a ring of keys" for
  a node. And the reranker judges clips against the search, so it confidently
  kept trails for a trail search. A config key, `per_scene_queries`, looked like
  the fix and was read by nothing.
  Now `plan_beats()` groups a scene's shots into beats of about `beat_seconds`,
  `prepare_beats()` has `llm.beat_queries()` write a literal search for every
  beat of the video in one request, and each beat is searched, reranked against
  its own passage and cut separately. Three things were learned by running the
  prompt against the Claude script before building anything, which is the cheap
  order: offering the scene's `[visual:]` as the writer's intent kept it where
  it did not fit ("person closing laptop" under a line about chatbots, where the
  bare passage gave "chatbot interface on screen"), so it is not sent; banning
  "text on screen" pushed a software topic away from the only literal footage
  it has, so screens showing an app or code are named as good shots; and
  without a rule against filler every abstract line became "person typing on
  computer". `beats.json` is keyed by heading and passage text, not position,
  so it cannot go stale on a redraft, and a beat's rerank verdict is keyed
  `index.beat` and records its search, so `invalidate(only=...)` and
  `bench/rank_clips collect` both had to learn that shape.
  Two repeats rode along. A studio shooting a series supplies near-identical
  takes under different ids - the same dancer in the same aisle four times -
  so a scene takes one clip per creator and does not open on the creator the
  previous scene ended on. And `credits_block()` skipped a creator it had
  already listed, links and all, which linked 24 of 33 clips in a two-minute
  build; it now lists every clip's link under its creator, and because a
  nine-minute build's credits approach YouTube's 5000-character description
  limit on their own, `description_box()` trims the prose rather than the
  credits and `check` reports a description over the limit.
- **A black clip passed the reranker twice, because it showed the right
  subject.** "Night highway drive following a truck" is a truck, so City and
  Documentary both kept it, and it played as a black frame with two headlights
  behind the captions. Nothing judged whether a still could be read at all.
  `too_dark()` measures it in code rather than asking the model: a still with
  under 10% of pixels above luma 60 is never shown to the model and never
  picked. Measured on 90 night-traffic candidates before choosing the cut: the
  offender was 5% lit, streets with their lights on 15 to 40%, daylight 60 to
  100%. It runs only where reranking runs, so a build with no Gemini key can
  still pick one, and verdicts cached before it existed are not re-judged.
- **Karaoke highlights overlapped whenever a word was very short.** Each word's
  event was floored at 60ms, and edge-tts reports words like "a" at 30 to 40ms,
  so the floor carried it past the next word's start. libass stacks any two
  events that overlap, so the caption line jumped upward for the rest of the
  word: 8 times in a two-minute video and 30 in a nine-minute one. The
  line-level guard against exactly this already existed; it just did not apply
  inside a line. `test_caption_lines_never_overlap` passed throughout because
  `make_scene()` spaces words evenly. The new test speaks at 25 words a second.
- **`vidsmith check <name>` exists because reading the output beat reading the
  source three times in one day.** It compares delivered files against each
  other rather than against the code that wrote them: every credit in
  `credits<tag>.txt` against `description<tag>.txt`, its own cut's, because
  checking them all against one description let a 9:16 credit pass whenever the
  16:9 description happened to name the same photographer; each thumbnail's
  orientation against the cut it names, caption and chapter timings against the
  runtime, and any jpg that matches no delivered cut. Every check in it is a
  fault that actually shipped, and each one looked correct in isolation. It
  calls no model and no network, so it works on a spent day, which is exactly
  when a hurried refresh gets published. Run it before uploading anything.
- **And it cannot see the one place every shipped fault actually landed.**
  `check` passed a delivery as ready while the published video's description had
  silently failed to save, its tags had been lost to the same trap, and its only
  caption track was YouTube's own transcription rather than the exact edge-tts
  timings the whole pipeline exists to produce. `published.py` reads the public
  watch page - one unauthenticated GET, no key, no OAuth, no quota, so it keeps
  the property that makes `check` worth running - and pulls title, description,
  tags and the caption track list out of `ytInitialPlayerResponse`. A track with
  no `kind` was uploaded; `kind: "asr"` is YouTube guessing. It is opt-in behind
  `--published` precisely because the offline half must stay offline.
  The attribution rule is applied **per credit line, by the domain in the line**,
  not in bulk: a Pexels line needs the photographer named, a Pixabay line needs
  only that Pixabay is named as the source. Bulk-checking either way is wrong in
  a direction that has already shipped - demanding every Pixabay uploader makes
  it cry wolf on correct videos, and excusing Pexels contributors is the trim
  that dropped eleven and thirteen photographers from two published
  descriptions. Run against the live `rome` video the hour it was written, it
  found a real one: the thumbnail's Pexels photographer was not in the published
  description.
- **A private video looks like an empty one to the public page.** The first
  real `vidsmith upload` went up private, as it should, and `check --published`
  reported "the published video has no description at all". The API, read with
  the upload's own token, showed the description was exactly `description.txt`.
  A private watch page still carries `ytInitialPlayerResponse`, with no
  `videoDetails` and `playabilityStatus` `LOGIN_REQUIRED` / "Private video", and
  `fetch()` read that as a video with blank fields. It now raises `Unreachable`
  naming the reason, and the CLI no longer ends an unread check with "matches
  what is published", which it had been printing for any unreadable page.
  **Then it reads a private video anyway, as the channel.** On `Private`,
  `check --published` takes the upload's saved login with `interactive=False`
  (a check must never open a browser) and `fetch_signed_in()` reads the same
  fields through the Data API for two quota units. No login, or a refused
  read, still ends in "the published copy was not checked".
- **Replacing a thumbnail invalidates `description.txt`, which is the file that
  gets published.** `description.txt` and `youtube.txt` are composed from the
  `credits*.txt` files, so `thumbs --refresh` corrected the credits and left the
  description beside them naming the photographer it had just dropped. Four
  videos sat upload-ready in exactly that state, and the credits file that
  looked right was not the one anybody pastes into YouTube. The refresh now
  rebuilds the metadata from `youtube.json` already on disk, which costs no
  model call, so honest attribution never depends on having quota left. When
  changing anything a credits file feeds, ask what else is derived from it.
- **"Untitled" is a sentinel, and every entry point has to resolve it.**
  `build()` fills an empty or `Untitled` config title from the script heading and
  writes it back; `thumbs --refresh` read `cfg.title` raw, slugged it to
  `untitled`, and wrote a pair of orphan jpgs beside the real thumbnails while
  leaving those stale. Nothing looked wrong: it reported two thumbnails
  rewritten, and two files had genuinely been written. Only the mtimes gave it
  away. `pipeline.resolve_title()` is now the one resolver and both callers use
  it, because the same divergence had already happened once between the CLI and
  the web job. Anything slugging `cfg.title` straight into a filename is the bug.
- **The macOS narration hang is not fixed, and the `-t` did not fix it.**
  `build_narration` hung again inside `ff.run` after that change, so the reading
  of the filtergraph that produced it was wrong. It is intermittent: many green
  runs either side. What is fixed is the reporting. A `TimeoutExpired` carries
  whatever the process printed before it was killed, and that was being thrown
  away, so both occurrences were reported as a stack trace through `subprocess`
  with nothing from ffmpeg in it. `VIDSMITH_FFMPEG_TIMEOUT` is now set to 45s in
  CI, under the 120s pytest limit, so our own guard fires first and prints what
  ffmpeg said; at the 900s default pytest always won the race and the guard
  never spoke. All three jobs carry both limits now, since a hang on ubuntu or
  windows was every bit as opaque. Next occurrence, read that output before
  theorising again.
  **And the guard still did not fire on the third occurrence, for a reason that
  had nothing to do with ffmpeg.** `TIMEOUT` was read once at import, so the
  only way to test it was `importlib.reload`, which monkeypatch does not undo.
  The two tests for it reloaded to read the value and reloaded again to restore
  it, but the restoring reload ran *while monkeypatch was still in force*, so it
  restored the module against the patched environment and left it holding 900.
  `test_filter_paths` sorts before `test_integration`, so every later file ran
  at 900 whatever the job had set, and pytest won the race every time. Setting
  45 in all three jobs had changed nothing at all. It is `timeout_limit()`
  reading the environment per call now, so there is no module state to go stale.
  Proven by measurement before it was fixed: with the variable set to 45, a test
  running after that file saw 900.0. The lesson is not about timeouts. **A test
  that mutates module state can silently disarm a safety net three files away**,
  and the second time this happened in one day was threads outliving their test
  and appending to the next test's list.
  **Fourth occurrence, 2026-09-02, and the guard finally fired. It told us
  nothing, and the reason is our own log level.** Same test,
  `test_each_scene_speaks_at_its_own_start`, on the first CI run of PR #58,
  with ubuntu and windows green and a re-run of the same job passing in 65s.
  The 45s from the job environment was honoured, so `timeout_limit()` reading
  per call is working. Both bounds the last round added were present:
  `atrim=0:22.746` in the graph and `-t 22.746` on the command. It sat the full
  45 seconds on about a second of work and was killed. What it said before it
  was killed: nothing at all.
  That empty capture reads like a finding and is not one. `ff.run` takes
  `quiet=True` by default and no caller overrides it, so every ffmpeg call in
  this project runs at `-loglevel error`, and at that level a **healthy** ffmpeg
  also prints nothing. So the capture cannot tell a process that hung before it
  started from one that stopped halfway, which is the single thing worth
  knowing here. The reporting fix from the last round answers a question it is
  not equipped to answer, and reading its output as evidence about the
  filtergraph is a fifth round of the same mistake this file keeps recording.
  **That is now fixed, and the fifth occurrence should be readable.** Every
  `ff.run` carries `-progress pipe:1`, which writes `out_time` to stdout
  regardless of the log level, and the timeout report reads the last one back:
  "it reached out_time=00:00:13.000000 before it was killed", or "it never
  reported any progress, so it had not begun encoding". Those are different
  faults and the previous three hangs could not tell them apart. Verified
  against real ffmpeg rather than only mocked: a `veryslow` 720p encode killed
  at 4s reported `out_time=00:00:08.000000`.
  Two details worth keeping. Silence on stderr is now reported as the
  non-finding it is, in those words, so nobody reads it as evidence a fifth
  time. And progress lines are stripped out of ordinary failure messages by
  `_without_progress()`, because a broken filtergraph buried under half a
  second of counters is a worse message than the one we had.
  **Sixth occurrence, 2026-09-03, on PR #69, and the progress line paid for
  itself.** Same test, ubuntu and windows green, and a re-run of the same job
  passing. What it said this time:

  ```
  it reached out_time=00:00:21.342000 before it was killed
  ```

  21.342 of a 22.746s output. So it is **not** failing to start and the graph is
  **not** failing to produce - it gets to within a second and a half of the end
  and then sits for the whole 45s. Five rounds of theorising had no way to tell
  those apart; one line of output did.
  The tail is the region a bare `apad` owns, so `build_narration` now uses
  `apad=whole_dur=<total>` and the graph holds nothing that generates forever.
  A bare apad pads until something downstream stops asking, and `atrim` drops
  the frames past the end **without propagating EOF upstream**, so apad went on
  producing silence for atrim to throw away. Measured against real ffmpeg on the
  same three-input graph before changing it: identical 22.746s output either
  way, and less work bounded, 0.18s against 0.30s.
  **This is a narrowing, not a proven fix, and the next person should not read
  it as one.** The hang is intermittent and does not reproduce off macOS, so
  nothing here has watched it stop happening. What is true is that the one
  unbounded element is gone and the evidence points at the region it owned. If
  it happens a seventh time, that reading is wrong and the pad is not the
  culprit: look at `amix` with `dropout_transition=0`, which is the other filter
  in the tail, and read the new `out_time` before theorising again.
  **Proven, 2026-09-13, after two attempts that proved nothing.** A CI probe
  (`workflows/narration-hang.yml`) called `build_narration` 3000 times with the
  bounded pad and 3000 with the bare one it replaced: zero hangs either way. A
  second probe (`narration-hang-suite.yml`) looped the whole suite 767 times
  with the same two arms: zero hangs either way. Both looked like a refutation
  of everything above. They were not measuring anything: both installed with
  `brew install ffmpeg`, and by the time they ran that resolved to **9.0.1**.
  Every hang on record was on **8.1.2** - the macOS runner image had moved on
  between 2026-09-04 and 2026-09-08, `brew`'s snapshot moved with it, and
  nothing had checked which version either probe actually ran.
  Re-run pinned to `ffmpeg@8` (exactly 8.1.2), with `scripts/which_ffmpeg.py`
  refusing the job outright if vidsmith's own resolver reports anything else:
  bare **hung 439 of 3000 calls, 14.6%**, every single one reaching the same
  `out_time=00:00:21.342000` this file already recorded from the real
  incidents. Bounded hung **0 of 3000**. The fix holds, on the ffmpeg that
  causes the fault - and reads as clean on a newer one only because that ffmpeg
  does not reproduce the bug at all. Verifying a fix without first confirming
  the environment reproduces the failure is the same mistake as reading a
  healthy `-loglevel error` silence as evidence, three entries up: absence of
  the symptom is not absence of the cause unless the trigger is actually
  present. When any future ffmpeg-version bump touches this runner, re-run
  `narration-hang.yml` before trusting green CI to mean the hang is gone.
- **An ffmpeg call with no timeout can hang forever, and one did.** `apad` is
  infinite by definition, so `build_narration` left `atrim` as the only thing
  ending its output; `master()` had always passed `-t` as well, and this one did
  not. macOS CI hung inside exactly that call. Both are fixed: the narration
  encode is bounded by `-t` as well as the graph, and `ff.run()` takes a
  `VIDSMITH_FFMPEG_TIMEOUT` (900s) so a stuck encode raises instead of sitting.
  The timeout matters well beyond CI. The web service holds one render slot and
  gives it back on the way out of the job, and a subprocess that never returns
  takes no way out, so the instance stops accepting work permanently. When
  bounding an encode, keep the limit generous: it is a bound on forever, not a
  performance budget, and killing an honest long encode is worse than the hang.
- **PowerShell unrolls a single-element array on its way out of a statement.**
  `$live = if (...) { @(...) } else { @() }` hands back a *String* when the array
  holds one item, so `$live[-1]` indexes the string and yields its last
  character. `serve-public.ps1` printed a 24-character access token as `c`, and
  `.Count` is 1 either way so nothing looked wrong. Assign in two statements, and
  read the last element with `Select-Object -Last 1`, which behaves the same on
  a scalar and an array. Testing the same lines with a direct assignment - the
  form that keeps the array - passes every time and proves nothing.
- **A dropped connection has to become `LLMUnavailable` too, and without the
  key.** Both request loops handled every HTTP status and none of requests' own
  exceptions, so a connection reset, a DNS failure or a timeout came out of
  `generate()` and `generate_vision()` as a requests error and skipped every
  handler that only catches `LLMUnavailable`. That is more of them than it
  sounds: `/api/draft` (an unhandled 500), `thumbs --refresh` (a traceback
  instead of "not refreshing"), `suggest_queries` (the whole build ends at the
  queries stage rather than falling back to keywords) and `bench.rank_clips
  run`, which is how it was found: `ConnectionResetError(10054)` after 122 good
  calls on 2026-09-13. Both loops now retry a `RequestException` on the same
  backoff as `RETRY_STATUS`, and `test_every_request_loop_handles_the_network`
  fails by name on any function that posts without catching one.
  The part worth knowing before repeating a requests error anywhere: the key
  travels as `?key=` and urllib3 quotes the whole URL, so a DNS failure or
  connect timeout reads `Max retries exceeded with url: ...?key=AIza...`.
  Checked against a real lookup, not assumed. `_network_failure()` redacts it,
  because that text goes into a 502 body, the build log, and bench results
  files that are not gitignored. The test that should have caught the route
  stubbed `draft_script` to raise `LLMUnavailable("connection reset")`, which
  is what the route assumed rather than what the network does; the one added
  beside it fails at `requests.post`.
- **A spent model quota is not a retryable failure.** Gemini answers `429
  RESOURCE_EXHAUSTED` when the free allowance is gone, and the generic retry
  loop spent four more requests on a number only the next day restores.
  `llm.QuotaExhausted` is raised immediately instead, and `_refuse_if_spent()`
  sits outside *both* request loops: the guard was added to `generate()` and not
  `generate_vision()`, so every thumbnail pick went on retrying for another day.
  The web layer maps it to **429, not 502** - a 5xx invites a tunnel or proxy to
  substitute its own HTML error page, and the page then reports `Unexpected
  token '<'` instead of what happened. The daily window is Pacific-aligned, so it
  does not roll over at local midnight.
- **Not every `RESOURCE_EXHAUSTED` is the day, and the body says which.** The
  same 429 covers the per-minute burst limit, which clears on its own, and the
  daily cap, which does not. Refusing both kills a build over a blip; retrying
  both spends what is left of a budget already gone. The `QuotaFailure` detail
  carries a `quotaId` that names the window
  (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), the model, and the
  ceiling, so `_refuse_if_spent()` refuses on `PerDay` and returns a wait on
  `PerMinute`. The number is worth repeating back: "500 requests a day for
  gemini-3.5-flash-lite" tells you to switch models, "out of quota" does not.
- **The `RetryInfo` beside it is not a promise.** Against a spent daily cap it
  advertised 8s, then 56s, then 56s, then 52s. All four waits were honoured and
  all four met another 429. It is trusted only once the `quotaId` says waiting
  can help, and clamped so a bad value cannot hang a build. A wait longer than
  the ordinary backoff is announced through the build log, because reranking
  runs up to three times per beat and several silent minute-long pauses inside one build are
  indistinguishable from the hang described in [tests.md](tests.md). `rank_clips()` takes
  `log` for exactly that reason; calling it without one makes the pause silent
  again. The rule is enforced, not remembered: every helper in `llm.py` that
  issues a request must accept a `log` and hand it down, and
  `test_every_request_helper_can_announce_a_wait` fails by name on any that does
  not. That test exists because the same mistake had already been made twice:
  the quota guard went into `generate()` and not `generate_vision()`, then the
  wait announcement went into `rank_clips()` and not `design_diagram()`, which
  runs just as often. Both were caught by reading, after shipping.
- **The free ceiling here is requests, not tokens.** 500 generate calls a day
  per model. That is unlike the Groq trap noted in the global `CLAUDE.md`, where
  the binding limit is tokens per day and appears in no header; Gemini prints
  its metric, its ceiling and the model in the error body. Do not carry the
  Groq assumption across. Note also that the last few requests trickle rather
  than stopping cleanly, so one probe succeeding does not mean the day is open.
- **A deliberate refresh should refuse where a build degrades.**
  `thumbs.from_stock()` falls back to a keyword search when the model is
  unavailable, because a render must never fail over a thumbnail. `vidsmith
  thumbs --refresh` passes `strict=True` and refuses instead: writing the same
  fallback over an existing thumbnail is worse than leaving it alone.
- **Heredocs mangle backslash escapes here.** Writing Python containing `\n` or
  `\1` through `bash <<'EOF'` has repeatedly produced real newlines and control
  characters mid-string. Use the Write or Edit tools for anything with escapes.
