"""
User CRUD on Postgres. Thin layer over intelligence.db.get_connection.
Every method uses parameterized queries; never interpolates strings into SQL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import psycopg2.extras

from intelligence.db import get_connection
from services.auth_service import (
    generate_token,
    hash_password,
    hash_token,
    is_valid_email,
    normalize_email,
    verify_password,
)

TOKEN_KIND_VERIFY_EMAIL = "verify_email"
TOKEN_KIND_PASSWORD_RESET = "password_reset"

_TOKEN_TTL = {
    TOKEN_KIND_VERIFY_EMAIL: timedelta(days=7),
    TOKEN_KIND_PASSWORD_RESET: timedelta(hours=1),
}


class UserError(Exception):
    """Domain errors surfaced to API callers as 400/401/409."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def create_user(email: str, password: str, display_name: Optional[str] = None) -> Dict[str, Any]:
    email_norm = normalize_email(email)
    if not is_valid_email(email_norm):
        raise UserError("invalid_email", "Invalid email address", 400)
    if not isinstance(password, str) or len(password) < 8:
        raise UserError("weak_password", "Password must be at least 8 characters", 400)
    pw_hash = hash_password(password)
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM users WHERE email = %s;",
                (email_norm,),
            )
            if cur.fetchone():
                raise UserError("email_taken", "Email already registered", 409)
            cur.execute(
                """
                INSERT INTO users (email, password_hash, display_name)
                VALUES (%s, %s, %s)
                RETURNING id, email, display_name, role, is_active, created_at;
                """,
                (email_norm, pw_hash, display_name or None),
            )
            row = cur.fetchone()
    return dict(row)


def authenticate(email: str, password: str) -> Dict[str, Any]:
    email_norm = normalize_email(email)
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, email, password_hash, display_name, role, is_active
                FROM users WHERE email = %s;
                """,
                (email_norm,),
            )
            row = cur.fetchone()
    if not row or not row.get("is_active") or not verify_password(password, row["password_hash"]):
        # Same error for both to prevent user enumeration.
        raise UserError("invalid_credentials", "Invalid email or password", 401)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login_at = NOW() WHERE id = %s;",
                (row["id"],),
            )
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
    }


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, email, display_name, role, is_active, created_at,
                       last_login_at, email_verified_at
                FROM users WHERE id = %s;
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    email_norm = normalize_email(email)
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, email, display_name, role, is_active, email_verified_at
                FROM users WHERE email = %s;
                """,
                (email_norm,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


# --- One-time tokens: verify email / password reset -----------------------

def _assert_known_kind(kind: str) -> None:
    if kind not in _TOKEN_TTL:
        raise UserError("invalid_token_kind", f"Unknown token kind: {kind}", 400)


def issue_token(user_id: int, kind: str) -> str:
    """
    Create a new auth token for (user_id, kind), returning the plaintext token
    to email to the user. Only the sha256 hash is persisted. Any existing
    unconsumed tokens of the same kind for this user are invalidated.
    """
    _assert_known_kind(kind)
    token = generate_token()
    th = hash_token(token)
    expires_at = datetime.now(timezone.utc) + _TOKEN_TTL[kind]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE auth_tokens
                SET consumed_at = NOW()
                WHERE user_id = %s AND kind = %s AND consumed_at IS NULL;
                """,
                (int(user_id), kind),
            )
            cur.execute(
                """
                INSERT INTO auth_tokens (user_id, kind, token_hash, expires_at)
                VALUES (%s, %s, %s, %s);
                """,
                (int(user_id), kind, th, expires_at),
            )
    return token


def consume_token(token: str, kind: str) -> Dict[str, Any]:
    """
    Validate and atomically consume a token. Returns the matching user row.
    Raises UserError on expired / unknown / already-consumed / wrong-kind.
    """
    _assert_known_kind(kind)
    if not token or not isinstance(token, str):
        raise UserError("invalid_token", "Token missing or malformed", 400)
    th = hash_token(token)
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, user_id, expires_at, consumed_at
                FROM auth_tokens
                WHERE token_hash = %s AND kind = %s
                FOR UPDATE;
                """,
                (th, kind),
            )
            row = cur.fetchone()
            if not row:
                raise UserError("invalid_token", "Token is invalid or has already been used", 400)
            if row.get("consumed_at"):
                raise UserError("token_consumed", "Token has already been used", 400)
            expires_at = row.get("expires_at")
            if expires_at and expires_at < datetime.now(timezone.utc):
                raise UserError("token_expired", "Token has expired", 400)
            cur.execute(
                "UPDATE auth_tokens SET consumed_at = NOW() WHERE id = %s;",
                (row["id"],),
            )
            cur.execute(
                """
                SELECT id, email, display_name, role, is_active, email_verified_at
                FROM users WHERE id = %s;
                """,
                (row["user_id"],),
            )
            user = cur.fetchone()
    if not user:
        raise UserError("invalid_token", "Token user no longer exists", 400)
    return dict(user)


def mark_email_verified(user_id: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users SET email_verified_at = NOW()
                WHERE id = %s AND email_verified_at IS NULL;
                """,
                (int(user_id),),
            )


def update_password(user_id: int, new_password: str) -> None:
    if not isinstance(new_password, str) or len(new_password) < 8:
        raise UserError("weak_password", "Password must be at least 8 characters", 400)
    pw_hash = hash_password(new_password)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s;",
                (pw_hash, int(user_id)),
            )
