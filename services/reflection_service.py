import json
from datetime import datetime, timedelta, timezone
from typing import Dict

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso
from services.llm_service import analyse_custom_json


def _cutoff_iso(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _journal_reflection(summary: str, metrics: Dict):
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_journal (run_id, journal_type, summary, metrics_json, created_at)
        VALUES (NULL, 'reflection', ?, ?, ?)
        """,
        (summary, json.dumps(metrics or {}), utc_now_iso()),
    )
    conn.commit()
    conn.close()


def run_reflection(db=None):
    ensure_agent_tables()
    conn = db or get_conn()
    close_conn = db is None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, article_id, decision_type, reason, confidence, priority_score, state, created_at, thesis_key
        FROM agent_decisions
        WHERE state = 'open'
          AND COALESCE(created_at, '') <= ?
        ORDER BY created_at ASC, id ASC
        LIMIT 10
        """,
        (_cutoff_iso(24),),
    )
    decisions = [dict(row) for row in cur.fetchall()]
    metrics = {
        "decisions_reviewed": 0,
        "correct": 0,
        "partial": 0,
        "wrong": 0,
        "lessons_recorded": 0,
        "calibration_adjustments": 0,
    }
    if not decisions:
        if close_conn:
            conn.close()
        _journal_reflection("Reflection found no eligible open decisions.", metrics)
        return metrics

    for decision in decisions:
        metrics["decisions_reviewed"] += 1
        cur.execute(
            """
            SELECT ia.headline
            FROM article_enrichment ae
            JOIN ingested_articles ia ON ia.id = ae.article_id
            WHERE LOWER(COALESCE(ae.thesis, '')) LIKE ?
               OR LOWER(COALESCE(ae.cluster_key, '')) LIKE ?
            ORDER BY ia.id DESC
            LIMIT 8
            """,
            (
                "%" + str(decision.get("thesis_key", "")).lower()[:40] + "%",
                "%" + str(decision.get("thesis_key", "")).lower()[:40] + "%",
            ),
        )
        headlines = [str(row["headline"] or "") for row in cur.fetchall()]
        support_count = sum(1 for headline in headlines if any(token in headline.lower() for token in ("rise", "support", "upgrade", "strong")))
        contradict_count = sum(1 for headline in headlines if any(token in headline.lower() for token in ("fall", "contradict", "downgrade", "weak")))
        fallback_verdict = "wrong" if contradict_count > support_count else ("correct" if support_count else "partial")
        base_conf = float(int(decision.get("confidence", 0) or 0)) / 100.0
        fallback = {
            "verdict": fallback_verdict,
            "confidence_was": base_conf,
            "confidence_should_have_been": max(0.1, base_conf - 0.25 if fallback_verdict == "wrong" else base_conf - 0.10 if fallback_verdict == "partial" else base_conf),
            "lesson": "Wait for stronger corroboration before escalating similar evidence." if fallback_verdict == "wrong" else "Keep corroborating evidence tied to the thesis before escalating.",
            "update_thesis": fallback_verdict == "wrong",
        }
        system_text = (
            "You made this decision 24 hours ago and must review it. "
            "Return JSON only with verdict, confidence_was, confidence_should_have_been, lesson, update_thesis."
        )
        user_text = (
            f"Decision summary: {decision.get('decision_type', '')} · {decision.get('reason', '')}\n"
            f"Since then these articles appeared: {json.dumps(headlines, ensure_ascii=False)}"
        )

        def _valid(payload):
            return isinstance(payload, dict) and str(payload.get("verdict", "")).strip().lower() in {"correct", "partial", "wrong"}

        def _clean(payload):
            return {
                "verdict": str(payload.get("verdict") or fallback["verdict"]).strip().lower(),
                "confidence_was": float(payload.get("confidence_was") or fallback["confidence_was"]),
                "confidence_should_have_been": float(payload.get("confidence_should_have_been") or fallback["confidence_should_have_been"]),
                "lesson": str(payload.get("lesson") or fallback["lesson"]).strip(),
                "update_thesis": bool(payload.get("update_thesis", fallback["update_thesis"])),
            }

        analysis = analyse_custom_json(
            system_text,
            user_text,
            fallback=fallback,
            mode="reflection",
            cache_key=f"reflection::{decision.get('id')}::{decision.get('thesis_key', '')}",
            validator=_valid,
            cleaner=_clean,
        )["analysis"]

        verdict = str(analysis.get("verdict", "partial") or "partial").lower()
        metrics[verdict] += 1
        confidence_delta = abs(float(analysis.get("confidence_was", 0.0) or 0.0) - float(analysis.get("confidence_should_have_been", 0.0) or 0.0))

        cur.execute(
            """
            INSERT INTO agent_lessons (decision_id, thesis_key, verdict, lesson, confidence_delta, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(decision.get("id", 0) or 0),
                str(decision.get("thesis_key", "") or ""),
                verdict,
                str(analysis.get("lesson", "") or ""),
                confidence_delta,
                utc_now_iso(),
            ),
        )
        metrics["lessons_recorded"] += 1

        if verdict == "wrong" and confidence_delta > 0.20:
            cur.execute(
                """
                INSERT INTO agent_calibration (
                    source_name, category, over_confident, count, created_at, thesis_key,
                    decision_id, verdict, lesson, confidence_delta, row_type
                )
                VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?, ?, 'reflection')
                """,
                (
                    "unknown",
                    "other",
                    utc_now_iso(),
                    str(decision.get("thesis_key", "") or ""),
                    int(decision.get("id", 0) or 0),
                    verdict,
                    str(analysis.get("lesson", "") or ""),
                    confidence_delta,
                ),
            )
            metrics["calibration_adjustments"] += 1

    conn.commit()
    if close_conn:
        conn.close()
    _journal_reflection("Reflection reviewed older open decisions.", metrics)
    return metrics
