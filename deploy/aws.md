# Hosting vidsmith on AWS

The only host here that gives a **stable URL without your own machine being on**.
A Cloudflare quick tunnel dies with its window, and Hugging Face now wants PRO,
so this is the route when the link has to keep working.

Nothing below needs Docker. `scripts/fetch-runtime-deps.sh` exists for hosts with
no package manager, but a plain Ubuntu box has `apt`, so ffmpeg and the fonts
install as packages.

## Before you launch anything: the billing trap

Credits are a **deadline, not a balance**. They usually expire twelve months from
the grant, and when they run out the account bills your card without asking.

Do these two things first, not after:

1. **Billing → Budgets → Create budget.** A cost budget with an alert at a few
   dollars. Note that while credits are covering the bill your net cost sits at
   zero, so the alert stays quiet right up until the credits are gone and then
   fires on real money. That is what you want, but it means silence is not
   evidence you are safe.
2. **Billing → Credits.** Write down the expiry date. That date, not the
   balance, is what decides how long this runs.

An Elastic IP is free **only while attached to a running instance**. Stop the
instance for a month and forget the IP, and you are paying for the IP.

## What to run it on

Encoding is CPU-bound, but the box is idle almost all the time, which is what
burstable instances are for: they bank CPU credits while nothing is happening and
spend them during a render.

| | spec | roughly | notes |
| --- | --- | --- | --- |
| **Lightsail 2 GB** | 2 vCPU, 2 GB | $12/mo flat | transfer included, simplest firewall |
| EC2 t3.small | 2 vCPU, 2 GB | ~$15/mo + transfer | itemised billing |
| EC2 t3.medium | 2 vCPU, 4 GB | ~$30/mo + transfer | headroom for 1080p |

**Do not use a 1 GB free-tier instance.** A 1080p x264 encode will run out of
memory on it; a 512 MB Render instance already does, which is why `render.yaml`
holds `VIDSMITH_MAX_MINUTES` at 2. Two gigabytes is the floor, and on 2 GB add
swap (below) so a long encode cannot be killed halfway.

Prices are indicative. Check the calculator for your region before committing.

## Install

Everything below is in `scripts/cloud-init.sh`. Paste that into **User data** on
EC2, or **Launch script** on Lightsail, and the box builds itself while it boots:
packages, swap, the checkout, the venv, the systemd unit and the proxy. It is
written to contain no secrets, because user data is readable from the instance
metadata endpoint and visible in the console. Keys go in `.env` afterwards.

If it did not come up, `/var/log/vidsmith-setup.log` says why. The manual steps
are kept below so the script is readable rather than magic.


Ubuntu 24.04 LTS. `apt` gives you ffmpeg with libass, which matters: a build
without it has no `subtitles` filter at all, and ffmpeg reports that as
`No option name near <path>`, which reads exactly like a quoting fault.

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg python3-venv python3-pip git fonts-dejavu-core
```

```bash
git clone https://github.com/veer0608/vidsmith.git ~/vidsmith
```

```bash
cd ~/vidsmith && python3 -m venv .venv && .venv/bin/pip install -r requirements-web.txt
```

Confirm the box can actually do the work before going further. This reports the
ffmpeg it found, whether libass is present, and which keys resolved:

```bash
cd ~/vidsmith && .venv/bin/python -m vidsmith doctor
```

### Swap, on a 2 GB instance

An encode that is killed by the OOM reaper looks like a crash with no
explanation. Two gigabytes of swap costs disk and removes that failure.

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

```bash
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Keys and the token

Create `~/vidsmith/.env`. It is gitignored, and `pipeline.find_keys()` reads it.
Put your real values in it with an editor rather than echoing them into a shell,
so the keys do not land in `~/.bash_history`:

```
GEMINI_API_KEY=
PEXELS_API_KEY=
VIDSMITH_TOKEN=
```

**`VIDSMITH_TOKEN` is not optional here.** Unlike the tunnel, this URL does not
die when you close a window. Without a token anyone who finds it can start
renders, and every render spends your Pexels and Gemini quota: 200 Pexels
requests an hour, 500 Gemini requests a day, against one key no matter how many
people are rendering.

Generate one on the box and paste it into `.env`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(18))"
```

## Run it as a service

Renders take minutes and the queue lives in memory, so the process has to
survive your SSH session closing and come back after a reboot.

Write `/etc/systemd/system/vidsmith.service`:

```ini
[Unit]
Description=vidsmith
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/vidsmith
Environment=VIDSMITH_MAX_MINUTES=3
Environment=VIDSMITH_JOBS=/home/ubuntu/vidsmith/jobs
ExecStart=/home/ubuntu/vidsmith/.venv/bin/uvicorn web.app:app --host 127.0.0.1 --port 8077 --timeout-keep-alive 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`--host 127.0.0.1` on purpose: the app listens only on loopback and the reverse
proxy below is the only thing exposed. Binding it to `0.0.0.0` would put an
unencrypted service on the public internet, and the token travels in the URL for
media and download links.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now vidsmith && sudo systemctl status vidsmith --no-pager
```

`VIDSMITH_MAX_MINUTES=3` because two vCPUs encode at roughly half realtime: a
three minute script is something like six minutes of work. Raise it only after
watching one finish.

## TLS, and why plain HTTP will not do

The page accepts its token as a `?t=` query parameter, because a `<video>`
element cannot set a header. Over plain HTTP that token crosses the network in
cleartext, and it is the only thing standing between a stranger and your API
quota.

Caddy gets a certificate automatically, so this is the whole configuration.
Point a domain's A record at the instance IP first, then write `/etc/caddy/Caddyfile`:

```
vidsmith.YOUR-DOMAIN.example {
    reverse_proxy 127.0.0.1:8077
}
```

```bash
sudo apt-get install -y caddy && sudo systemctl restart caddy
```

Without a domain you have an IP and no certificate. That is acceptable for ten
minutes of testing and not for a link you hand out.

## Firewall

Open 80 and 443 only. Port 8077 must **not** be reachable from outside; that is
the whole point of binding uvicorn to loopback.

On Lightsail this is the Networking tab. On EC2 it is the security group. Leave
SSH restricted to your own address rather than open to the world.

## Check it before trusting it

On the instance, where the address is the same for everyone:

```bash
curl -s http://127.0.0.1:8077/healthz
```

`ok` should be true and `ffmpeg` should be a path.

`keys` appears only when you pass the token. It is an inventory of which
credentials this box holds, AWS included once the polly voice is configured, and
a stranger who found the URL has no business reading it. Send your own token as
an `X-Vidsmith-Token` header to see the field. If a key then reads false, the
name in `.env` is wrong rather than the value.

Then from your own machine, against the real hostname, which also proves the
proxy and the certificate work rather than just the app.

## What to expect

- **Speed.** Two vCPUs is roughly half a modern laptop, so budget about twice
  your local render time. Burst credits cover an occasional render comfortably;
  a queue of them back to back will exhaust the credits and then crawl.
- **One at a time.** Queue depth is one by design, so a second caller gets a 429
  rather than both renders starving. `GET /api/busy` is unguarded so the page can
  say the box is working before someone writes a script.
- **Jobs are swept an hour after they finish.** Anything worth keeping has to be
  downloaded or copied into `projects/`.
- **A reboot loses the queue,** which lives in memory. `Restart=always` brings
  the service back; it does not bring back a render that was in flight.

## The thing this does not solve

A visitor will not wait ten minutes for a render. If the goal is a portfolio
link, the page with finished videos on it is what people actually look at, and
this instance is for the one visitor who wants to run it themselves. Deploying
this instead of publishing the output is the expensive way to show nobody
anything.
