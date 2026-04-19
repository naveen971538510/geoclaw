"""
GeoClaw auth — stdlib-only password hashing and JWT (HS256).

Design goals:
- Zero new dependencies (stdlib hashlib/hmac/secrets).
- Constant-time comparisons to resist timing attacks.
- Password hashes self-describing ("scrypt$N$r$p$salt$hash") so params can evolve.
- JWTs signed with GEOCLAW_JWT_SECRET (separate from GEOCLAW_LOCAL_TOKEN).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple

# Password hashing: PBKDF2-HMAC-SHA256. OWASP 2023 minimum is 600k iterations;
# we use 700k for a small margin. Stored as "pbkdf2_sha256$iter$salt$hash" so
# the iteration count can be bumped without breaking existing users.
# (scrypt is preferred but not available on LibreSSL macOS; pbkdf2 is stdlib.)
_PBKDF2_ITERS = 700_000
_PBKDF2_DKLEN = 32
_SALT_BYTES = 16

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


# --- Password hashing -------------------------------------------------------

def hash_password(plaintext: str) -> str:
    if not isinstance(plaintext, str) or len(plaintext) < 8:
        raise ValueError("password must be a string of at least 8 characters")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        plaintext.encode("utf-8"),
        salt,
        _PBKDF2_ITERS,
        dklen=_PBKDF2_DKLEN,
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${_b64u_encode(salt)}${_b64u_encode(dk)}"


def verify_password(plaintext: str, stored: str) -> bool:
    if not stored or not isinstance(stored, str):
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iters = int(parts[1])
        salt = _b64u_decode(parts[2])
        expected = _b64u_decode(parts[3])
    except Exception:
        return False
    try:
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            plaintext.encode("utf-8"),
            salt,
            iters,
            dklen=len(expected),
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected)


# --- Email validation -------------------------------------------------------

def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


# --- JWT (HS256) ------------------------------------------------------------

def _jwt_secret() -> bytes:
    secret = (os.environ.get("GEOCLAW_JWT_SECRET") or "").strip()
    if not secret:
        raise RuntimeError(
            "GEOCLAW_JWT_SECRET is not set. Generate one with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(64))'"
        )
    if len(secret) < 32:
        raise RuntimeError("GEOCLAW_JWT_SECRET must be at least 32 characters")
    return secret.encode("utf-8")


def create_access_token(user_id: int, email: str, ttl_seconds: int = 60 * 60 * 24 * 7) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": int(user_id),
        "email": str(email),
        "iat": now,
        "exp": now + int(ttl_seconds),
        "iss": "geoclaw",
    }
    header_b64 = _b64u_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(_jwt_secret(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64u_encode(sig)}"


def verify_access_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        expected_sig = hmac.new(_jwt_secret(), signing_input, hashlib.sha256).digest()
        provided_sig = _b64u_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected_sig, provided_sig):
        return None
    try:
        header = json.loads(_b64u_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64u_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        return None
    if payload.get("iss") != "geoclaw":
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or int(time.time()) >= exp:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, int):
        return None
    return payload


def extract_bearer_token(auth_header: str) -> str:
    if not auth_header:
        return ""
    h = auth_header.strip()
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return ""
