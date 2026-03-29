from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Dict, List

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso


SOURCE_CONFIDENCE = {
    "rss": 0.80,
    "newsapi": 0.75,
    "guardian": 0.85,
    "gdelt": 0.60,
}
URGENCY_RANK = {"low": 1, "medium": 2, "high": 3, "immediate": 4, "urgent": 4}
IMPACT_RANK = {"local": 1, "market-specific": 2, "regional": 3, "global": 4, "cross_asset": 4}


def _normalize_title(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _merge_details(existing: str, incoming: str) -> str:
    existing = str(existing or "").strip()
    incoming = str(incoming or "").strip()
    if not existing:
        return incoming
    if not incoming or incoming.lower() == existing.lower():
        return existing
    if incoming.lower() in existing.lower():
        return existing
    return existing + " | " + incoming


def _confidence_for_source(source_name: str) -> float:
    return float(SOURCE_CONFIDENCE.get(str(source_name or "").strip().lower(), 0.50))


def _ttl_for_urgency(urgency_level: str) -> str:
    level = str(urgency_level or "medium").strip().lower()
    hours = {
        "immediate": 2,
        "urgent": 2,
        "high": 6,
        "medium": 24,
        "low": 72,
    }.get(level, 24)
    return (datetime.utcnow() + timedelta(hours=hours)).isoformat()


def _task_from_row(row) -> Dict:
    item = dict(row)
    item["source_count"] = int(item.get("source_count", 1) or 1)
    item["confidence_score"] = float(item.get("confidence_score", 0.5) or 0.5)
    item["urgency_level"] = str(item.get("urgency_level", "medium") or "medium")
    item["impact_radius"] = str(item.get("impact_radius", "regional") or "regional")
    item["ttl"] = item.get("ttl") or ""
    item["identity_key"] = str(item.get("identity_key", "") or "")
    item["closed_reason"] = str(item.get("closed_reason", "") or "")
    item["thesis_key"] = str(item.get("thesis_key", "") or "")
    return item


def _pick_urgency(existing: str, incoming: str) -> str:
    existing = str(existing or "medium").strip().lower()
    incoming = str(incoming or "medium").strip().lower()
    return incoming if URGENCY_RANK.get(incoming, 2) >= URGENCY_RANK.get(existing, 2) else existing


def _pick_impact(existing: str, incoming: str) -> str:
    existing = str(existing or "regional").strip().lower()
    incoming = str(incoming or "regional").strip().lower()
    return incoming if IMPACT_RANK.get(incoming, 3) >= IMPACT_RANK.get(existing, 3) else existing


def _pick_ttl(existing: str, incoming: str) -> str:
    existing = str(existing or "").strip()
    incoming = str(incoming or "").strip()
    if not existing:
        return incoming
    if not incoming:
        return existing
    return incoming if incoming < existing else existing


def _last_open_tasks(cur, limit: int = 50) -> List[Dict]:
    cur.execute(
        """
        SELECT
            id,
            task_type,
            title,
            details,
            related_article_id,
            status,
            due_hint,
            created_at,
            updated_at,
            source_count,
            confidence_score,
            urgency_level,
            impact_radius,
            ttl,
            identity_key,
            closed_reason,
            thesis_key
        FROM agent_tasks
        WHERE status = 'open'
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    return [dict(row) for row in cur.fetchall()]


def _find_similar_open_task(cur, title: str, identity_key: str = ""):
    identity_key = _normalize_title(identity_key)
    if identity_key:
        for item in _last_open_tasks(cur, limit=50):
            if _normalize_title(item.get("identity_key", "")) == identity_key:
                return item
    normalized_new = _normalize_title(title)
    best = None
    best_ratio = 0.0
    for item in _last_open_tasks(cur, limit=50):
        ratio = SequenceMatcher(None, normalized_new, _normalize_title(item.get("title", ""))).ratio()
        if ratio > 0.80 and ratio > best_ratio:
            best = item
            best_ratio = ratio
    return best


def _source_name_for_task(cur, related_article_id: int = None, source_name: str = "") -> str:
    explicit = str(source_name or "").strip().lower()
    if explicit:
        return explicit
    if related_article_id is None:
        return ""
    cur.execute("SELECT source_name FROM ingested_articles WHERE id = ?", (int(related_article_id),))
    row = cur.fetchone()
    return str(row["source_name"] or "").strip().lower() if row else ""


def create_task(
    task_type: str,
    title: str,
    details: str = "",
    related_article_id: int = None,
    status: str = "open",
    due_hint: str = "",
    source_count: int = 1,
    confidence_score: float = None,
    urgency_level: str = "medium",
    impact_radius: str = "regional",
    ttl: str = "",
    source_name: str = "",
    identity_key: str = "",
    thesis_key: str = "",
) -> Dict:
    ensure_agent_tables()
    now = utc_now_iso()
    conn = get_conn()
    cur = conn.cursor()

    task_type = str(task_type or "follow_up")
    title = str(title or "Untitled task")
    details = str(details or "")
    related_article_id = int(related_article_id) if related_article_id is not None else None
    status = str(status or "open")
    due_hint = str(due_hint or "")
    urgency_level = str(urgency_level or "medium")
    impact_radius = str(impact_radius or "regional")
    identity_key = str(identity_key or "").strip().lower()
    thesis_key = str(thesis_key or "").strip().lower()

    existing = _find_similar_open_task(cur, title=title, identity_key=identity_key) if status == "open" else None
    if existing:
        merged_confidence = max(float(existing.get("confidence_score", 0.5) or 0.5), float(confidence_score if confidence_score is not None else 0.5))
        cur.execute(
            """
            UPDATE agent_tasks
            SET details = ?,
                updated_at = ?,
                source_count = COALESCE(source_count, 1) + ?,
                confidence_score = ?,
                urgency_level = ?,
                impact_radius = ?,
                ttl = ?,
                identity_key = COALESCE(NULLIF(identity_key, ''), ?),
                thesis_key = COALESCE(NULLIF(thesis_key, ''), ?)
            WHERE id = ?
            """,
            (
                _merge_details(existing.get("details", ""), details),
                now,
                max(1, int(source_count or 1)),
                merged_confidence,
                _pick_urgency(existing.get("urgency_level", "medium"), urgency_level),
                _pick_impact(existing.get("impact_radius", "regional"), impact_radius),
                _pick_ttl(existing.get("ttl", ""), ttl or _ttl_for_urgency(urgency_level)),
                identity_key,
                thesis_key,
                int(existing["id"]),
            ),
        )
        task_id = int(existing["id"])
    else:
        resolved_source = _source_name_for_task(cur, related_article_id=related_article_id, source_name=source_name)
        insert_confidence = float(confidence_score) if confidence_score is not None else _confidence_for_source(resolved_source)
        insert_ttl = ttl or _ttl_for_urgency(urgency_level)
        cur.execute(
            """
            INSERT INTO agent_tasks (
                task_type, title, details, related_article_id, status, due_hint, created_at, updated_at,
                source_count, confidence_score, urgency_level, impact_radius, ttl, identity_key, closed_reason, thesis_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_type,
                title,
                details,
                related_article_id,
                status,
                due_hint,
                now,
                now,
                max(1, int(source_count or 1)),
                insert_confidence,
                urgency_level,
                impact_radius,
                insert_ttl,
                identity_key,
                "",
                thesis_key,
            ),
        )
        task_id = int(cur.lastrowid)

    conn.commit()
    conn.close()
    return [row for row in list_tasks(limit=200, status=None, task_id=task_id) if int(row["id"]) == task_id][0]


def list_tasks(limit: int = 100, status: str = "open", task_id: int = None) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    sql = """
        SELECT
            t.id,
            t.task_type,
            t.title,
            t.details,
            t.related_article_id,
            t.status,
            t.due_hint,
            t.created_at,
            t.updated_at,
            t.source_count,
            t.confidence_score,
            t.urgency_level,
            t.impact_radius,
            t.ttl,
            t.identity_key,
            t.closed_reason,
            t.thesis_key,
            ia.headline,
            ia.url,
            ia.source_name
        FROM agent_tasks t
        LEFT JOIN ingested_articles ia
          ON ia.id = t.related_article_id
    """
    params = []
    if status:
        sql += " WHERE t.status = ?"
        params.append(str(status))
    else:
        sql += " WHERE 1 = 1"
    if task_id is not None:
        sql += " AND t.id = ?"
        params.append(int(task_id))
    sql += " ORDER BY CASE WHEN t.status = 'open' THEN 0 ELSE 1 END, t.updated_at DESC, t.id DESC LIMIT ?"
    params.append(int(limit))
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    conn.close()
    return [_task_from_row(row) for row in rows]


def _parse_ttl(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def close_expired_tasks() -> int:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    open_tasks = _last_open_tasks(cur, limit=400)
    now = datetime.utcnow()
    changed = 0
    for task in open_tasks:
        ttl_dt = _parse_ttl(task.get("ttl"))
        if not ttl_dt:
            continue
        try:
            cmp_dt = ttl_dt.replace(tzinfo=None) if ttl_dt.tzinfo else ttl_dt
        except Exception:
            cmp_dt = ttl_dt
        if cmp_dt < now:
            cur.execute(
                """
                UPDATE agent_tasks
                SET status = 'stale', closed_reason = ?, updated_at = ?
                WHERE id = ? AND status = 'open'
                """,
                ("TTL expired without fresh confirmation.", utc_now_iso(), int(task["id"])),
            )
            changed += int(cur.rowcount or 0)
    conn.commit()
    conn.close()
    return changed


def close_tasks(identity_key: str = "", related_article_id: int = None, new_status: str = "completed", reason: str = "") -> int:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    clauses = ["status = 'open'"]
    params = []
    clean_identity = _normalize_title(identity_key)
    if clean_identity:
        clauses.append("LOWER(COALESCE(identity_key, '')) = ?")
        params.append(clean_identity)
    if related_article_id is not None:
        clauses.append("related_article_id = ?")
        params.append(int(related_article_id))
    if len(clauses) == 1:
        conn.close()
        return 0
    sql = f"""
        UPDATE agent_tasks
        SET status = ?, closed_reason = ?, updated_at = ?
        WHERE {' AND '.join(clauses)}
    """
    params = [str(new_status or "completed"), str(reason or ""), utc_now_iso()] + params
    cur.execute(sql, tuple(params))
    changed = int(cur.rowcount or 0)
    conn.commit()
    conn.close()
    return changed
