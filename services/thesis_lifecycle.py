from datetime import datetime, timezone
from typing import Dict, List

from config import DB_PATH
from services.db_helpers import get_conn


CONFIDENCE_FLOOR = 0.15
STALE_HOURS = 72
DECAY_PER_6H = 0.025


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_iso(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _record_event(cur, thesis_key: str, event_type: str, note: str, confidence: float, evidence_count: int):
    cur.execute(
        """
        INSERT INTO thesis_events (
            thesis_key, event_type, note, confidence_at_event, evidence_count_at_event, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(thesis_key or "").strip().lower(),
            str(event_type or "").strip(),
            str(note or "").strip(),
            float(confidence or 0.0),
            int(evidence_count or 0),
            _utc_now().isoformat(),
        ),
    )


def decay_stale_theses(db_path=None) -> Dict:
    conn = get_conn(db_path or DB_PATH)
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT thesis_key, confidence, status, evidence_count, last_updated_at, created_at
        FROM agent_theses
        WHERE COALESCE(status, 'active') NOT IN ('superseded', 'contradicted')
        """
    ).fetchall()

    now = _utc_now()
    decayed = 0
    superseded = 0
    for row in rows:
        updated = _parse_iso(row["last_updated_at"] or row["created_at"])
        if updated is None:
            continue
        age_hours = max(0.0, (now - updated).total_seconds() / 3600.0)
        if age_hours < 12:
            continue

        slots_missed = int(age_hours / 6)
        decay = slots_missed * DECAY_PER_6H
        old_conf = float(row["confidence"] or 0.5)
        new_conf = max(0.01, old_conf - decay)
        if new_conf <= CONFIDENCE_FLOOR:
            cur.execute(
                """
                UPDATE agent_theses
                SET confidence = ?, status = 'superseded', last_updated_at = ?
                WHERE thesis_key = ?
                """,
                (new_conf, now.isoformat(), row["thesis_key"]),
            )
            superseded += 1
            _record_event(cur, row["thesis_key"], "stale", "Confidence decayed below the active floor.", new_conf, int(row["evidence_count"] or 0))
        else:
            cur.execute(
                """
                UPDATE agent_theses
                SET confidence = ?, last_updated_at = ?
                WHERE thesis_key = ?
                """,
                (new_conf, now.isoformat(), row["thesis_key"]),
            )
        decayed += 1

    conn.commit()
    conn.close()
    return {"decayed": decayed, "superseded": superseded}


def promote_demote_theses(db_path=None) -> Dict:
    conn = get_conn(db_path or DB_PATH)
    cur = conn.cursor()
    promoted = 0
    weakened = 0

    promoted_rows = cur.execute(
        """
        SELECT thesis_key, confidence, evidence_count
        FROM agent_theses
        WHERE confidence >= 0.78
          AND evidence_count >= 3
          AND COALESCE(status, 'active') = 'active'
        """
    ).fetchall()
    for row in promoted_rows:
        cur.execute(
            """
            UPDATE agent_theses
            SET status = 'confirmed', last_updated_at = ?
            WHERE thesis_key = ?
            """,
            (_utc_now().isoformat(), row["thesis_key"]),
        )
        promoted += 1
        _record_event(cur, row["thesis_key"], "strengthened", "Thesis promoted to confirmed by confidence and evidence.", float(row["confidence"] or 0.0), int(row["evidence_count"] or 0))

    weakened_rows = cur.execute(
        """
        SELECT thesis_key, confidence, evidence_count
        FROM agent_theses
        WHERE confidence < 0.38
          AND COALESCE(status, 'active') IN ('active', 'tracking')
        """
    ).fetchall()
    for row in weakened_rows:
        cur.execute(
            """
            UPDATE agent_theses
            SET status = 'weakened', last_updated_at = ?
            WHERE thesis_key = ?
            """,
            (_utc_now().isoformat(), row["thesis_key"]),
        )
        weakened += 1
        _record_event(cur, row["thesis_key"], "weakened", "Thesis confidence fell below the active threshold.", float(row["confidence"] or 0.0), int(row["evidence_count"] or 0))

    cur.execute(
        """
        UPDATE agent_theses
        SET status = 'active', last_updated_at = ?
        WHERE confidence >= 0.50
          AND COALESCE(status, '') = 'weakened'
        """,
        (_utc_now().isoformat(),),
    )
    conn.commit()
    conn.close()
    return {"promoted_to_confirmed": promoted, "demoted_to_weakened": weakened}


def check_contradictions(new_thesis_key, new_delta, db_path=None) -> List[Dict]:
    clean_key = str(new_thesis_key or "").strip().lower()
    if not clean_key:
        return []
    conn = get_conn(db_path or DB_PATH)
    cur = conn.cursor()
    existing = cur.execute(
        """
        SELECT thesis_key, confidence
        FROM agent_theses
        WHERE LOWER(COALESCE(thesis_key, '')) LIKE ?
          AND COALESCE(status, 'active') <> 'superseded'
        LIMIT 10
        """,
        (f"%{clean_key[:30]}%",),
    ).fetchall()
    conn.close()

    contradictions = []
    for row in existing:
        existing_conf = float(row["confidence"] or 0.5)
        if float(new_delta or 0.0) < -0.05 and existing_conf > 0.60:
            contradictions.append(
                {
                    "thesis_key": row["thesis_key"],
                    "existing_conf": existing_conf,
                    "contradicting_delta": float(new_delta or 0.0),
                }
            )
    return contradictions
