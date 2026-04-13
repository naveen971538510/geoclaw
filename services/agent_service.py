import json
import time
from copy import deepcopy
from typing import Dict, List

from config import (
    DB_PATH,
    AGENT_MAX_RECORDS_PER_SOURCE,
    ENABLE_GDELT,
    ENABLE_GUARDIAN,
    ENABLE_NEWSAPI,
    ENABLE_RSS,
    ENABLE_SOCIAL_MEDIA,
    GDELT_STATE_FILE,
)
from market import fetch_and_store_market_snapshots, get_latest_market_snapshots
from services.db_helpers import get_conn as shared_get_conn
from services.ingest_service import run_ingestion_cycle
from services.terminal_service import get_terminal_payload


TOPIC_QUERIES = [
    {
        "name": "macro_broad",
        "query": '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency OR recession)',
        "enabled_sources": ["rss", "social", "gdelt", "newsapi", "guardian"],
    },
    {
        "name": "oil",
        "query": '(oil OR brent OR wti OR crude OR opec OR refinery OR tanker)',
        "enabled_sources": ["rss", "social", "newsapi", "guardian"],
    },
    {
        "name": "gold",
        "query": '(gold OR bullion OR xau OR safe haven)',
        "enabled_sources": ["rss", "social", "newsapi", "guardian"],
    },
    {
        "name": "fx",
        "query": '(forex OR currency OR currencies OR usd OR gbp OR eur OR yen OR sterling OR fx)',
        "enabled_sources": ["rss", "social", "newsapi", "guardian"],
    },
    {
        "name": "rates",
        "query": '(fed OR boe OR ecb OR interest rate OR bond yield OR treasury OR cpi OR inflation)',
        "enabled_sources": ["rss", "social", "newsapi", "guardian"],
    },
    {
        "name": "equities_geopolitics",
        "query": '(stocks OR equities OR shares OR nasdaq OR s&p OR dow OR ftse OR nikkei OR war OR sanctions OR tariff OR strike OR conflict)',
        "enabled_sources": ["rss", "social", "newsapi", "guardian"],
    },
]
QUERY_INSENSITIVE_SOURCES = {"rss", "social"}


def get_conn():
    return shared_get_conn(DB_PATH)


def _gdelt_state():
    try:
        if GDELT_STATE_FILE.exists():
            return json.loads(GDELT_STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _gdelt_cooldown_active() -> bool:
    state = _gdelt_state()
    until = float(state.get("cooldown_until", 0) or 0)
    return time.time() < until


def _active_sources(enabled_sources: List[str]) -> List[str]:
    active = []
    for raw_name in enabled_sources or []:
        name = str(raw_name or "").strip().lower()
        if name == "rss" and ENABLE_RSS:
            active.append(name)
        elif name == "social" and ENABLE_SOCIAL_MEDIA:
            active.append(name)
        elif name == "gdelt" and ENABLE_GDELT and not _gdelt_cooldown_active():
            active.append(name)
        elif name == "newsapi" and ENABLE_NEWSAPI:
            active.append(name)
        elif name == "guardian" and ENABLE_GUARDIAN:
            active.append(name)
    return active


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
    started = time.time()
    max_records_per_source = int(max_records_per_source or AGENT_MAX_RECORDS_PER_SOURCE)
    market_started = time.time()
    market_result = fetch_and_store_market_snapshots()
    market_duration = round(max(0.0, time.time() - market_started), 3)

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
        "duration_seconds": 0.0,
        "market_duration_seconds": market_duration,
        "actual_ingestion_cycles": 0,
        "reused_topic_runs": 0,
    }
    # Keep ingestion focused on fetch/classify/store. The dedicated reasoning
    # pipeline runs immediately after ingestion in the live loop, so building
    # extra reasoning chains here only adds duplicate latency.
    remaining_reasoning_budget = 0
    shared_results = {}

    for topic in TOPIC_QUERIES:
        topic_started = time.time()
        active_sources = _active_sources(topic.get("enabled_sources", []))
        reuse_key = tuple(sorted(active_sources)) if active_sources and set(active_sources).issubset(QUERY_INSENSITIVE_SOURCES) else None
        reused = False
        if reuse_key and reuse_key in shared_results:
            result = deepcopy(shared_results[reuse_key])
            reused = True
            topic_duration = round(max(0.0, time.time() - topic_started), 3)
            totals["reused_topic_runs"] += 1
        else:
            result = run_ingestion_cycle(
                query=topic["query"],
                max_records_per_source=max_records_per_source,
                enabled_sources=topic.get("enabled_sources", []),
                reasoning_budget=remaining_reasoning_budget,
            )
            topic_duration = round(max(0.0, time.time() - topic_started), 3)
            totals["actual_ingestion_cycles"] += 1
            if reuse_key:
                shared_results[reuse_key] = deepcopy(result)

        totals["topic_runs"] += 1
        topic_llm = result.get("llm_metrics", {}) or {}
        if not reused:
            totals["items_fetched"] += int(result.get("items_fetched", 0))
            totals["items_kept"] += int(result.get("items_kept", 0))
            totals["alerts_created"] += int(result.get("alerts_created", 0))
            totals["errors"].extend(result.get("errors", []))
            totals["reasoning_chains_built"] += int(result.get("reasoning_chains_built", 0) or 0)
            remaining_reasoning_budget = max(0, remaining_reasoning_budget - int(result.get("reasoning_chains_built", 0) or 0))
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
                "items_fetched": 0 if reused else result.get("items_fetched", 0),
                "items_kept": 0 if reused else result.get("items_kept", 0),
                "items_suppressed": 0 if reused else result.get("items_suppressed", 0),
                "alerts_created": 0 if reused else result.get("alerts_created", 0),
                "reasoning_chains_built": 0 if reused else result.get("reasoning_chains_built", 0),
                "duration_seconds": topic_duration,
                "source_timings": (
                    [{"source": ",".join(active_sources) or "none", "status": "reused", "items_fetched": 0, "duration_seconds": 0.0}]
                    if reused
                    else result.get("source_timings", [])
                ),
                "used_sources": active_sources if reused else result.get("used_sources", []),
                "llm_metrics": {} if reused else topic_llm,
                "substep_durations": {} if reused else result.get("substep_durations", {}),
                "reused": reused,
                "reuse_key": list(reuse_key) if reuse_key else [],
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
    totals["duration_seconds"] = round(max(0.0, time.time() - started), 3)

    return totals
