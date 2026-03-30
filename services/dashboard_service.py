import json
import sqlite3
from typing import Dict, List

from services.ai_contracts import normalize_action_reasoning
from services.event_bus import get_bus
from services.query_engine import SUGGESTIONS


def _db(db_path: str):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (str(table_name or "").strip(),),
    ).fetchone()
    return bool(row)


def _load_run_markers(conn) -> Dict:
    rows = conn.execute(
        """
        SELECT id, created_at, metrics_json
        FROM agent_journal
        WHERE journal_type='agent_loop'
        ORDER BY id DESC
        LIMIT 2
        """
    ).fetchall()
    latest = dict(rows[0]) if rows else {}
    previous = dict(rows[1]) if len(rows) > 1 else {}
    latest_metrics = {}
    if latest:
        try:
            latest_metrics = json.loads(latest.get("metrics_json") or "{}")
        except Exception:
            latest_metrics = {}
    return {
        "latest": latest,
        "previous": previous,
        "latest_metrics": latest_metrics,
    }


def _top_confidence_changes(conn, since_iso: str, until_iso: str, limit: int = 3) -> List[Dict]:
    if not since_iso or not _table_exists(conn, "thesis_confidence_log"):
        return []
    rows = conn.execute(
        """
        SELECT thesis_key, confidence, recorded_at
        FROM thesis_confidence_log
        WHERE recorded_at >= ? AND recorded_at <= ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT 400
        """,
        (since_iso, until_iso or "9999-12-31T23:59:59"),
    ).fetchall()
    latest_by_key = {}
    for row in rows:
        thesis_key = str(row["thesis_key"] or "").strip()
        if thesis_key and thesis_key not in latest_by_key:
            latest_by_key[thesis_key] = dict(row)
    changes = []
    for thesis_key, item in latest_by_key.items():
        previous = conn.execute(
            """
            SELECT confidence, recorded_at
            FROM thesis_confidence_log
            WHERE thesis_key=? AND recorded_at < ?
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
            """,
            (thesis_key, since_iso),
        ).fetchone()
        previous_confidence = float((previous["confidence"] if previous else 0.5) or 0.5)
        current_confidence = float(item.get("confidence", 0.0) or 0.0)
        delta = current_confidence - previous_confidence
        changes.append(
            {
                "thesis_key": thesis_key,
                "current_confidence": round(current_confidence, 3),
                "previous_confidence": round(previous_confidence, 3),
                "delta": round(delta, 3),
            }
        )
    changes.sort(key=lambda item: abs(float(item.get("delta", 0.0) or 0.0)), reverse=True)
    return changes[: int(limit)]


def _recent_events(limit: int = 5) -> List[Dict]:
    try:
        events = [item for item in get_bus().get_history(30) if str(item.get("type") or "") != "heartbeat"]
    except Exception:
        return []
    trimmed = list(reversed(events[-int(limit):]))
    return [
        {
            "type": item.get("type", ""),
            "description": item.get("description", ""),
            "timestamp": item.get("timestamp", 0),
            "data": item.get("data", {}) if isinstance(item.get("data"), dict) else {},
        }
        for item in trimmed
    ]


