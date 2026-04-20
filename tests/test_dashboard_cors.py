"""Regression tests for the dashboard_api CORS tightening (L1).

Before this, `CORSMiddleware` was configured with
``allow_methods=["*"]`` and ``allow_headers=["*"]``. That's not a
vulnerability on its own (the origin allow-list is explicit), but it's
defense-in-depth to only permit the methods and headers the API
actually serves / reads.

These tests exercise the real ``fastapi.CORSMiddleware`` behaviour via
``TestClient``, not the internal allow-lists, so a future regression
that widens ``allow_methods`` / ``allow_headers`` back to ``"*"`` would
quietly fail by echoing headers we assert against here.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("GEOCLAW_DB_BACKEND", "sqlite")

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from fastapi.testclient import TestClient  # noqa: E402

import dashboard_api  # noqa: E402


ALLOWED_ORIGIN = "http://localhost:5173"
DISALLOWED_ORIGIN = "https://evil.example.com"


class DashboardCorsPreflightTests(unittest.TestCase):
    """Drive OPTIONS preflights through the real CORSMiddleware and
    verify the explicit allow-lists are honoured."""

    def setUp(self):
        self.client = TestClient(dashboard_api.app)

    def _preflight(self, origin: str, method: str, headers: str = "content-type"):
        return self.client.options(
            "/api/signals",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": headers,
            },
        )

    def test_allowed_get_preflight_echoes_origin(self):
        resp = self._preflight(ALLOWED_ORIGIN, "GET")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            ALLOWED_ORIGIN,
        )
        allowed = {m.strip() for m in (resp.headers.get("access-control-allow-methods") or "").split(",")}
        self.assertIn("GET", allowed)
        self.assertIn("POST", allowed)

    def test_allowed_post_preflight_ok(self):
        resp = self._preflight(ALLOWED_ORIGIN, "POST")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            ALLOWED_ORIGIN,
        )

    def test_disallowed_method_preflight_rejected(self):
        """Starlette's CORSMiddleware fails preflight with 400 when the
        requested method isn't in allow_methods. Browsers treat the 400
        as a blocked preflight and refuse to send the actual request,
        even though the middleware still echoes informational CORS
        headers on the error response."""
        for method in ("DELETE", "PUT", "PATCH"):
            resp = self._preflight(ALLOWED_ORIGIN, method)
            self.assertEqual(resp.status_code, 400, msg=method)
            # Regression guard: the rejected method must NOT appear in
            # the advertised allow-methods (otherwise the allow-list is
            # effectively widened by mistake).
            allowed = {
                m.strip().upper()
                for m in (resp.headers.get("access-control-allow-methods") or "").split(",")
            }
            self.assertNotIn(method, allowed, msg=method)

    def test_methods_header_never_wildcards(self):
        """Regression guard: if someone reverts allow_methods back to
        ``["*"]``, the response header would be ``*``. Pin it."""
        resp = self._preflight(ALLOWED_ORIGIN, "GET")
        methods = resp.headers.get("access-control-allow-methods") or ""
        self.assertNotEqual(methods.strip(), "*")

    def test_headers_header_never_wildcards(self):
        resp = self._preflight(ALLOWED_ORIGIN, "POST", headers="authorization, content-type")
        allow_headers = resp.headers.get("access-control-allow-headers") or ""
        self.assertNotEqual(allow_headers.strip(), "*")
        # And the headers we care about must be present.
        present = {h.strip().lower() for h in allow_headers.split(",")}
        self.assertIn("authorization", present)
        self.assertIn("content-type", present)

    def test_disallowed_origin_preflight_is_rejected(self):
        resp = self._preflight(DISALLOWED_ORIGIN, "GET")
        # CORSMiddleware returns 400 for disallowed origins in preflight.
        self.assertNotEqual(
            resp.headers.get("access-control-allow-origin"),
            DISALLOWED_ORIGIN,
        )
        self.assertNotEqual(resp.headers.get("access-control-allow-origin"), "*")

    def test_actual_get_request_still_works_from_allowed_origin(self):
        """Sanity: a real GET from an allowed origin must still succeed
        and echo the Origin back on the actual response (not the preflight)."""
        resp = self.client.get("/api/instruments", headers={"Origin": ALLOWED_ORIGIN})
        self.assertEqual(resp.status_code, 200, resp.text)
        # CORSMiddleware adds ACAO on actual (non-preflight) CORS responses too.
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            ALLOWED_ORIGIN,
        )


if __name__ == "__main__":
    unittest.main()
