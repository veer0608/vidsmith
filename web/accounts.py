"""People who asked for access, and the tokens the owner gave them.

The instance token (`VIDSMITH_TOKEN`) is the owner's. Everyone else registers,
waits for the owner to approve, and is handed a token of their own with a daily
cap on renders, so one person cannot use up the shared free-tier budget.

State is one JSON file beside the jobs, `_accounts.json`; the leading underscore
is what keeps the job sweep from treating it as a dead render. A member's token
is stored only as a hash. The plaintext exists from approval until the person
collects it from their status link, once, and is deleted when they do.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Registrations waiting at once. A public form with no bound is a way to fill the
# disk and the owner's inbox, so a full list refuses until something is decided.
MAX_PENDING = 30
# Renders one member may start per UTC day.
DAILY_RENDERS = int(os.environ.get("VIDSMITH_DAILY_RENDERS", "3"))
# Topic drafts one member may ask for per UTC day. Each is a Gemini call on the
# owner's free tier, and a draft costs nothing to ask for again, so it is capped
# on its own rather than left to the render cap.
DAILY_DRAFTS = int(os.environ.get("VIDSMITH_DAILY_DRAFTS", "5"))

# what each kind of spending is called in a request's row
_KINDS = {"render": "count", "draft": "drafts"}

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


class Refused(Exception):
    """The request cannot be taken; the message says why, for the person."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class Accounts:
    def __init__(self, workdir: Path) -> None:
        self.path = Path(workdir) / "_accounts.json"
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"requests": {}, "owners": {}}
        try:
            self._data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass                       # first run, or an unreadable file: start empty

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- the person asking -------------------------------------------------- #
    def register(self, name: str, email: str, note: str = "") -> str:
        """Record a request and return the secret that lets them check on it."""
        name, email, note = name.strip(), email.strip().lower(), note.strip()
        if not name or len(name) > 80:
            raise Refused("Add your name, up to 80 characters.")
        if not _EMAIL.match(email):
            raise Refused("That does not look like an email address.")
        with self._lock:
            reqs = self._data["requests"]
            if any(r["email"] == email and r["status"] != "denied" for r in reqs.values()):
                raise Refused("That email has already asked. Use the link you were "
                              "given to check on it.")
            if sum(1 for r in reqs.values() if r["status"] == "pending") >= MAX_PENDING:
                raise Refused("Too many requests are waiting. Try again later.")
            secret = secrets.token_urlsafe(18)
            reqs[_hash(secret)] = {"id": secrets.token_hex(4), "name": name,
                                   "email": email, "note": note[:300],
                                   "status": "pending", "asked": int(time.time()),
                                   "token_hash": "", "pending_token": "",
                                   "day": "", "count": 0, "drafts": 0}
            self._save()
            return secret

    def check(self, secret: str) -> Dict[str, Any]:
        """Where a request stands. An approved one hands over its token, once."""
        with self._lock:
            req = self._data["requests"].get(_hash(secret))
            if req is None:
                raise Refused("No such request.")
            body: Dict[str, Any] = {"status": req["status"], "name": req["name"]}
            if req["status"] == "approved" and req["pending_token"]:
                body["token"] = req["pending_token"]
                req["pending_token"] = ""
                self._save()
            elif req["status"] == "approved":
                body["collected"] = True
            return body

    # -- the owner ---------------------------------------------------------- #
    def listing(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"id": r["id"], "name": r["name"], "email": r["email"],
                     "note": r["note"], "status": r["status"], "asked": r["asked"],
                     "today": r["count"] if r["day"] == _today() else 0,
                     "drafts": r.get("drafts", 0) if r["day"] == _today() else 0}
                    for r in sorted(self._data["requests"].values(),
                                    key=lambda r: r["asked"], reverse=True)]

    def _find(self, request_id: str) -> Optional[Dict[str, Any]]:
        for req in self._data["requests"].values():
            if req["id"] == request_id:
                return req
        return None

    def pending(self) -> int:
        with self._lock:
            return sum(1 for r in self._data["requests"].values()
                       if r["status"] == "pending")

    def reissue(self, request_id: str) -> str:
        """A new token for an approved member who lost theirs; the old one stops.

        Returned for the owner to send on as a login link, and also left for the
        person's status link, in case they still have that.
        """
        with self._lock:
            req = self._find(request_id)
            if req is None or req["status"] != "approved":
                raise Refused("Only an approved member can be given a new token.")
            token = secrets.token_urlsafe(24)
            req.update(token_hash=_hash(token), pending_token=token)
            self._save()
            return token

    def decide(self, request_id: str, approve: bool) -> Dict[str, Any]:
        with self._lock:
            req = self._find(request_id)
            if req is None:
                raise Refused("No such request.")
            if approve and req["status"] != "approved":
                token = secrets.token_urlsafe(24)
                req.update(status="approved", token_hash=_hash(token),
                           pending_token=token)
            elif not approve:
                # also how a member is switched off: the hash is dropped, so the
                # token stops working at once
                req.update(status="denied", token_hash="", pending_token="")
            self._save()
            return {"id": req["id"], "status": req["status"]}

    # -- a member's token --------------------------------------------------- #
    def member(self, token: str) -> Optional[str]:
        """The request id this token belongs to, or None."""
        if not token:
            return None
        digest = _hash(token)
        with self._lock:
            for req in self._data["requests"].values():
                if req["token_hash"] and secrets.compare_digest(req["token_hash"], digest):
                    return req["id"]
        return None

    def _cap(self, kind: str) -> int:
        return DAILY_RENDERS if kind == "render" else DAILY_DRAFTS

    def _roll(self, req: Dict[str, Any]) -> None:
        """Start the day's counts again when the stored day is not today."""
        if req["day"] != _today():
            req["day"], req["count"], req["drafts"] = _today(), 0, 0

    def spend(self, member: str, kind: str = "render") -> int:
        """Count one render or draft against today's cap; raise when it is spent."""
        field, cap = _KINDS[kind], self._cap(kind)
        with self._lock:
            req = self._find(member)
            if req is None or req["status"] != "approved":
                raise Refused("This access has ended.")
            self._roll(req)
            if req.get(field, 0) >= cap:
                noun = "renders" if kind == "render" else "drafts"
                raise Refused(f"You have used your {cap} {noun} for today. "
                              "The count resets at midnight UTC.")
            req[field] = req.get(field, 0) + 1
            self._save()
            return cap - req[field]

    def refund(self, member: str, kind: str = "render") -> None:
        """Give one back when the thing it paid for never happened."""
        field = _KINDS[kind]
        with self._lock:
            req = self._find(member)
            if req is not None and req["day"] == _today() and req.get(field, 0) > 0:
                req[field] -= 1
                self._save()

    def left(self, member: str) -> Dict[str, int]:
        """Renders and drafts still available today."""
        with self._lock:
            req = self._find(member)
            if req is None:
                return {"renders": 0, "drafts": 0}
            today = req["day"] == _today()
            return {"renders": DAILY_RENDERS - (req["count"] if today else 0),
                    "drafts": DAILY_DRAFTS - (req.get("drafts", 0) if today else 0)}

    # -- who made which render --------------------------------------------- #
    def own(self, job_id: str, member: str) -> None:
        with self._lock:
            self._data["owners"][job_id] = member
            self._save()

    def owns(self, job_id: str, member: str) -> bool:
        with self._lock:
            return self._data["owners"].get(job_id) == member

    def jobs_of(self, member: str) -> set:
        with self._lock:
            return {j for j, m in self._data["owners"].items() if m == member}
