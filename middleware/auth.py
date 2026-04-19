"""
Auth middleware: resolves request.state.user_id from JWT if present, and
enforces auth on /api/* paths.

Auth layers (first match wins):
1. Valid JWT (signed with GEOCLAW_JWT_SECRET) → user_id on request.state.
2. Legacy GEOCLAW_LOCAL_TOKEN — treated as "authenticated" but with no user_id.
3. Localhost — always allowed in dev; useful for scheduled jobs.
"""
from __future__ import annotations

import hmac
import os
from typing import Iterable, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

from services.auth_service import verify_access_token

AUTH_PUBLIC_PREFIXES: Tuple[str, ...] = ("/api/auth/signup", "/api/auth/login")


def is_protected_path(path: str) -> bool:
    if any(path.startswith(p) for p in AUTH_PUBLIC_PREFIXES):
        return False
    return path.startswith("/api/") or path == "/bias"


def extract_token(request: Request) -> str:
    auth_header = str(request.headers.get("authorization") or "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return str(request.query_params.get("token") or "").strip()


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
                    request.state.user_id = int(claims.get("sub") or 0) or None
                    request.state.user_email = claims.get("email") or None
            except Exception:
                pass

        if is_protected_path(request.url.path):
            client_host = str((request.client.host if request.client else "") or "")
            is_local = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
            if request.state.user_id:
                pass
            elif api_token:
                if not hmac.compare_digest(provided, api_token) and not is_local:
                    return unauth_response(request, allowed)
            elif not is_local:
                return unauth_response(request, allowed)

        return await call_next(request)

    return middleware
