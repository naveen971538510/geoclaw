"""
Unit tests for the auth & multi-tenant foundation.

No database required — exercises pure logic in:
  - services.auth_service (password hashing, JWT)
  - services.tenant_scope (row-level filter)
  - middleware.auth / middleware.rate_limit (via FastAPI TestClient)

Run:  python -m pytest tests/test_auth_and_tenant.py -v
Or:   python tests/test_auth_and_tenant.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests must have a JWT secret set before importing auth_service functions
# that touch JWT signing.
os.environ.setdefault("GEOCLAW_JWT_SECRET", "test-" + "x" * 60)

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from middleware.auth import build_middleware as build_auth_mw
from middleware.rate_limit import (
    DEFAULT_LIMIT,
    EXPENSIVE_LIMIT,
    build_middleware as build_rate_mw,
)
from services.auth_service import (
    create_access_token,
    hash_password,
    is_valid_email,
    normalize_email,
    verify_access_token,
    verify_password,
)
from services.tenant_scope import and_scope, scope_where


# ---------- auth_service --------------------------------------------------

def test_password_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_password_too_short_rejected():
    try:
        hash_password("short")
    except ValueError:
        return
    raise AssertionError("expected ValueError for short password")


def test_email_validation():
    assert is_valid_email("a@b.co")
    assert is_valid_email(" A@B.Co ".strip())
    assert not is_valid_email("nope")
    assert not is_valid_email("a@b")
    assert normalize_email(" Foo@BAR.com ") == "foo@bar.com"


def test_jwt_roundtrip():
    tok = create_access_token(42, "u@x.com", ttl_seconds=60)
    claims = verify_access_token(tok)
    assert claims and claims["sub"] == 42 and claims["email"] == "u@x.com"


def test_jwt_tampered_rejected():
    tok = create_access_token(1, "a@b.com")
    tampered = tok[:-4] + ("AAAA" if tok[-4:] != "AAAA" else "BBBB")
    assert verify_access_token(tampered) is None


def test_jwt_expired_rejected():
    tok = create_access_token(1, "a@b.com", ttl_seconds=-1)
    assert verify_access_token(tok) is None


def test_jwt_missing_secret_raises():
    saved = os.environ.pop("GEOCLAW_JWT_SECRET")
    try:
        try:
            create_access_token(1, "a@b.com")
        except RuntimeError:
            return
        raise AssertionError("expected RuntimeError")
    finally:
        os.environ["GEOCLAW_JWT_SECRET"] = saved


# ---------- tenant_scope ---------------------------------------------------

def test_scope_anonymous_sees_only_shared():
    clause, params = scope_where(None)
    assert clause == "user_id IS NULL" and params == []


def test_scope_authenticated_sees_shared_and_own():
    clause, params = scope_where(7)
    assert "user_id IS NULL" in clause and "user_id = %s" in clause
    assert params == [7]


def test_and_scope_preserves_base_where():
    clause, params = and_scope("ts >= %s", 9)
    assert clause.startswith("(ts >= %s) AND ") and params == [9]


def test_and_scope_with_alias():
    clause, _ = and_scope("", 3, alias="gs")
    assert "gs.user_id" in clause


# ---------- middleware: rate limit ----------------------------------------

def _make_rate_app():
    app = FastAPI()
    app.middleware("http")(build_rate_mw(allowed_origins=[]))

    @app.get("/api/news")
    def news():
        return {"ok": True}

    @app.get("/api/other")
    def other():
        return {"ok": True}

    @app.get("/public")
    def public():
        return {"ok": True}

    return app


def test_rate_limit_expensive_throttles():
    app = _make_rate_app()
    c = TestClient(app)
    expected_limit = EXPENSIVE_LIMIT[0]
    codes = [c.get("/api/news").status_code for _ in range(expected_limit + 4)]
    assert codes[:expected_limit] == [200] * expected_limit
    assert codes[expected_limit] == 429


def test_rate_limit_non_api_unthrottled():
    app = _make_rate_app()
    c = TestClient(app)
    codes = [c.get("/public").status_code for _ in range(120)]
    assert all(code == 200 for code in codes)


# ---------- middleware: auth ----------------------------------------------

def _make_auth_app(legacy_token: str = ""):
    app = FastAPI()
    app.middleware("http")(build_auth_mw(allowed_origins=[], legacy_token=legacy_token))

    @app.get("/api/ping")
    def ping(request: Request):
        return {"user_id": getattr(request.state, "user_id", None)}

    @app.post("/api/auth/login")
    def login():
        return {"ok": True}

    @app.get("/public")
    def public():
        return {"ok": True}

    return app


def test_auth_localhost_allowed_without_token():
    app = _make_auth_app()
    c = TestClient(app)
    r = c.get("/api/ping")
    assert r.status_code == 200
    assert r.json()["user_id"] is None


def test_auth_valid_jwt_sets_user_id():
    app = _make_auth_app()
    tok = create_access_token(99, "test@ex.com")
    c = TestClient(app)
    r = c.get("/api/ping", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["user_id"] == 99


def test_auth_invalid_jwt_falls_through_to_local():
    app = _make_auth_app()
    c = TestClient(app)
    r = c.get("/api/ping", headers={"Authorization": "Bearer not-a-real-jwt"})
    # localhost still wins → 200, but user_id remains None.
    assert r.status_code == 200 and r.json()["user_id"] is None


def test_auth_public_login_not_gated():
    app = _make_auth_app(legacy_token="abc")
    c = TestClient(app)
    r = c.post("/api/auth/login")
    assert r.status_code == 200


def test_auth_public_paths_not_gated():
    app = _make_auth_app(legacy_token="abc")
    c = TestClient(app)
    r = c.get("/public")
    assert r.status_code == 200


if __name__ == "__main__":
    # Minimal runner if pytest isn't available.
    import traceback
    failures = 0
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failures} failed")
    sys.exit(1 if failures else 0)
