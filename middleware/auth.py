"""
Auth middleware: resolves request.state.user_id from JWT if present, and
enforces auth on /api/* paths.

Auth layers (first match wins):
1. Valid JWT (signed with GEOCLAW_JWT_SECRET) → user_id on request.state.
2. Legacy GEOCLAW_LOCAL_TOKEN — treated as "authenticated" but with no user_id.
3. Localhost — only allowed if GEOCLAW_ALLOW_LOCALHOST_AUTH=1 (dev only).
   In prod the default is DENY so a misconfigured reverse proxy can't silently
   grant cross-tenant access.
"""
from __future__ import annotations

import hmac
import os
from typing import Iterable, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

from services.auth_service import verify_access_token

# Public (unauthenticated) prefixes. We match with a trailing-slash boundary so
# `/api/auth/signup-admin` (a hypothetical private endpoint) cannot be reached
# by accident.
AUTH_PUBLIC_PATHS: Tuple[str, ...] = (
    "/api/auth/signup",
    "/api/auth/login",
    "/api/auth/verify-email",
    "/api/auth/request-password-reset",
    "/api/auth/reset-password",
)

# Endpoints that accept a one-time token via `?token=` query param (reset /
# verify links emailed to users). Every *other* endpoint ignores the query
# param so JWTs don't leak via URL to logs and Referer.
QUERY_TOKEN_PATHS: Tuple[str, ...] = (
    "/api/auth/verify-email",
    "/api/auth/reset-password",
    "/api/stream",  # SSE cannot send Authorization headers cross-browser
)


def _path_matches(path: str, candidates: Tuple[str, ...]) -> bool:
    for p in candidates:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def is_protected_path(path: str) -> bool:
    if _path_matches(path, AUTH_PUBLIC_PATHS):
        return False
    return path.startswith("/api/") or path == "/bias"


def _localhost_auth_allowed() -> bool:
    """Returns True only when operators explicitly opt in (dev only)."""
    raw = (os.environ.get("GEOCLAW_ALLOW_LOCALHOST_AUTH") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def extract_token(request: Request) -> str:
    """Header token is always accepted. Query-string token is only accepted on
    specific email-link / SSE endpoints (see QUERY_TOKEN_PATHS)."""
    auth_header = str(request.headers.get("authorization") or "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if _path_matches(request.url.path, QUERY_TOKEN_PATHS):
        return str(request.query_params.get("token") or "").strip()
    return ""


def unauth_response(request: Request, allowed_origins: Iterable[str], message: Optional[str] = None) -> JSONResponse:
    origin = str(request.headers.get("origin") or "")
    headers: dict = {}
    if origin in tuple(allowed_origins):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        {
            "status": "error",
            "error": message or (
                "Unauthorized — sign in via /api/auth/login, or pass a legacy "
                "Authorization: Bearer <GEOCLAW_LOCAL_TOKEN>."
            ),
        },
        status_code=401,
        headers=headers,
    )


def build_middleware(allowed_origins: Iterable[str], legacy_token: str = ""):
    allowed = tuple(allowed_origins)
    api_token = str(legacy_token or "").strip()

    async def middleware(request: Request, call_next):
        request.state.user_id = None
        request.state.user_email = None

        if request.method == "OPTIONS":
            return await call_next(request)

        provided = extract_token(request)
        if provided:
            try:
                claims = verify_access_token(provided)
                if claims:
                    sub = claims.get("sub")
                    if isinstance(sub, int) and sub > 0:
                        request.state.user_id = sub
                        request.state.user_email = claims.get("email") or None
            except Exception:
                pass

        if is_protected_path(request.url.path):
            client_host = str((request.client.host if request.client else "") or "")
            is_local = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
            localhost_ok = is_local and _localhost_auth_allowed()
            if request.state.user_id:
                pass
            elif api_token and provided and hmac.compare_digest(provided, api_token):
                pass
            elif localhost_ok:
                pass
            else:
                return unauth_response(request, allowed)

        return await call_next(request)

    return middleware
