"""Best-effort outbound email (SMTP over STARTTLS).

Off unless NOTIFY_EMAIL_ENABLED=true and the SMTP_* settings are present, so
nothing changes for an existing setup. A failure is logged and swallowed: an
email problem must never block or roll back a workflow action.

Settings are read at send time (not import time) so they follow .env changes
and tests can set them freely. Credentials live only in the environment.

NOTIFY_EMAIL_OVERRIDE_TO (optional) redirects every message to one address,
with the intended recipient named in the subject -- for trying the emails out
without writing to real people.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def email_enabled() -> bool:
    return (
        os.getenv("NOTIFY_EMAIL_ENABLED", "false").strip().lower() == "true"
        and bool(os.getenv("SMTP_HOST"))
        and bool(os.getenv("SMTP_USER"))
        and bool(os.getenv("SMTP_PASSWORD"))
    )


def send_email(to: str, subject: str, body: str, html: str | None = None) -> bool:
    """Send an email (plain text, plus an HTML alternative when given).
    Returns True when handed to the SMTP server."""
    if not to or not email_enabled():
        return False

    override = (os.getenv("NOTIFY_EMAIL_OVERRIDE_TO") or "").strip()
    if override and override.lower() != to.lower():
        subject = f"[to: {to}] {subject}"
        to = override

    sender = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
            smtp.starttls()
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)
        return True
    except Exception as exc:  # never let email break the caller
        logger.warning("Email to %s not sent: %s", to, type(exc).__name__)
        return False
