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
CHECK_IP = "https://checkip.amazonaws.com"


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
                "In the EC2 console set the inbound rule for port 22 to My IP"
                + (f" (you are {ip} now)" if ip else "") + ", then run this again.")
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
                      "-i", str(Path(key).expanduser()), f"{user}@{host}", REMOTE],
                     capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise DeployFailed("ssh connected but the deploy ran past ten minutes")
    except FileNotFoundError:
        raise DeployFailed("there is no ssh on PATH")
    if result.returncode != 0:
        raise DeployFailed(ssh_failure(result.stderr, get, host, key))
    lines: List[str] = [line for line in (result.stdout or "").splitlines() if line.strip()]
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
    busy = _json(get, f"{base}/api/busy") or {}
    problems = []
    if not health.get("ok"):
        problems.append(f"healthz is not ok: {health.get('ffmpeg', 'no reason given')}")
    if len(health.get("fonts") or []) < 2:
        problems.append(f"healthz lists fonts {health.get('fonts')}, not the two DejaVu faces")
    if "waiting" not in busy:
        problems.append("/api/busy did not answer with a waiting count")
    if problems:
        raise DeployFailed(f"{target} is live, but: " + "; ".join(problems))
    log(f"live: {base} is running {target}, with ffmpeg and both fonts")
    return target
