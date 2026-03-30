import json
import logging
import os
import urllib.request


logger = logging.getLogger("geoclaw.telegram")


class TelegramBot:
    def __init__(self, db_path=None):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.db_path = db_path
        self._base = f"https://api.telegram.org/bot{self.token}" if self.token else ""

    def available(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self.available():
            return False
        try:
            payload = json.dumps(
                {
                    "chat_id": self.chat_id,
                    "text": str(text or "")[:4000],
                    "parse_mode": parse_mode,
                }
            ).encode()
            req = urllib.request.Request(
                f"{self._base}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)
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
            text = (
                "*GeoClaw Daily Brief*\n"
                f"_{str(row['generated_at'] or '')[:16].replace('T', ' ')} UTC_\n\n"
                f"{str(row['briefing_text'] or '')[:3500]}"
            )
            return self.send_message(text)
        except Exception as exc:
            logger.error("Telegram briefing failed: %s", exc)
            return False

    def send_alert(self, title: str, body: str) -> bool:
        return self.send_message(f"🚨 *GeoClaw Alert: {str(title or '')}*\n\n{str(body or '')[:500]}")

    def send_thesis_update(self, thesis_key: str, confidence: float, delta: float, status: str) -> bool:
        conf_pct = round(float(confidence or 0.0) * 100)
        delta_pct = round(float(delta or 0.0) * 100)
        delta_text = f"+{delta_pct}%" if delta_pct > 0 else f"{delta_pct}%"
        icon = "🟢" if delta_pct > 0 else "🔴"
        text = (
            f"{icon} *Thesis Update*\n\n"
            f"_{str(thesis_key or '')[:120]}_\n\n"
            f"Confidence: *{conf_pct}%* ({delta_text})\n"
            f"Status: {str(status or 'active')}"
        )
        return self.send_message(text)

    def process_incoming(self, update: dict) -> str:
        msg = update.get("message", {}) or {}
        text = str(msg.get("text", "") or "").strip()
        chat_id = str(((msg.get("chat", {}) or {}).get("id", "")) or "")
        if not text or chat_id != str(self.chat_id):
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
            self.send_message(response)
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
            with urllib.request.urlopen(f"{self._base}/getUpdates?offset={int(offset or 0)}&timeout=30", timeout=35) as response:
                data = json.loads(response.read())
                return data.get("result", [])
        except Exception as exc:
            logger.error("getUpdates failed: %s", exc)
            return []
