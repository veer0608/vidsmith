"""Put `main` on the live box, and prove it got there.

Every PR here ended with the same pasted ssh line and the same three manual
checks afterwards, and each of the ways it goes wrong has already happened:

- a deploy that restarted the service in the middle of someone's render;
- an ssh that timed out because a home connection's address had moved, read as
  a dead box;
- a "successful" deploy that changed nothing, because the commands ran on the
  wrong machine or the pull did not land.

So this checks the box is idle before touching it, names the firewall and your
current address when ssh times out, prints the hostname the commands actually
ran on, and does not call it done until `/healthz` on the public URL reports the
commit that was pushed. The public endpoints are the witness, not the restart.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

HOST = os.environ.get("VIDSMITH_HOST", "vidsmith.duckdns.org")
USER = os.environ.get("VIDSMITH_SSH_USER", "ubuntu")
KEY = os.environ.get("VIDSMITH_SSH_KEY", "~/.ssh/vidsmith-key.pem")
# the one line deploy/aws.md documents, with `hostname` first so the output
# says which machine it ran on
REMOTE = ("hostname; cd vidsmith; git fetch origin; git checkout main; "
          "git pull --ff-only; git log --oneline -1; "
          "bash scripts/fetch-runtime-deps.sh --fonts-only; "
          "sudo systemctl daemon-reload; sudo systemctl restart vidsmith; "
          "systemctl is-active vidsmith")
# Asked on the box, over loopback, because /api/youtube is behind the token and
# the token has no business travelling to a laptop to answer a health question.
# Printed last on its own line, so the parsing above it is unchanged.
# `/api/youtube` builds the redirect from the request it is answering, so a
# bare loopback call reports `http://127.0.0.1:8077/...` and comparing that to
# the public URL fails a healthy box - which it did, on the first real run.
# Caddy sends these two headers in ordinary traffic and uvicorn trusts them
# from loopback, so the probe sends them too and gets the public answer.
def youtube_probe(host: str) -> str:
    return (
        "; sleep 3; printf 'youtube:'; curl -s -m 10 "
        "-H \"x-vidsmith-token: $(grep -m1 '^VIDSMITH_TOKEN=' .env | cut -d= -f2-)\" "
        f"-H \"Host: {host}\" -H 'X-Forwarded-Proto: https' "
        "http://127.0.0.1:8077/api/youtube; echo")


def youtube_state(stdout: str) -> Optional[Dict[str, Any]]:
    """What the box says about uploading, or None when it did not answer."""
    for line in (stdout or "").splitlines():
        if line.startswith("youtube:"):
            try:
                body = json.loads(line[len("youtube:"):] or "{}")
            except ValueError:
                return None
            return body if isinstance(body, dict) else None
    return None


def youtube_report(state: Optional[Dict[str, Any]], host: str) -> tuple:
    """A line to log and a problem to raise, for uploading from the page.

    The live box ran for weeks with no YouTube client at all, and nothing said
    so: the fault would have surfaced as `redirect_uri_mismatch` in front of
    whoever first tried to publish from the page. A wrong redirect is the same
    shape, so it is a problem rather than a line, but only when a client is
    configured - a box that does no uploading is a legitimate box.
    """
    if state is None:
        return "uploading from the page: the box did not answer", ""
    if not state.get("configured"):
        return ("uploading from the page: no YouTube client on the box "
                "(set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in its .env)"), ""
    wanted = f"https://{host}/api/youtube/callback"
    live = state.get("redirect_uri") or ""
    if live != wanted:
        return "", (f"its YouTube client redirects to {live or 'nothing'}, not "
                    f"{wanted}, so Google will refuse the consent")
    if not state.get("connected"):
        return ("uploading from the page: configured, not connected yet "
                "(POST /api/youtube/connect and allow it)"), ""
    return "uploading from the page: ready", ""
CHECK_IP = "https://checkip.amazonaws.com"
# The box is in Mumbai. A console opened on "Global" shows no security groups at
# all, and the IAM page is where a search for "security" lands: both happened
# while the port 22 rule was waiting to be changed.
REGION = os.environ.get("VIDSMITH_AWS_REGION", "ap-south-1")


def security_groups_url(region: str = REGION) -> str:
    return (f"https://{region}.console.aws.amazon.com/ec2/home"
            f"?region={region}#SecurityGroups:")


class DeployFailed(RuntimeError):
    """A deploy that did not happen or did not land, with what to do about it."""


Run = Callable[..., subprocess.CompletedProcess]
Get = Callable[..., Any]


def target_commit(run: Run = subprocess.run) -> str:
    """The short sha of `main` on GitHub, which is what the box will pull."""
    result = run(["git", "ls-remote", "origin", "refs/heads/main"],
                 capture_output=True, text=True, timeout=60)
    sha = (result.stdout or "").split()[0] if (result.stdout or "").strip() else ""
    if result.returncode != 0 or len(sha) < 7:
        raise DeployFailed("could not read main from origin: "
                           f"{(result.stderr or '').strip() or 'no output'}")
    return sha[:7]


def _json(get: Get, url: str) -> Optional[Dict[str, Any]]:
    try:
        r = get(url, timeout=20)
        if getattr(r, "status_code", 0) != 200:
            return None
        body = r.json()
        return body if isinstance(body, dict) else None
    except (requests.RequestException, ValueError):
        return None


def _json_settled(get: Get, url: str, sleep: Callable[[float], None],
                  tries: int = 4, pause: float = 5.0) -> Optional[Dict[str, Any]]:
    """Read a public endpoint, allowing for a connection that drops.

    Only for the checks that run *after* the restart, where a miss is read as a
    broken deploy. A slow home connection timed out on `/api/busy` seconds
    after `/healthz` had confirmed the new commit, and a deploy that had
    entirely worked reported failure. The earlier probes stay single-shot:
    there a miss means "not live yet", which the loops already handle.
    """
    for attempt in range(tries):
        body = _json(get, url)
        if body is not None:
            return body
        if attempt < tries - 1:
            sleep(pause)
    return None


def _current_ip(get: Get) -> str:
    try:
        return get(CHECK_IP, timeout=10).text.strip()
    except requests.RequestException:
        return ""


def ssh_failure(stderr: str, get: Get, host: str, key: str) -> str:
    """What an ssh failure means, in the terms deploy/aws.md already uses."""
    text = (stderr or "").lower()
    if "timed out" in text or "operation timed out" in text:
        ip = _current_ip(get)
        return ("ssh timed out, which is the security group rather than a dead box: "
                "port 22 only admits one address, and yours has probably changed. "
                "Open the EC2 security groups (not IAM) in "
                f"{REGION}:\n\n  {security_groups_url()}\n\n"
                "and set the SSH inbound rule's source to My IP"
                + (f" ({ip}/32)" if ip else "") + ", then run this again.")
    if "connection refused" in text:
        return f"{host} refused the connection: the firewall let you in and sshd is not running."
    if "permission denied" in text:
        return f"{host} rejected the key {key}: you are reaching the box and failing to log in."
    if "no such file" in text or "not accessible" in text:
        return f"the ssh key {key} is not there."
    return f"ssh failed: {(stderr or '').strip()[-400:] or 'no output'}"


def deploy(host: str = HOST, user: str = USER, key: str = KEY,
           wait_minutes: float = 0.0, force: bool = False,
           log: Callable[[str], None] = print, run: Run = subprocess.run,
           get: Get = requests.get, sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.time, settle_seconds: float = 120.0) -> str:
    """Deploy `main` to `host` and return the commit now live there."""
    base = f"https://{host}"
    target = target_commit(run)
    health = _json(get, f"{base}/healthz") or {}
    live = health.get("commit", "")
    log(f"main is {target}; {host} is running {live or 'something that did not answer'}")
    if live == target and not force:
        log("already live; nothing to deploy")
        return live

    # A restart takes the render in flight with it, so wait for the slot or refuse.
    deadline = clock() + wait_minutes * 60
    while True:
        busy = _json(get, f"{base}/api/busy")
        if not busy or not busy.get("busy"):
            break
        if clock() >= deadline:
            stage = busy.get("stage") or "working"
            raise DeployFailed(f"a render is running ({stage}, {busy.get('waiting', 0)} "
                               "waiting); a restart would kill it. Try again later, "
                               "or pass --wait MINUTES")
        log(f"waiting for the render in progress ({busy.get('stage') or 'working'})")
        sleep(15)

    log(f"deploying to {user}@{host}")
    try:
        result = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                      "-i", str(Path(key).expanduser()), f"{user}@{host}", REMOTE + youtube_probe(host)],
                     capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise DeployFailed("ssh connected but the deploy ran past ten minutes")
    except FileNotFoundError:
        raise DeployFailed("there is no ssh on PATH")
    if result.returncode != 0:
        raise DeployFailed(ssh_failure(result.stderr, get, host, key))
    lines: List[str] = [line for line in (result.stdout or "").splitlines()
                       if line.strip() and not line.startswith("youtube:")]
    if lines:
        # the machine it ran on, first, so a deploy to the wrong box cannot pass for one
        log(f"ran on {lines[0]}; service {lines[-1]}")

    # Not done until the public URL says so: a restart can report active while
    # the process is still loading, or while an old one still answers.
    until = clock() + settle_seconds
    while True:
        health = _json(get, f"{base}/healthz") or {}
        if health.get("commit") == target:
            break
        if clock() >= until:
            raise DeployFailed(f"the service restarted but {base}/healthz reports "
                               f"{health.get('commit') or 'nothing'}, not {target}")
        sleep(3)
    busy = _json_settled(get, f"{base}/api/busy", sleep) or {}
    problems = []
    if not health.get("ok"):
        problems.append(f"healthz is not ok: {health.get('ffmpeg', 'no reason given')}")
    if len(health.get("fonts") or []) < 2:
        problems.append(f"healthz lists fonts {health.get('fonts')}, not the two DejaVu faces")
    if "waiting" not in busy:
        problems.append("/api/busy did not answer with a waiting count")
    note, refused = youtube_report(youtube_state(result.stdout), host)
    if refused:
        problems.append(refused)
    if problems:
        raise DeployFailed(f"{target} is live, but: " + "; ".join(problems))
    log(f"live: {base} is running {target}, with ffmpeg and both fonts")
    if note:
        log(note)
    return target
