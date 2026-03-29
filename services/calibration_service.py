from collections import defaultdict
from typing import Dict, List

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso


def record_prediction(thesis_key, predicted_confidence, predicted_direction, source_name, category):
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_calibration (
            source_name, category, over_confident, count, created_at, thesis_key,
            predicted_confidence, predicted_direction, actual_outcome, row_type
        )
        VALUES (?, ?, 0, 1, ?, ?, ?, ?, '', 'prediction')
        """,
        (
            str(source_name or "unknown"),
            str(category or "other"),
            utc_now_iso(),
            str(thesis_key or ""),
            float(predicted_confidence or 0.0),
            str(predicted_direction or ""),
        ),
    )
    calibration_id = int(cur.lastrowid)
    conn.commit()
    cur.execute("SELECT * FROM agent_calibration WHERE id = ?", (calibration_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def record_outcome(thesis_key, actual_outcome):
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE agent_calibration
        SET actual_outcome = ?
        WHERE id IN (
            SELECT id
            FROM agent_calibration
            WHERE LOWER(COALESCE(thesis_key, '')) = LOWER(?)
              AND row_type = 'prediction'
              AND COALESCE(actual_outcome, '') = ''
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        )
        """,
        (str(actual_outcome or ""), str(thesis_key or "")),
    )
    changed = int(cur.rowcount or 0)
    conn.commit()
    conn.close()
    return changed


def get_calibration_score(source_name, category):
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT predicted_confidence, actual_outcome
        FROM agent_calibration
        WHERE LOWER(COALESCE(source_name, '')) = LOWER(?)
          AND LOWER(COALESCE(category, '')) = LOWER(?)
          AND row_type = 'prediction'
          AND COALESCE(actual_outcome, '') <> ''
        """,
        (str(source_name or ""), str(category or "")),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    total = len(rows)
    correct_rows = [row for row in rows if str(row.get("actual_outcome", "") or "").lower() == "confirmed"]
    wrong_rows = [row for row in rows if row not in correct_rows]
    accuracy = (len(correct_rows) / float(total)) if total else 0.0
    avg_correct = sum(float(row.get("predicted_confidence", 0.0) or 0.0) for row in correct_rows) / float(len(correct_rows) or 1)
    avg_wrong = sum(float(row.get("predicted_confidence", 0.0) or 0.0) for row in wrong_rows) / float(len(wrong_rows) or 1)
    if accuracy >= 0.9:
        grade = "A"
    elif accuracy >= 0.75:
        grade = "B"
    elif accuracy >= 0.6:
        grade = "C"
    elif accuracy >= 0.4:
        grade = "D"
    else:
        grade = "F"
    return {
        "source_name": str(source_name or ""),
        "category": str(category or ""),
        "total_predictions": total,
        "correct": len(correct_rows),
        "accuracy": round(accuracy, 4),
        "average_confidence_when_correct": round(avg_correct, 4) if correct_rows else 0.0,
        "average_confidence_when_wrong": round(avg_wrong, 4) if wrong_rows else 0.0,
        "calibration_grade": grade,
    }


def get_penalty_multiplier(source_name, category) -> float:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT SUM(COALESCE(count, 0))
        FROM agent_calibration
        WHERE LOWER(COALESCE(source_name, '')) = LOWER(?)
          AND LOWER(COALESCE(category, '')) = LOWER(?)
          AND row_type = 'reflection'
          AND over_confident = 1
        """,
        (str(source_name or ""), str(category or "")),
    )
    row = cur.fetchone()
    conn.close()
    over_count = int(row[0] or 0) if row else 0
    return 0.85 if over_count > 3 else 1.0


def get_calibration_report() -> Dict:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT source_name, category
        FROM agent_calibration
        WHERE COALESCE(source_name, '') <> '' OR COALESCE(category, '') <> ''
        ORDER BY source_name, category
        """
    )
    pairs = [dict(row) for row in cur.fetchall()]
    conn.close()
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for pair in pairs:
        source = pair.get("source_name", "") or "unknown"
        grouped[source].append(get_calibration_score(source, pair.get("category", "") or "other"))
    return {"items": grouped}
