"""Regression tests for the security surfaces hardened in PR #1 and PR #4.

Pins the shell-smoke-test assertions that shipped in PR #4's post-merge
verification into pytest so CI catches regressions automatically.

Covers three surfaces:

1. ``main._stream_cors_headers`` — the SSE CORS allow-list that replaced
   the wildcard ``Access-Control-Allow-Origin: *`` on
   ``GET /api/events/stream``.
2. ``main._mutation_guard`` — the constant-time ``hmac.compare_digest``
   token check gating every mutation route.
3. ``POST /api/telegram/webhook`` — the ``X-Telegram-Bot-Api-Secret-Token``
   guard activated by ``TELEGRAM_WEBHOOK_SECRET``.

These tests never start uvicorn. CORS and mutation-guard tests exercise
the helper functions directly with hand-crafted starlette ``Request``
objects; webhook tests go through ``fastapi.testclient.TestClient``.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from starlette.requests import Request

# Silence optional startup deps when importing main.py under pytest.
os.environ.setdefault("GEOCLAW_DB_BACKEND", "sqlite")

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import main  # noqa: E402  (sys.path injection)


def _make_request(
    host: str,
    headers: dict | None = None,
    query: str = "",
    method: str = "POST",
    path: str = "/system-reset",
) -> Request:
    """Build a bare Request with a fake ASGI scope.

    Crucially we set ``scope["client"]`` so we can exercise the non-local
    branch of ``_local_client`` — ``fastapi.testclient.TestClient`` always
    reports ``testclient`` as the client host which short-circuits through
    the localhost allow-list.
    """
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": hdrs,
        "query_string": query.encode(),
        "client": (host, 12345),
    }
    return Request(scope)


class StreamCorsHeadersTests(unittest.TestCase):
    """Exercise ``_stream_cors_headers`` directly.

    Any regression that re-introduces ``Access-Control-Allow-Origin: *``
    or echoes an arbitrary Origin would flip one of these assertions.
    """

    def test_no_origin_returns_empty_dict(self):
        request = _make_request("127.0.0.1", method="GET", path="/api/events/stream")
        self.assertEqual(main._stream_cors_headers(request), {})

    def test_allowlisted_localhost_origin_is_echoed(self):
        request = _make_request(
            "127.0.0.1",
            headers={"origin": "http://localhost:3000"},
            method="GET",
            path="/api/events/stream",
        )
        headers = main._stream_cors_headers(request)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "http://localhost:3000")
        self.assertEqual(headers.get("Access-Control-Allow-Credentials"), "true")
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_disallowed_origin_returns_empty_dict(self):
        request = _make_request(
            "127.0.0.1",
            headers={"origin": "https://evil.example.com"},
            method="GET",
            path="/api/events/stream",
        )
        self.assertEqual(main._stream_cors_headers(request), {})

    def test_never_returns_wildcard(self):
        """The allow-list must NEVER respond with ``*``."""
        for origin in (
            "",
            "http://localhost:3000",
            "https://evil.example.com",
            "null",
            "*",
        ):
            request = _make_request(
                "127.0.0.1",
                headers={"origin": origin} if origin else {},
                method="GET",
                path="/api/events/stream",
            )
            headers = main._stream_cors_headers(request)
            self.assertNotEqual(headers.get("Access-Control-Allow-Origin"), "*", origin)

    def test_production_origin_added_to_allowlist(self):
        """Sanity-check that ``GEOCLAW_PRODUCTION_ORIGIN`` is honoured.

        Exercises the live module-level set rather than reloading main
        (which would drop a lot of expensive startup state).
        """
        prod = "https://staging.example.com"
        with mock.patch.object(main, "_STREAM_ALLOWED_ORIGINS", main._STREAM_ALLOWED_ORIGINS | {prod}):
            request = _make_request(
                "127.0.0.1",
                headers={"origin": prod},
                method="GET",
                path="/api/events/stream",
            )
            headers = main._stream_cors_headers(request)
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), prod)


class MutationGuardTests(unittest.TestCase):
    """Exercise ``_mutation_guard`` via hand-crafted ASGI scope.

    Each assertion tracks directly to a row in the post-merge smoke test.
    The ``mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", ...)`` calls swap
    out the module-level copy of the config so we don't have to reload
    main between tests.
    """

    TOKEN = "testtoken123"

    def test_non_local_client_with_no_token_raises(self):
        with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", self.TOKEN):
            request = _make_request("203.0.113.42")
            with self.assertRaises(PermissionError):
                main._mutation_guard(request)

    def test_non_local_client_with_wrong_token_raises(self):
        with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", self.TOKEN):
            request = _make_request("203.0.113.42", headers={"x-geoclaw-token": "wrong"})
            with self.assertRaises(PermissionError):
                main._mutation_guard(request)

    def test_non_local_client_with_correct_header_passes(self):
        with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", self.TOKEN):
            request = _make_request("203.0.113.42", headers={"x-geoclaw-token": self.TOKEN})
            self.assertIsNone(main._mutation_guard(request))

    def test_non_local_client_with_correct_query_param_passes(self):
        with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", self.TOKEN):
            request = _make_request("203.0.113.42", query=f"token={self.TOKEN}")
            self.assertIsNone(main._mutation_guard(request))

    def test_localhost_passes_with_no_env_token(self):
        with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", ""):
            request = _make_request("127.0.0.1")
            self.assertIsNone(main._mutation_guard(request))

    def test_non_local_client_rejected_when_no_env_token(self):
        """Regression: when the env token is empty, non-local callers
        must still be rejected. Don't let an empty env silently open
        every mutation route to the public internet."""
        with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", ""):
            request = _make_request("203.0.113.42")
            with self.assertRaises(PermissionError):
                main._mutation_guard(request)

    def test_ipv6_loopback_counts_as_local(self):
        with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", ""):
            request = _make_request("::1")
            self.assertIsNone(main._mutation_guard(request))

    def test_token_comparison_is_length_safe(self):
        """Regression for the original ``==``-based bug: a shorter
        provided token must not raise a different exception type or
        short-circuit into success. We just care that it's a clean
        ``PermissionError`` whether the mismatch is in byte 0 or byte N.
        """
        for guess in ("a", "testtoken12", "testtoken1234", "completely-different"):
            with mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", self.TOKEN):
                request = _make_request("203.0.113.42", headers={"x-geoclaw-token": guess})
                with self.assertRaises(PermissionError, msg=guess):
                    main._mutation_guard(request)


class TelegramWebhookSecretTests(unittest.TestCase):
    """Exercise the full ``POST /api/telegram/webhook`` round-trip.

    ``TELEGRAM_WEBHOOK_SECRET`` is read at request time (not at module
    load), so plain ``mock.patch.dict(os.environ, ...)`` is enough.
    """

    ENDPOINT = "/api/telegram/webhook"
    SECRET = "hookSecret123"

    def setUp(self):
        # Defer TestClient import so sqlite-backed app startup happens only
        # once the sys.path injection above has fired.
        from fastapi.testclient import TestClient

        self.client = TestClient(main.app)

    def _post(self, headers: dict | None = None):
        return self.client.post(self.ENDPOINT, json={}, headers=headers or {})

    def test_env_set_no_header_returns_401(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": self.SECRET}):
            resp = self._post()
            self.assertEqual(resp.status_code, 401)
            self.assertIn("invalid webhook secret", resp.text)

    def test_env_set_wrong_header_returns_401(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": self.SECRET}):
            resp = self._post({"X-Telegram-Bot-Api-Secret-Token": "wrong"})
            self.assertEqual(resp.status_code, 401)
            self.assertIn("invalid webhook secret", resp.text)

    def test_env_set_correct_header_returns_200(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": self.SECRET}):
            resp = self._post({"X-Telegram-Bot-Api-Secret-Token": self.SECRET})
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertIn('"status":"ok"', resp.text.replace(" ", ""))

    def test_env_unset_no_header_falls_through_to_200(self):
        """Backward-compat regression: empty env must not break legacy
        deployments that haven't yet configured ``setWebhook?secret_token=``.
        """
        env = os.environ.copy()
        env.pop("TELEGRAM_WEBHOOK_SECRET", None)
        with mock.patch.dict(os.environ, env, clear=True):
            resp = self._post()
            self.assertEqual(resp.status_code, 200, resp.text)


if __name__ == "__main__":
    unittest.main()
