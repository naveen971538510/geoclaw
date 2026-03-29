import json
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List

from config import DB_PATH
from market import get_latest_market_snapshots
from services.action_service import list_actions
from services.reasoning_service import list_reasoning_chains
from services.thesis_service import get_thesis_detail, get_thesis_timeline, normalize_thesis_key
from services.db_helpers import get_conn as shared_get_conn


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    return shared_get_conn(DB_PATH)


def _loads(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


def _table_columns(cur, table_name: str):
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def _fetch_cards(limit: int = 100) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    enrichment_columns = _table_columns(cur, "article_enrichment")
    why_it_matters_sql = "ae.why_it_matters" if "why_it_matters" in enrichment_columns else "'' AS why_it_matters"
    confidence_score_sql = "ae.confidence_score" if "confidence_score" in enrichment_columns else "0.5 AS confidence_score"
    urgency_level_sql = "ae.urgency_level" if "urgency_level" in enrichment_columns else "'medium' AS urgency_level"
    impact_radius_sql = "ae.impact_radius" if "impact_radius" in enrichment_columns else "'regional' AS impact_radius"
    contradicts_narrative_sql = (
        "ae.contradicts_narrative" if "contradicts_narrative" in enrichment_columns else "0 AS contradicts_narrative"
    )
    llm_category_sql = "ae.llm_category" if "llm_category" in enrichment_columns else "'other' AS llm_category"
    llm_importance_sql = "ae.llm_importance" if "llm_importance" in enrichment_columns else "'medium' AS llm_importance"
    llm_mode_sql = "ae.llm_mode" if "llm_mode" in enrichment_columns else "'' AS llm_mode"
    llm_fallback_reason_sql = "ae.llm_fallback_reason" if "llm_fallback_reason" in enrichment_columns else "'' AS llm_fallback_reason"
    cluster_key_sql = "ae.cluster_key" if "cluster_key" in enrichment_columns else "'' AS cluster_key"
    cluster_size_sql = "ae.cluster_size" if "cluster_size" in enrichment_columns else "1 AS cluster_size"
    cur.execute(
        f"""
        SELECT
            ia.id AS article_id,
            ia.source_name,
            ia.headline,
            ia.summary,
            ia.url,
            ia.published_at,
            ia.fetched_at,
            ae.signal,
            ae.sentiment_score,
            ae.impact_score,
            ae.asset_tags,
            ae.macro_tags,
            ae.watchlist_hits,
            ae.alert_tags,
            ae.thesis,
            ae.bull_case,
            ae.bear_case,
            ae.what_to_watch,
            ae.confidence,
            {why_it_matters_sql},
            {confidence_score_sql},
            {urgency_level_sql},
            {impact_radius_sql},
            {contradicts_narrative_sql},
            {llm_category_sql},
            {llm_importance_sql},
            {llm_mode_sql},
            {llm_fallback_reason_sql},
            {cluster_key_sql},
            {cluster_size_sql},
            ae.created_at
        FROM ingested_articles ia
        LEFT JOIN article_enrichment ae
          ON ia.id = ae.article_id
        ORDER BY COALESCE(ae.impact_score, 0) DESC, ia.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    cur.execute("SELECT thesis_key, confidence FROM agent_theses")
    thesis_rows = cur.fetchall()
    conn.close()

    thesis_confidence_map = {
        normalize_thesis_key(row["thesis_key"] or ""): float(row["confidence"] or 0.5)
        for row in thesis_rows
    }

    out = []
    for row in rows:
        article_confidence = float(row["confidence"] or 0) / 100.0 if row["confidence"] is not None else 0.0
        thesis_key = normalize_thesis_key(row["thesis"] or "")
        thesis_confidence = thesis_confidence_map.get(thesis_key)
        display_confidence = thesis_confidence
        confidence_source = "thesis"
        if display_confidence is None:
            if article_confidence > 0:
                display_confidence = article_confidence
                confidence_source = "article"
            else:
                display_confidence = float(row["confidence_score"] if row["confidence_score"] is not None else 0.5)
                confidence_source = "llm"
        out.append(
            {
                "article_id": row["article_id"],
                "source": row["source_name"],
                "headline": row["headline"],
                "summary": row["summary"] or "",
                "url": row["url"],
                "published_at": row["published_at"] or "",
                "fetched_at": row["fetched_at"] or "",
                "signal": row["signal"] or "Neutral",
                "sentiment_score": row["sentiment_score"] or 0.0,
                "impact_score": row["impact_score"] or 0,
                "asset_tags": _loads(row["asset_tags"]),
                "macro_tags": _loads(row["macro_tags"]),
                "watchlist_hits": _loads(row["watchlist_hits"]),
                "alert_tags": _loads(row["alert_tags"]),
                "thesis": row["thesis"] or "",
                "bull_case": row["bull_case"] or "",
                "bear_case": row["bear_case"] or "",
                "what_to_watch": row["what_to_watch"] or "",
                "confidence": row["confidence"] or 0,
                "article_confidence": article_confidence,
                "thesis_key": thesis_key,
                "thesis_confidence": thesis_confidence,
                "display_confidence": max(0.0, min(1.0, float(display_confidence or 0.0))),
                "confidence_source": confidence_source,
                "why_it_matters": row["why_it_matters"] or "",
                "confidence_score": row["confidence_score"] if row["confidence_score"] is not None else 0.5,
                "urgency_level": row["urgency_level"] or "medium",
                "impact_radius": row["impact_radius"] or "regional",
                "contradicts_narrative": bool(row["contradicts_narrative"]),
                "llm_category": row["llm_category"] or "other",
                "llm_importance": row["llm_importance"] or "medium",
                "llm_mode": row["llm_mode"] or "",
                "llm_fallback_reason": row["llm_fallback_reason"] or "",
                "cluster_key": row["cluster_key"] or "",
                "cluster_size": int(row["cluster_size"] or 1),
                "created_at": row["created_at"] or "",
            }
        )
    return out


def _fetch_top_alerts(limit: int = 10) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            al.id,
            al.priority,
            al.reason,
            al.status,
            al.is_starred,
            al.created_at,
            ia.headline,
            ia.url,
            ia.source_name,
            ae.confidence
        FROM alert_events al
        JOIN ingested_articles ia
          ON al.article_id = ia.id
        LEFT JOIN article_enrichment ae
          ON ae.article_id = ia.id
        ORDER BY al.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    out = []
    for row in rows:
        out.append(
            {
                "priority": row["priority"],
                "reason": row["reason"],
                "status": row["status"] or "open",
                "is_starred": bool(row["is_starred"]),
                "confidence_score": round(float(row["confidence"] or 0) / 100.0, 2) if row["confidence"] is not None else 0.5,
                "created_at": row["created_at"],
                "headline": row["headline"],
                "url": row["url"],
                "source": row["source_name"],
            }
        )
    return out


def _fetch_theses(limit: int = 40) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_theses'")
    if not cur.fetchone():
        conn.close()
        return []
    cur.execute(
        """
        SELECT
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
            evidence_count,
            contradiction_count,
            last_updated_at,
            last_update_reason,
            terminal_risk,
            watchlist_suggestion,
            timeframe,
            confidence_velocity
        FROM agent_theses
        ORDER BY last_updated_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "thesis_key": row["thesis_key"] or "",
            "title": row["title"] or row["current_claim"] or "",
            "current_claim": row["current_claim"] or "",
            "bull_case": row["bull_case"] or "",
            "bear_case": row["bear_case"] or "",
            "key_risk": row["key_risk"] or "",
            "watch_for_next": row["watch_for_next"] or "",
            "category": row["category"] or "other",
            "confidence": float(row["confidence"] or 0.5),
            "status": row["status"] or "active",
            "evidence_count": int(row["evidence_count"] or 0),
            "contradiction_count": int(row["contradiction_count"] or 0),
            "last_updated_at": row["last_updated_at"] or "",
            "last_update_reason": row["last_update_reason"] or "",
            "terminal_risk": row["terminal_risk"] or "",
            "watchlist_suggestion": row["watchlist_suggestion"] or "",
            "timeframe": row["timeframe"] or "",
            "confidence_velocity": float(row["confidence_velocity"] or 0.0),
        }
        for row in rows
    ]


