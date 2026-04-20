"""Regression tests for the shared security-headers middleware.

Hardens both ``main.py`` and ``dashboard_api.py`` so every HTTP response
lands with a baseline set of security headers (nosniff, frame-deny,
strict referrer, locked-down permissions, HSTS).

The tests go through ``TestClient`` and assert on the real response
headers, so a future regression that drops the middleware or widens
any value will flip a test.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("GEOCLAW_DB_BACKEND", "sqlite")

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from fastapi.testclient import TestClient  # noqa: E402

from services.security_headers import (  # noqa: E402
    apply_security_headers,
    default_security_headers,
)


EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
}
# Permissions-Policy contents are asserted on looser contains-checks —
# the full string is long and brittle to diff against.


class SecurityHeadersHelperTests(unittest.TestCase):
    def test_default_returns_fresh_copy(self):
        a = default_security_headers()
        b = default_security_headers()
        self.assertIsNot(a, b)
        a["X-Frame-Options"] = "SAMEORIGIN"
        self.assertEqual(b["X-Frame-Options"], "DENY")

    def test_apply_with_no_existing_headers(self):
        result = apply_security_headers(None)
        self.assertEqual(result.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(result.get("X-Frame-Options"), "DENY")
        self.assertIn("camera=()", result.get("Permissions-Policy", ""))

    def test_apply_skips_already_present(self):
        """If an upstream handler already set X-Frame-Options to
        SAMEORIGIN (legitimate embed case), the middleware should NOT
        clobber it."""
        existing = [(b"x-frame-options", b"SAMEORIGIN")]
        result = apply_security_headers(existing)
        self.assertNotIn("X-Frame-Options", result)
        # Other headers still applied.
        self.assertEqual(result.get("X-Content-Type-Options"), "nosniff")

    def test_apply_case_insensitive_skip(self):
        """Starlette lowercases raw_headers; make sure the skip logic
        works regardless of case."""
        existing = [(b"X-FRAME-OPTIONS", b"SAMEORIGIN")]
        result = apply_security_headers(existing)
        self.assertNotIn("X-Frame-Options", result)

    def test_apply_overrides_dict_wins(self):
        result = apply_security_headers(None, overrides={"X-Frame-Options": "SAMEORIGIN"})
        self.assertEqual(result.get("X-Frame-Options"), "SAMEORIGIN")


class MainAppSecurityHeadersTests(unittest.TestCase):
    """End-to-end: the main FastAPI app must set the headers on every
    response path — HTML, JSON, redirect, 404."""

    @classmethod
    def setUpClass(cls):
        import main  # noqa: E402

        cls.client = TestClient(main.app, follow_redirects=False)

    def _assert_baseline_headers(self, resp):
        for key, value in EXPECTED_HEADERS.items():
            self.assertEqual(
                resp.headers.get(key),
                value,
                msg=f"{resp.status_code} {resp.url} missing/wrong {key}",
            )
        pp = resp.headers.get("permissions-policy", "")
        self.assertIn("camera=()", pp)
        self.assertIn("microphone=()", pp)
        self.assertIn("geolocation=()", pp)

    def test_home_redirect_gets_headers(self):
        resp = self.client.get("/")
        # GET / is a 302 redirect; headers must still be present.
        self.assertIn(resp.status_code, (301, 302, 307))
        self._assert_baseline_headers(resp)

    def test_status_json_gets_headers(self):
        resp = self.client.get("/status")
        self.assertEqual(resp.status_code, 200)
        self._assert_baseline_headers(resp)

    def test_404_html_gets_headers(self):
        # Any unregistered route should still pass through the
        # middleware (FastAPI generates a JSON 404 response).
        resp = self.client.get("/this-route-does-not-exist-12345")
        self.assertEqual(resp.status_code, 404)
        self._assert_baseline_headers(resp)


class DashboardAppSecurityHeadersTests(unittest.TestCase):
    """Same contract for dashboard_api's separate FastAPI app."""

    @classmethod
    def setUpClass(cls):
        import dashboard_api  # noqa: E402

        cls.client = TestClient(dashboard_api.app, follow_redirects=False)

    def test_health_endpoint_gets_headers(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(resp.headers.get("x-frame-options"), "DENY")
        self.assertEqual(resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin")
        self.assertIn("max-age=", resp.headers.get("strict-transport-security", ""))

    def test_unauth_path_still_gets_headers(self):
        """An API path that returns 401 (via _auth_middleware) must
        still carry the security headers — they live on the response
        after the auth middleware runs."""
        resp = self.client.get("/api/instruments", headers={"X-Forwarded-For": "203.0.113.1"})
        # May 200 or 401 depending on how the TestClient presents the
        # client host; either way headers must be set.
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(resp.headers.get("x-frame-options"), "DENY")


if __name__ == "__main__":
    unittest.main()
