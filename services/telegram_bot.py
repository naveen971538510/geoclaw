"""Telegram bot client + webhook-dispatch handler.

Security audit notes (see also tests/test_telegram_bot_security.py):

* The bot interpolates attacker- and database-sourced strings into
  outbound messages.  Markdown is not a structural format — a thesis
  headline containing ``*`` or ``[`` is enough to break formatting or
  inject a clickable link pointing somewhere nasty.  Every helper that
  includes user- or DB-sourced content therefore sends with
  ``parse_mode="HTML"`` and runs the content through ``html.escape``
  first; the reply loop in ``process_incoming`` sends back with
  ``parse_mode=""`` (plain text) because ``QueryEngine.ask(...)``
  output is DB-derived and we cannot guarantee it is markup-safe.

* ``process_incoming`` authenticates the sender by comparing the
  incoming ``chat.id`` to ``TELEGRAM_CHAT_ID``.  Two failure modes get
  fixed here:
    - an empty/unset ``TELEGRAM_CHAT_ID`` used to match the empty
      ``chat.id`` on a malformed payload, letting any sender through
      on a misconfigured deploy.
    - the comparison used a plain ``!=`` operator; we now use
      ``hmac.compare_digest`` as defense in depth, even though the
      numeric chat-id space is small.
"""
import hmac
import html
import json
import logging
import os
import urllib.request


logger = logging.getLogger("geoclaw.telegram")


# Hard-cap incoming user text before it reaches ``QueryEngine.ask`` so
# a huge body can't pin a worker.  4096 is Telegram's own outbound
# message limit; anything longer than that is already not a normal
# user message.
_MAX_INCOMING_TEXT = 4096


def _escape_html(value) -> str:
    """Escape untrusted text for safe interpolation into an HTML
    Telegram message.  Telegram's HTML parser accepts ``<b>``,
    ``<i>``, ``<code>``, ``<a>`` etc.; anything else must be
    html-escaped so a stray ``<`` can't start a tag."""
    return html.escape(str(value or ""), quote=False)