def _fetch_journal(limit: int = 2) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, run_id, journal_type, summary, metrics_json, created_at
        FROM agent_journal
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        except Exception:
            item["metrics"] = {}
        rows.append(item)
    conn.close()
    return rows


def get_terminal_agent_summary() -> Dict:
    latest = (_fetch_journal(limit=1) or [{}])[0]
    metrics = latest.get("metrics", {}) or {}
    touched = ((metrics.get("thesis_updates", {}) or {}).get("touched", []) or [])
    top_detail = get_thesis_detail(touched[0]) if touched else {}
    llm_metrics = metrics.get("llm_metrics", {}) or {}
    contradiction_llm = metrics.get("contradiction_llm_metrics", {}) or {}
    llm_calls = int(llm_metrics.get("llm_calls_made", 0) or 0) + int(contradiction_llm.get("llm_calls_made", 0) or 0)
    tasks_closed = int(sum(int(value or 0) for value in (metrics.get("task_closures", {}) or {}).values()) or 0)
    return {
        "created_at": latest.get("created_at", ""),
        "summary": latest.get("summary", ""),
        "stories_reviewed": int(metrics.get("items_kept", 0) or metrics.get("items_fetched", 0) or 0),
        "clusters_reviewed": int(metrics.get("cluster_identities_seen", 0) or 0),
        "theses_updated": int((metrics.get("thesis_updates", {}) or {}).get("upserts", 0) or 0)
        + int((metrics.get("thesis_updates", {}) or {}).get("confidence_updates", 0) or 0),
        "tasks_closed": tasks_closed,
        "actions_proposed": int(metrics.get("action_proposals_created", 0) or 0),
        "top_belief_change": {
            "thesis_key": top_detail.get("thesis_key", ""),
            "title": top_detail.get("title", "") or top_detail.get("current_claim", ""),
            "status": top_detail.get("status", ""),
            "confidence": float(top_detail.get("confidence", 0.0) or 0.0),
        },
        "top_reason": top_detail.get("last_update_reason", "") or latest.get("summary", ""),
        "llm_path": "live" if llm_calls > 0 else "fallback",
        "llm_calls_made": llm_calls,
        "llm_cache_hits": int(llm_metrics.get("cache_hits", 0) or 0),
        "duration_seconds": float(metrics.get("duration_seconds", 0.0) or 0.0),
    }


