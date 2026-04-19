"""
User CRUD on Postgres. Thin layer over intelligence.db.get_connection.
Every method uses parameterized queries; never interpolates strings into SQL.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import psycopg2.extras

from intelligence.db import get_connection
from services.auth_service import (
    hash_password,
    is_valid_email,
    normalize_email,
    verify_password,
)


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
                SELECT id, email, display_name, role, is_active, created_at, last_login_at
                FROM users WHERE id = %s;
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
    return dict(row) if row else None
