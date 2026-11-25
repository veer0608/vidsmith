"""An email to the owner when someone asks for access.

Until this, a request was seen only as the "N waiting for access" pill on the
owner's own page, so a person could wait days for an approval nobody knew was
due. Plain SMTP, so it works with any mailbox (Gmail with an app password,
Outlook, a provider's relay) and needs no new account or library.

Off unless every setting is present, and never in the way of the request: the
mail goes on a background thread, and a mail server that is down or refuses the
login is logged and forgotten. The person registering is told they are waiting
either way, because they are.

Settings, from the environment or the instance's `.env`, like the token:

  VIDSMITH_ALERT_TO        where the alert goes
  VIDSMITH_SMTP_HOST       e.g. smtp.gmail.com
  VIDSMITH_SMTP_PORT       587 (STARTTLS) unless set; 465 means implicit TLS
  VIDSMITH_SMTP_USER       the mailbox that sends it
  VIDSMITH_SMTP_PASSWORD   for Gmail, an app password, never the account's own
  VIDSMITH_SMTP_FROM       optional; the user when unset
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Optional, Sequence

from vidsmith.config import env

log = logging.getLogger("vidsmith.alerts")

NAMES = ("VIDSMITH_ALERT_TO", "VIDSMITH_SMTP_HOST", "VIDSMITH_SMTP_PORT",
         "VIDSMITH_SMTP_USER", "VIDSMITH_SMTP_PASSWORD", "VIDSMITH_SMTP_FROM")
TIMEOUT = 20


@dataclass
class Mail:
    to: str
    host: str
    port: int
    user: str
    password: str
    sender: str


def settings(dotenvs: Sequence[Path] = ()) -> Optional[Mail]:
    """The mail settings, or None when any required one is missing."""
    got = {name: env(name, *dotenvs) for name in NAMES}
    if not all(got[n] for n in ("VIDSMITH_ALERT_TO", "VIDSMITH_SMTP_HOST",
                                "VIDSMITH_SMTP_USER", "VIDSMITH_SMTP_PASSWORD")):
        return None
    try:
        port = int(got["VIDSMITH_SMTP_PORT"] or 587)
    except ValueError:
        port = 587
    return Mail(to=got["VIDSMITH_ALERT_TO"], host=got["VIDSMITH_SMTP_HOST"], port=port,
                user=got["VIDSMITH_SMTP_USER"], password=got["VIDSMITH_SMTP_PASSWORD"],
                sender=got["VIDSMITH_SMTP_FROM"] or got["VIDSMITH_SMTP_USER"])


def _one_line(text: str, limit: int) -> str:
    """Anything a stranger typed, made safe to put in a header: one line, short."""
    return re.sub(r"[\r\n\t]+", " ", text or "").strip()[:limit]


def message(mail: Mail, name: str, email: str, note: str, admin_url: str,
            waiting: int) -> EmailMessage:
    msg = EmailMessage()
    # The name is the requester's own text, so it is flattened before it goes
    # anywhere near a header: a newline in a Subject is a header injection.
    msg["Subject"] = f"vidsmith: {_one_line(name, 60)} asked for access"
    msg["From"] = mail.sender
    msg["To"] = mail.to
    lines = [f"{_one_line(name, 80)} <{_one_line(email, 254)}> asked for access to vidsmith.",
             ""]
    if note.strip():
        lines += ["They said:", note.strip()[:300], ""]
    lines += [f"Approve or deny it at {admin_url}",
              "",
              f"{waiting} request{'s' if waiting != 1 else ''} waiting in all."]
    msg.set_content("\n".join(lines) + "\n")
    return msg


def send(mail: Mail, msg: EmailMessage, smtp=smtplib) -> None:
    """Send one message. Raises on failure; `notify` is what keeps that quiet."""
    context = ssl.create_default_context()
    if mail.port == 465:
        with smtp.SMTP_SSL(mail.host, mail.port, timeout=TIMEOUT, context=context) as s:
            s.login(mail.user, mail.password)
            s.send_message(msg)
    else:
        with smtp.SMTP(mail.host, mail.port, timeout=TIMEOUT) as s:
            s.starttls(context=context)
            s.login(mail.user, mail.password)
            s.send_message(msg)


def notify(mail: Optional[Mail], name: str, email: str, note: str, admin_url: str,
           waiting: int, sender: Optional[Callable] = None, background: bool = True) -> bool:
    """Tell the owner about a new request. False when alerts are off.

    Never raises: the request has already been recorded, and the person asking
    must get their answer whether or not the owner's mail server is up.
    """
    if mail is None:
        return False
    msg = message(mail, name, email, note, admin_url, waiting)
    # Looked up now, not bound as a default argument: a default is fixed when
    # the function is defined, so replacing `send` later never reached it, and
    # the first test of this "stubbed" path made a real DNS lookup for its host.
    deliver = sender or send

    def go() -> None:
        try:
            deliver(mail, msg)
        except Exception as exc:                 # logged, never in the way
            log.warning("access-request alert to %s failed: %s", mail.to, exc)

    if background:
        threading.Thread(target=go, daemon=True).start()
    else:
        go()
    return True