def get_terminal_diff() -> Dict:
    journal = _fetch_journal(limit=2)
    latest = journal[0] if journal else {}
    previous = journal[1] if len(journal) > 1 else {}
    latest_metrics = latest.get("metrics", {}) or {}
    previous_metrics = previous.get("metrics", {}) or {}
    since = previous.get("created_at", "") or ""
    conn = get_conn()
    cur = conn.cursor()
    thesis_events = []
    task_changes = []
    action_changes = []
    if since:
        cur.execute(
            """
            SELECT thesis_key, event_type, note, created_at
            FROM thesis_events
            WHERE created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """,
            (since,),
        )
        thesis_events = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT id, title, status, closed_reason, updated_at
            FROM agent_tasks
            WHERE updated_at >= ?
              AND COALESCE(status, '') <> 'open'
            ORDER BY updated_at DESC, id DESC
            LIMIT 20
            """,
            (since,),
        )
        task_changes = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT id, action_type, thesis_key, status, audit_note, created_at, reviewed_at
            FROM agent_actions
            WHERE created_at >= ?
               OR reviewed_at >= ?
            ORDER BY COALESCE(reviewed_at, created_at) DESC, id DESC
            LIMIT 20
            """,
            (since, since),
        )
        action_changes = [dict(row) for row in cur.fetchall()]
    conn.close()
    numeric_fields = [
        "items_fetched",
        "items_kept",
        "alerts_created",
        "action_proposals_created",
        "reasoning_chains_built",
        "research_agent_runs",
        "autonomous_goals_created",
    ]
    deltas = {
        field: int(latest_metrics.get(field, 0) or 0) - int(previous_metrics.get(field, 0) or 0)
        for field in numeric_fields
    }
    return {
        "latest_created_at": latest.get("created_at", ""),
        "previous_created_at": previous.get("created_at", ""),
        "metric_deltas": deltas,
        "thesis_changes": thesis_events,
        "tasks_closed": task_changes,
        "actions_changed": action_changes,
    }


