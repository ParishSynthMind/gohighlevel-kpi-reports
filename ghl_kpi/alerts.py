"""Failure and staleness alerts, plus emailing the finished report. All optional, all from env vars."""
from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, List, Optional

from .db import last_run, now_utc

log = logging.getLogger(__name__)


def send_email(subject: str, body: str, attachments: Iterable[str] = ()) -> bool:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("REPORT_EMAIL_TO")
    if not host or not to:
        log.info("email skipped: SMTP_HOST / REPORT_EMAIL_TO not set")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("REPORT_EMAIL_FROM") or os.environ.get("SMTP_USER", "")
    msg.set_content(body)
    for p in attachments:
        data = Path(p).read_bytes()
        sub = "pdf" if p.endswith(".pdf") else "html" if p.endswith(".html") else "octet-stream"
        maintype = "text" if sub == "html" else "application"
        msg.add_attachment(data, maintype=maintype, subtype=sub, filename=Path(p).name)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


def post_webhook(text: str) -> bool:
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        return False
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return 200 <= r.status < 300


def alert(subject: str, body: str) -> List[str]:
    """Send an alert on every configured channel. Always logs, so a failure is never silent."""
    log.error("ALERT: %s | %s", subject, body)
    sent = []
    for name, fn in (("email", lambda: send_email(subject, body)), ("webhook", lambda: post_webhook(f"{subject}\n{body}"))):
        try:
            if fn():
                sent.append(name)
        except Exception as exc:
            log.error("alert via %s failed: %s", name, exc)
    return sent


def stale_problem(session, stale_after_hours: float, now: Optional[datetime] = None) -> Optional[str]:
    """Return a reason string if syncing has stopped or the last run failed, else None."""
    now = now or now_utc()
    latest = last_run(session)
    if latest is None:
        return "No sync has ever run."
    if latest.status == "failed":
        return f"The last sync (run {latest.id}, {latest.started_at:%Y-%m-%d %H:%M} UTC) failed: {latest.error}"
    ok = last_run(session, ok_only=True)
    if ok is None:
        return "No sync has ever finished successfully."
    age = now - (ok.finished_at or ok.started_at)
    if age > timedelta(hours=stale_after_hours):
        return f"The last successful sync was {age.total_seconds() / 3600:.1f} hours ago (limit {stale_after_hours:g}h)."
    return None
