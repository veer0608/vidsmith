"""vidsmith - script in, narrated YouTube video out."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from . import llm, music, pipeline, thumbs, voice
from .config import ASPECTS, aspect_tag, load_config, write_default_config
from .genres import GENRES
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
        "genre": args.genre,
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
            write_metadata(proj.out, json.loads(meta_json.read_text(encoding="utf-8")),
                           source=cfg.source)
            print(f"credits  description.txt now names the photographers in use")
        except (OSError, ValueError) as exc:
            print(f"warning: could not refresh description.txt ({exc});"
                  f" run: vidsmith meta {args.name}")
    return 0


def _check_live(proj, ref: str, tag: str = ""):
    """Check the published copy of a video against this delivery.

    Returns (problems, compared). `compared` is False when the video could not
    be read at all, so nobody claims a match they never made. Private videos
    are read through the API as the channel, never with a browser.
    """
    from .published import (Private, Unreachable, check_published,
                            fetch_signed_in, record, video_id)

    try:
        try:
            found = check_published(proj.out, ref, tag=tag)
        except Private as exc:
            # still private is the cheapest moment to catch a fault, so read
            # it as the channel; never opens a browser from a check
            from .upload import UploadFailed, access_token
            repo_root = Path(__file__).resolve().parent.parent
            keys = find_keys(proj.root)
            try:
                token = access_token(repo_root, keys.get("yt_client", ""),
                                     keys.get("yt_secret", ""), interactive=False)
            except UploadFailed as why:
                raise Unreachable(f"{exc}, and it cannot be read signed in "
                                  f"either: {why}")
            print(f"info     {exc}; reading it through the API as the channel")
            live = fetch_signed_in(video_id(ref), token)
            found = check_published(proj.out, ref, live=live, tag=tag)
    except (Unreachable, ValueError) as exc:
        print(f"warn     could not read the published video: {exc}")
        return [], False
    if not found:
        # only a clean check is worth remembering: a receipt written over a
        # failing one would claim the published copy is good
        record(proj.out, ref, tag=tag)
    return found, True


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
    compared = False
    if getattr(args, "published", None):
        found, compared = _check_live(proj, args.published,
                                      aspect_tag(getattr(args, "aspect", "")
                                                 or load_config(proj.config_path).render.aspect))
        if compared and not found:
            # A clean comparison has just rewritten the receipt, so drift judged
            # against the old one is already answered. It used to be reported
            # anyway: the run that confirmed a re-pasted description failed on
            # "the description published there is stale", and a second run passed.
            problems = check(proj.out)
        problems.extend(found)

    if not problems:
        # never claim a match with a copy that could not be read
        where = " and matches what is published" if compared else ""
        unread = ("; the published copy was not checked"
                  if getattr(args, "published", None) and not compared else "")
        print(f"ok       {proj.out} is consistent{where} and ready to upload{unread}")
        return 0
    print(f"\n{len(problems)} problem(s) in {proj.out}:\n")
    for line in problems:
        print(f"  - {line}")
    return 1


def cmd_publish(args) -> int:
    """Make an uploaded video visible, then prove the visible copy is right.

    Uploads go up private so they can be read first. Going public was a hand
    edit in Studio followed by a separate `check --published`; this is both,
    in the order that matters: the offline check refuses first, because a
    fault is cheapest to fix while nobody can see the video.
    """
    from .check import _drift_of, check
    from .published import receipt_name, video_id
    from .upload import UploadFailed, access_token, set_metadata, set_privacy

    proj = Project(_project_dir(args.name))
    cfg = load_config(proj.config_path)
    tag = aspect_tag(args.aspect or cfg.render.aspect)
    receipt_path = proj.out / receipt_name(tag)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt = {}
    receipt = receipt if isinstance(receipt, dict) else {}
    ref = args.video or receipt.get("video_id")
    if not ref:
        print(f"no video id: pass --video, or upload first so "
              f"{receipt_name(tag)} names one")
        return 1
    vid = video_id(ref)
    meta_too = getattr(args, "meta", False)
    privacy = args.privacy or ("" if meta_too else "public")

    def connect() -> str:
        keys = find_keys(proj.root)
        repo_root = Path(__file__).resolve().parent.parent
        return access_token(repo_root, keys["yt_client"], keys["yt_secret"])

    if privacy == "private" and not meta_too:
        # Taking a video down is never refused: nobody can see it afterwards,
        # so there is nothing about it to verify, and the video being retired
        # is usually an old one whose files are long gone from out/.
        try:
            now = set_privacy(connect(), vid, "private")
        except UploadFailed as exc:
            print(f"publish failed: {exc}")
            return 1
        print(f"{now:<8} https://www.youtube.com/watch?v={vid}; nobody else can "
              f"see it now, so nothing about it was checked")
        return 0

    problems = check(proj.out)
    if meta_too:
        why = _meta_refused(proj, receipt, vid, tag)
        if why:
            print(f"refused  {why}")
            return 1
        # the stale description this receipt reports is what --meta replaces
        answered = set(_drift_of(proj.out, receipt_path))
        problems = [p for p in problems if p not in answered]
    if problems and not args.force:
        print(f"\n{len(problems)} problem(s) in {proj.out}, so {vid} was left "
              "as it is:\n")
        for line in problems:
            print(f"  - {line}")
        print("\nfix them, or publish anyway with --force")
        return 1

    try:
        token = connect()
        if meta_too:
            meta = json.loads((proj.out / "youtube.json").read_text(encoding="utf-8"))
            saved = set_metadata(token, vid, meta.get("title", ""),
                                 (proj.out / f"description{tag}.txt").read_text(encoding="utf-8"),
                                 meta.get("tags") or [])
            print(f"meta     '{saved.get('title', '')}', its description and "
                  f"{len(saved.get('tags') or [])} tags")
        now = set_privacy(token, vid, privacy) if privacy else ""
    except UploadFailed as exc:
        print(f"publish failed: {exc}")
        return 1
    if now:
        print(f"{now:<8} https://www.youtube.com/watch?v={vid}")
    now = now or "updated"

    found, compared = _check_live(proj, vid, tag)
    if not compared:
        print(f"warn     {vid} is {now}, but its published copy was not checked; "
              f"run: vidsmith check {args.name} --published {vid}")
        return 0
    if found:
        print(f"\n{vid} is {now} and has {len(found)} problem(s):\n")
        for line in found:
            print(f"  - {line}")
        return 1
    print(f"ok       {vid} is {now} and matches {proj.out}")
    return 0


def _meta_refused(proj, receipt, vid: str, tag: str) -> str:
    """Why this cut's words may not replace the live video's, or "".

    Only over the same cut. A new description on a rebuilt video credits
    footage the published one does not contain, which is the one move that
    makes public credits wrong, so the receipt must show the video unchanged.
    """
    from .published import cut_file, digest

    if receipt.get("video_id") != vid:
        return (f"{vid} is not the video this cut was uploaded as "
                f"({receipt.get('video_id') or 'none recorded'}), so nothing "
                f"shows it carries this cut")
    cut = receipt.get("cut") if isinstance(receipt.get("cut"), dict) else {}
    if not cut.get("digest"):
        return ("this receipt predates recording the cut, so it cannot show the "
                "video is unchanged; upload the cut instead")
    here = cut_file(proj.out, tag)
    if here is None or digest(here) != cut["digest"]:
        return ("the cut has changed since it was uploaded; upload it with its own "
                "description rather than pasting that onto the old video, which "
                "would credit footage it does not contain")
    for name in ("youtube.json", f"description{tag}.txt"):
        if not (proj.out / name).exists():
            return f"no {name} in {proj.out}"
    return ""


def cmd_retake(args) -> int:
    """Search a scene's footage again, keeping the narration and every other cut.

    The footage is the part most often wrong, and the fix used to be a whole
    rebuild in which every other scene was free to change too. This drops one
    scene's cached beat searches and its clips, so the next build writes new
    searches from the same words and re-films only that scene.

    What it cannot do is change the subject: the search is written from the
    narration, so a scene that comes back wrong twice needs its words changed
    rather than another roll of the dice.
    """
    from . import visuals
    from .pipeline import invalidate
    from .script_parser import load_scenes

    if getattr(args, "shot", None) is not None:
        return _retake_shot(args)
    if getattr(args, "clip", None) or getattr(args, "search", None):
        print("--clip and --search choose footage for one shot; add --shot")
        return 1

    root = _project_dir(args.name)
    proj = Project(root)
    scenes_json = proj.build / "scenes.json"
    if not scenes_json.exists():
        print(f"nothing built yet - run: vidsmith build {args.name}")
        return 1

    scenes = load_scenes(scenes_json)
    index = args.scene
    if not 0 <= index < len(scenes):
        print(f"scene {index} does not exist; this build has 0 to {len(scenes) - 1}")
        return 1

    scene = scenes[index]
    dropped = visuals.forget_beats(proj.build, scene)
    print(f"retake   scene {index}: {scene.heading or 'no heading'}")
    for query in dropped:
        print(f"         forgetting the search: {query}")
    if not dropped:
        print("         no cached beat search here, so only its clips go")
    invalidate(proj, only={index})

    out = pipeline.build(
        root,
        overrides={k: v for k, v in {"provider": args.provider,
                                     "genre": args.genre}.items() if v},
    )
    print(f"\n{out}")
    print(f"next:    vidsmith sheet {args.name}    # read the new shots")
    return 0


def _retake_shot(args) -> int:
    """Change one shot and keep every other, through the page's own retake.

    A scene retake re-films every shot in it, so fixing one bad slot could
    lose the good shots around it: howto's scene 1 traded a real `pip install`
    log for the same bad clip on its second roll. With no `--clip` this lists
    the shot's other candidates, verdicts beside them, and tiles their stills
    into one image; `--clip` puts one in the old slot and delivers the video.
    """
    from . import retake

    if args.provider or args.genre:
        print("--provider and --genre re-film a whole scene; a shot keeps the "
              "project's library")
        return 1
    root = _project_dir(args.name)
    where = f"--scene {args.scene} --shot {args.shot}"
    search = f' --search "{args.search}"' if args.search else ""
    try:
        if args.clip:
            out = retake.replace(root, args.scene, args.shot, args.clip,
                                 query=args.search)
        else:
            found = retake.candidates(root, args.scene, args.shot, query=args.search)
    except retake.RetakeRefused as exc:
        print(f"refused  {exc}")
        return 1
    if args.clip:
        print(f"\n{out}")
        print(f"next:    vidsmith sheet {args.name}    # read the new shot")
        return 0

    rows = found["candidates"]
    print(f"retake   scene {args.scene} shot {args.shot}, {found['duration']:.1f}s, "
          f"searched '{found['query']}'; now {found['current'] or 'no clip'}")
    if not rows:
        print("         nothing in these results is free to use; "
              "try --search with other words")
        return 1
    for n, row in enumerate(rows, 1):
        print(f"  {n:>3}  {row['id']:<10} {row['verdict'] or '-':<9}"
              f"{(row['author'] or '')[:24]:<25}{row['page']}")
    try:
        sheet = retake.candidate_sheet(root, args.scene, args.shot, rows)
    except Exception as exc:                  # the stills are help, not the retake
        print(f"         no stills ({exc})")
        sheet = None
    if sheet:
        print(f"stills   {sheet}")
    print(f"next:    vidsmith retake {args.name} {where} --clip {rows[0]['id']}"
          f"{search}    # the first above")
    return 0


def cmd_published(args) -> int:
    """List every video this repo has uploaded, from the receipts it left.

    Test uploads accumulate on a channel quietly: two private ones were there
    within a day of the upload path working, and nothing in the tool could say
    so. The receipts already know, one per published cut, so this reads them.

    Offline by default, like `check`. `--live` spends one quota unit per fifty
    videos to add what YouTube says each one is now, which is the half that
    catches a video deleted in Studio while its receipt still claims it.
    """
    from .published import Unreachable, privacy_of, receipts

    root = (Path(__file__).resolve().parent.parent / "projects")
    if args.name:
        projects = [_project_dir(args.name)]
    else:
        projects = sorted(p for p in root.glob("*") if (p / "out").is_dir())

    rows = []
    for proj_dir in projects:
        for row in receipts(proj_dir / "out"):
            row["project"] = proj_dir.name
            rows.append(row)
    if not rows:
        print("nothing published from here yet")
        return 0

    live = {}
    if args.live:
        keys = find_keys(projects[0])
        repo_root = Path(__file__).resolve().parent.parent
        from .upload import UploadFailed, access_token
        try:
            token = access_token(repo_root, keys.get("yt_client", ""),
                                 keys.get("yt_secret", ""), interactive=False)
            live = privacy_of([r["video_id"] for r in rows], token)
        except (UploadFailed, Unreachable) as exc:
            print(f"warn     could not read the channel: {exc}")

    if args.box:
        # the live instance keeps its own receipts, in its job directories, so
        # a video it published is invisible to a listing of this checkout
        from . import deploy

        try:
            body = deploy.remote_api("/api/jobs", host=args.host or deploy.HOST,
                                     key=args.key or deploy.KEY)
        except deploy.DeployFailed as exc:
            print(f"warn     could not read the live box: {exc}")
            body = {}
        for render in body.get("renders") or []:
            posted = (render.get("youtube") or {}).get("video_id")
            if posted:
                rows.append({"project": "live box", "tag": render.get("aspect", ""),
                             "video_id": posted, "checked": "held on the box",
                             "cut": "", "moved": []})

    for row in sorted(rows, key=lambda r: (r["project"], r["tag"])):
        shape = row["tag"] or "16:9"
        state = live.get(row["video_id"])
        if args.live:
            said = state["privacy"] if state else "gone from the channel"
        else:
            said = row["checked"] or "checked, date unknown"
        if row.get("replaced_by"):
            now = f"{said}, " if args.live else ""
            when = f" on {row['until']}" if row.get("until") else ""
            said = f"{now}replaced by {row['replaced_by']}{when}"
        drift = f"  drift: {', '.join(row['moved'])}" if row["moved"] else ""
        print(f"{row['project']:<22} {shape:<6} {row['video_id']}  {said}{drift}")
    return 0


def cmd_upload(args) -> int:
    """Fill the upload form from the files the build already wrote.

    `check` runs first and refuses on any fault, because everything it looks for
    is a fault that is *worse* once published: a description naming photo-
    graphers who are not in the cut is a licence problem the moment it is public,
    and pulling a video back down does not unpublish it. --force exists for the
    case where the operator has read the problems and disagrees.

    The three files this sends are resolved by the same tag, so the description,
    the thumbnail and the captions all belong to the cut being uploaded. Reading
    them by their unsuffixed names is how a vertical upload ends up carrying the
    widescreen credits.
    """
    from .check import check
    from .published import record
    from .upload import UploadFailed, access_token, publish

    root = _project_dir(args.name)
    proj = Project(root)
    cfg = load_config(proj.config_path)
    if args.aspect:
        cfg.render.aspect = args.aspect
    tag = aspect_tag(cfg.render.aspect)

    cut = _delivery_file(proj, cfg, tag)
    if cut is None:
        print(f"nothing built for {cfg.render.aspect} yet - run: vidsmith build {args.name}")
        return 1

    problems = check(proj.out)
    if problems and not args.force:
        print(f"\n{len(problems)} problem(s) in {proj.out}, "
              "so nothing was uploaded:\n")
        for line in problems:
            print(f"  - {line}")
        print("\nfix them, or upload anyway with --force")
        return 1
    if problems:
        print(f"warn     uploading over {len(problems)} unresolved check problem(s)")

    meta_path = proj.out / "youtube.json"
    if not meta_path.exists():
        print(f"no {meta_path.name} - run: vidsmith meta {args.name}")
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    desc_path = proj.out / f"description{tag}.txt"
    if not desc_path.exists():
        print(f"no {desc_path.name} beside {cut.name}; its credits would not be published")
        return 1
    description = desc_path.read_text(encoding="utf-8")

    # Optional inputs are guarded on None, never on truth: Path("") is Path("."),
    # which is truthy and exists, and that spelling has bitten this project once
    # already by handing ffmpeg a directory where a subtitle file belonged.
    srt = proj.out / f"captions{tag}.srt"
    captions = None if args.no_captions or not srt.exists() else srt
    jpg = cut.with_suffix(".jpg")
    thumbnail = None if args.no_thumbnail or not jpg.exists() else jpg
    if captions is None and not args.no_captions:
        print(f"warn     no {srt.name}; YouTube will transcribe the audio itself")

    keys = find_keys(root)
    repo_root = Path(__file__).resolve().parent.parent
    try:
        token = access_token(repo_root, keys["yt_client"], keys["yt_secret"])
        vid = publish(cut, meta, description, token, thumbnail=thumbnail,
                      captions=captions, category=args.category,
                      privacy=args.privacy)
    except UploadFailed as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    receipt = record(proj.out, vid, tag=tag)
    print(f"receipt  {receipt.name} witnesses "
          f"description{tag}.txt and credits{tag}.txt")
    print(f"\nuploaded as {args.privacy}. When it is public, verify it:\n"
          f"  vidsmith check {args.name} --published {vid}")
    return 0


def cmd_deploy(args) -> int:
    """Deploy main to the live instance: idle check, pull, restart, verify."""
    from . import deploy

    try:
        deploy.deploy(host=args.host or deploy.HOST, key=args.key or deploy.KEY,
                      wait_minutes=args.wait, force=args.force)
    except deploy.DeployFailed as exc:
        print(f"\nnot deployed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_sheet(args) -> int:
    """A frame per shot beside the words spoken over it."""
    from . import sheet as sheet_mod

    root = _project_dir(args.name)
    try:
        page = sheet_mod.build_sheet(root, aspect=args.aspect or "")
    except sheet_mod.SheetFailed as exc:
        print(f"\nno sheet: {exc}", file=sys.stderr)
        return 1
    print(f"\nopen     {page}")
    return 0


def cmd_fetch(args) -> int:
    """Download a finished render off the live instance, retrying a bad line."""
    from . import deploy, fetch as fetch_mod
    from .config import env

    host = args.host or deploy.HOST
    token = fetch_mod.job_token(args.token, lambda name: env(name, *_dotenvs()))
    out = Path(args.out) if args.out else fetch_mod.default_out(args.job)
    print(f"fetch    {args.job} from {host}")
    try:
        written = fetch_mod.fetch(args.job, host, token, out, wait=not args.no_wait)
    except fetch_mod.FetchFailed as exc:
        print(f"\nnot fetched: {exc}", file=sys.stderr)
        return 1
    print(f"\ndone     {len(written)} files in {out}")
    return 0


def _dotenvs() -> List[Path]:
    here = Path.cwd()
    return [here / ".env", here.parent / ".env", Path(__file__).resolve().parent.parent / ".env"]


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
    print(pipeline.write_metadata(proj.out, meta, source=cfg.source))
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
    b.add_argument("--genre", choices=list(GENRES),
                   help="the kind of stock footage to search for")
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
    b.add_argument("--force", help="comma list of stages to redo: "
                                  "voice,visuals,render,diagrams")
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
    ck.add_argument("--aspect", choices=ASPECTS,
                    help="which cut --published names (default: the project's own)")
    ck.add_argument("--published", metavar="ID_OR_URL",
                    help="also read the live video and check the description, "
                         "chapters, tags and caption track against this build")
    ck.set_defaults(func=cmd_check)

    up = sub.add_parser("upload", help="upload a finished build to YouTube")
    up.add_argument("name")
    up.add_argument("--aspect", choices=ASPECTS,
                    help="which cut to upload (default: the project's own)")
    up.add_argument("--privacy", choices=("private", "unlisted", "public"),
                    default="private",
                    help="default private, so the listing can be read before "
                         "anyone else sees it")
    up.add_argument("--category", default="28",
                    help="YouTube category id, default 28 (Science & Technology)")
    up.add_argument("--no-captions", action="store_true",
                    help="do not upload the srt; YouTube will transcribe instead")
    up.add_argument("--no-thumbnail", action="store_true")
    up.add_argument("--force", action="store_true",
                    help="upload even though check reported problems")
    up.set_defaults(func=cmd_upload)

    rt = sub.add_parser("retake", help="search one scene's footage again, "
                                       "keeping the narration")
    rt.add_argument("name")
    rt.add_argument("--scene", type=int, required=True, metavar="N",
                    help="which scene to re-film, as the shot sheet numbers them")
    rt.add_argument("--shot", type=int, metavar="J",
                    help="change one shot of the scene and keep the rest; the "
                         "sheet's scene 1.2 is --scene 1 --shot 2")
    rt.add_argument("--clip", metavar="ID",
                    help="with --shot: the candidate to put there")
    rt.add_argument("--search", metavar="WORDS",
                    help="with --shot: look for candidates with these words instead")
    rt.add_argument("--provider", choices=("pexels", "pixabay", "cards", "local"),
                    help="default: the project's own")
    rt.add_argument("--genre", choices=sorted(GENRES),
                    help="default: the project's own")
    rt.set_defaults(func=cmd_retake)

    pb = sub.add_parser("publish", help="make an uploaded video visible, then "
                                        "check the visible copy")
    pb.add_argument("name")
    pb.add_argument("--video", metavar="ID_OR_URL",
                    help="default: the video named in out/published.json")
    pb.add_argument("--aspect", choices=ASPECTS,
                    help="which cut to publish (default: the project's own)")
    pb.add_argument("--privacy", choices=("public", "unlisted", "private"),
                    help="default public; private takes a video down and checks "
                         "nothing, since nobody can see it")
    pb.add_argument("--meta", action="store_true",
                    help="put this cut's title, description and tags on the video "
                         "it was uploaded as; refused if the cut has changed since")
    pb.add_argument("--force", action="store_true",
                    help="publish even though check reported problems")
    pb.set_defaults(func=cmd_publish)

    pl = sub.add_parser("published", help="list every video uploaded from here, "
                                          "with any drift since it was checked")
    pl.add_argument("name", nargs="?", help="one project; default: all of them")
    pl.add_argument("--box", action="store_true",
                    help="also list what the live instance uploaded (over ssh)")
    pl.add_argument("--host", default=None, help="default: VIDSMITH_HOST")
    pl.add_argument("--key", default=None, help="ssh key for --box")
    pl.add_argument("--live", action="store_true",
                    help="also ask YouTube what each one is now (1 quota unit per 50)")
    pl.set_defaults(func=cmd_published)

    d = sub.add_parser("doctor", help="check ffmpeg, edge-tts and API keys")
    d.set_defaults(func=cmd_doctor)

    dp = sub.add_parser("deploy", help="put main on the live box and prove it is live")
    dp.add_argument("--host", default=None, help="default: VIDSMITH_HOST or vidsmith.duckdns.org")
    dp.add_argument("--key", default=None, help="ssh key; default ~/.ssh/vidsmith-key.pem")
    dp.add_argument("--wait", type=float, default=0.0, metavar="MINUTES",
                    help="wait this long for a running render instead of refusing")
    dp.add_argument("--force", action="store_true",
                    help="deploy even when the box already runs main")
    dp.set_defaults(func=cmd_deploy)

    sp = sub.add_parser("sheet", help="a frame per shot beside the words spoken over it")
    sp.add_argument("name")
    sp.add_argument("--aspect", choices=sorted(ASPECTS), default=None,
                    help="which cut to read; default the project's own")
    sp.set_defaults(func=cmd_sheet)

    fp = sub.add_parser("fetch", help="download a finished render off the live box")
    fp.add_argument("job", help="the job id the page shows")
    fp.add_argument("--host", default=None, help="default: VIDSMITH_HOST or vidsmith.duckdns.org")
    fp.add_argument("--token", default=None,
                    help="the INSTANCE's token; default $VIDSMITH_TOKEN. Not a local .env one")
    fp.add_argument("--out", default=None, help="where to write; default jobs/<id>")
    fp.add_argument("--no-wait", action="store_true",
                    help="refuse a render still in progress instead of waiting for it")
    fp.set_defaults(func=cmd_fetch)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
