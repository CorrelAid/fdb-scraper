"""Send a failure notification over SMTP. Stdlib only.

Reads SMTP_HOST, SMTP_PORT (default 587), SMTP_USERNAME, SMTP_PASSWORD,
NOTIFY_FROM, NOTIFY_TO (comma-separated). Silent no-op if NOTIFY_TO is unset,
so the same script works locally with no config.
"""
from __future__ import annotations

import os
import smtplib
import traceback
from email.message import EmailMessage


def send_failure(exc: BaseException, command: str) -> None:
    to = os.environ.get("NOTIFY_TO", "").strip()
    if not to:
        return
    msg = EmailMessage()
    msg["Subject"] = f"[fdb-scraper] pipeline failed: {exc.__class__.__name__}"
    msg["From"] = os.environ["NOTIFY_FROM"]
    msg["To"] = to
    msg.set_content(
        f"Command: {command}\n\n"
        f"Error: {exc.__class__.__name__}: {exc}\n\n"
        f"{traceback.format_exc()}"
    )
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)