class TelegramBot:
    def __init__(self, db_path=None):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.db_path = db_path
        self._base = f"https://api.telegram.org/bot{self.token}" if self.token else ""

    def available(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send ``text`` to the configured chat.

        ``parse_mode`` may be ``"Markdown"``, ``"MarkdownV2"``,
        ``"HTML"``, or ``""`` (plain text, no parsing).  Callers that
        interpolate untrusted / DB-sourced content should use
        ``"HTML"`` with ``_escape_html`` or ``""`` — never plain
        ``"Markdown"`` with raw interpolation (see module docstring).
        """
        if not self.available():
            return False
        try:
            body = {
                "chat_id": self.chat_id,
                "text": str(text or "")[:4000],
            }
            if parse_mode:
                body["parse_mode"] = parse_mode
            payload = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{self._base}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)  # noqa: S310 (fixed Telegram API URL)
            logger.info("Telegram message sent")
            return True
        except Exception as exc:
            logger.error("Telegram failed: %s", exc)
            return False

    def send_briefing(self, db_path=None) -> bool:
        db_path = db_path or self.db_path
        try:
            import sqlite3

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT briefing_text, generated_at
                FROM agent_briefings
                ORDER BY generated_at DESC
                LIMIT 1
                """
            ).fetchone()
            conn.close()
            if not row:
                return False
            generated = str(row["generated_at"] or "")[:16].replace("T", " ")
            briefing = str(row["briefing_text"] or "")[:3500]
            # Briefing body comes from the DB (LLM-generated); escape
            # before embedding in HTML so Markdown/HTML metacharacters
            # can't break formatting or inject tags.
            text = (
                "<b>GeoClaw Daily Brief</b>\n"
                f"<i>{_escape_html(generated)} UTC</i>\n\n"
                f"{_escape_html(briefing)}"
            )
            return self.send_message(text, parse_mode="HTML")
        except Exception as exc:
            logger.error("Telegram briefing failed: %s", exc)
            return False

    def send_alert(self, title: str, body: str) -> bool:
        # Title + body are caller-controlled (can transit
        # DB-sourced thesis text); escape before interpolating into
        # the HTML template.
        safe_title = _escape_html(title)[:200]
        safe_body = _escape_html(body)[:500]
        text = f"🚨 <b>GeoClaw Alert: {safe_title}</b>\n\n{safe_body}"
        return self.send_message(text, parse_mode="HTML")

    def send_thesis_update(self, thesis_key: str, confidence: float, delta: float, status: str) -> bool:
        conf_pct = round(float(confidence or 0.0) * 100)
        delta_pct = round(float(delta or 0.0) * 100)
        delta_text = f"+{delta_pct}%" if delta_pct > 0 else f"{delta_pct}%"
        icon = "🟢" if delta_pct > 0 else "🔴"
        # thesis_key + status flow from the agent thesis DB — escape
        # before embedding in HTML.
        safe_key = _escape_html(thesis_key)[:120]
        safe_status = _escape_html(status or "active")
        text = (
            f"{icon} <b>Thesis Update</b>\n\n"
            f"<i>{safe_key}</i>\n\n"
            f"Confidence: <b>{conf_pct}%</b> ({delta_text})\n"
            f"Status: {safe_status}"
        )
        return self.send_message(text, parse_mode="HTML")

    def process_incoming(self, update: dict) -> str:
        # Defense against a stringly-typed / malformed webhook body.
        if not isinstance(update, dict):
            return ""
        # Require a configured chat id — an empty env var used to
        # match the empty ``chat.id`` on a malformed payload.
        if not self.chat_id:
            return ""

        msg = update.get("message") if isinstance(update.get("message"), dict) else {}
        text = str(msg.get("text", "") or "").strip()[:_MAX_INCOMING_TEXT]
        chat_payload = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = str(chat_payload.get("id", "") or "")
        if not text or not chat_id:
            return ""
        # Timing-safe compare — the numeric chat-id space is small
        # and the bot only speaks to one operator, but compare_digest
        # is cheap insurance and keeps every ``chat_id`` check in
        # the codebase uniform (``main.py``'s token guard already
        # uses it).
        if not hmac.compare_digest(chat_id, str(self.chat_id)):
            return ""

        from services.query_engine import QueryEngine

        engine = QueryEngine(self.db_path)
        lower = text.lower()

        if lower in ("/start", "/help"):
            response = (
                "🤖 *GeoClaw Bot*\n\n"
                "Commands:\n"
                "/brief — latest intelligence brief\n"
                "/status — agent status\n"
                "/top — top 5 theses\n"
                "/risk — current risk level\n"
                "/regime — market regime\n"
                "Or just type any question."
            )
        elif lower == "/brief":
            response = self._get_latest_briefing()[:4000] or "No briefing available yet."
        elif lower == "/status":
            response = engine.ask("summary").get("answer", "No data.")
        elif lower == "/top":
            response = engine.ask("show top theses").get("answer", "No theses found.")
        elif lower == "/risk":
            response = engine.ask("what is the risk right now").get("answer", "No risk data.")
        elif lower == "/regime":
            response = engine.ask("what is the market regime").get("answer", "No regime data.")
        elif lower.startswith("/"):
            response = "Unknown command. Try /help"
        else:
            response = engine.ask(text).get("answer", "I couldn't find an answer to that.")

        if response:
            # ``response`` is QueryEngine output built from DB rows —
            # send as plain text so any ``*`` / ``_`` / ``[`` / ``<``
            # sitting in the data can't break formatting or inject
            # links/tags.  The ``/start`` and ``/help`` branches use a
            # hand-authored Markdown template and are safe by
            # construction, but we trade their formatting for the
            # stronger safety property.
            self.send_message(response, parse_mode="")
        return response

    def _get_latest_briefing(self) -> str:
        try:
            import sqlite3

            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                """
                SELECT briefing_text
                FROM agent_briefings
                ORDER BY generated_at DESC
                LIMIT 1
                """
            ).fetchone()
            conn.close()
            return row[0] if row else ""
        except Exception:
            return ""

    def get_updates(self, offset: int = 0) -> list:
        if not self.token:
            return []
        try:
            with urllib.request.urlopen(  # noqa: S310 (fixed Telegram API URL)
                f"{self._base}/getUpdates?offset={int(offset or 0)}&timeout=30", timeout=35
            ) as response:
                data = json.loads(response.read())
                return data.get("result", [])
        except Exception as exc:
            logger.error("getUpdates failed: %s", exc)
            return []
