import json
import logging
import os
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage


logger = logging.getLogger("geoclaw.alerts")


ALERT_CONDITIONS = [
    {
        "name": "high_confidence_thesis",
        "check": lambda t: (t.get("confidence") or 0) >= 0.80,
        "title": "High Confidence Thesis",
        "body_fn": lambda t: (
            f"Confidence reached {round((t.get('confidence') or 0) * 100)}%\n\n"
            f"{str(t.get('thesis_key') or '')[:200]}\n\n"
            f"Terminal risk: {t.get('terminal_risk', 'unknown')}\n"
            f"Watch: {t.get('watchlist_suggestion', '')}"
        ),
        "cooldown_hours": 4,
    },
    {
        "name": "confirmed_thesis",
        "check": lambda t: str(t.get("status") or "").lower() == "confirmed",
        "title": "Thesis Confirmed",
        "body_fn": lambda t: (
            f"Thesis promoted to CONFIRMED "
            f"({round((t.get('confidence') or 0) * 100)}%):\n\n"
            f"{str(t.get('thesis_key') or '')[:200]}"
        ),
        "cooldown_hours": 6,
    },
    {
        "name": "high_risk_action",
        "check": lambda a: "HIGH" in str(a.get("reason", "") or a.get("audit_note", "")).upper(),
        "title": "HIGH Risk Action Proposed",
        "body_fn": lambda a: (
            f"HIGH risk action:\n{a.get('action_type', '')}\n\n"
            f"Reason: {str(a.get('reason') or a.get('audit_note') or '')[:200]}"
        ),
        "cooldown_hours": 2,
    },
]


