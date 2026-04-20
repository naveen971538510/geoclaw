"""Regression tests for the opt-in ``GEOCLAW_TRUSTED_HOSTS`` guard.

Starlette's ``TrustedHostMiddleware`` is registered at module-import
time based on the env var, so to exercise both the off and on states
we re-import the target module under different environments.  We use
``importlib.reload`` rather than spawning a subprocess so the test is
fast and self-contained.

Covered:
  * Default (env unset) — no Host allow-list; arbitrary Host headers
    pass through.
  * Env set — Host header matching the allow-list passes, anything
    else is rejected with 400.
  * ``localhost`` and ``testserver`` are implicitly added so
    TestClient's default Host keeps working regardless of env.
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest

os.environ.setdefault("GEOCLAW_DB_BACKEND", "sqlite")

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from fastapi.testclient import TestClient  # noqa: E402


class _BaseReloadTests(unittest.TestCase):
    """Reload ``main`` (or ``dashboard_api``) under a controlled env."""

    MODULE_NAME: str = ""  # set by subclass

    @classmethod
    def _reload_with_env(cls, trusted_hosts: str | None):
        prev = os.environ.get("GEOCLAW_TRUSTED_HOSTS")
        try:
            if trusted_hosts is None:
                os.environ.pop("GEOCLAW_TRUSTED_HOSTS", None)
            else:
                os.environ["GEOCLAW_TRUSTED_HOSTS"] = trusted_hosts
            # ``sys.modules`` may hold a stale copy; pop to force a
            # fresh import that re-reads the env var at module scope.
            sys.modules.pop(cls.MODULE_NAME, None)
            return importlib.import_module(cls.MODULE_NAME)
        finally:
            if prev is None:
                os.environ.pop("GEOCLAW_TRUSTED_HOSTS", None)
            else:
                os.environ["GEOCLAW_TRUSTED_HOSTS"] = prev

    @classmethod
    def tearDownClass(cls):
        # Leave sys.modules in a fresh state so later tests re-import
        # without our env overrides.
        os.environ.pop("GEOCLAW_TRUSTED_HOSTS", None)
        sys.modules.pop(cls.MODULE_NAME, None)
        super().tearDownClass()


class MainAppTrustedHostsTests(_BaseReloadTests):
    MODULE_NAME = "main"

    def test_default_unset_accepts_any_host(self):
        mod = self._reload_with_env(None)
        client = TestClient(mod.app, follow_redirects=False)
        resp = client.get("/status", headers={"Host": "evil.example.com"})
        # /status should answer 200 whatever the Host is — middleware
        # isn't registered.
        self.assertEqual(resp.status_code, 200)

    def test_enabled_accepts_configured_host(self):
        mod = self._reload_with_env("app.example.com")
        client = TestClient(mod.app, follow_redirects=False)
        resp = client.get("/status", headers={"Host": "app.example.com"})
        self.assertEqual(resp.status_code, 200)

    def test_enabled_rejects_unknown_host(self):
        mod = self._reload_with_env("app.example.com")
        client = TestClient(mod.app, follow_redirects=False)
        resp = client.get("/status", headers={"Host": "evil.example.com"})
        self.assertEqual(resp.status_code, 400)
        # Starlette's default body; case-insensitive substring.
        self.assertIn(b"host header", resp.content.lower())

    def test_enabled_still_accepts_testserver(self):
        """The ``testserver`` default used by TestClient must keep
        working even with a strict allow-list."""
        mod = self._reload_with_env("app.example.com")
        client = TestClient(mod.app, follow_redirects=False)
        # No explicit Host header → TestClient sends "testserver".
        resp = client.get("/status")
        self.assertEqual(resp.status_code, 200)

    def test_enabled_still_accepts_localhost(self):
        mod = self._reload_with_env("app.example.com")
        client = TestClient(mod.app, follow_redirects=False)
        resp = client.get("/status", headers={"Host": "localhost"})
        self.assertEqual(resp.status_code, 200)

    def test_wildcard_subdomain(self):
        mod = self._reload_with_env("*.example.com")
        client = TestClient(mod.app, follow_redirects=False)
        ok = client.get("/status", headers={"Host": "app.example.com"})
        self.assertEqual(ok.status_code, 200)
        # Wildcard matches subdomains, not apex.
        apex = client.get("/status", headers={"Host": "example.com"})
        self.assertEqual(apex.status_code, 400)


class DashboardAppTrustedHostsTests(_BaseReloadTests):
    MODULE_NAME = "dashboard_api"

    def test_default_unset_accepts_any_host(self):
        mod = self._reload_with_env(None)
        client = TestClient(mod.app, follow_redirects=False)
        resp = client.get("/health", headers={"Host": "evil.example.com"})
        self.assertEqual(resp.status_code, 200)

    def test_enabled_rejects_unknown_host(self):
        mod = self._reload_with_env("app.example.com")
        client = TestClient(mod.app, follow_redirects=False)
        resp = client.get("/health", headers={"Host": "evil.example.com"})
        self.assertEqual(resp.status_code, 400)

    def test_enabled_accepts_configured_host(self):
        mod = self._reload_with_env("app.example.com")
        client = TestClient(mod.app, follow_redirects=False)
        resp = client.get("/health", headers={"Host": "app.example.com"})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
