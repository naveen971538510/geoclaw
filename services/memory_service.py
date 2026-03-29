import sqlite3
from typing import Dict, List, Optional

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso


def list_memory(limit: int = 100, statuses: Optional[List[str]] = None) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    sql = """
        SELECT id, article_id, memory_type, thesis, confidence, status, notes, linked_decision_id, thesis_key, created_at, updated_at
        FROM agent_memory
    """
    params = []
    if statuses:
        placeholders = ",".join(["?"] * len(statuses))
        sql += f" WHERE status IN ({placeholders})"
        params.extend(statuses)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def latest_memory_for_article(article_id: int, memory_type: str = None) -> Optional[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    if memory_type:
        cur.execute(
            """
            SELECT id, article_id, memory_type, thesis, confidence, status, notes, linked_decision_id, thesis_key, created_at, updated_at
            FROM agent_memory
            WHERE article_id = ? AND memory_type = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (int(article_id), str(memory_type)),
        )
    else:
        cur.execute(
            """
            SELECT id, article_id, memory_type, thesis, confidence, status, notes, linked_decision_id, thesis_key, created_at, updated_at
            FROM agent_memory
            WHERE article_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (int(article_id),),
        )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def write_memory(
    article_id: int = None,
    memory_type: str = "thesis",
    thesis: str = "",
    confidence: int = 0,
    status: str = "active",
    notes: str = "",
    linked_decision_id: int = None,
    thesis_key: str = "",
) -> Dict:
    ensure_agent_tables()
    now = utc_now_iso()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_memory (article_id, memory_type, thesis, confidence, status, notes, linked_decision_id, thesis_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(article_id) if article_id is not None else None,
            str(memory_type or "thesis"),
            str(thesis or ""),
            int(confidence or 0),
            str(status or "active"),
            str(notes or ""),
            int(linked_decision_id) if linked_decision_id else None,
            str(thesis_key or ""),
            now,
            now,
        ),
    )
    memory_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    for item in list_memory(limit=200):
        if int(item["id"]) == memory_id:
            return item
    return {}


def outcome_summary(limit: int = 200) -> Dict:
    rows = list_memory(limit=limit)
    counts = {"confirmed": 0, "weakened": 0, "contradicted": 0, "stale": 0}
    for row in rows:
        status = str(row.get("status", "") or "").lower()
        if status in counts:
            counts[status] += 1
    return counts
