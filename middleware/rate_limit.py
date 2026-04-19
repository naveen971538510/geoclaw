"""
Per-IP sliding-window rate limiting middleware.

In-memory only — scaling to multiple workers/machines needs Redis.
Default: 60 req/min for /api/*. Expensive LLM endpoints: 10 req/min.
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Deque, Dict, Iterable, Tuple

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


def client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = str(request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    return str((request.client.host if request.client else "") or "unknown")


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
