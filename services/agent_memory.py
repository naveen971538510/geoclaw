import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List


class AgentMemory:
    """
    Persistent memory across agent runs.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def remember(self, memory_type: str, subject: str, content: Dict, importance: float = 0.5, run_id: int = 0):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.now(timezone.utc).isoformat()
        subject_text = str(subject or "")[:200]
        content_json = json.dumps(content or {}, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO agent_memory (
                memory_type, subject, content, importance, created_at, updated_at, run_id,
                thesis, thesis_key, status, notes, confidence, recall_count, expired
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (
                str(memory_type or "lesson"),
                subject_text,
                content_json,
                min(1.0, max(0.0, float(importance or 0.5))),
                now,
                now,
                int(run_id or 0),
                subject_text,
                subject_text,
                "remembered",
                content_json[:500],
                int(min(100, max(0, round(float(importance or 0.5) * 100)))),
            ),
        )
        conn.commit()
        conn.close()

    def recall(self, memory_type: str = None, subject: str = None, limit: int = 10) -> List[Dict]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        where = ["COALESCE(expired, 0) = 0"]
        params = []
        if memory_type and str(memory_type).strip().lower() != "all":
            where.append("memory_type = ?")
            params.append(str(memory_type))
        if subject:
            like = f"%{str(subject).strip()}%"
            where.append("(COALESCE(subject, '') LIKE ? OR COALESCE(thesis_key, '') LIKE ? OR COALESCE(thesis, '') LIKE ?)")
            params.extend([like, like, like])
        sql = f"""
            SELECT *
            FROM agent_memory
            WHERE {' AND '.join(where)}
            ORDER BY importance DESC, COALESCE(updated_at, created_at) DESC, id DESC
            LIMIT ?
        """
        params.append(int(limit or 10))
        rows = conn.execute(sql, tuple(params)).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            conn.execute(
                """
                UPDATE agent_memory
                SET last_recalled = ?, recall_count = COALESCE(recall_count, 0) + 1
                WHERE id = ?
                """,
                (now, int(row["id"])),
            )
        conn.commit()
        conn.close()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["content"] = json.loads(item.get("content") or "{}")
            except Exception:
                pass
            items.append(item)
        return items

    def record_run_lessons(self, run_metrics: Dict, run_id: int):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        top_theses = conn.execute(
            """
            SELECT thesis_key, confidence, status
            FROM agent_theses
            WHERE confidence >= 0.75
              AND status != 'superseded'
            ORDER BY confidence DESC
            LIMIT 5
            """
        ).fetchall()
        conn.close()

        for thesis in top_theses:
            self.remember(
                "pattern",
                subject=str(thesis["thesis_key"] or "")[:100],
                content={"confidence": thesis["confidence"], "status": thesis["status"], "run_id": run_id},
                importance=float(thesis["confidence"] or 0.5),
                run_id=run_id,
            )

        rule_learning = run_metrics.get("rule_learning", {}) or {}
        if int(rule_learning.get("new_rules", 0) or 0) > 0:
            self.remember("lesson", "rule_learning", rule_learning, importance=0.7, run_id=run_id)

        active_research = run_metrics.get("active_research", {}) or {}
        if int(active_research.get("articles_found", 0) or 0) > 0:
            self.remember("decision", "active_research", active_research, importance=0.5, run_id=run_id)

        actions_executed = run_metrics.get("actions_executed", {}) or {}
        if int(actions_executed.get("auto", 0) or 0) or int(actions_executed.get("manual", 0) or 0):
            self.remember("outcome", "actions_executed", actions_executed, importance=0.6, run_id=run_id)

    def get_context_for_thesis(self, thesis_key: str) -> str:
        memories = self.recall(subject=thesis_key, limit=5)
        if not memories:
            return ""
        lines = []
        for memory in memories:
            content = memory.get("content", {})
            if isinstance(content, dict):
                lines.append(
                    "Previous run: conf={} status={}".format(
                        content.get("confidence", "?"),
                        content.get("status", "?"),
                    )
                )
            else:
                lines.append(str(content)[:80])
        return "Agent memory: " + " | ".join(lines[:3])
