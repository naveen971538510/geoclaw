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
    SMTP_ALLOW_PLAINTEXT  "1" to permit auth + send over an unencrypted socket
                          (dev only; refuses to login plaintext by default).
"""
from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

from services.auth_service import MAX_EMAIL_LEN, is_valid_email

# Hard caps — defense in depth against header / injection abuse.
_MAX_SUBJECT_LEN = 300
_MAX_BODY_LEN = 200_000  # plain-text; generous but bounded


def is_configured() -> bool:
    return bool((os.environ.get("SMTP_HOST") or "").strip())


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _from_address() -> str:
    """Build a safe From header. Uses email.utils.formataddr so a display
    name containing CR/LF/quotes can't inject extra SMTP headers."""
    addr = (os.environ.get("SMTP_FROM") or "no-reply@geoclaw.local").strip()
    # Strip any control characters from the address itself.
    addr = "".join(c for c in addr if c.isprintable() and c not in {"\r", "\n"})
    name = (os.environ.get("SMTP_FROM_NAME") or "").strip()
    name = "".join(c for c in name if c.isprintable() and c not in {"\r", "\n"})
    if name:
        return formataddr((name, addr))
    return addr


def _safe_subject(subject: str) -> str:
    s = (subject or "").replace("\r", " ").replace("\n", " ").strip()
    return s[:_MAX_SUBJECT_LEN]


def send_email(to_address: str, subject: str, body_text: str, body_html: Optional[str] = None) -> bool:
    """
    Send a plaintext (optionally HTML) email.

    Returns True if delivery was attempted successfully. Returns False if SMTP
    is not configured (message is logged to stdout instead), if inputs are
    invalid, or if delivery failed. Never raises.
    """
    to_address = (to_address or "").strip()
    if not to_address or len(to_address) > MAX_EMAIL_LEN or not is_valid_email(to_address):
        # Reject upfront — guards against header injection via newline-laden addresses.
        sys.stderr.write(f"[email_service] refusing to send: invalid recipient address\n")
        return False

    subject_safe = _safe_subject(subject)
    body_text = (body_text or "")[:_MAX_BODY_LEN]

    msg = EmailMessage()
    msg["Subject"] = subject_safe
    msg["From"] = _from_address()
    msg["To"] = to_address
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html[:_MAX_BODY_LEN], subtype="html")

    if not is_configured():
        sys.stdout.write(
            f"[email_service] SMTP not configured — would have sent to {to_address}:\n"
            f"  subject: {subject_safe}\n  body: {body_text[:240]}\n"
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
    allow_plaintext = _bool_env("SMTP_ALLOW_PLAINTEXT", default=False)

    # Refuse to send credentials (or any message) over an unencrypted socket
    # unless the operator has explicitly opted in. Belt-and-braces guard
    # against a misconfigured SMTP_PORT silently downgrading to plaintext.
    if not (use_ssl or use_tls) and not allow_plaintext:
        sys.stderr.write(
            "[email_service] refusing to send: SMTP_USE_TLS/SMTP_USE_SSL not set and "
            "SMTP_ALLOW_PLAINTEXT is off. Set SMTP_ALLOW_PLAINTEXT=1 for local dev.\n"
        )
        return False

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
                if user and (use_tls or allow_plaintext):
                    s.login(user, password)
                s.send_message(msg)
        return True
    except Exception as exc:
        sys.stderr.write(f"[email_service] send to {to_address} failed: {exc}\n")
        sys.stderr.flush()
        return False