def build_dashboard_decision_view(db_path: str) -> Dict:
    conn = _db(db_path)
    try:
        run_markers = _load_run_markers(conn)
        latest = run_markers["latest"]
        previous = run_markers["previous"]
        latest_metrics = run_markers["latest_metrics"]
        latest_created_at = str(latest.get("created_at") or "")
        previous_created_at = str(previous.get("created_at") or "")

        action_counts_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(approval_state), ''), status) AS approval_bucket,
                   COUNT(*) AS count
            FROM agent_actions
            GROUP BY approval_bucket
            """
        ).fetchall()
        action_counts = {str(row["approval_bucket"] or "unknown"): int(row["count"] or 0) for row in action_counts_rows}
        action_rows = conn.execute(
            """
            SELECT id, action_type, thesis_key, status, approval_state, reason, metadata,
                   created_at, reviewed_at, executed_at, payload_json, audit_note
            FROM agent_actions
            WHERE status IN ('pending', 'draft', 'approved', 'rejected', 'proposed', 'auto_approved')
            ORDER BY created_at DESC, id DESC
            LIMIT 8
            """
        ).fetchall()
        action_items = []
        for row in action_rows:
            item = dict(row)
            for key in ("payload_json", "metadata"):
                try:
                    item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
                except Exception:
                    item[key.replace("_json", "")] = {}
            reasoning = normalize_action_reasoning(item)
            action_items.append(
                {
                    "id": item.get("id", 0),
                    "action_type": item.get("action_type", ""),
                    "thesis_key": item.get("thesis_key", ""),
                    "status": item.get("status", ""),
                    "created_at": item.get("created_at", ""),
                    "reason": reasoning["reason"],
                    "why_now": reasoning["why_now"],
                    "approval_state": reasoning["approval_state"],
                }
            )

        thesis_rows = conn.execute(
            """
            SELECT thesis_key, title, current_claim, confidence, confidence_velocity, status,
                   terminal_risk, last_update_reason, watchlist_suggestion, timeframe
            FROM agent_theses
            WHERE status != 'superseded'
            ORDER BY confidence DESC, evidence_count DESC, last_updated_at DESC
            LIMIT 4
            """
        ).fetchall()
        top_theses = []
        for row in thesis_rows:
            item = dict(row)
            why_now = str(item.get("last_update_reason") or item.get("watchlist_suggestion") or item.get("current_claim") or "").strip()
            top_theses.append(
                {
                    "thesis_key": item.get("thesis_key", ""),
                    "title": item.get("title") or item.get("current_claim") or item.get("thesis_key") or "",
                    "confidence": float(item.get("confidence", 0.0) or 0.0),
                    "confidence_velocity": float(item.get("confidence_velocity", 0.0) or 0.0),
                    "status": item.get("status", ""),
                    "terminal_risk": item.get("terminal_risk", ""),
                    "last_update_reason": item.get("last_update_reason", ""),
                    "why_now": why_now[:160],
                    "drilldown_href": "/theses?focus=" + str(item.get("thesis_key", "") or ""),
                    "timeframe": item.get("timeframe", ""),
                }
            )

        prediction_rows = conn.execute(
            """
            SELECT outcome, COUNT(*) AS count
            FROM thesis_predictions
            GROUP BY outcome
            """
        ).fetchall() if _table_exists(conn, "thesis_predictions") else []
        prediction_counts = {str(row["outcome"] or "pending"): int(row["count"] or 0) for row in prediction_rows}
        verified = prediction_counts.get("verified", 0)
        refuted = prediction_counts.get("refuted", 0)
        neutral = prediction_counts.get("neutral", 0)
        latest_predictions = conn.execute(
            """
            SELECT thesis_key, symbol, predicted_direction, actual_change_pct, outcome, checked_at, outcome_note
            FROM thesis_predictions
            WHERE outcome IN ('verified', 'refuted', 'neutral')
            ORDER BY checked_at DESC, id DESC
            LIMIT 5
            """
        ).fetchall() if _table_exists(conn, "thesis_predictions") else []

        new_actions = conn.execute(
            """
            SELECT action_type, thesis_key, status, created_at
            FROM agent_actions
            WHERE created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 5
            """,
            (previous_created_at or latest_created_at or "1970-01-01T00:00:00",),
        ).fetchall() if latest_created_at else []
        new_alerts = conn.execute(
            """
            SELECT title, alert_type, created_at
            FROM alert_events
            WHERE created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 5
            """,
            (previous_created_at or latest_created_at or "1970-01-01T00:00:00",),
        ).fetchall() if latest_created_at and _table_exists(conn, "alert_events") else []
        new_contradictions = conn.execute(
            """
            SELECT thesis_key, explanation, severity, created_at
            FROM contradictions
            WHERE created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 5
            """,
            (previous_created_at or latest_created_at or "1970-01-01T00:00:00",),
        ).fetchall() if latest_created_at and _table_exists(conn, "contradictions") else []
        research_logs = conn.execute(
            """
            SELECT query, result_count, searched_at, thesis_key
            FROM web_search_log
            ORDER BY searched_at DESC, id DESC
            LIMIT 5
            """
        ).fetchall() if _table_exists(conn, "web_search_log") else []
        active_research = latest_metrics.get("active_research", {}) or {}

        changes = {
            "run_goals": list(latest_metrics.get("run_goals", []) or [])[:5],
            "top_confidence_changes": _top_confidence_changes(conn, previous_created_at, latest_created_at, limit=3),
            "new_actions": [dict(row) for row in new_actions],
            "new_alerts": [dict(row) for row in new_alerts],
            "new_contradictions": [dict(row) for row in new_contradictions],
            "new_anomalies": list(latest_metrics.get("anomalies", []) or [])[:5],
            "research": {
                "searches_done": int(active_research.get("searches_done", 0) or 0),
                "articles_found": int(active_research.get("articles_found", 0) or 0),
                "articles_saved": int(active_research.get("articles_saved", 0) or 0),
                "needs_found": int(active_research.get("needs_found", 0) or 0),
                "needs": list(active_research.get("needs", []) or [])[:3],
                "log": [dict(row) for row in research_logs],
            },
            "last_run_duration": float(latest_metrics.get("duration_seconds", 0.0) or 0.0),
            "latest_run_at": latest_created_at,
        }

        return {
            "ask_suggestions": SUGGESTIONS[:3],
            "changes": changes,
            "top_theses": top_theses,
            "action_queue": {
                "counts": {
                    "pending": int(action_counts.get("pending", 0) or 0),
                    "draft": int(action_counts.get("draft", 0) or 0),
                    "approved": int(action_counts.get("approved", 0) or 0) + int(action_counts.get("auto_approved", 0) or 0),
                    "rejected": int(action_counts.get("rejected", 0) or 0),
                },
                "items": action_items,
            },
            "prediction_truth": {
                "verified": verified,
                "refuted": refuted,
                "neutral": neutral,
                "hit_rate_pct": round((verified / max(verified + refuted, 1)) * 100, 1),
                "latest": [dict(row) for row in latest_predictions],
            },
            "live_events": _recent_events(limit=5),
        }
    finally:
        conn.close()
