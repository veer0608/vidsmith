# Deploying

**There is a live instance, and it is the AWS one.** `vidsmith.duckdns.org`,
an EC2 Ubuntu 24.04 box with 2 vCPU and 2 GB, uvicorn on loopback behind Caddy
for TLS, run by a systemd unit called `vidsmith`. `deploy/aws.md` is the whole
of it, including how to get a shell, which is the part that used to be missing.

Three things about that box are worth knowing before touching it.

**The app is at `/home/ubuntu/vidsmith`**, which is `APP_DIR` in
`cloud-init.sh`. Not `/opt`. An afternoon went into a deploy against `/opt`
because nobody checked the doc that already said so.

**Drive it with `ssh host "commands"`, never an interactive session.** With a
session in one window and a local shell in another, commands meant for the
server get typed into the local one, which answers plausibly: a missing
directory, an unknown command, a commit hash from the wrong machine. Four
rounds of "ran it, here is the output" once came from a laptop while the server
was never touched. `hostname` first if working interactively, and **when a
remote fix reports success and the symptom does not move, check which machine
it ran on before theorising about the code.**

**The public endpoints are the honest witness.** `/healthz` should list the two
DejaVu faces and `/api/busy` should carry `waiting`. Both are one request, both
are unauthenticated, and between them they have caught every deploy here that
reported success and had changed nothing.

**Deploy with `vidsmith deploy`.** It reads `main`'s sha from origin, stops if
`/healthz` already reports it, refuses while `/api/busy` says a render is running
(`--wait MINUTES` waits instead), runs the line below over ssh with `hostname`
first so the output names the machine, and does not return until the public
`/healthz` reports the new commit with ffmpeg and both fonts and `/api/busy`
answers. An ssh timeout comes back as the security group, with your current
address from checkip as the `/32` the rule wants and a link straight to the EC2
security groups in `ap-south-1` - the console opened on "Global" shows none, and
a search for "security" lands on IAM, both of which happened; refused, a bad key
and a missing key file are each named. The checks after the restart retry four
times, because on a connection that drops a deploy that had worked reported
failure twice in one evening. `VIDSMITH_HOST`, `VIDSMITH_SSH_KEY` and
`VIDSMITH_AWS_REGION` override the defaults.

**A deploy also says whether the page can upload.** The box ran for weeks
with no YouTube client at all and nothing reported it; the fault would have
landed as `redirect_uri_mismatch` in front of whoever first published from the
page. `deploy` asks the box its own `/api/youtube` over loopback, so the token
is read there and never travels, and reports ready, not configured, or not
connected. A client whose redirect is not `https://<host>/api/youtube/callback`
fails the deploy, because consent will be refused at Google.

The probe sends `Host:` and `X-Forwarded-Proto: https`, which Caddy sends in
ordinary traffic and uvicorn trusts from loopback. Without them the route builds
the redirect from the loopback request, answers `http://127.0.0.1:8077/...`, and
the check failed a healthy box on its first real run.

**Bring a finished render down with `vidsmith fetch <job>`.** It waits for a
render still in progress, retries each file, and writes through a `.part` file
so a dropped line never leaves half an mp4 that looks finished; three downloads
died mid-file in one evening while the server held a good mp4 each time. The
token is **the instance's**, read from `/home/ubuntu/vidsmith/.env`, not the one
in a local `.env`, and a 401 says so.

The line it runs, for when it cannot. The restart is what sweeps orphaned job
directories, so it cleans up on the way in. Finished renders survive it, but a
render still in flight does not, so check `/api/busy` first:

```bash
ssh -t -i ~/.ssh/vidsmith-key.pem ubuntu@vidsmith.duckdns.org "cd vidsmith; git pull --ff-only; bash scripts/fetch-runtime-deps.sh --fonts-only; sudo systemctl daemon-reload; sudo systemctl restart vidsmith"
```

SSH is restricted to one address and a home connection's address changes on its
own, so a timeout is the security group needing your current IP rather than a
dead box. A refused connection is a different fault, and so is a key error.

The other three ways out, and the first is usually right for showing someone.

**A Cloudflare quick tunnel** (`scripts/serve-public.ps1`) puts the local server
on a public URL: free, no account, no domain, and `cloudflared` from winget. The
render happens on this machine, so it runs at full local speed instead of a
hosted instance's fraction of a CPU. The URL lasts only as long as the window,
which is the point when the audience is one person for ten minutes.

The two real hosts solve the ffmpeg problem differently.

**Render** (`render.yaml`) uses the *native Python runtime rather than a
container*, because ffmpeg and the fonts are fetched in the build step by
`scripts/fetch-runtime-deps.sh`. Encoding is CPU-bound, so the free instance runs
10 to 15 minutes for a 90 second video and can exhaust memory at 1080p; the
blueprint therefore asks for `starter` and holds `VIDSMITH_MAX_MINUTES` at 2,
with jobs in `/tmp`. `autoDeploy` is off, and the keys are `sync: false` so they
are set in the dashboard and never committed.

**Hugging Face Spaces** (`deploy/huggingface.md`) is the better machine but no
longer free: since 2026-08-25 a Docker Space on free cpu-basic is refused with
`402 Payment Required` and needs PRO. Its 2 vCPU and 16 GB do encode 1080p where
a 512 MB instance does not. It builds the `Dockerfile` on their side, so this stays true even
though Docker cannot run on this machine. Keys go in Space secrets, and
`/healthz` is the check that matters after a build: `fonts` should list the two
DejaVu files, and `keys`, which needs the token, should show `gemini` and
`pexels` true. A free Space
sleeps after an idle stretch and takes a minute to wake.
