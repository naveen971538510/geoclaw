"""Regression tests for the opt-in read-only /api/* guard (M4).

The guard is OFF by default (backward-compat), ON when
``GEOCLAW_GUARD_READ_API`` is set to a truthy value. Tests patch the
module-level ``_READ_API_GUARD_ENABLED`` directly instead of reloading
``main`` (same pattern as tests/test_security.py) so we don't pay
the expensive uvicorn startup cost twice.

Coverage:

  * helper ``_read_api_guard_applies`` — matrix of on/off, path prefix,
    exempt list.
  * helper ``_extract_provided_token`` — three presentation channels.
  * middleware behaviour via ``TestClient`` + hand-crafted non-local
    scope: 401 on no token, 200 on correct token via each channel,
    localhost passthrough, exempt paths always through.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from starlette.requests import Request
from starlette.responses import Response

os.environ.setdefault("GEOCLAW_DB_BACKEND", "sqlite")

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

import main  # noqa: E402


class ReadApiGuardAppliesTests(unittest.TestCase):
    """Pure logic of ``_read_api_guard_applies``."""

    def test_off_by_default(self):
        with mock.patch.object(main, "_READ_API_GUARD_ENABLED", False):
            for path in ("/api/predictions", "/api/research/log", "/api/foo"):
                self.assertFalse(main._read_api_guard_applies(path), path)

    def test_on_matches_api_prefix(self):
        with mock.patch.object(main, "_READ_API_GUARD_ENABLED", True):
            self.assertTrue(main._read_api_guard_applies("/api/predictions"))
            self.assertTrue(main._read_api_guard_applies("/api/research/log"))
            self.assertTrue(main._read_api_guard_applies("/api/intelligence/regime"))

    def test_on_ignores_non_api(self):
        with mock.patch.object(main, "_READ_API_GUARD_ENABLED", True):
            for path in ("/", "/dashboard", "/static/x.css", "/geoclaw/ask", "/health"):
                self.assertFalse(main._read_api_guard_applies(path), path)

    def test_exempt_paths_always_pass(self):
        with mock.patch.object(main, "_READ_API_GUARD_ENABLED", True):
            self.assertFalse(main._read_api_guard_applies("/api/events/stream"))
            self.assertFalse(main._read_api_guard_applies("/api/telegram/webhook"))


class ExtractProvidedTokenTests(unittest.TestCase):
    """Three valid presentation channels for the token."""

    @staticmethod
    def _req(headers: dict | None = None, query: str = "") -> Request:
        hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/predictions",
            "headers": hdrs,
            "query_string": query.encode(),
            "client": ("127.0.0.1", 12345),
        }
        return Request(scope)

    def test_header_channel(self):
        r = self._req(headers={"x-geoclaw-token": "abc"})
        self.assertEqual(main._extract_provided_token(r), "abc")

    def test_bearer_channel(self):
        r = self._req(headers={"authorization": "Bearer xyz"})
        self.assertEqual(main._extract_provided_token(r), "xyz")

    def test_query_channel(self):
        r = self._req(query="token=qqq")
        self.assertEqual(main._extract_provided_token(r), "qqq")

    def test_header_wins_over_query(self):
        r = self._req(headers={"x-geoclaw-token": "header"}, query="token=query")
        self.assertEqual(main._extract_provided_token(r), "header")

    def test_none_present(self):
        r = self._req()
        self.assertEqual(main._extract_provided_token(r), "")


class ReadApiGuardMiddlewareTests(unittest.TestCase):
    """End-to-end through the real FastAPI middleware stack.

    We drive the request through a hand-crafted ASGI scope with a
    spoofed non-loopback ``client`` so the middleware exercises the
    reject / token-accept paths that TestClient's default
    ``testclient`` host short-circuits past.
    """

    TOKEN = "readguardtoken"
    # Any declared GET /api route exists for testing. api_reactive_status
    # is simple and stable; any cheap /api GET would work.
    PROBE_PATH = "/api/agent/reactive/status"

    def _call(
        self,
        headers: dict | None = None,
        query: str = "",
        client_host: str = "203.0.113.42",
        enabled: bool = True,
        token: str = TOKEN,
    ) -> tuple[int, bytes]:
        """Invoke the ASGI app directly, capturing status + body.

        We don't need the route handler to succeed — a 500 from the
        handler would still mean the middleware let us through.  The
        relevant question is ``401 vs. anything-else``.
        """
        hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self.PROBE_PATH,
            "raw_path": self.PROBE_PATH.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": hdrs,
            "client": (client_host, 12345),
            "server": ("testserver", 80),
            "state": {},
        }
        sent: list = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        import asyncio

        async def run():
            with (
                mock.patch.object(main, "_READ_API_GUARD_ENABLED", enabled),
                mock.patch.object(main, "GEOCLAW_LOCAL_TOKEN", token),
            ):
                await main.app(scope, receive, send)

        asyncio.run(run())
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, body

    def test_guard_off_lets_non_local_through(self):
        status, _ = self._call(enabled=False)
        # Without the guard, the request reaches the route handler and
        # succeeds (or fails on its own merits, but NOT with 401).
        self.assertNotEqual(status, 401)

    def test_guard_on_rejects_non_local_no_token(self):
        status, body = self._call(enabled=True)
        self.assertEqual(status, 401)
        self.assertIn(b"Unauthorized", body)
        self.assertIn(b"GEOCLAW_GUARD_READ_API", body)

    def test_guard_on_rejects_non_local_wrong_token(self):
        status, _ = self._call(headers={"x-geoclaw-token": "wrong"}, enabled=True)
        self.assertEqual(status, 401)

    def test_guard_on_accepts_correct_header_token(self):
        status, _ = self._call(headers={"x-geoclaw-token": self.TOKEN}, enabled=True)
        self.assertNotEqual(status, 401)

    def test_guard_on_accepts_correct_bearer_token(self):
        status, _ = self._call(headers={"authorization": f"Bearer {self.TOKEN}"}, enabled=True)
        self.assertNotEqual(status, 401)

    def test_guard_on_accepts_correct_query_token(self):
        status, _ = self._call(query=f"token={self.TOKEN}", enabled=True)
        self.assertNotEqual(status, 401)

    def test_guard_on_localhost_bypasses_token(self):
        status, _ = self._call(client_host="127.0.0.1", enabled=True)
        self.assertNotEqual(status, 401)

    def test_guard_on_but_no_env_token_still_rejects_non_local(self):
        """When ``GEOCLAW_LOCAL_TOKEN`` is empty and the flag is on,
        non-local callers get 401 — no silent open-access mode."""
        status, _ = self._call(enabled=True, token="")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
