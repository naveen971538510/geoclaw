"""Regression tests for ``services.telegram_bot.TelegramBot``.

Covers four findings from the audit:

1. Empty ``TELEGRAM_CHAT_ID`` used to match a malformed payload with
   an empty ``chat.id``.  Now rejected explicitly.
2. ``chat_id`` comparison was ``!=`` — now ``hmac.compare_digest``.
3. Outbound messages interpolated DB-sourced strings into Markdown;
   a stray ``*`` or ``[link](...)`` could break formatting or inject
   content.  Now HTML-escaped + sent with ``parse_mode="HTML"``.
4. ``process_incoming`` reply is now sent with ``parse_mode=""``
   (plain text) because ``QueryEngine.ask(...)`` output is DB-derived.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from typing import List
from unittest.mock import patch

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))


def _reload_with_env(env: dict):
    """Reload ``services.telegram_bot`` with the given env vars in
    place so the module-level ``TelegramBot`` captures them.

    Returns the freshly-imported module.  Tests use this instead of
    patching ``TelegramBot.__init__`` directly so the behaviour
    matches what the app sees at boot.
    """
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        os.environ.pop(key, None)
    for key, value in env.items():
        os.environ[key] = value
    if "services.telegram_bot" in sys.modules:
        return importlib.reload(sys.modules["services.telegram_bot"])
    return importlib.import_module("services.telegram_bot")


class _CapturingUrlopen:
    """Stand-in for ``urllib.request.urlopen`` that records calls."""

    def __init__(self):
        self.calls: List[dict] = []

    def __call__(self, req, timeout=0):
        body = getattr(req, "data", None)
        self.calls.append(
            {
                "url": getattr(req, "full_url", str(req)),
                "headers": dict(getattr(req, "headers", {}) or {}),
                "body": json.loads(body.decode()) if body else None,
            }
        )

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_):
                return False

            def read(self_inner):
                return b"{}"

        return _Resp()


class ProcessIncomingAuthTests(unittest.TestCase):
    """Authentication guards on the webhook entry point."""

    def test_empty_chat_id_env_rejects_everything(self):
        tg = _reload_with_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": ""})
        bot = tg.TelegramBot(db_path=":memory:")
        self.assertEqual(bot.chat_id, "")
        # Malformed payloads should NOT enter the command path just
        # because their chat.id also happens to be empty.
        self.assertEqual(bot.process_incoming({}), "")
        self.assertEqual(bot.process_incoming({"message": {"text": "hi"}}), "")
        self.assertEqual(
            bot.process_incoming({"message": {"text": "hi", "chat": {"id": ""}}}),
            "",
        )

    def test_non_dict_update_rejected(self):
        tg = _reload_with_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"})
        bot = tg.TelegramBot(db_path=":memory:")
        for payload in (None, "not-a-dict", 7, [1, 2], b"bytes"):
            self.assertEqual(bot.process_incoming(payload), "")  # type: ignore[arg-type]

    def test_mismatched_chat_id_rejected(self):
        tg = _reload_with_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"})
        bot = tg.TelegramBot(db_path=":memory:")
        self.assertEqual(
            bot.process_incoming({"message": {"text": "hi", "chat": {"id": "43"}}}),
            "",
        )
        # Corner case: attacker sends a prefix match.  Timing-safe
        # compare_digest rejects it.
        self.assertEqual(
            bot.process_incoming({"message": {"text": "hi", "chat": {"id": "4"}}}),
            "",
        )

    def test_known_command_from_right_chat_is_accepted(self):
        tg = _reload_with_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"})
        bot = tg.TelegramBot(db_path=":memory:")
        with patch.object(bot, "send_message", return_value=True) as send:
            response = bot.process_incoming(
                {"message": {"text": "/help", "chat": {"id": "42"}}}
            )
            self.assertIn("GeoClaw Bot", response)
            send.assert_called_once()


class OutboundEscapingTests(unittest.TestCase):
    """Every helper that interpolates caller/DB-sourced content must
    HTML-escape it before sending.  Verified by capturing the actual
    JSON body sent to the Telegram API."""

    def setUp(self):
        self.tg = _reload_with_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"})
        self.bot = self.tg.TelegramBot(db_path=":memory:")
        self.urlopen = _CapturingUrlopen()
        self._patcher = patch.object(
            self.tg.urllib.request, "urlopen", self.urlopen
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_send_alert_escapes_markdown_and_html_metachars(self):
        self.assertTrue(
            self.bot.send_alert(
                "title * with <script>", "body [link](https://evil.example.com)"
            )
        )
        call = self.urlopen.calls[-1]
        sent = call["body"]["text"]
        self.assertEqual(call["body"]["parse_mode"], "HTML")
        # Raw ``<script>`` must have been escaped.
        self.assertNotIn("<script>", sent)
        self.assertIn("&lt;script&gt;", sent)
        # Literal Markdown link text passes through but its ``[`` ``]``
        # brackets don't form a Telegram-Markdown link because we're
        # sending HTML.  What matters: no executable URL.
        self.assertIn("[link]", sent)

    def test_send_thesis_update_escapes_thesis_key(self):
        payload = "OIL * spikes <b>injected</b>"
        self.assertTrue(self.bot.send_thesis_update(payload, 0.8, 0.1, "confirmed"))
        call = self.urlopen.calls[-1]
        sent = call["body"]["text"]
        self.assertEqual(call["body"]["parse_mode"], "HTML")
        self.assertIn("&lt;b&gt;injected&lt;/b&gt;", sent)
        self.assertNotIn("<b>injected</b>", sent)

    def test_send_briefing_html_escapes_db_body(self):
        # Plant a row with malicious-looking content via an in-memory
        # sqlite db and call send_briefing.
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE agent_briefings (briefing_text TEXT, generated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO agent_briefings VALUES (?, ?)",
            ("payload <script>x</script> * _italic_", "2030-01-02T03:04:05"),
        )
        conn.commit()

        with patch("sqlite3.connect", return_value=conn):
            self.assertTrue(self.bot.send_briefing())

        call = self.urlopen.calls[-1]
        sent = call["body"]["text"]
        self.assertEqual(call["body"]["parse_mode"], "HTML")
        self.assertIn("&lt;script&gt;", sent)
        self.assertNotIn("<script>", sent)


class ProcessIncomingReplyPlainTextTests(unittest.TestCase):
    """The reply path in ``process_incoming`` must go through
    ``send_message(..., parse_mode="")`` because the body comes from
    ``QueryEngine.ask(...)`` (DB-derived, not markup-safe)."""

    def test_reply_sent_as_plain_text(self):
        tg = _reload_with_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "42"})
        bot = tg.TelegramBot(db_path=":memory:")
        calls = []

        def _send(text, parse_mode="Markdown"):
            calls.append((text, parse_mode))
            return True

        with patch.object(bot, "send_message", side_effect=_send):
            bot.process_incoming({"message": {"text": "/help", "chat": {"id": "42"}}})

        self.assertEqual(len(calls), 1)
        _, parse_mode = calls[0]
        self.assertEqual(parse_mode, "")


if __name__ == "__main__":
    unittest.main()