class AlertService:
    def __init__(self, db_path):
        self.db_path = db_path
        self.email_from = os.environ.get("ALERT_EMAIL_FROM", "")
        self.email_to = os.environ.get("ALERT_EMAIL_TO", "")
        self.email_pass = os.environ.get("ALERT_EMAIL_PASS", "")
        self.smtp_host = os.environ.get("ALERT_SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("ALERT_SMTP_PORT", "587"))
        self.webhook_url = os.environ.get("ALERT_WEBHOOK_URL", "")
        self.desktop = os.environ.get("ALERT_DESKTOP", "true").lower() == "true"

    def _cooldown_ok(self, alert_name, cooldown_hours):
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                """
                SELECT created_at
                FROM alert_events
                WHERE alert_type = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (str(alert_name or ""),),
            ).fetchone()
            conn.close()
            if not row:
                return True
            last = datetime.fromisoformat(str(row[0] or "").replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - last) > timedelta(hours=int(cooldown_hours or 0))
        except Exception:
            return True

    def _resolve_article_id(self, payload=None):
        payload = payload or {}
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            article_id = int(payload.get("article_id") or 0)
            if article_id:
                row = cur.execute("SELECT id FROM ingested_articles WHERE id = ? LIMIT 1", (article_id,)).fetchone()
                if row:
                    conn.close()
                    return int(row["id"])

            thesis_key = str(payload.get("thesis_key") or "").strip().lower()
            if thesis_key:
                row = cur.execute(
                    """
                    SELECT last_article_id
                    FROM agent_theses
                    WHERE LOWER(COALESCE(thesis_key, '')) = ?
                    LIMIT 1
                    """,
                    (thesis_key,),
                ).fetchone()
                if row and row["last_article_id"]:
                    conn.close()
                    return int(row["last_article_id"])

                row = cur.execute(
                    """
                    SELECT article_id
                    FROM reasoning_chains
                    WHERE LOWER(COALESCE(thesis_key, '')) = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (thesis_key,),
                ).fetchone()
                if row and row["article_id"]:
                    conn.close()
                    return int(row["article_id"])

            row = cur.execute(
                """
                SELECT id
                FROM ingested_articles
                ORDER BY COALESCE(published_at, fetched_at, '') DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            conn.close()
            return int(row["id"]) if row else None
        except Exception:
            return None

    def _log_alert(self, alert_name, title, body, payload=None):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(alert_events)").fetchall()}
            created_at = datetime.now(timezone.utc).isoformat()
            if "article_id" in columns:
                article_id = self._resolve_article_id(payload)
                if not article_id:
                    logger.warning("Alert log skipped because no article_id could be resolved for %s", alert_name)
                    conn.close()
                    return
                conn.execute(
                    """
                    INSERT INTO alert_events (
                        article_id, priority, reason, created_at,
                        is_read, status, resolved, resolution_note, resolved_at,
                        alert_type, title, body
                    )
                    VALUES (?, ?, ?, ?, 0, 'open', 0, '', '', ?, ?, ?)
                    """,
                    (
                        int(article_id),
                        "high",
                        str(title or "")[:200],
                        created_at,
                        str(alert_name or "")[:120],
                        str(title or "")[:200],
                        str(body or "")[:500],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO alert_events (
                        alert_type, title, body, created_at, resolved, resolution_note, resolved_at
                    )
                    VALUES (?, ?, ?, ?, 0, '', '')
                    """,
                    (
                        str(alert_name or "")[:120],
                        str(title or "")[:200],
                        str(body or "")[:500],
                        created_at,
                    ),
                )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.error("Alert log failed: %s", exc)

    def _send_desktop(self, title, body):
        safe_title = str(title or "").replace('"', "'")
        safe_body = str(body or "").replace('"', "'").replace("\n", " ")
        try:
            import subprocess

            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{safe_body[:100]}" with title "GeoClaw: {safe_title}"',
                ],
                timeout=5,
                capture_output=True,
                check=False,
            )
        except Exception:
            try:
                import subprocess

                subprocess.run(
                    ["notify-send", f"GeoClaw: {safe_title}", safe_body[:100]],
                    timeout=5,
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass

    def _send_email(self, title, body):
        if not (self.email_from and self.email_to and self.email_pass):
            return
        try:
            msg = EmailMessage()
            msg["Subject"] = f"GeoClaw Alert: {title}"
            msg["From"] = self.email_from
            msg["To"] = self.email_to
            msg.set_content(
                f"{body}\n\n---\nGeoClaw | {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}"
            )
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(self.email_from, self.email_pass)
                smtp.send_message(msg)
            logger.info("Email alert sent: %s", title)
        except Exception as exc:
            logger.error("Email failed: %s", exc)

    def _send_webhook(self, title, body):
        if not self.webhook_url:
            return
        try:
            import urllib.request

            payload = json.dumps({"text": f"*GeoClaw: {title}*\n{str(body or '')[:400]}"}).encode()
            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            logger.info("Webhook sent: %s", title)
        except Exception as exc:
            logger.error("Webhook failed: %s", exc)

    def fire(self, title, body, alert_name="manual", cooldown_hours=2, payload=None):
        if not self._cooldown_ok(alert_name, cooldown_hours):
            return False
        self._log_alert(alert_name, title, body, payload=payload)
        if self.desktop:
            self._send_desktop(title, body)
        if self.email_from:
            self._send_email(title, body)
        if self.webhook_url:
            self._send_webhook(title, body)
        logger.info("Alert fired: %s", alert_name)
        return True

    def evaluate_theses(self, theses):
        count = 0
        for thesis in theses or []:
            thesis_dict = dict(thesis or {})
            for cond in ALERT_CONDITIONS:
                if "action" in cond["name"]:
                    continue
                try:
                    uid = cond["name"] + "_" + str(thesis_dict.get("id", ""))
                    if cond["check"](thesis_dict) and self._cooldown_ok(uid, cond["cooldown_hours"]):
                        self.fire(
                            cond["title"],
                            cond["body_fn"](thesis_dict),
                            uid,
                            cond["cooldown_hours"],
                            payload=thesis_dict,
                        )
                        count += 1
                except Exception as exc:
                    logger.error("Alert eval error: %s", exc)
        return count

    def evaluate_actions(self, actions):
        count = 0
        for action in actions or []:
            action_dict = dict(action or {})
            for cond in ALERT_CONDITIONS:
                if "action" not in cond["name"]:
                    continue
                try:
                    uid = cond["name"] + "_" + str(action_dict.get("id", ""))
                    if cond["check"](action_dict) and self._cooldown_ok(uid, cond["cooldown_hours"]):
                        self.fire(
                            cond["title"],
                            cond["body_fn"](action_dict),
                            uid,
                            cond["cooldown_hours"],
                            payload=action_dict,
                        )
                        count += 1
                except Exception as exc:
                    logger.error("Alert eval error: %s", exc)
        return count
