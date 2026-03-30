import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict


logger = logging.getLogger("geoclaw.executor")

AUTO_APPROVE_SAFE = {
    "alert",
    "briefing_highlight",
    "add_to_watchlist",
    "log_intelligence",
    "send_telegram",
    "desktop_notification",
}

REQUIRE_HUMAN = {
    "trade",
    "transfer",
    "external_api",
    "risk_flag",
    "human_review",
    "send_email",
    "send_webhook",
    "webhook",
    "email_summary",
    "slack_payload",
}


class ActionExecutor:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def execute_action(self, action: Dict) -> Dict:
        action = dict(action or {})
        action_type = str(action.get("action_type") or "unknown").strip()
        reason = str(action.get("reason") or action.get("audit_note") or "").strip()
        metadata = self._as_json(action.get("metadata"))
        payload = self._as_json(action.get("payload"))
        if not payload and action.get("payload_json"):
            payload = self._as_json(action.get("payload_json"))
        merged = {**payload, **metadata}
        handlers = {
            "add_to_watchlist": self._execute_add_watchlist,
            "briefing_highlight": self._execute_briefing_highlight,
            "log_intelligence": self._execute_log_intelligence,
            "send_telegram": self._execute_send_telegram,
            "desktop_notification": self._execute_desktop_notification,
            "send_email": self._execute_send_email,
            "email_summary": self._execute_send_email,
            "send_webhook": self._execute_send_webhook,
            "webhook": self._execute_send_webhook,
            "slack_payload": self._execute_send_webhook,
            "risk_flag": self._execute_risk_flag,
            "close_thesis": self._execute_close_thesis,
            "write_file": self._execute_write_file,
            "alert": self._execute_alert,
        }
        handler = handlers.get(action_type, self._execute_unknown)
        logger.info("Executing action: %s — %s", action_type, reason[:80])
        try:
            result = handler(action, merged)
            self._mark_executed(int(action.get("id", 0) or 0), action, result)
            return {"status": "ok", "action_type": action_type, "result": result}
        except Exception as exc:
            logger.error("Action execution failed (%s): %s", action_type, exc, exc_info=True)
            self._mark_failed(int(action.get("id", 0) or 0), action, str(exc))
            return {"status": "error", "action_type": action_type, "error": str(exc)}

    def _as_json(self, value):
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}

    def _execute_add_watchlist(self, action: Dict, meta: Dict):
        thesis_key = str(action.get("thesis_key") or "").strip()
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        thesis = conn.execute(
            "SELECT watchlist_suggestion FROM agent_theses WHERE thesis_key = ? LIMIT 1",
            (thesis_key,),
        ).fetchone()
        suggestion = (thesis[0] if thesis else "") or meta.get("symbol") or "WATCH"
        import re

        symbols = re.findall(r"\b[A-Z]{2,8}(?:/[A-Z]{2,3})?\b", str(suggestion or ""))
        symbol = symbols[0] if symbols else str(suggestion or "WATCH")[:12]
        conn.execute(
            """
            INSERT OR IGNORE INTO watchlist (
                symbol, asset_type, thesis_key, reason, direction, added_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                "auto",
                thesis_key,
                str(action.get("reason") or action.get("audit_note") or "")[:200],
                "Monitor",
                datetime.now(timezone.utc).isoformat(),
                "active",
            ),
        )
        conn.commit()
        conn.close()
        return {"added": symbol, "thesis": thesis_key[:80]}

    def _execute_briefing_highlight(self, action: Dict, meta: Dict):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        thesis_key = str(action.get("thesis_key") or "").strip()
        if thesis_key:
            conn.execute(
                """
                UPDATE agent_theses
                SET last_update_reason = ?
                WHERE thesis_key = ?
                """,
                (f"[HIGHLIGHTED] {str(action.get('reason') or action.get('audit_note') or '')[:100]}", thesis_key),
            )
            conn.commit()
        conn.close()
        return {"highlighted": thesis_key[:80]}

    def _execute_log_intelligence(self, action: Dict, meta: Dict):
        log_path = os.path.join(os.path.dirname(self.db_path), "intelligence_log.txt")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        line = f"[{now}] {action.get('action_type', '')} | {str(action.get('thesis_key') or '')[:120]} | {str(action.get('reason') or action.get('audit_note') or '')[:240]}\n"
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
        return {"log_path": log_path, "written": True}

    def _execute_send_telegram(self, action: Dict, meta: Dict):
        from services.telegram_bot import TelegramBot

        bot = TelegramBot(self.db_path)
        if not bot.available():
            return {"sent": False, "reason": "Telegram not configured"}
        thesis_key = str(action.get("thesis_key") or "")
        reason = str(action.get("reason") or action.get("audit_note") or "")
        message = f"⚡ *GeoClaw Action*\n\n{reason[:300]}\n\n_{thesis_key[:120]}_"
        return {"sent": bool(bot.send_message(message))}

    def _execute_desktop_notification(self, action: Dict, meta: Dict):
        from services.alert_service import AlertService

        AlertService(self.db_path)._send_desktop(
            title=f"Action: {str(action.get('action_type') or '')}",
            body=str(action.get("reason") or action.get("audit_note") or "")[:120],
        )
        return {"notified": True}

    def _execute_send_email(self, action: Dict, meta: Dict):
        from services.alert_service import AlertService

        alerter = AlertService(self.db_path)
        if not alerter.email_from:
            return {"sent": False, "reason": "Email not configured"}
        thesis_key = str(action.get("thesis_key") or "")
        body = str(meta.get("body") or action.get("reason") or action.get("audit_note") or "")
        alerter._send_email(
            title=f"GeoClaw: {str(action.get('action_type') or 'action')}",
            body=f"{body}\n\nThesis: {thesis_key[:200]}",
        )
        return {"sent": True}

    def _execute_send_webhook(self, action: Dict, meta: Dict):
        webhook_url = str(meta.get("url") or os.environ.get("ALERT_WEBHOOK_URL") or "").strip()
        if not webhook_url:
            return {"sent": False, "reason": "No webhook URL"}
        import urllib.request

        payload = json.dumps(meta or {"text": f"GeoClaw: {action.get('action_type')} — {action.get('reason') or action.get('audit_note') or ''}"}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return {"sent": True, "url": webhook_url[:80]}

    def _execute_risk_flag(self, action: Dict, meta: Dict):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            UPDATE agent_theses
            SET terminal_risk = 'HIGH',
                last_update_reason = ?
            WHERE thesis_key = ?
            """,
            (f"[FLAGGED] {str(action.get('reason') or action.get('audit_note') or '')[:100]}", str(action.get("thesis_key") or "")),
        )
        conn.commit()
        conn.close()
        return {"flagged": str(action.get("thesis_key") or "")[:80]}

    def _execute_close_thesis(self, action: Dict, meta: Dict):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            UPDATE agent_theses
            SET status = 'superseded',
                last_update_reason = ?
            WHERE thesis_key = ?
            """,
            (f"Closed by action: {str(action.get('reason') or action.get('audit_note') or '')[:80]}", str(action.get("thesis_key") or "")),
        )
        conn.commit()
        conn.close()
        return {"closed": str(action.get("thesis_key") or "")[:80]}

    def _execute_write_file(self, action: Dict, meta: Dict):
        filename = str(meta.get("filename") or "geoclaw_output.txt")
        content = str(meta.get("content") or action.get("reason") or action.get("audit_note") or "")
        path = os.path.join(os.path.dirname(self.db_path), filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return {"file": path}

    def _execute_alert(self, action: Dict, meta: Dict):
        from services.alert_service import AlertService

        title = str(meta.get("title") or action.get("thesis_key") or "GeoClaw Alert")
        body = str(meta.get("current_claim") or action.get("reason") or action.get("audit_note") or "")[:240]
        AlertService(self.db_path).fire(title, body, alert_name="action_alert")
        return {"alerted": True, "title": title[:120]}

    def _execute_unknown(self, action: Dict, meta: Dict):
        logger.warning("Unknown action type: %s", action.get("action_type"))
        return {"executed": False, "reason": "unknown action type"}

    def _mark_executed(self, action_id: int, action: Dict, result: Dict):
        if not action_id:
            return
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        approval_state = str(action.get("approval_state") or "").strip() or (
            "auto_approved" if str(action.get("status") or "") == "auto_approved" else "approved"
        )
        payload = self._as_json(action.get("metadata"))
        payload.update(result or {})
        payload["executed_at"] = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE agent_actions
            SET status = 'completed',
                approval_state = ?,
                executed_at = ?,
                metadata = ?
            WHERE id = ?
            """,
            (approval_state, payload["executed_at"], json.dumps(payload, ensure_ascii=False), int(action_id)),
        )
        conn.commit()
        conn.close()

    def _mark_failed(self, action_id: int, action: Dict, error: str):
        if not action_id:
            return
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        payload = self._as_json(action.get("metadata"))
        payload.update({"error": str(error), "failed_at": datetime.now(timezone.utc).isoformat()})
        conn.execute(
            """
            UPDATE agent_actions
            SET status = 'failed',
                metadata = ?
            WHERE id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), int(action_id)),
        )
        conn.commit()
        conn.close()

    def execute_auto_approved(self) -> Dict:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM agent_actions
            WHERE status = 'auto_approved'
              AND action_type IN ({})
              AND COALESCE(executed_at, '') = ''
            ORDER BY created_at ASC, id ASC
            LIMIT 10
            """.format(",".join("?" * len(AUTO_APPROVE_SAFE))),
            tuple(sorted(AUTO_APPROVE_SAFE)),
        ).fetchall()
        conn.close()
        executed = 0
        results = []
        for row in rows:
            result = self.execute_action(dict(row))
            results.append(result)
            if result.get("status") == "ok":
                executed += 1
        return {"auto_executed": executed, "results": results}

    def execute_manually_approved(self) -> Dict:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM agent_actions
            WHERE approval_state = 'approved'
              AND status = 'approved'
              AND COALESCE(executed_at, '') = ''
            ORDER BY created_at ASC, id ASC
            LIMIT 10
            """
        ).fetchall()
        conn.close()
        executed = 0
        results = []
        for row in rows:
            result = self.execute_action(dict(row))
            results.append(result)
            if result.get("status") == "ok":
                executed += 1
        return {"manually_executed": executed, "results": results}
