import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List


from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_dt(value: str):
    s = str(value or "").strip()
    if not s:
        return None
    for candidate in (s, s.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def _loads(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


def get_what_changed(window_minutes: int = 30, limit: int = 8) -> Dict:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            ia.id AS article_id,
            ia.source_name,
            ia.headline,
            ia.url,
            ia.fetched_at,
            ia.published_at,
            ae.signal,
            ae.impact_score,
            ae.asset_tags,
            ae.alert_tags
        FROM ingested_articles ia
        LEFT JOIN article_enrichment ae
          ON ia.id = ae.article_id
        ORDER BY ia.id DESC
        LIMIT 250
        """
    )
    rows = cur.fetchall()

    cur.execute(
        """
        SELECT
            al.id,
            al.priority,
            al.reason,
            al.created_at,
            ia.headline,
            ia.source_name,
            ia.url
        FROM alert_events al
        JOIN ingested_articles ia
          ON al.article_id = ia.id
        ORDER BY al.id DESC
        LIMIT 100
        """
    )
    alert_rows = cur.fetchall()

    cur.execute(
        """
        SELECT id, run_type, started_at, finished_at, status, items_fetched, items_kept, alerts_created, error_text
        FROM agent_runs
        ORDER BY id DESC
        LIMIT 2
        """
    )
    run_rows = cur.fetchall()
    conn.close()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=int(window_minutes))

    recent_articles = []
    for row in rows:
        dt = _parse_dt(row["fetched_at"] or row["published_at"])
        if dt and dt >= cutoff:
            recent_articles.append(
                {
                    "article_id": row["article_id"],
                    "source": row["source_name"],
                    "headline": row["headline"],
                    "url": row["url"],
                    "fetched_at": row["fetched_at"] or "",
                    "published_at": row["published_at"] or "",
                    "signal": row["signal"] or "Neutral",
                    "impact_score": row["impact_score"] or 0,
                    "asset_tags": _loads(row["asset_tags"]),
                    "alert_tags": _loads(row["alert_tags"]),
                }
            )

    recent_alerts = []
    for row in alert_rows:
        dt = _parse_dt(row["created_at"])
        if dt and dt >= cutoff:
            recent_alerts.append(
                {
                    "priority": row["priority"],
                    "reason": row["reason"],
                    "created_at": row["created_at"],
                    "headline": row["headline"],
                    "source": row["source_name"],
                    "url": row["url"],
                }
            )

    recent_articles.sort(key=lambda x: (x["impact_score"], x["fetched_at"]), reverse=True)
    recent_alerts.sort(key=lambda x: x["created_at"], reverse=True)

    latest_run = dict(run_rows[0]) if len(run_rows) >= 1 else {}
    previous_run = dict(run_rows[1]) if len(run_rows) >= 2 else {}

    delta = {}
    if latest_run and previous_run:
        delta = {
            "items_fetched_delta": int(latest_run.get("items_fetched", 0) or 0) - int(previous_run.get("items_fetched", 0) or 0),
            "items_kept_delta": int(latest_run.get("items_kept", 0) or 0) - int(previous_run.get("items_kept", 0) or 0),
            "alerts_created_delta": int(latest_run.get("alerts_created", 0) or 0) - int(previous_run.get("alerts_created", 0) or 0),
        }

    return {
        "status": "ok",
        "window_minutes": window_minutes,
        "summary": {
            "new_articles": len(recent_articles),
            "new_alerts": len(recent_alerts),
            "latest_run_status": latest_run.get("status", "n/a") if latest_run else "n/a",
            "latest_run_started_at": latest_run.get("started_at", "") if latest_run else "",
        },
        "delta": delta,
        "recent_articles": recent_articles[:limit],
        "recent_alerts": recent_alerts[:limit],
    }
