import sqlite3
from typing import Dict, List

from config import DB_PATH
from market import fetch_and_store_market_snapshots, get_latest_market_snapshots
from services.ingest_service import run_ingestion_cycle
from services.terminal_service import get_terminal_payload


TOPIC_QUERIES = [
    {"name": "oil", "query": '(oil OR brent OR wti OR crude OR opec OR refinery OR tanker)'},
    {"name": "gold", "query": '(gold OR bullion OR xau OR safe haven)'},
    {"name": "fx", "query": '(forex OR currency OR currencies OR usd OR gbp OR eur OR yen OR sterling OR fx)'},
    {"name": "rates", "query": '(fed OR boe OR ecb OR interest rate OR bond yield OR treasury OR cpi OR inflation)'},
    {"name": "equities", "query": '(stocks OR equities OR shares OR nasdaq OR s&p OR dow OR ftse OR nikkei)'},
    {"name": "geopolitics", "query": '(war OR sanctions OR tariff OR strike OR conflict OR missile OR default OR recession)'},
]


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


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

    return {
        "runs": runs,
        "terminal_stats": payload.get("stats", {}),
        "market_count": len(market),
        "top_alerts_count": len(payload.get("top_alerts", [])),
    }


def run_agent_cycle(max_records_per_source: int = 10) -> Dict:
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
    }

    for topic in TOPIC_QUERIES:
        result = run_ingestion_cycle(
            query=topic["query"],
            max_records_per_source=max_records_per_source,
        )

        totals["topic_runs"] += 1
        totals["items_fetched"] += int(result.get("items_fetched", 0))
        totals["items_kept"] += int(result.get("items_kept", 0))
        totals["alerts_created"] += int(result.get("alerts_created", 0))
        totals["errors"].extend(result.get("errors", []))
        totals["topics"].append(
            {
                "name": topic["name"],
                "status": result.get("status", "unknown"),
                "items_fetched": result.get("items_fetched", 0),
                "items_kept": result.get("items_kept", 0),
                "alerts_created": result.get("alerts_created", 0),
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

    return totals
