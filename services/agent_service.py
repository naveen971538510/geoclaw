import json
from typing import Dict, List

from config import DB_PATH, AGENT_MAX_RECORDS_PER_SOURCE, GDELT_STATE_FILE
from market import fetch_and_store_market_snapshots, get_latest_market_snapshots
from services.db_helpers import get_conn as shared_get_conn
from services.ingest_service import run_ingestion_cycle
from services.terminal_service import get_terminal_payload


TOPIC_QUERIES = [
    {
        "name": "macro_broad",
        "query": '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency OR recession)',
        "enabled_sources": ["rss", "gdelt", "newsapi", "guardian"],
    },
    {
        "name": "oil",
        "query": '(oil OR brent OR wti OR crude OR opec OR refinery OR tanker)',
        "enabled_sources": ["rss", "newsapi", "guardian"],
    },
    {
        "name": "gold",
        "query": '(gold OR bullion OR xau OR safe haven)',
        "enabled_sources": ["rss", "newsapi", "guardian"],
    },
    {
        "name": "fx",
        "query": '(forex OR currency OR currencies OR usd OR gbp OR eur OR yen OR sterling OR fx)',
        "enabled_sources": ["rss", "newsapi", "guardian"],
    },
    {
        "name": "rates",
        "query": '(fed OR boe OR ecb OR interest rate OR bond yield OR treasury OR cpi OR inflation)',
        "enabled_sources": ["rss", "newsapi", "guardian"],
    },
    {
        "name": "equities_geopolitics",
        "query": '(stocks OR equities OR shares OR nasdaq OR s&p OR dow OR ftse OR nikkei OR war OR sanctions OR tariff OR strike OR conflict)',
        "enabled_sources": ["rss", "newsapi", "guardian"],
    },
]


def get_conn():
    return shared_get_conn(DB_PATH)


def _gdelt_state():
    try:
        if GDELT_STATE_FILE.exists():
            return json.loads(GDELT_STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def get_agent_status(limit: int = 12) -> Dict:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, run_type, started_at, finished_at, status, items_fetched, items_kept, alerts_created, error_text
        FROM agent_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    runs = [dict(row) for row in cur.fetchall()]
    conn.close()

    payload = get_terminal_payload(limit=30)
    market = get_latest_market_snapshots()
    real_loop = {
        "decision_count": 0,
        "task_count": 0,
        "journal_count": 0,
        "outcomes": {},
    }
    try:
        from services.decision_service import list_decisions
        from services.task_service import list_tasks
        from services.agent_loop_service import list_journal
        from services.memory_service import outcome_summary

        real_loop = {
            "decision_count": len(list_decisions(limit=50, open_only=False)),
            "task_count": len(list_tasks(limit=50, status=None)),
            "journal_count": len(list_journal(limit=20)),
            "outcomes": outcome_summary(limit=200),
        }
    except Exception:
        pass

    return {
        "runs": runs,
        "terminal_stats": payload.get("stats", {}),
        "market_count": len(market),
        "top_alerts_count": len(payload.get("top_alerts", [])),
        "gdelt_state": _gdelt_state(),
        "real_loop": real_loop,
    }


def run_agent_cycle(max_records_per_source: int = None) -> Dict:
    max_records_per_source = int(max_records_per_source or AGENT_MAX_RECORDS_PER_SOURCE)
    market_result = fetch_and_store_market_snapshots()

    totals = {
        "status": "ok",
        "topic_runs": 0,
        "items_fetched": 0,
        "items_kept": 0,
        "alerts_created": 0,
        "errors": [],
        "topics": [],
        "market": market_result,
        "llm_metrics": {
            "eligible_articles": 0,
            "eligible_clusters": 0,
            "llm_calls_made": 0,
            "article_calls_made": 0,
            "cluster_calls_made": 0,
            "fallback_articles": 0,
            "fallback_reasons": {},
        },
        "reasoning_chains_built": 0,
    }
    remaining_reasoning_budget = 5

    for topic in TOPIC_QUERIES:
        result = run_ingestion_cycle(
            query=topic["query"],
            max_records_per_source=max_records_per_source,
            enabled_sources=topic.get("enabled_sources", []),
            reasoning_budget=remaining_reasoning_budget,
        )

        totals["topic_runs"] += 1
        totals["items_fetched"] += int(result.get("items_fetched", 0))
        totals["items_kept"] += int(result.get("items_kept", 0))
        totals["alerts_created"] += int(result.get("alerts_created", 0))
        totals["errors"].extend(result.get("errors", []))
        totals["reasoning_chains_built"] += int(result.get("reasoning_chains_built", 0) or 0)
        remaining_reasoning_budget = max(0, remaining_reasoning_budget - int(result.get("reasoning_chains_built", 0) or 0))
        topic_llm = result.get("llm_metrics", {}) or {}
        totals["llm_metrics"]["eligible_articles"] += int(topic_llm.get("eligible_articles", 0) or 0)
        totals["llm_metrics"]["eligible_clusters"] += int(topic_llm.get("eligible_clusters", 0) or 0)
        totals["llm_metrics"]["llm_calls_made"] += int(topic_llm.get("llm_calls_made", 0) or 0)
        totals["llm_metrics"]["article_calls_made"] += int(topic_llm.get("article_calls_made", 0) or 0)
        totals["llm_metrics"]["cluster_calls_made"] += int(topic_llm.get("cluster_calls_made", 0) or 0)
        totals["llm_metrics"]["fallback_articles"] += int(topic_llm.get("fallback_articles", 0) or 0)
        for reason, count in (topic_llm.get("fallback_reasons", {}) or {}).items():
            totals["llm_metrics"]["fallback_reasons"][reason] = (
                int(totals["llm_metrics"]["fallback_reasons"].get(reason, 0) or 0)
                + int(count or 0)
            )
        totals["topics"].append(
            {
                "name": topic["name"],
                "status": result.get("status", "unknown"),
                "items_fetched": result.get("items_fetched", 0),
                "items_kept": result.get("items_kept", 0),
                "items_suppressed": result.get("items_suppressed", 0),
                "alerts_created": result.get("alerts_created", 0),
                "reasoning_chains_built": result.get("reasoning_chains_built", 0),
                "used_sources": result.get("used_sources", []),
                "llm_metrics": topic_llm,
            }
        )

    if totals["errors"]:
        totals["status"] = "partial"

    payload = get_terminal_payload(limit=50)
    totals["terminal_stats"] = payload.get("stats", {})
    totals["top_preview"] = [
        {
            "headline": x.get("headline", ""),
            "source": x.get("source", ""),
            "impact_score": x.get("impact_score", 0),
            "signal": x.get("signal", "Neutral"),
            "asset_tags": x.get("asset_tags", []),
            "alert_tags": x.get("alert_tags", []),
        }
        for x in payload.get("cards", [])[:10]
    ]
    totals["gdelt_state"] = _gdelt_state()

    return totals