def get_terminal_drilldown(thesis_key: str) -> Dict:
    clean_key = normalize_thesis_key(thesis_key)
    if not clean_key:
        return {}
    detail = get_thesis_detail(clean_key)
    timeline = get_thesis_timeline(clean_key)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            d.id,
            d.article_id,
            d.cluster_key,
            d.decision_type,
            d.reason,
            d.state,
            d.created_at,
            ia.headline,
            ia.url
        FROM agent_decisions d
        LEFT JOIN ingested_articles ia
          ON ia.id = d.article_id
        WHERE LOWER(COALESCE(d.thesis_key, '')) = ?
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT 20
        """,
        (clean_key,),
    )
    decisions = [dict(row) for row in cur.fetchall()]
    conn.close()
    reasoning = [item for item in list_reasoning_chains(limit=50) if normalize_thesis_key(item.get("thesis_key", "")) == clean_key][:10]
    actions = [item for item in list_actions(limit=100) if normalize_thesis_key(item.get("thesis_key", "")) == clean_key][:10]
    return {
        "thesis": detail,
        "timeline": timeline,
        "decisions": decisions,
        "reasoning": reasoning,
        "actions": actions,
        "trace": {
            "articles": detail.get("linked_articles", []) if detail else [],
            "clusters": [item.get("cluster_key", "") for item in decisions if item.get("cluster_key")],
            "policy_notes": [item.get("audit_note", "") for item in actions if item.get("audit_note")],
        },
    }


def get_terminal_payload(limit: int = 100) -> Dict:
    cards = _fetch_cards(limit=limit)
    market_snapshot = get_latest_market_snapshots()
    top_alerts = _fetch_top_alerts(limit=10)
    theses = _fetch_theses(limit=30)

    source_counter = Counter()
    asset_counter = Counter()

    bullish = bearish = neutral = alerts = watch_hits = 0

    for card in cards:
        source_counter[card["source"]] += 1
        for asset in card["asset_tags"]:
            asset_counter[asset] += 1

        if card["signal"] == "Bullish":
            bullish += 1
        elif card["signal"] == "Bearish":
            bearish += 1
        else:
            neutral += 1

        if card["alert_tags"]:
            alerts += 1
        if card["watchlist_hits"]:
            watch_hits += 1

    return {
        "mode": "intelligence",
        "updated_at": utc_now_iso(),
        "market_snapshot": market_snapshot,
        "stats": {
            "articles": len(cards),
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "alerts": alerts,
            "watchlist_hits": watch_hits,
        },
        "source_distribution": [
            {"source": k, "count": v}
            for k, v in source_counter.most_common(12)
        ],
        "asset_heat": [
            {"asset": k, "count": v}
            for k, v in asset_counter.most_common(12)
        ],
        "top_alerts": top_alerts,
        "theses": theses,
        "cards": cards,
    }


def get_terminal_theses(limit: int = 40) -> List[Dict]:
    return _fetch_theses(limit=limit)


def get_terminal_actions(limit: int = 60) -> List[Dict]:
    return list_actions(limit=limit)


def list_terminal_articles(limit: int = 20, offset: int = 0, days: int = 2) -> Dict:
    conn = get_conn()
    cur = conn.cursor()
    days_value = max(1, int(days or 2))
    cur.execute(
        """
        SELECT COUNT(*)
        FROM ingested_articles
        WHERE COALESCE(fetched_at, published_at, '') >= datetime('now', ?)
        """,
        (f"-{days_value} days",),
    )
    total = int(cur.fetchone()[0] or 0)
    cur.execute(
        """
        SELECT id, headline, url, source_name, published_at, fetched_at
        FROM ingested_articles
        WHERE COALESCE(fetched_at, published_at, '') >= datetime('now', ?)
        ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (f"-{days_value} days", int(limit or 20), int(offset or 0)),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {
        "total": total,
        "articles": [
            {
                "id": int(row.get("id", 0) or 0),
                "headline": row.get("headline", ""),
                "url": row.get("url", ""),
                "source": row.get("source_name", ""),
                "published_at": row.get("published_at", ""),
                "fetched_at": row.get("fetched_at", ""),
            }
            for row in rows
        ],
    }


def list_terminal_clusters(limit: int = 30) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            COALESCE(NULLIF(TRIM(ae.cluster_key), ''), 'uncategorized') AS label,
            COUNT(*) AS count,
            MAX(ia.published_at) AS latest_published_at,
            MAX(ia.id) AS latest_article_id
        FROM article_enrichment ae
        JOIN ingested_articles ia ON ia.id = ae.article_id
        GROUP BY COALESCE(NULLIF(TRIM(ae.cluster_key), ''), 'uncategorized')
        ORDER BY count DESC, latest_published_at DESC
        LIMIT ?
        """,
        (int(limit or 30),),
    )
    rows = []
    for row in cur.fetchall():
        latest_article_id = int(row["latest_article_id"] or 0)
        latest_article = {}
        if latest_article_id:
            cur.execute(
                "SELECT headline, url FROM ingested_articles WHERE id = ? LIMIT 1",
                (latest_article_id,),
            )
            latest = cur.fetchone()
            latest_article = dict(latest) if latest else {}
        rows.append(
            {
                "label": row["label"],
                "count": int(row["count"] or 0),
                "latest_article": latest_article,
                "latest_published_at": row["latest_published_at"] or "",
            }
        )
    conn.close()
    return rows


def search_terminal_records(query: str, limit: int = 20) -> Dict:
    clean = str(query or "").strip()
    if not clean:
        return {"articles": [], "theses": [], "actions": []}
    like = f"%{clean}%"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, headline, url, source_name, published_at
        FROM ingested_articles
        WHERE headline LIKE ? OR summary LIKE ?
        ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC
        LIMIT ?
        """,
        (like, like, int(limit or 20)),
    )
    articles = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT thesis_key, title, current_claim, confidence, status
        FROM agent_theses
        WHERE thesis_key LIKE ? OR title LIKE ? OR current_claim LIKE ?
        ORDER BY confidence DESC, last_updated_at DESC
        LIMIT ?
        """,
        (like, like, like, int(limit or 20)),
    )
    theses = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT id, action_type, thesis_key, status, audit_note, payload_json
        FROM agent_actions
        WHERE thesis_key LIKE ? OR audit_note LIKE ? OR payload_json LIKE ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (like, like, like, int(limit or 20)),
    )
    actions = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"articles": articles, "theses": theses, "actions": actions}
