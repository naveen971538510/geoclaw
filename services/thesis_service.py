from datetime import datetime, timezone
from typing import Dict, List, Optional

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso
from services.llm_service import analyse_custom_json


SOURCE_RELIABILITY = {
    "guardian": 0.90,
    "bbc": 0.90,
    "reuters": 0.88,
    "ap": 0.88,
    "newsapi": 0.75,
    "rss": 0.70,
    "gdelt": 0.55,
    "unknown": 0.45,
}


def normalize_thesis_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())[:160]


def _source_weight(source_name: str) -> float:
    clean = str(source_name or "").strip().lower()
    for key, weight in SOURCE_RELIABILITY.items():
        if key != "unknown" and key in clean:
            return float(weight)
    return float(SOURCE_RELIABILITY["unknown"])


def _parse_iso(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _recency_weight(published_at: str) -> float:
    dt = _parse_iso(published_at)
    if dt is None:
        return 0.50
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    if age_seconds <= 3600:
        return 1.0
    if age_seconds <= 21600:
        return 0.85
    if age_seconds <= 86400:
        return 0.70
    return 0.50


def compute_recency_weight(published_at_str: str) -> float:
    return _recency_weight(published_at_str)


def compute_source_diversity_bonus(thesis_key: str, db_path: str = None) -> float:
    try:
        conn = get_conn(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(DISTINCT ia.source_name) AS source_count
            FROM reasoning_chains rc
            JOIN ingested_articles ia ON rc.article_id = ia.id
            WHERE LOWER(COALESCE(rc.thesis_key, '')) = ?
              AND rc.created_at >= datetime('now', '-48 hours')
            """,
            (normalize_thesis_key(thesis_key),),
        )
        row = cur.fetchone()
        conn.close()
        source_count = int((row["source_count"] if row else 0) or 0)
        if source_count >= 4:
            return 0.05
        if source_count >= 3:
            return 0.03
        if source_count >= 2:
            return 0.01
        return 0.0
    except Exception:
        return 0.0


def _thesis_row(cur, key: str):
    cur.execute(
        """
        SELECT
            id,
            thesis_key,
            title,
            current_claim,
            bull_case,
            bear_case,
            key_risk,
            watch_for_next,
            category,
            confidence,
            status,
            last_updated_at,
            evidence_count,
            created_at,
            last_article_id,
            last_decision_id,
            contradiction_count,
            notes,
            last_update_reason,
            terminal_risk,
            watchlist_suggestion,
            timeframe,
            confidence_velocity
        FROM agent_theses
        WHERE thesis_key = ?
        LIMIT 1
        """,
        (key,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def get_thesis(thesis_key: str) -> Optional[Dict]:
    ensure_agent_tables()
    key = normalize_thesis_key(thesis_key)
    if not key:
        return None
    conn = get_conn()
    cur = conn.cursor()
    row = _thesis_row(cur, key)
    conn.close()
    return row


def build_thesis_claim(headline, related_headlines, source_name, category) -> Dict:
    headline = str(headline or "").strip()
    related = [str(item or "").strip() for item in (related_headlines or []) if str(item or "").strip()]
    source_name = str(source_name or "").strip()
    category = str(category or "other").strip().lower() or "other"
    fallback = {
        "title": " ".join(headline.split()[:8]).strip() or "Story thread",
        "current_claim": headline,
        "bull_case": "",
        "bear_case": "",
        "key_risk": "",
        "watch_for_next": "",
    }
    if not headline:
        return fallback

    system_text = (
        "You are a professional financial and geopolitical analyst. "
        "Based on this headline and related context, write a structured thesis. "
        "Return JSON only with exactly these fields: "
        "{title, current_claim, bull_case, bear_case, key_risk, watch_for_next}. "
        "Be specific. Use real entity names, numbers, and market terms where possible. "
        "Never use vague phrases like 'this could impact markets'."
    )
    user_text = (
        f"Headline: {headline}\n"
        f"Related: {', '.join(related)}\n"
        f"Source: {source_name}\n"
        f"Category: {category}"
    )

    def _valid(payload):
        if not isinstance(payload, dict):
            return False
        required = ("title", "current_claim", "bull_case", "bear_case", "key_risk", "watch_for_next")
        return all(isinstance(payload.get(key), str) for key in required)

    def _clean(payload):
        return {
            "title": str(payload.get("title") or fallback["title"]).strip()[:120] or fallback["title"],
            "current_claim": str(payload.get("current_claim") or fallback["current_claim"]).strip() or fallback["current_claim"],
            "bull_case": str(payload.get("bull_case") or "").strip(),
            "bear_case": str(payload.get("bear_case") or "").strip(),
            "key_risk": str(payload.get("key_risk") or "").strip(),
            "watch_for_next": str(payload.get("watch_for_next") or "").strip(),
        }

    return analyse_custom_json(
        system_text,
        user_text,
        fallback=fallback,
        mode="thesis_claim",
        cache_key=f"thesis_claim::{normalize_thesis_key(headline)}::{category}",
        validator=_valid,
        cleaner=_clean,
        lane="reason",
        task_type="thesis_claim_bundle",
    )["analysis"]


def record_thesis_event(thesis_key, event_type, note, confidence, evidence_count) -> Dict:
    ensure_agent_tables()
    key = normalize_thesis_key(thesis_key)
    if not key:
        return {}
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO thesis_events (
            thesis_key, event_type, note, confidence_at_event, evidence_count_at_event, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            str(event_type or "").strip(),
            str(note or "").strip(),
            float(confidence or 0.0),
            int(evidence_count or 0),
            utc_now_iso(),
        ),
    )
    event_id = int(cur.lastrowid)
    conn.commit()
    cur.execute(
        """
        SELECT id, thesis_key, event_type, note, confidence_at_event, evidence_count_at_event, created_at
        FROM thesis_events
        WHERE id = ?
        LIMIT 1
        """,
        (event_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def _record_thesis_event_with_cursor(cur, thesis_key, event_type, note, confidence, evidence_count) -> Dict:
    key = normalize_thesis_key(thesis_key)
    if not key:
        return {}
    cur.execute(
        """
        INSERT INTO thesis_events (
            thesis_key, event_type, note, confidence_at_event, evidence_count_at_event, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            str(event_type or "").strip(),
            str(note or "").strip(),
            float(confidence or 0.0),
            int(evidence_count or 0),
            utc_now_iso(),
        ),
    )
    return {
        "id": int(cur.lastrowid or 0),
        "thesis_key": key,
        "event_type": str(event_type or "").strip(),
        "note": str(note or "").strip(),
        "confidence_at_event": float(confidence or 0.0),
        "evidence_count_at_event": int(evidence_count or 0),
    }


def get_thesis_timeline(thesis_key: str) -> List[Dict]:
    ensure_agent_tables()
    key = normalize_thesis_key(thesis_key)
    if not key:
        return []
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, thesis_key, event_type, note, confidence_at_event, evidence_count_at_event, created_at
        FROM thesis_events
        WHERE LOWER(COALESCE(thesis_key, '')) = ?
        ORDER BY created_at ASC, id ASC
        """,
        (key,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def upsert_thesis(
    thesis_key: str,
    current_claim: str,
    confidence: float = 0.5,
    status: str = "active",
    evidence_delta: int = 1,
    last_article_id: int = None,
    last_decision_id: int = None,
    notes: str = "",
    contradiction_delta: int = 0,
    last_update_reason: str = "",
    title: str = "",
    bull_case: str = "",
    bear_case: str = "",
    key_risk: str = "",
    watch_for_next: str = "",
    source_name: str = "",
    category: str = "other",
    related_headlines: List[str] = None,
) -> Dict:
    ensure_agent_tables()
    key = normalize_thesis_key(thesis_key)
    if not key:
        return {}
    now = utc_now_iso()
    conn = get_conn()
    cur = conn.cursor()
    previous = _thesis_row(cur, key)
    if previous:
        merged_notes = str(notes or "").strip() or str(previous.get("notes") or "")
        merged_claim = str(current_claim or "").strip() or str(previous.get("current_claim") or "")
        update_reason = str(last_update_reason or "").strip() or str(previous.get("last_update_reason") or "")
        cur.execute(
            """
            UPDATE agent_theses
            SET title = ?,
                current_claim = ?,
                bull_case = ?,
                bear_case = ?,
                key_risk = ?,
                watch_for_next = ?,
                category = ?,
                confidence = ?,
                status = ?,
                last_updated_at = ?,
                evidence_count = ?,
                last_article_id = ?,
                last_decision_id = ?,
                contradiction_count = ?,
                notes = ?,
                last_update_reason = ?
            WHERE thesis_key = ?
            """,
            (
                str(title or "").strip() or str(previous.get("title") or ""),
                merged_claim,
                str(bull_case or "").strip() or str(previous.get("bull_case") or ""),
                str(bear_case or "").strip() or str(previous.get("bear_case") or ""),
                str(key_risk or "").strip() or str(previous.get("key_risk") or ""),
                str(watch_for_next or "").strip() or str(previous.get("watch_for_next") or ""),
                str(category or "").strip().lower() or str(previous.get("category") or "other"),
                max(0.05, min(0.95, float(confidence or 0.5))),
                str(status or "active"),
                now,
                max(0, int(previous.get("evidence_count", 0) or 0) + int(evidence_delta or 0)),
                int(last_article_id) if last_article_id else None,
                int(last_decision_id) if last_decision_id else None,
                max(0, int(previous.get("contradiction_count", 0) or 0) + int(contradiction_delta or 0)),
                merged_notes,
                update_reason,
                key,
            ),
        )
    else:
        claim_bundle = build_thesis_claim(
            title or current_claim or thesis_key,
            related_headlines or [],
            source_name or "unknown",
            category or "other",
        )
        cur.execute(
            """
            INSERT INTO agent_theses (
                thesis_key,
                title,
                current_claim,
                bull_case,
                bear_case,
                key_risk,
                watch_for_next,
                category,
                confidence,
                status,
                last_updated_at,
                evidence_count,
                created_at,
                last_article_id,
                last_decision_id,
                contradiction_count,
                notes,
                last_update_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                str(claim_bundle.get("title") or title or "").strip(),
                str(claim_bundle.get("current_claim") or current_claim or "").strip(),
                str(claim_bundle.get("bull_case") or bull_case or ""),
                str(claim_bundle.get("bear_case") or bear_case or ""),
                str(claim_bundle.get("key_risk") or key_risk or ""),
                str(claim_bundle.get("watch_for_next") or watch_for_next or ""),
                str(category or "other").strip().lower() or "other",
                max(0.05, min(0.95, float(confidence or 0.5))),
                str(status or "active"),
                now,
                max(0, int(evidence_delta or 0)),
                now,
                int(last_article_id) if last_article_id else None,
                int(last_decision_id) if last_decision_id else None,
                max(0, int(contradiction_delta or 0)),
                str(notes or ""),
                str(last_update_reason or ""),
            ),
        )
        _record_thesis_event_with_cursor(
            cur,
            key,
            "created",
            str(claim_bundle.get("current_claim") or current_claim or ""),
            float(confidence or 0.5),
            max(0, int(evidence_delta or 0)),
        )
    conn.commit()
    conn.close()
    return get_thesis(key) or {}


