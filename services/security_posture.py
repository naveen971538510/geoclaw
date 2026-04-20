"""Startup security-posture log.

Emits one structured log line at app boot showing which security-
relevant env vars are configured.  Never leaks a secret value — every
token-style var surfaces only as ``SET`` or ``UNSET``.  Non-sensitive
vars (origin allow-lists, host allow-lists, boolean flags) surface as
their actual values because an operator needs to be able to confirm
the exact configuration from logs.

Example line::

    security: token=SET guard_read_api=OFF webhook_secret=UNSET \
        trusted_hosts=app.example.com,*.example.com \
        production_origin=https://app.example.com

This lets ops teams catch the classic failure mode ``GEOCLAW_LOCAL_TOKEN
never made it into Railway's env``: one grep against the startup logs
and they know every deploy's posture at a glance.
"""
from __future__ import annotations

import os
from typing import Dict


# Env vars whose **presence** alone is the security signal — values are
# secrets and MUST NOT be logged.
_SECRET_FLAG_VARS = (
    "GEOCLAW_LOCAL_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
)

# Boolean-flavoured flags — ``ON`` / ``OFF`` covers the truthy / falsy
# mapping without echoing the raw user string.
_BOOL_FLAG_VARS = (
    "GEOCLAW_GUARD_READ_API",
)

# Vars whose value is itself public info (an origin URL, a host list)
# that operators need to verify by eye.  Safe to log verbatim.
_VALUE_VARS = (
    "GEOCLAW_PRODUCTION_ORIGIN",
    "GEOCLAW_TRUSTED_HOSTS",
)

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(value: str) -> str:
    return "SET" if str(value or "").strip() else "UNSET"


def _bool_flag(value: str) -> str:
    return "ON" if str(value or "").strip().lower() in _TRUTHY else "OFF"


def compute_posture(env: Dict[str, str] | None = None) -> Dict[str, str]:
    """Return a dict of ``{short_name: surface_value}`` describing the
    current security posture.  Used by ``log_security_posture`` and by
    the unit tests."""
    e = env if env is not None else os.environ
    out: Dict[str, str] = {}
    for name in _SECRET_FLAG_VARS:
        out[_short(name)] = _flag(e.get(name, ""))
    for name in _BOOL_FLAG_VARS:
        out[_short(name)] = _bool_flag(e.get(name, ""))
    for name in _VALUE_VARS:
        v = str(e.get(name, "") or "").strip()
        out[_short(name)] = v or "UNSET"
    return out


def _short(env_name: str) -> str:
    """Strip the ``GEOCLAW_`` / ``TELEGRAM_`` prefix for a readable log
    key, then lowercase.  Keeps the log line short and greppable."""
    trimmed = env_name
    for prefix in ("GEOCLAW_", "TELEGRAM_"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix):]
            break
    return trimmed.lower()


def format_posture(posture: Dict[str, str]) -> str:
    """Format the posture dict as a single-line log message."""
    # Stable ordering so a deploy can diff logs line-for-line.
    parts = [f"{k}={posture[k]}" for k in sorted(posture.keys())]
    return "security: " + " ".join(parts)


def log_security_posture(logger) -> None:
    """Emit the posture line on the provided logger at INFO level.

    Accepts a ``logging.Logger`` (duck-typed: anything with an ``info``
    method works, which keeps the tests trivial).
    """
    posture = compute_posture()
    logger.info(format_posture(posture))
