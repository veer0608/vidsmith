"""vidsmith - script in, narrated YouTube video out."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import llm, pipeline, thumbs, voice
from .config import ASPECTS, load_config, write_default_config
from .theme import PRESETS as THEME_PRESETS
from .pipeline import Project, find_keys

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
    else:
        script.write_text(STARTER.format(title=title), encoding="utf-8")

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
        "no_cards": "1" if args.no_cards else "",
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


def cmd_thumbs(args) -> int:
    from .theme import resolve as resolve_theme

    root = _project_dir(args.name)
    proj = Project(root)
    cfg = load_config(proj.config_path)
    if args.aspect:
        cfg.render.aspect = args.aspect
    tag = "" if cfg.render.aspect == "16:9" else "-" + cfg.render.aspect.replace(":", "x")

    # the picture track has no captions or watermark burned into it
    source = proj.build / f"picture{tag}.mp4"
    if not source.exists():
        source = next(iter(proj.out.glob(f"*{tag}.mp4")), None)
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


def cmd_doctor(args) -> int:
    ok = True
    try:
        from . import ffmpeg_util as ff

        print(f"[ok]   ffmpeg    {ff.ffmpeg_bin()}")
        print(f"[ok]   ffprobe   {ff.ffprobe_bin()}")
    except RuntimeError as exc:
        ok = False
        print(f"[MISS] ffmpeg    {exc}")

    try:
        import edge_tts  # noqa: F401

        print("[ok]   edge-tts  installed (no API key needed)")
    except ImportError:
        ok = False
        print("[MISS] edge-tts  pip install edge-tts")

    keys = find_keys(Path.cwd())
    for name, label in (("gemini", "GEMINI_API_KEY "), ("pexels", "PEXELS_API_KEY "),
                        ("pixabay", "PIXABAY_API_KEY")):
        if keys[name]:
            print(f"[ok]   {label} found ({keys[name][:6]}...)")
        else:
            note = {
                "gemini": "optional - b-roll queries + YouTube metadata",
                "pexels": "optional - real stock footage; free at pexels.com/api",
                "pixabay": "optional - alternative stock source",
            }[name]
            print(f"[--]   {label} not set ({note})")

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
    text = pipeline._readable_meta(meta)
    (proj.out / "youtube.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv=None) -> int:
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
    b.add_argument("--music", help="path to a background music file")
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
    t.set_defaults(func=cmd_thumbs)

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