def list_theses(limit: int = 100, statuses: List[str] = None) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    sql = """
        SELECT
            id,
            thesis_key,
            title,
            current_claim,
            bull_case,
            bear_case,
            key_risk,
            watch_for_next,
            category,
            confidence,
            status,
            terminal_risk,
            watchlist_suggestion,
            timeframe,
            confidence_velocity,
            last_updated_at,
            evidence_count,
            created_at,
            last_article_id,
            last_decision_id,
            contradiction_count,
            notes,
            last_update_reason
        FROM agent_theses
    """
    params = []
    if statuses:
        placeholders = ",".join(["?"] * len(statuses))
        sql += f" WHERE status IN ({placeholders})"
        params.extend([str(item) for item in statuses])
    sql += " ORDER BY last_updated_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    cur.execute(sql, tuple(params))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def get_thesis_detail(thesis_key: str) -> Dict:
    ensure_agent_tables()
    key = normalize_thesis_key(thesis_key)
    if not key:
        return {}
    conn = get_conn()
    cur = conn.cursor()
    thesis = _thesis_row(cur, key)
    if not thesis:
        conn.close()
        return {}

    cur.execute(
        """
        SELECT DISTINCT
            ia.headline,
            ia.url,
            ia.source_name,
            ia.published_at
        FROM agent_memory am
        JOIN ingested_articles ia
          ON ia.id = am.article_id
        WHERE LOWER(COALESCE(am.thesis_key, '')) = ?
        ORDER BY ia.published_at DESC, ia.id DESC
        LIMIT 12
        """,
        (key,),
    )
    linked_articles = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT
            decision_type,
            reason,
            state,
            confidence,
            priority_score,
            created_at
        FROM agent_decisions
        WHERE LOWER(COALESCE(thesis_key, '')) = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 12
        """,
        (key,),
    )
    linked_decisions = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT
            title,
            status,
            task_type,
            updated_at
        FROM agent_tasks
        WHERE LOWER(COALESCE(thesis_key, '')) = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 12
        """,
        (key,),
    )
    linked_tasks = [dict(row) for row in cur.fetchall()]
    conn.close()

    return {
        "thesis_key": thesis.get("thesis_key", ""),
        "title": thesis.get("title", "") or thesis.get("current_claim", ""),
        "current_claim": thesis.get("current_claim", ""),
        "bull_case": thesis.get("bull_case", "") or "",
        "bear_case": thesis.get("bear_case", "") or "",
        "key_risk": thesis.get("key_risk", "") or "",
        "watch_for_next": thesis.get("watch_for_next", "") or "",
        "category": thesis.get("category", "other") or "other",
        "confidence": float(thesis.get("confidence", 0.5) or 0.5),
        "status": thesis.get("status", "active"),
        "terminal_risk": thesis.get("terminal_risk", "") or "",
        "watchlist_suggestion": thesis.get("watchlist_suggestion", "") or "",
        "timeframe": thesis.get("timeframe", "") or "",
        "confidence_velocity": float(thesis.get("confidence_velocity", 0.0) or 0.0),
        "evidence_count": int(thesis.get("evidence_count", 0) or 0),
        "contradiction_count": int(thesis.get("contradiction_count", 0) or 0),
        "last_updated_at": thesis.get("last_updated_at", ""),
        "last_update_reason": thesis.get("last_update_reason", "") or thesis.get("notes", ""),
        "linked_articles": [
            {
                "headline": row.get("headline", ""),
                "url": row.get("url", ""),
                "source_name": row.get("source_name", ""),
                "published_at": row.get("published_at", ""),
            }
            for row in linked_articles
        ],
        "linked_decisions": [
            {
                "summary": f"{row.get('decision_type', 'decision')} · {row.get('reason', '')}",
                "decision_type": row.get("decision_type", ""),
                "reason": row.get("reason", ""),
                "state": row.get("state", ""),
                "confidence": row.get("confidence", 0),
                "priority_score": row.get("priority_score", 0),
                "created_at": row.get("created_at", ""),
            }
            for row in linked_decisions
        ],
        "linked_tasks": [
            {
                "title": row.get("title", ""),
                "status": row.get("status", ""),
                "task_type": row.get("task_type", ""),
                "updated_at": row.get("updated_at", ""),
            }
            for row in linked_tasks
        ],
    }


