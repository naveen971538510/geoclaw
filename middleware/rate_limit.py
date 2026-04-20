"""
Per-IP sliding-window rate limiting middleware.

In-memory only — scaling to multiple workers/machines needs Redis.
Default: 60 req/min for /api/*. Expensive LLM endpoints: 10 req/min.

Proxy trust: by default we ignore X-Forwarded-For / X-Real-IP because any
client can set them (trivial rate-limit bypass by rotating the header value).
Set `GEOCLAW_TRUSTED_PROXIES` to a comma-separated list of hosts you trust
(e.g. `127.0.0.1,10.0.0.0/8` — exact-match only here, no CIDR parsing) to
opt in. Typical prod deployments set this to the loopback address of your
fronting reverse proxy (Fly, Railway, nginx, etc.).
"""
from __future__ import annotations

import os
import time
from collections import deque
from threading import Lock
from typing import Deque, Dict, FrozenSet, Iterable, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

DEFAULT_LIMIT: Tuple[int, int] = (60, 60)
EXPENSIVE_LIMIT: Tuple[int, int] = (10, 60)
EXPENSIVE_PREFIXES: Tuple[str, ...] = (
    "/api/ask",
    "/api/briefing",
    "/api/scenarios",
    "/api/stream",
    "/api/news",
    "/api/llm",
    "/api/agent",
)

_buckets: Dict[str, Deque[float]] = {}
_lock = Lock()


def _trusted_proxies() -> FrozenSet[str]:
    raw = (os.environ.get("GEOCLAW_TRUSTED_PROXIES") or "").strip()
    if not raw:
        return frozenset()
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def client_ip(request: Request) -> str:
    """Return the client IP. Only honour X-Forwarded-For / X-Real-IP when the
    direct peer is in GEOCLAW_TRUSTED_PROXIES — otherwise clients can spoof
    the header and bypass rate limits by rotating values."""
    peer = str((request.client.host if request.client else "") or "unknown")
    trusted = _trusted_proxies()
    if peer in trusted:
        forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
        real = str(request.headers.get("x-real-ip") or "").strip()
        if real:
            return real
    return peer


def _limit_for(path: str) -> Tuple[int, int]:
    if any(path.startswith(p) for p in EXPENSIVE_PREFIXES):
        return EXPENSIVE_LIMIT
    return DEFAULT_LIMIT


def _check(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        bucket = _buckets.get(key)
        if bucket is None:
            bucket = deque()
            _buckets[key] = bucket
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def build_middleware(allowed_origins: Iterable[str]):
    allowed = tuple(allowed_origins)

    async def middleware(request: Request, call_next):
        path = request.url.path
        if request.method != "OPTIONS" and path.startswith("/api/"):
            limit, window = _limit_for(path)
            ip = client_ip(request)
            key = f"{ip}:{'expensive' if (limit, window) == EXPENSIVE_LIMIT else 'default'}"
            if not _check(key, limit, window):
                headers = {"Retry-After": str(window)}
                origin = str(request.headers.get("origin") or "")
                if origin in allowed:
                    headers["Access-Control-Allow-Origin"] = origin
                    headers["Access-Control-Allow-Credentials"] = "true"
                return JSONResponse(
                    {
                        "status": "error",
                        "error": f"Rate limit exceeded: {limit} requests per {window}s. Retry after {window}s.",
                    },
                    status_code=429,
                    headers=headers,
                )
        return await call_next(request)

    return middleware
