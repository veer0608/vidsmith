# Tests

`pytest.ini` sets `pythonpath = . tests` and defines the one marker, `slow`, for
the tests that shell out to a real ffmpeg and encode video. GitHub Actions runs
the whole suite, encodes included, on **ubuntu, windows and macos** for every
push to main and every PR (`.github/workflows/tests.yml`); it installs ffmpeg
and a real font, because without one Pillow falls back to a bitmap default and
the card tests measure text nobody would ship.

macOS became a required check on 2026-08-28. It was advisory until then, which
meant the one runner that has found a platform-specific fault here could go red
without blocking a merge.

Each platform earns its minutes. Windows is the only runner that hands ffmpeg a
drive letter, which is what the path escaping exists for. macOS was added
because the README invites a mac user to clone this, and it found a real fault
within one run. It also carries `--timeout=120`, because it once sat in the
suite for twenty-five minutes and reported nothing: a hang produces no output,
and a per-test timeout turns it into a failing test with a traceback.

**Three ffmpegs are in play and they do not agree.** Ubuntu 6.1.1 on the
instance, winget 9.0 on this machine, Homebrew on the macOS runner - unpinned
in the regular `tests.yml` job, so it floats to whatever Homebrew's snapshot
currently ships (8.1.2 through 2026-09-04, 9.0.1 from 2026-09-08; see the
narration-hang entry in [incidents.md](incidents.md) for what that floating cost a debugging session).
Both versions ship without libass, confirmed on 9.0.1 the same way as 8.1.2, so
`tests.yml` still reports "subtitles filter: absent" and has no `subtitles`
filter at all regardless of which one lands.
Never assume a capability from a version number: `ffmpeg_util.filters()` asks
the binary and `require_filter()` names what is missing and what it costs. A CI
job's installer can also succeed while installing nothing, so every job now runs
`ffmpeg -version` after installing rather than trusting the exit status.

**`test_lint.py` gates on undefined names.** pyflakes, narrowed to the faults
that ship broken behaviour: a name that does not resolve, or a `nonlocal` that
is never bound. It exists because a thumbnail ranking ran through an undefined
variable for months behind a bare `except`. It deliberately ignores unused
imports, so the signal stays worth reading, and it carries a test proving the
gate itself can still fail.

**No test reads this machine's credentials, and CI proves it.** `config.env()`
prefers `os.environ` over every dotenv, deliberately, so a developer with real
keys exported hands them to anything that resolves one.
`test_env_handles_a_bom` asserted against a live `GEMINI_API_KEY` that way: red
on the machine it was written on, green in CI, and reported as a `main` failure
through two PRs before anyone read it. An autouse fixture in `conftest.py` now
clears every name in `KEY_ENV` before each test, which also means no test can
spend real Gemini quota or reach a provider by accident. It reads `KEY_ENV`
rather than keeping a second list, so a credential added there is covered
without anyone remembering the fixture exists.

Two things keep that guard honest, because a guard nobody can see fail is the
recurring shape in [incidents.md](incidents.md).
`test_no_test_can_see_this_machines_credentials` fails if the fixture is
removed, the same way `test_lint.py` carries proof its own gate still works. And
the ubuntu job runs the suite a **second** time with dummy credentials in the
environment, because CI exporting nothing is precisely the configuration in
which this class of fault cannot fail. Anything reading a credential by a route
the fixture cannot see goes red there instead of on one person's laptop.

**Build scenes with `make_scene()` from `tests/conftest.py`, never by hand.**
Word timings drive the edit, the captions and the mix, so a test only means
something against words shaped the way edge-tts reports them: punctuation
stripped, times in seconds from the start of the speech, `duration` agreeing with
the words it holds. A hand-written `words` list passes against input the TTS
could never produce. The `scene` and `scenes` fixtures wrap it.
