"""Regression tests for the startup security-posture log.

The log line is the operator's first line of defence against 'I
deployed the hardening PR but forgot to set the env var' — make sure
it never leaks secret values and always surfaces the full set of
vars it claims to cover.
"""
from __future__ import annotations

import logging
import os
import sys
import unittest
from io import StringIO

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from services.security_posture import (  # noqa: E402
    compute_posture,
    format_posture,
    log_security_posture,
)


class ComputePostureTests(unittest.TestCase):
    def test_all_unset(self):
        posture = compute_posture(env={})
        self.assertEqual(posture["local_token"], "UNSET")
        self.assertEqual(posture["webhook_secret"], "UNSET")
        self.assertEqual(posture["guard_read_api"], "OFF")
        self.assertEqual(posture["production_origin"], "UNSET")
        self.assertEqual(posture["trusted_hosts"], "UNSET")

    def test_secrets_never_leak(self):
        env = {
            "GEOCLAW_LOCAL_TOKEN": "super-secret-token-value",
            "TELEGRAM_WEBHOOK_SECRET": "telegram-secret-value",
        }
        posture = compute_posture(env=env)
        self.assertEqual(posture["local_token"], "SET")
        self.assertEqual(posture["webhook_secret"], "SET")
        # The raw secret values MUST NOT appear anywhere in the output.
        rendered = format_posture(posture)
        self.assertNotIn("super-secret-token-value", rendered)
        self.assertNotIn("telegram-secret-value", rendered)

    def test_bool_flag_truthy_variants(self):
        for raw in ("1", "true", "True", "YES", " on "):
            posture = compute_posture(env={"GEOCLAW_GUARD_READ_API": raw})
            self.assertEqual(posture["guard_read_api"], "ON", raw)

    def test_bool_flag_falsy_variants(self):
        for raw in ("", "0", "false", "no", "off", "random-garbage"):
            posture = compute_posture(env={"GEOCLAW_GUARD_READ_API": raw})
            self.assertEqual(posture["guard_read_api"], "OFF", raw)

    def test_public_vars_surface_verbatim(self):
        env = {
            "GEOCLAW_PRODUCTION_ORIGIN": "https://app.example.com",
            "GEOCLAW_TRUSTED_HOSTS": "app.example.com,*.example.com",
        }
        posture = compute_posture(env=env)
        self.assertEqual(posture["production_origin"], "https://app.example.com")
        self.assertEqual(posture["trusted_hosts"], "app.example.com,*.example.com")


class FormatPostureTests(unittest.TestCase):
    def test_stable_ordering(self):
        env = {
            "GEOCLAW_LOCAL_TOKEN": "x",
            "GEOCLAW_PRODUCTION_ORIGIN": "https://a.example.com",
        }
        line = format_posture(compute_posture(env=env))
        self.assertTrue(line.startswith("security: "))
        # Keys must appear in sorted order so deploys can diff logs.
        keys_in_order = [p.split("=", 1)[0] for p in line[len("security: ") :].split(" ")]
        self.assertEqual(keys_in_order, sorted(keys_in_order))


class LogSecurityPostureTests(unittest.TestCase):
    """End-to-end: the function emits a single INFO line via the
    provided logger and never records any secret values.

    We use a ``StringIO`` handler rather than a ``MagicMock`` so the
    test exercises the real logging path the way it would run in
    production.
    """

    def setUp(self):
        self._prev_env = {
            k: os.environ.get(k)
            for k in (
                "GEOCLAW_LOCAL_TOKEN",
                "TELEGRAM_WEBHOOK_SECRET",
                "GEOCLAW_GUARD_READ_API",
                "GEOCLAW_PRODUCTION_ORIGIN",
                "GEOCLAW_TRUSTED_HOSTS",
            )
        }

    def tearDown(self):
        for k, v in self._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_emits_one_info_line(self):
        # Set a recognisable secret so we can grep for it in the output.
        os.environ["GEOCLAW_LOCAL_TOKEN"] = "UNIQUE-LEAK-CANARY-VALUE"

        logger = logging.getLogger("test_security_posture")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        buf = StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False

        log_security_posture(logger)
        output = buf.getvalue()

        self.assertEqual(output.count("security: "), 1, output)
        self.assertIn("local_token=SET", output)
        self.assertNotIn("UNIQUE-LEAK-CANARY-VALUE", output)


if __name__ == "__main__":
    unittest.main()
