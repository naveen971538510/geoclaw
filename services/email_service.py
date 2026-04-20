"""
GeoClaw email delivery — stdlib-only.

Configuration (all optional — if `SMTP_HOST` is unset, delivery is disabled
and messages are logged to stdout for local development).

    SMTP_HOST       hostname of the SMTP relay (e.g. smtp.sendgrid.net)
    SMTP_PORT       port (default 587 for STARTTLS, 465 for SMTPS)
    SMTP_USER       username, if the relay requires auth
    SMTP_PASSWORD   password / API token
    SMTP_USE_TLS    "1" / "true" → STARTTLS (default when port==587)
    SMTP_USE_SSL    "1" / "true" → SMTPS from the handshake (default when port==465)
    SMTP_FROM       "From" header (default: "no-reply@geoclaw.local")
    SMTP_FROM_NAME  optional display name for the From address
    SMTP_TIMEOUT    socket timeout, seconds (default 15)
"""
from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from typing import Optional


def is_configured() -> bool:
    return bool((os.environ.get("SMTP_HOST") or "").strip())


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _from_address() -> str:
    addr = (os.environ.get("SMTP_FROM") or "no-reply@geoclaw.local").strip()
    name = (os.environ.get("SMTP_FROM_NAME") or "").strip()
    return f"{name} <{addr}>" if name else addr


def send_email(to_address: str, subject: str, body_text: str, body_html: Optional[str] = None) -> bool:
    """
    Send a plaintext (optionally HTML) email.

    Returns True if delivery was attempted successfully. Returns False if SMTP
    is not configured (message is logged to stdout instead) or delivery failed.
    Never raises.
    """
    to_address = (to_address or "").strip()
    if not to_address:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _from_address()
    msg["To"] = to_address
    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    if not is_configured():
        sys.stdout.write(
            f"[email_service] SMTP not configured — would have sent to {to_address}:\n"
            f"  subject: {subject}\n  body: {body_text[:240]}\n"
        )
        sys.stdout.flush()
        return False

    host = (os.environ.get("SMTP_HOST") or "").strip()
    port = int((os.environ.get("SMTP_PORT") or "587").strip() or 587)
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASSWORD") or "").strip()
    timeout = float((os.environ.get("SMTP_TIMEOUT") or "15").strip() or 15)
    use_ssl = _bool_env("SMTP_USE_SSL", default=(port == 465))
    use_tls = _bool_env("SMTP_USE_TLS", default=(port == 587))

    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                if use_tls:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if user:
                    s.login(user, password)
                s.send_message(msg)
        return True
    except Exception as exc:
        sys.stderr.write(f"[email_service] send to {to_address} failed: {exc}\n")
        sys.stderr.flush()
        return False