def update_thesis_confidence(thesis_key: str, new_evidence_source, new_evidence_confidence) -> Dict:
    ensure_agent_tables()
    key = normalize_thesis_key(thesis_key)
    if not key:
        return {}
    conn = get_conn()
    cur = conn.cursor()
    thesis = _thesis_row(cur, key)
    if not thesis:
        conn.close()
        return {}

    source_name = str(new_evidence_source or "unknown").strip().lower() or "unknown"
    source_weight = _source_weight(source_name)

    published_at = ""
    last_article_id = int(thesis.get("last_article_id", 0) or 0)
    if last_article_id:
        cur.execute("SELECT published_at FROM ingested_articles WHERE id = ?", (last_article_id,))
        row = cur.fetchone()
        published_at = str(row["published_at"] or "") if row else ""
    recency_weight = _recency_weight(published_at)

    cur.execute(
        """
        SELECT COUNT(DISTINCT am.article_id)
        FROM agent_memory am
        JOIN ingested_articles ia
          ON ia.id = am.article_id
        WHERE LOWER(COALESCE(am.thesis_key, '')) = ?
          AND LOWER(COALESCE(ia.source_name, 'unknown')) = ?
        """,
        (key, source_name),
    )
    prior_contribs = int(cur.fetchone()[0] or 0)
    duplicate_penalty = 0.40 if prior_contribs > 2 else 1.0

    existing_confidence = float(thesis.get("confidence", 0.5) or 0.5)
    evidence_confidence = max(0.0, min(1.0, float(new_evidence_confidence or 0.5)))
    adjusted = evidence_confidence * source_weight * recency_weight * duplicate_penalty
    new_confidence = min(0.95, (existing_confidence * 0.6) + (adjusted * 0.4))
    current_delta = new_confidence - existing_confidence
    old_velocity = float(thesis.get("confidence_velocity", 0.0) or 0.0)
    new_velocity = (0.3 * current_delta) + (0.7 * old_velocity)

    penalty_text = "duplicate penalty 0.40" if duplicate_penalty < 1.0 else "duplicate penalty none"
    reason = (
        f"Updated from {source_name} "
        f"(weight {source_weight:.2f}, recency {recency_weight:.2f}, {penalty_text})"
    )
    cur.execute(
        """
        UPDATE agent_theses
        SET confidence = ?, confidence_velocity = ?, last_updated_at = ?, last_update_reason = ?
        WHERE thesis_key = ?
        """,
        (new_confidence, new_velocity, utc_now_iso(), reason, key),
    )
    conn.commit()
    conn.close()
    record_thesis_event(
        key,
        "strengthened" if new_confidence > existing_confidence else "weakened",
        reason,
        new_confidence,
        int(thesis.get("evidence_count", 0) or 0),
    )
    return get_thesis(key) or {}
