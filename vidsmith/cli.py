"""vidsmith - script in, narrated YouTube video out."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import llm, music, pipeline, thumbs, voice
from .config import ASPECTS, aspect_tag, load_config, write_default_config
from .theme import PRESETS as THEME_PRESETS
from .pipeline import (KEY_ENV, KEY_NOTES, Project, _slug, find_keys,
                       resolve_title, set_thumbnail_credit, write_metadata)

STARTER = """# {title}

## Hook
[visual: sunrise over a quiet city street]
Write the first two sentences so someone who does not care yet decides to stay.
Say what the payoff is, plainly.

## What is actually going on
[visual: hands sorting paper documents]
Each paragraph becomes one scene with its own shot. Keep the sentences short,
because they are going to be spoken out loud rather than read.

## The takeaway
[visual: wide empty road at dusk]
End on one thing worth remembering. That is the line people repeat.
"""


def _project_dir(name: str) -> Path:
    p = Path(name)
    if p.exists() and (p / "script.md").exists():
        return p.resolve()
    if any(sep in name for sep in ("/", "\\")) or p.is_absolute():
        return p.resolve()
    return (Path(__file__).resolve().parent.parent / "projects" / name).resolve()


def cmd_new(args) -> int:
    root = _project_dir(args.name)
    root.mkdir(parents=True, exist_ok=True)
    title = args.title or args.topic or args.name.replace("-", " ").title()

    script = root / "script.md"
    drafted = False
    if script.exists() and not args.force:
        print(f"{script} already exists (use --force to overwrite)")
    elif args.topic:
        keys = find_keys(root)
        if not keys["gemini"]:
            print("--topic needs GEMINI_API_KEY; writing the starter script instead")
            script.write_text(STARTER.format(title=title), encoding="utf-8")
        else:
            print(f"drafting a ~{args.minutes} minute script on: {args.topic}")
            script.write_text(llm.draft_script(args.topic, args.minutes, keys["gemini"]),
                              encoding="utf-8")
            drafted = True
    else:
        script.write_text(STARTER.format(title=title), encoding="utf-8")

    if drafted:
        # the drafted script writes its own headline, which is a real title -
        # the topic is a search phrase and reads like one on a thumbnail
        from .script_parser import parse_script

        written, _ = parse_script(script)
        if written:
            title = written

    cfgp = root / "config.yaml"
    if not cfgp.exists() or args.force:
        write_default_config(cfgp, title)

    print(f"project  {root}")
    print(f"script   {script}")
    print(f"config   {cfgp}")
    print(f"\nnext:    vidsmith build {args.name}")
    return 0


def cmd_build(args) -> int:
    root = _project_dir(args.name)
    overrides = {
        "aspect": args.aspect, "provider": args.provider, "voice": args.voice,
        "music": args.music, "captions": args.captions, "theme": args.theme,
        "accent": args.accent, "watermark": args.watermark,
        "no_cards": "1" if args.no_cards else "", "mood": args.mood,
    }
    out = pipeline.build(
        root,
        force=[f.strip() for f in (args.force or "").split(",") if f.strip()],
        stop_after=args.stop_after or "",
        overrides={k: v for k, v in overrides.items() if v},
    )
    print(f"\n{out}")
    return 0


def cmd_voices(args) -> int:
    for v in voice.list_voices(args.lang):
        tags = ", ".join(v.get("VoiceTag", {}).get("VoicePersonalities", []) or [])
        print(f"{v['ShortName']:<34} {v['Gender']:<7} {tags}")
    return 0


def _delivery_file(proj, cfg, tag: str):
    """The finished mp4 for this aspect, and no other cut's.

    A bare `*{tag}.mp4` glob is wrong exactly where it matters: 16:9 has an
    empty tag, so the pattern collapsed to `*.mp4` and matched every cut in
    out/. `demo-1x1.mp4` sorts before `demo.mp4`, so asking for the widescreen
    thumbnails sampled the square video and said nothing about it. Only
    reachable once build/picture.mp4 is gone, which invalidate() does on every
    redraft.
    """
    exact = proj.out / f"{pipeline._slug(cfg.title)}{tag}.mp4"
    if exact.exists():
        return exact
    # the title may have moved since the build, so fall back to a scan that
    # still refuses the other aspects by name
    others = {aspect_tag(a) for a in ASPECTS if a != cfg.render.aspect} - {""}
    for path in sorted(proj.out.glob("*.mp4")):
        if any(path.stem.endswith(other) for other in others):
            continue
        if tag and not path.stem.endswith(tag):
            continue
        return path
    return None


def cmd_thumbs(args) -> int:
    from .theme import resolve as resolve_theme

    if args.refresh:
        return _refresh_thumbnails(args)

    root = _project_dir(args.name)
    proj = Project(root)
    cfg = load_config(proj.config_path)
    if args.aspect:
        cfg.render.aspect = args.aspect
    tag = aspect_tag(cfg.render.aspect)

    # the picture track has no captions or watermark burned into it
    source = proj.build / f"picture{tag}.mp4"
    if not source.exists():
        source = _delivery_file(proj, cfg, tag)
        if source is None:
            print(f"nothing built for {cfg.render.aspect} yet - run: vidsmith build {args.name}")
            return 1
        print("note: using the delivery file; captions will be in these frames")

    theme = resolve_theme(cfg.theme.preset, cfg.theme.accent, cfg.theme.font)
    print(f"sampling {source.name}")
    files = thumbs.extract(
        source, proj.out / f"thumbs{tag}", proj.build / ".thumbframes",
        cfg.title, theme, count=args.count, with_title=not args.no_title,
    )
    print(f"\n{len(files)} files in {proj.out / ('thumbs' + tag)}")
    return 0


def _refresh_thumbnails(args) -> int:
    """Redo the delivery thumbnails for every cut that exists.

    Separate from a rebuild on purpose: the thumbnail is the one output that
    does not depend on the render, so it can be redone in seconds. That matters
    when it was first made with the model out of quota, where the search falls
    back to keywords and nothing picks between the candidates.
    """
    from . import visuals
    from .script_parser import load_scenes
    from .theme import resolve as resolve_theme

    root = _project_dir(args.name)
    proj = Project(root)
    cfg = load_config(proj.config_path)
    scenes_json = proj.build / "scenes.json"
    if not scenes_json.exists():
        print(f"nothing built yet - run: vidsmith build {args.name}")
        return 1

    scenes = load_scenes(scenes_json)
    theme = resolve_theme(cfg.theme.preset, cfg.theme.accent, cfg.theme.font)
    keys = find_keys(root)
    subjects = ", ".join(dict.fromkeys(visuals.scene_query(s) for s in scenes))
    # the same resolution build() uses, or a project whose config was never
    # written back slugs to "untitled" and refreshes files nothing delivers
    slug = _slug(resolve_title(proj, cfg))

    done = 0
    for aspect in sorted(ASPECTS):
        tag = aspect_tag(aspect)
        if not (proj.build / f"picture{tag}.mp4").exists():
            continue
        size = ASPECTS[aspect]
        target = (1280, 720) if size[0] >= size[1] else None
        try:
            stock = thumbs.from_stock(cfg.title, subjects, size, keys,
                                      proj.build / ".thumbstock", strict=True)
        except llm.LLMUnavailable as exc:
            print(f"\nnot refreshing: {exc}")
            print("the existing thumbnails are untouched; try again once it resets")
            return 1
        if not stock:
            print(f"  {aspect:5} no stock photo; leaving the existing thumbnail")
            continue
        out = proj.out / f"{slug}{tag}.jpg"
        thumbs.titled(stock["path"], out, cfg.title, theme, target)
        # the photo changed, so the attribution has to change with it; a credits
        # file naming the photographer we just dropped is a licence problem
        set_thumbnail_credit(proj.out / f"credits{tag}.txt", stock)
        print(f"  {aspect:5} {stock['query']:32} by {stock['author']}")
        done += 1

    if not done:
        print("no thumbnails were replaced")
        return 1
    print(f"\n{done} thumbnail(s) rewritten in {proj.out}")

    # description.txt is built from the credits files, so replacing a thumbnail
    # leaves the one file you actually paste into YouTube crediting the
    # photographer that was just dropped. Rewritten from the metadata already on
    # disk, so this costs no model call and cannot fail for want of quota.
    meta_json = proj.out / "youtube.json"
    if meta_json.exists():
        try:
            write_metadata(proj.out, json.loads(meta_json.read_text(encoding="utf-8")))
            print(f"credits  description.txt now names the photographers in use")
        except (OSError, ValueError) as exc:
            print(f"warning: could not refresh description.txt ({exc});"
                  f" run: vidsmith meta {args.name}")
    return 0


def cmd_check(args) -> int:
    """Read the delivered files against each other before anything is published.

    Costs nothing and needs no key, so it can run on a spent day. It exists
    because every fault it looks for was found by hand, after the fact, in
    files that each looked correct on their own.
    """
    from .check import check

    proj = Project(_project_dir(args.name))
    problems = check(proj.out)

    # --published is the one part of check that touches the network, and it is
    # opt-in so the offline guarantee above still holds by default.
    if getattr(args, "published", None):
        from .published import Unreachable, check_published

        try:
            problems.extend(check_published(proj.out, args.published))
        except (Unreachable, ValueError) as exc:
            print(f"warn     could not read the published video: {exc}")

    if not problems:
        where = " and matches what is published" if getattr(args, "published", None) else ""
        print(f"ok       {proj.out} is consistent{where} and ready to upload")
        return 0
    print(f"\n{len(problems)} problem(s) in {proj.out}:\n")
    for line in problems:
        print(f"  - {line}")
    return 1


def cmd_doctor(args) -> int:
    ok = True
    from . import build_info

    sha = build_info.commit()
    print(f"[ok]   commit    {sha}" if sha else
          "[--]   commit    not a git checkout")
    try:
        from . import ffmpeg_util as ff

        print(f"[ok]   ffmpeg    {ff.ffmpeg_bin()}")
        print(f"[ok]   ffprobe   {ff.ffprobe_bin()}")
        # A build without libass has no subtitles filter, and ffmpeg answers a
        # request for one with "No option name near <path>", which reads like a
        # quoting fault. Say it here, where someone looks before rendering.
        if "subtitles" in ff.filters():
            print("[ok]   libass    the subtitles filter is present")
        else:
            ok = False
            print("[MISS] libass    this ffmpeg has no subtitles filter, so "
                  "captions cannot be burned in")
    except RuntimeError as exc:
        ok = False
        print(f"[MISS] ffmpeg    {exc}")

    try:
        import edge_tts  # noqa: F401

        print("[ok]   edge-tts  installed (no API key needed)")
    except ImportError:
        ok = False
        print("[MISS] edge-tts  pip install edge-tts")

    # Iterated from pipeline.KEY_ENV rather than a list kept here. The list kept
    # here reported three keys and stayed at three when a new voice provider added
    # more, so `doctor` answered "which keys resolve" incompletely and with
    # no sign that it had.
    keys = find_keys(Path.cwd())
    width = max(len(v) for v in KEY_ENV.values())
    for name, var in KEY_ENV.items():
        if keys.get(name):
            print(f"[ok]   {var:<{width}} found ({keys[name][:6]}...)")
        else:
            print(f"[--]   {var:<{width}} not set ({KEY_NOTES.get(name, 'optional')})")

    print("\nprovider fallback: without a stock key, scenes render as generated cards.")
    return 0 if ok else 1


def cmd_meta(args) -> int:
    root = _project_dir(args.name)
    proj = Project(root)
    from .script_parser import load_scenes

    scenes_json = proj.build / "scenes.json"
    if not scenes_json.exists():
        print("build the project first so scene timings exist")
        return 1
    keys = find_keys(root)
    if not keys["gemini"]:
        print("GEMINI_API_KEY not found")
        return 1
    cfg = load_config(proj.config_path)
    meta = llm.upload_metadata(cfg.title, load_scenes(scenes_json), keys["gemini"])
    # Through the pipeline's own writer, not a second copy of it: the copy that
    # used to live here omitted the credits block, so regenerating a description
    # stripped the attribution out of the file you paste into YouTube.
    print(pipeline.write_metadata(proj.out, meta))
    return 0


def _printable_console() -> None:
    """Stop a name this tool did not choose from killing a command.

    A Windows console is cp1252 and almost nothing here is: stock creators,
    edge-tts voice names, and any drafted script are all arbitrary Unicode. The
    files are written as utf-8 throughout, so the only thing that ever breaks is
    the print - and it breaks *after* the work is done, which is the worst
    possible time. `vidsmith meta` died on a Pexels photographer with U+1ECB in
    their name having already written every file correctly.

    errors="replace" rather than a narrower fix: a mangled character in the
    terminal is a cosmetic problem, and a traceback over a finished build is
    not.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv=None) -> int:
    _printable_console()
    p = argparse.ArgumentParser(prog="vidsmith", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create a project (optionally drafting the script)")
    n.add_argument("name")
    n.add_argument("--topic", help="have Gemini draft the script on this topic")
    n.add_argument("--minutes", type=float, default=4.0)
    n.add_argument("--title")
    n.add_argument("--force", action="store_true")
    n.set_defaults(func=cmd_new)

    b = sub.add_parser("build", help="render the project to mp4")
    b.add_argument("name")
    b.add_argument("--aspect", choices=sorted(ASPECTS), help="override output shape")
    b.add_argument("--provider", choices=["cards", "pexels", "pixabay", "local"])
    b.add_argument("--voice", help="edge-tts voice, e.g. en-IN-PrabhatNeural")
    b.add_argument("--music", help='"auto", "none", or a path to a music file')
    b.add_argument("--mood", choices=music.moods(),
                   help="which generated bed to use with --music auto")
    b.add_argument("--captions", choices=["karaoke", "block", "none"])
    b.add_argument("--theme", choices=sorted(THEME_PRESETS),
                   help="colour and type preset for cards, captions and overlays")
    b.add_argument("--accent", help='accent colour override, e.g. "#FF7A59"')
    b.add_argument("--watermark", help="channel handle, drawn small bottom-right")
    b.add_argument("--no-cards", action="store_true",
                   help="skip the generated title and end cards")
    b.add_argument("--force", help="comma list of stages to redo: voice,visuals,render")
    b.add_argument("--stop-after", choices=pipeline.STAGES)
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("voices", help="list available narration voices")
    v.add_argument("--lang", default="en", help="locale prefix, e.g. en, en-IN, hi")
    v.set_defaults(func=cmd_voices)

    m = sub.add_parser("meta", help="regenerate the YouTube title/description/chapters")
    m.add_argument("name")
    m.set_defaults(func=cmd_meta)

    t = sub.add_parser("thumbs", help="rank thumbnail frames from a finished build")
    t.add_argument("name")
    t.add_argument("--count", type=int, default=6, help="how many candidates")
    t.add_argument("--aspect", choices=sorted(ASPECTS), help="which cut to sample")
    t.add_argument("--no-title", action="store_true",
                   help="skip the composed title thumbnail")
    t.add_argument("--refresh", action="store_true",
                   help="redo the delivery thumbnails from stock photos, no re-render")
    t.set_defaults(func=cmd_thumbs)

    ck = sub.add_parser("check", help="read a finished build for faults "
                                      "before publishing it")
    ck.add_argument("name")
    ck.add_argument("--published", metavar="ID_OR_URL",
                    help="also read the live video and check the description, "
                         "chapters, tags and caption track against this build")
    ck.set_defaults(func=cmd_check)

    d = sub.add_parser("doctor", help="check ffmpeg, edge-tts and API keys")
    d.set_defaults(func=cmd_doctor)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
