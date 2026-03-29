from typing import Dict, List

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso


DECISION_RANK = {
    "ignore": 0,
    "follow_up": 1,
    "queue": 2,
    "downgrade": 2,
    "upgrade": 3,
    "alert": 4,
}


def _key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _load_decision(cur, decision_id: int) -> Dict:
    cur.execute(
        """
        SELECT
            d.id,
            d.article_id,
            d.decision_type,
            d.reason,
            d.confidence,
            d.priority_score,
            d.state,
            d.created_at,
            d.cluster_key,
            d.thesis_key,
            ia.headline,
            ia.url,
            ia.source_name
        FROM agent_decisions d
        LEFT JOIN ingested_articles ia
          ON ia.id = d.article_id
        WHERE d.id = ?
        LIMIT 1
        """,
        (int(decision_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def _find_existing_open_decision(cur, cluster_key: str = "", thesis_key: str = ""):
    cluster_key = _key(cluster_key)
    thesis_key = _key(thesis_key)
    if not cluster_key and not thesis_key:
        return None
    cur.execute(
        """
        SELECT id
        FROM agent_decisions
        WHERE state = 'open'
          AND (
            (? <> '' AND LOWER(COALESCE(cluster_key, '')) = ?)
            OR
            (? <> '' AND LOWER(COALESCE(thesis_key, '')) = ?)
          )
        ORDER BY id DESC
        LIMIT 1
        """,
        (cluster_key, cluster_key, thesis_key, thesis_key),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


def _supersede_weaker_open_decisions(cur, keep_id: int, cluster_key: str = "", thesis_key: str = "", chosen_kind: str = ""):
    cluster_key = _key(cluster_key)
    thesis_key = _key(thesis_key)
    if not cluster_key and not thesis_key:
        return 0
    cur.execute(
        """
        SELECT id, decision_type
        FROM agent_decisions
        WHERE state = 'open'
          AND id <> ?
          AND (
            (? <> '' AND LOWER(COALESCE(cluster_key, '')) = ?)
            OR
            (? <> '' AND LOWER(COALESCE(thesis_key, '')) = ?)
          )
        """,
        (int(keep_id), cluster_key, cluster_key, thesis_key, thesis_key),
    )
    rows = cur.fetchall()
    changed = 0
    target_rank = DECISION_RANK.get(str(chosen_kind or ""), 0)
    for row in rows:
        if DECISION_RANK.get(str(row["decision_type"] or ""), 0) <= target_rank:
            cur.execute(
                "UPDATE agent_decisions SET state = 'superseded' WHERE id = ?",
                (int(row["id"]),),
            )
            changed += int(cur.rowcount or 0)
    return changed


def create_decision(
    article_id: int = None,
    decision_type: str = "queue",
    reason: str = "",
    confidence: int = 0,
    priority_score: int = 0,
    state: str = "open",
    cluster_key: str = "",
    thesis_key: str = "",
) -> Dict:
    ensure_agent_tables()
    now = utc_now_iso()
    conn = get_conn()
    cur = conn.cursor()
    cluster_key = _key(cluster_key)
    thesis_key = _key(thesis_key)
    existing_id = _find_existing_open_decision(cur, cluster_key=cluster_key, thesis_key=thesis_key) if str(state or "open") == "open" else None
    if existing_id:
        existing = _load_decision(cur, existing_id)
        existing_kind = str(existing.get("decision_type", "") or "ignore")
        incoming_kind = str(decision_type or "queue")
        chosen_kind = incoming_kind if DECISION_RANK.get(incoming_kind, 0) >= DECISION_RANK.get(existing_kind, 0) else existing_kind
        cur.execute(
            """
            UPDATE agent_decisions
            SET article_id = COALESCE(?, article_id),
                decision_type = ?,
                reason = ?,
                confidence = ?,
                priority_score = ?,
                state = ?,
                cluster_key = ?,
                thesis_key = ?,
                created_at = ?
            WHERE id = ?
            """,
            (
                int(article_id) if article_id is not None else None,
                chosen_kind,
                str(reason or existing.get("reason", "") or ""),
                max(int(confidence or 0), int(existing.get("confidence", 0) or 0)),
                max(int(priority_score or 0), int(existing.get("priority_score", 0) or 0)),
                str(state or existing.get("state", "open") or "open"),
                cluster_key or existing.get("cluster_key", ""),
                thesis_key or existing.get("thesis_key", ""),
                now,
                existing_id,
            ),
        )
        decision_id = existing_id
        _supersede_weaker_open_decisions(cur, decision_id, cluster_key=cluster_key, thesis_key=thesis_key, chosen_kind=chosen_kind)
    else:
        cur.execute(
            """
            INSERT INTO agent_decisions (article_id, decision_type, reason, confidence, priority_score, state, created_at, cluster_key, thesis_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(article_id) if article_id is not None else None,
                str(decision_type or "queue"),
                str(reason or ""),
                int(confidence or 0),
                int(priority_score or 0),
                str(state or "open"),
                now,
                cluster_key,
                thesis_key,
            ),
        )
        decision_id = int(cur.lastrowid)
        _supersede_weaker_open_decisions(cur, decision_id, cluster_key=cluster_key, thesis_key=thesis_key, chosen_kind=str(decision_type or "queue"))
    conn.commit()
    conn.close()
    return list_decisions(limit=200, open_only=False, article_id=article_id, decision_id=decision_id)[0]


def list_decisions(limit: int = 100, open_only: bool = False, article_id: int = None, decision_id: int = None) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    sql = """
        SELECT
            d.id,
            d.article_id,
            d.decision_type,
            d.reason,
            d.confidence,
            d.priority_score,
            d.state,
            d.created_at,
            d.cluster_key,
            d.thesis_key,
            ia.headline,
            ia.url,
            ia.source_name
        FROM agent_decisions d
        LEFT JOIN ingested_articles ia
          ON ia.id = d.article_id
        WHERE 1 = 1
    """
    params = []
    if open_only:
        sql += " AND d.state = 'open'"
    if article_id is not None:
        sql += " AND d.article_id = ?"
        params.append(int(article_id))
    if decision_id is not None:
        sql += " AND d.id = ?"
        params.append(int(decision_id))
    sql += " ORDER BY d.created_at DESC, d.id DESC LIMIT ?"
    params.append(int(limit))
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def decision_metrics(limit: int = 200) -> Dict:
    rows = list_decisions(limit=limit, open_only=False)
    totals = {"ignore": 0, "queue": 0, "alert": 0, "follow_up": 0, "downgrade": 0, "upgrade": 0}
    for row in rows:
        kind = str(row.get("decision_type", "") or "")
        if kind in totals:
            totals[kind] += 1
    return totals
