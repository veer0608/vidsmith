# Hosting vidsmith on Hugging Face Spaces

> **This is no longer free.** As of 2026-08-25 creating a Docker Space returns
> `402 Payment Required`: "Static Spaces are free for everyone, but hosting
> Gradio and Docker Spaces on free cpu-basic requires a PRO subscription."
> vidsmith needs Docker because it needs ffmpeg, so this route now costs a PRO
> subscription. Everything below still applies once you have one.

The CPU tier is **2 vCPU and 16 GB RAM**, enough to encode 1080p, which a
512 MB instance is not. `apt` is available, so ffmpeg and the fonts install as
packages rather than being fetched as static builds.

The Space builds the `Dockerfile` itself. Nothing below asks you to run Docker
on your own machine.

## Once

1. Create a free account at huggingface.co.
2. **New → Space.** Name it `vidsmith`, SDK **Docker**, hardware **CPU basic
   (free)**, and set visibility to **Private** (see the warning below). Creating
   it through the UI matters: the Space is a git repo and HF seeds its
   `README.md` with the front matter that declares the SDK and port.
3. In **Settings → Variables and secrets**, add two *secrets*:
   `PEXELS_API_KEY` and `GEMINI_API_KEY`.
4. Clone the Space and copy the code into it, keeping the README HF generated:

```powershell
cd ~/claude; git clone https://huggingface.co/spaces/YOURNAME/vidsmith hf-vidsmith
```

```powershell
robocopy vidsmith hf-vidsmith /E /XD .git .venv bin jobs projects assets build /XF .env
```

5. Confirm the Space README still starts with its front matter, then set
   `app_port` to 7860 in it if HF did not, and push:

```powershell
cd ~/claude/hf-vidsmith; git add -A; git commit -m "vidsmith"; git push
```

The first build takes a few minutes. Then check it before rendering anything:

```
https://YOURNAME-vidsmith.hf.space/healthz
```

`fonts` should list the two DejaVu files and `keys` should show `gemini` and
`pexels` as true. If a key reads false, the secret name is wrong.

## What to expect

- **Speed.** Two vCPUs is roughly half a modern laptop, so budget about twice
  your local render time. A 90 second video lands around five minutes.
- **Sleeping.** A free Space pauses after a long idle stretch and takes a minute
  to wake. A render already running when it pauses is lost, because the job queue is in
  memory.
- **One at a time.** The queue depth is one by design; a second caller gets a
  429 rather than both renders crawling.

## The warning worth reading

**A public Space is a public renderer.** Anyone who finds the URL can start
jobs, and those jobs spend *your* Pexels and Gemini quota. There is no auth in
front of it.

Make the Space **private** unless you specifically want it public. If you do
want it public, put something in front of it first: at minimum a shared secret
checked in the API, and a per-day render cap.
