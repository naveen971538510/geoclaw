"""
GeoClaw Agent Brain
====================
Full agentic loop powered by Groq (llama-3.1-8b-instant).

Architecture:
  Plan → Tool calls → Observe → Reason → Act → Telegram

Drop this file into ~/GeoClaw/ and run:
    python agent_brain.py

Requires in .env.geoclaw:
    GROQ_API_KEY=gsk_...
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...
"""

from __future__ import annotations

import html
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (
    DB_PATH,
    MAX_AUTONOMOUS_GOALS_PER_DAY,
    MAX_ACTION_PROPOSALS_PER_RUN,
    ACTION_PROPOSAL_MIN_CONFIDENCE,
    TRACKED_SYMBOLS,
    DEFAULT_WATCHLIST,
    _load_local_env,
    ENV_FILE,
)

from intelligence.db import save_memory_snapshot

_load_local_env(ENV_FILE)

from briefing_formatter import build_briefing
from services.thesis_tracker import update_theses_from_run_state

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
STATUS_FILE = ROOT / "logs" / "agent_brain.status.json"
STATUS_HTML_FILE = ROOT / "logs" / "agent_brain.status.html"
RUN_HISTORY_LIMIT = 12
DEGRADED_ALERT_THRESHOLD = 3
DEGRADED_ALERT_REPEAT_EVERY = 6
SIGNAL_SNAPSHOT_LIMIT = 20
SIGNAL_FRESHNESS_HOURS = 48
MACRO_FRESHNESS_DAYS = {
    "TREASURY_10Y": 14,
    "TREASURY_2Y": 14,
    "CPI_YOY_PCT": 75,
    "FEDFUNDS": 75,
    "UNRATE": 75,
    "NFP_LEVEL_THOUSANDS": 75,
    "NFP_MOM_THOUSANDS": 75,
    "GDP_GROWTH": 130,
}
REQUIRED_MACRO_METRICS = tuple(MACRO_FRESHNESS_DAYS.keys())
_CURRENT_RUN_STATE: Optional[Dict[str, Any]] = None

logger = logging.getLogger("geoclaw.agent_brain")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ─────────────────────────────────────────────
# TOOLS REGISTRY
# ─────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_latest_signals",
            "description": "Fetch the latest BUY/SELL signals from the GeoClaw signal engine database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of signals to return (default 10)",
                        "default": 10,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_signal_engine",
            "description": "Trigger a fresh signal engine run to score macro data and update geoclaw_signals.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_data",
            "description": "Get latest prices for tracked symbols (GLD, USO, GBPUSD, SPY, QQQ, etc.)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro_metrics",
            "description": "Fetch latest macro indicators from the database (inflation, employment, GDP, etc.)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_market_bias",
            "description": "Analyse current signals and prices to determine overall market bias (BULLISH/BEARISH/NEUTRAL) with reasoning.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for macro-financial information using DuckDuckGo. Returns top results with snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'Fed rate decision June 2026')",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_breaking_news",
            "description": "Fetch the latest breaking macro/geopolitical news from configured sources.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_thesis",
            "description": "Deep-dive research on a specific thesis — gathers supporting and contradicting evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thesis_key": {
                        "type": "string",
                        "description": "The thesis key/title to research",
                    }
                },
                "required": ["thesis_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_theses",
            "description": "Retrieve current active investment theses with confidence scores and status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ─────────────────────────────────────────────
# TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────


def _new_run_state() -> Dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    return {
        "run_id": started_at.strftime("%Y%m%dT%H%M%SZ"),
        "started_at": started_at.isoformat(),
        "degraded_mode": False,
        "degradation_notes": [],
        "degradation_codes": set(),
        "groq_result": {"status": "not_called"},
    }


def _run_state_for_tool(run_state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    return run_state if run_state is not None else _CURRENT_RUN_STATE


def _mark_degraded(run_state: Optional[Dict[str, Any]], code: str, detail: str) -> None:
    if run_state is None:
        return
    codes = run_state.setdefault("degradation_codes", set())
    if code in codes:
        return
    codes.add(code)
    run_state["degraded_mode"] = True
    note = f"{code}: {detail}"
    run_state.setdefault("degradation_notes", []).append(note)
    logger.warning("degraded_mode run_id=%s code=%s detail=%s", run_state.get("run_id"), code, detail)


def _run_status(run_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if run_state is None:
        return {"run_id": "", "degraded_mode": False, "degradation_notes": []}
    return {
        "run_id": run_state.get("run_id", ""),
        "started_at": run_state.get("started_at", ""),
        "degraded_mode": bool(run_state.get("degraded_mode")),
        "degradation_notes": list(run_state.get("degradation_notes", [])),
        "groq_result": dict(run_state.get("groq_result", {})),
        "last_price_refresh_time": run_state.get("last_price_refresh_time", ""),
        "last_telegram_send_time": run_state.get("last_telegram_send_time", ""),
    }


def _attach_run_status(tool_state: Dict[str, Any], run_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    tool_state["_run"] = _run_status(run_state)
    return tool_state


def _json_serial(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(str(item) for item in obj)
    return str(obj)


def tool_get_latest_signals(limit: int = 10, run_state: Optional[Dict[str, Any]] = None) -> Dict:
    run_state = _run_state_for_tool(run_state)
    try:
        from intelligence.db import ensure_intelligence_schema, query_all, get_database_url
        if not get_database_url():
            _mark_degraded(run_state, "signals_unavailable", "DATABASE_URL not set")
            return {"error": "DATABASE_URL not set", "signals": []}
        requested_limit = max(1, int(limit))
        if run_state is not None and "signals_snapshot" in run_state:
            signals = run_state["signals_snapshot"][:requested_limit]
            return {
                "signals": signals,
                "count": len(signals),
                "freshness": run_state.get("signal_freshness") or _signal_freshness(signals),
            }

        ensure_intelligence_schema()
        since = datetime.now(timezone.utc) - timedelta(hours=48)
        snapshot_limit = max(requested_limit, SIGNAL_SNAPSHOT_LIMIT) if run_state is not None else requested_limit
        fetch_limit = snapshot_limit * 3
        rows = query_all(
            """
            SELECT signal_name, direction, confidence, explanation_plain_english, ts
            FROM geoclaw_signals
            WHERE ts >= %s AND direction IN ('BUY','SELL')
            ORDER BY confidence DESC, ts DESC
            LIMIT %s;
            """,
            (since, fetch_limit),
        )
        snapshot = _dedupe_signals([dict(r) for r in rows])[:snapshot_limit]
        if run_state is not None:
            run_state["signals_snapshot"] = snapshot
        freshness = _signal_freshness(snapshot)
        if run_state is not None:
            run_state["signal_freshness"] = freshness
        if not snapshot:
            _mark_degraded(run_state, "signals_missing", "no fresh BUY/SELL signals in the 48h window")
        elif freshness.get("status") != "ok":
            _mark_degraded(run_state, "signal_freshness_failed", str(freshness))
        signals = snapshot[:requested_limit]
        return {"signals": signals, "count": len(signals), "freshness": freshness}
    except Exception as e:
        _mark_degraded(run_state, "signals_error", str(e))
        return {"error": str(e), "signals": []}


def tool_run_signal_engine(run_state: Optional[Dict[str, Any]] = None) -> Dict:
    run_state = _run_state_for_tool(run_state)
    if run_state is not None and "run_signal_engine" in run_state:
        return run_state["run_signal_engine"]
    try:
        from intelligence.signal_engine import run_signal_engine
        run_signal_engine()
        result = {"status": "ok", "message": "Signal engine completed successfully"}
    except Exception as e:
        result = {"status": "error", "message": str(e)}
        _mark_degraded(run_state, "signal_engine_error", str(e))
    if run_state is not None:
        run_state["run_signal_engine"] = result
        if result.get("status") == "ok":
            run_state.pop("signals_snapshot", None)
            run_state.pop("market_bias", None)
    return result


def _refresh_price_data_from_feed() -> Dict:
    try:
        from intelligence.db import ensure_intelligence_schema, get_connection
        from services.price_feed import PriceFeed

        ensure_intelligence_schema()

        source_symbols = [
            "^GSPC",      # -> SPX
            "GC=F",       # -> XAUUSD
            "BTC-USD",    # -> BTCUSD
            "GLD",
            "USO",
            "GBPUSD=X",   # -> GBPUSD
            "SPY",
            "QQQ",
        ]
        symbol_map = {
            "^GSPC": "SPX",
            "GC=F": "XAUUSD",
            "BTC-USD": "BTCUSD",
            "GBPUSD=X": "GBPUSD",
        }

        feed = PriceFeed()
        snapshot = feed.get_snapshot(source_symbols)
        if not snapshot:
            return {"status": "error", "message": "No fresh market snapshot returned", "inserted": 0}

        inserted = 0
        with get_connection() as conn:
            cur = conn.cursor()
            for source_symbol in source_symbols:
                data = snapshot.get(source_symbol) or {}
                price = data.get("price")
                ts = data.get("timestamp")
                if price is None:
                    continue
                ticker = symbol_map.get(source_symbol, source_symbol)
                cur.execute(
                    """
                    INSERT INTO price_data (ticker, price, ts)
                    VALUES (%s, %s, %s::timestamptz);
                    """,
                    (ticker, float(price), str(ts)),
                )
                inserted += 1
            cur.close()

        return {"status": "ok", "message": "price_data refreshed", "inserted": inserted}
    except Exception as e:
        return {"status": "error", "message": str(e), "inserted": 0}


def _refresh_price_data_once(run_state: Optional[Dict[str, Any]]) -> Dict:
    if run_state is not None and "price_refresh" in run_state:
        return run_state["price_refresh"]
    refresh = _refresh_price_data_from_feed()
    if run_state is not None:
        run_state["price_refresh"] = refresh
    if refresh.get("status") == "ok" and int(refresh.get("inserted") or 0) > 0:
        if run_state is not None:
            run_state["last_price_refresh_time"] = datetime.now(timezone.utc).isoformat()
        logger.info("Price refresh result: %s", json.dumps(refresh))
    else:
        _mark_degraded(run_state, "price_refresh_failed", str(refresh.get("message") or refresh))
    return refresh


def tool_get_price_data(run_state: Optional[Dict[str, Any]] = None) -> Dict:
    run_state = _run_state_for_tool(run_state)
    if run_state is not None and "price_data" in run_state:
        return run_state["price_data"]
    try:
        from intelligence.db import query_all

        refresh = _refresh_price_data_once(run_state)

        rows = query_all(
            "SELECT ticker, price, ts FROM price_data ORDER BY ts DESC LIMIT 100;"
        )
        seen = {}
        for r in rows:
            t = str(r["ticker"]).upper()
            if t not in seen:
                seen[t] = {"ticker": t, "price": float(r["price"]), "ts": str(r["ts"])}
        result = {"prices": list(seen.values()), "count": len(seen), "refresh": refresh}
        if not result["prices"]:
            _mark_degraded(run_state, "prices_missing", "no price rows available after refresh attempt")
        if run_state is not None:
            run_state["price_data"] = result
        return result
    except Exception as e:
        _mark_degraded(run_state, "price_data_error", str(e))
        result = {"error": str(e), "prices": [], "refresh": run_state.get("price_refresh") if run_state else {}}
        if run_state is not None:
            run_state["price_data"] = result
        return result


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _macro_freshness(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    by_name = {str(m.get("metric_name", "")).upper(): m for m in metrics}
    if not by_name:
        return {
            "status": "unavailable",
            "reason": "no macro rows available",
            "missing_metrics": list(REQUIRED_MACRO_METRICS),
            "stale_metrics": [],
            "degraded_metrics": [],
            "available_metrics": [],
        }
    missing = [name for name in REQUIRED_MACRO_METRICS if name not in by_name]
    stale = []
    degraded_metrics = []
    for name, max_age_days in MACRO_FRESHNESS_DAYS.items():
        metric = by_name.get(name)
        if not metric:
            continue
        observed_at = _parse_dt(metric.get("observed_at"))
        if observed_at is None:
            item = {"metric": name, "reason": "missing observed_at", "max_age_days": max_age_days}
            stale.append(item)
            degraded_metrics.append(item)
            continue
        age_days = (now - observed_at.astimezone(timezone.utc)).total_seconds() / 86400
        if age_days > max_age_days:
            item = {
                "metric": name,
                "observed_at": observed_at.isoformat(),
                "age_days": round(age_days, 1),
                "max_age_days": max_age_days,
            }
            stale.append(item)
            if age_days > max_age_days * 2:
                degraded_metrics.append(item)
    if len(missing) == len(REQUIRED_MACRO_METRICS):
        status = "unavailable"
    elif missing or degraded_metrics:
        status = "degraded"
    elif stale:
        status = "stale-but-usable"
    else:
        status = "fresh"
    return {
        "status": status,
        "missing_metrics": missing,
        "stale_metrics": stale,
        "degraded_metrics": degraded_metrics,
        "available_metrics": sorted(by_name.keys()),
    }


def tool_get_macro_metrics(run_state: Optional[Dict[str, Any]] = None) -> Dict:
    run_state = _run_state_for_tool(run_state)
    if run_state is not None and "macro_metrics" in run_state:
        return run_state["macro_metrics"]
    try:
        from intelligence.db import query_all
        rows = query_all(
            """
            SELECT DISTINCT ON (metric_name)
                metric_name, observed_at, value, previous_value, pct_change, created_at
            FROM macro_signals
            ORDER BY metric_name, observed_at DESC
            LIMIT 30;
            """
        )
        metrics = [dict(r) for r in rows]
        freshness = _macro_freshness(metrics)
        result = {"metrics": metrics, "count": len(metrics), "freshness": freshness}
        if freshness.get("status") in {"degraded", "unavailable"}:
            parts = []
            if freshness.get("missing_metrics"):
                parts.append("missing=" + ",".join(freshness["missing_metrics"]))
            degraded = freshness.get("degraded_metrics") or freshness.get("stale_metrics") or []
            if degraded:
                degraded_names = [str(item.get("metric")) for item in degraded]
                parts.append("degraded=" + ",".join(degraded_names))
            _mark_degraded(run_state, "macro_freshness_failed", "; ".join(parts) or "macro freshness check failed")
        elif freshness.get("status") == "stale-but-usable":
            stale_names = [str(item.get("metric")) for item in freshness.get("stale_metrics", [])]
            logger.warning(
                "macro_freshness_stale_but_usable run_id=%s stale=%s",
                run_state.get("run_id") if run_state else "",
                ",".join(stale_names),
            )
        if run_state is not None:
            run_state["macro_metrics"] = result
        return result
    except Exception as e:
        _mark_degraded(run_state, "macro_metrics_error", str(e))
        result = {
            "error": str(e),
            "metrics": [],
            "freshness": {
                "status": "unavailable",
                "reason": str(e),
                "missing_metrics": list(REQUIRED_MACRO_METRICS),
                "stale_metrics": [],
                "degraded_metrics": [],
                "available_metrics": [],
            },
        }
        if run_state is not None:
            run_state["macro_metrics"] = result
        return result


def tool_send_telegram_briefing(message: str) -> Dict:
    try:
        from services.telegram_bot import TelegramBot
        bot = TelegramBot(str(DB_PATH))
        if not bot.available():
            return {"status": "error", "message": "Telegram not configured"}
        ok = bot.send_message(message, parse_mode="HTML")
        return {"status": "ok" if ok else "error", "message": "Sent" if ok else "Failed to send"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _dedupe_signals(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_identity: Dict[str, Dict[str, Any]] = {}
    for signal in signals:
        name = str(signal.get("signal_name", "")).strip()
        if not name:
            continue
        key = name.casefold()
        current = by_identity.get(key)
        if current is None or _signal_ts(signal) >= _signal_ts(current):
            by_identity[key] = signal
    return sorted(
        by_identity.values(),
        key=lambda signal: (_signal_confidence(signal), _signal_ts(signal)),
        reverse=True,
    )


def _signal_confidence(signal: Dict[str, Any]) -> float:
    try:
        return float(signal.get("confidence") or 0)
    except Exception:
        return 0.0


def _signal_ts(signal: Dict[str, Any]) -> float:
    ts = signal.get("ts")
    if isinstance(ts, datetime):
        return ts.timestamp()
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _iso_from_timestamp(value: float) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _signal_freshness(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not signals:
        return {"status": "missing", "latest_signal_time": "", "count": 0}
    latest_ts = max(_signal_ts(signal) for signal in signals)
    age_hours = (datetime.now(timezone.utc).timestamp() - latest_ts) / 3600 if latest_ts else None
    status = "ok" if age_hours is not None and age_hours <= SIGNAL_FRESHNESS_HOURS else "stale"
    return {
        "status": status,
        "latest_signal_time": _iso_from_timestamp(latest_ts),
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "count": len(signals),
    }


def _latest_price_timestamp(prices: List[Dict[str, Any]]) -> str:
    latest_ts = 0.0
    for price in prices:
        parsed = _parse_dt(price.get("ts"))
        if parsed is not None:
            latest_ts = max(latest_ts, parsed.timestamp())
    return _iso_from_timestamp(latest_ts)


def _signal_totals(signals: List[Dict[str, Any]]) -> tuple[float, float]:
    buy_conf = sum(
        float(s.get("confidence") or 0)
        for s in signals
        if str(s.get("direction", "")).upper() == "BUY"
    )
    sell_conf = sum(
        float(s.get("confidence") or 0)
        for s in signals
        if str(s.get("direction", "")).upper() == "SELL"
    )
    return buy_conf, sell_conf


def _bias_from_totals(buy_conf: float, sell_conf: float) -> str:
    total = buy_conf + sell_conf
    if total <= 0:
        return "NEUTRAL"
    if buy_conf / total > 0.6:
        return "BULLISH"
    if sell_conf / total > 0.6:
        return "BEARISH"
    return "NEUTRAL"


def tool_assess_market_bias(run_state: Optional[Dict[str, Any]] = None) -> Dict:
    run_state = _run_state_for_tool(run_state)
    if run_state is not None and "market_bias" in run_state:
        return run_state["market_bias"]
    signals = tool_get_latest_signals(limit=SIGNAL_SNAPSHOT_LIMIT, run_state=run_state)
    unique_signals = _dedupe_signals(signals.get("signals", []) or [])
    prices = tool_get_price_data(run_state=run_state)
    buy_conf, sell_conf = _signal_totals(unique_signals)
    bias = _bias_from_totals(buy_conf, sell_conf)
    result = {
        "bias": bias,
        "buy_confidence_total": buy_conf,
        "sell_confidence_total": sell_conf,
        "signal_count": len(unique_signals),
        "price_count": prices.get("count", 0),
    }
    if run_state is not None:
        run_state["market_bias"] = result
    return result


# ─────────────────────────────────────────────
# AGENTIC TOOLS (web search, news, thesis research)
# ─────────────────────────────────────────────

def tool_web_search(query: str = "", run_state: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """DuckDuckGo search → top results with titles/snippets/URLs."""
    query = str(query or kwargs.get("query") or "").strip()
    if not query:
        return {"results": [], "error": "Empty query"}
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "GeoClaw/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        # Simple extraction of result titles and snippets from HTML
        results = []
        from html import unescape
        import re
        blocks = re.findall(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</span>', resp.text, re.DOTALL)
        for url_raw, title_raw, snippet_raw in blocks[:8]:
            title = re.sub(r'<[^>]+>', '', unescape(title_raw)).strip()
            snippet = re.sub(r'<[^>]+>', '', unescape(snippet_raw)).strip()
            # DuckDuckGo wraps URLs in a redirect
            url_match = re.search(r'uddg=([^&]+)', url_raw)
            url = requests.utils.unquote(url_match.group(1)) if url_match else url_raw
            results.append({"title": title[:200], "snippet": snippet[:300], "url": url[:500]})
        return {"query": query, "results": results, "count": len(results)}
    except Exception as exc:
        return {"query": query, "results": [], "error": str(exc)}


def tool_fetch_breaking_news(run_state: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """Fetch latest articles from all configured sources."""
    articles = []
    try:
        from sources.rss_client import fetch_rss_articles
        articles.extend(fetch_rss_articles()[:5])
    except Exception:
        pass
    try:
        from sources.gdelt_client import fetch_gdelt_articles
        articles.extend(fetch_gdelt_articles()[:5])
    except Exception:
        pass
    try:
        from sources.bluesky_client import fetch_bluesky_articles
        articles.extend(fetch_bluesky_articles()[:3])
    except Exception:
        pass
    try:
        from sources.reddit_client import fetch_reddit_articles
        articles.extend(fetch_reddit_articles(limit_per_sub=2)[:5])
    except Exception:
        pass
    return {
        "articles": [
            {"headline": str(a.get("headline", ""))[:200], "source": str(a.get("source", "")), "url": str(a.get("url", ""))}
            for a in articles
        ],
        "count": len(articles),
    }


def tool_research_thesis(thesis_key: str = "", run_state: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """Deep-dive on a thesis — gather supporting/contradicting evidence."""
    thesis_key = str(thesis_key or kwargs.get("thesis_key") or "").strip()
    if not thesis_key:
        return {"error": "No thesis_key provided"}

    evidence = {"supporting": [], "contradicting": [], "neutral": []}

    # 1. Search web for thesis topic
    search_result = tool_web_search(query=thesis_key, run_state=run_state)
    for r in search_result.get("results", [])[:5]:
        evidence["neutral"].append({"type": "web_search", "title": r.get("title", ""), "snippet": r.get("snippet", "")})

    # 2. Check existing theses for related/contradicting signals
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT thesis_key, confidence, terminal_risk, status
            FROM agent_theses
            WHERE thesis_key LIKE ? AND COALESCE(status, '') != 'superseded'
            ORDER BY confidence DESC LIMIT 5
            """,
            (f"%{thesis_key[:30]}%",),
        ).fetchall()
        conn.close()
        for row in rows:
            r = dict(row)
            evidence["supporting" if float(r.get("confidence") or 0) > 0.6 else "contradicting"].append(
                {"type": "related_thesis", "thesis": str(r.get("thesis_key", ""))[:120], "confidence": r.get("confidence")}
            )
    except Exception:
        pass

    return {
        "thesis_key": thesis_key,
        "evidence_count": sum(len(v) for v in evidence.values()),
        "evidence": evidence,
    }


def tool_get_active_theses(run_state: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """Retrieve current active investment theses with confidence and status."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT thesis_key, confidence, terminal_risk, status, watchlist_suggestion
            FROM agent_theses
            WHERE COALESCE(status, '') NOT IN ('superseded', 'expired')
            ORDER BY confidence DESC
            LIMIT 15
            """,
        ).fetchall()
        conn.close()
        theses = [
            {
                "thesis_key": str(dict(r).get("thesis_key", ""))[:200],
                "confidence": float(dict(r).get("confidence") or 0),
                "terminal_risk": str(dict(r).get("terminal_risk", "")),
                "status": str(dict(r).get("status", "")),
                "watchlist": str(dict(r).get("watchlist_suggestion", "")),
            }
            for r in rows
        ]
        return {"theses": theses, "count": len(theses)}
    except Exception as exc:
        return {"theses": [], "count": 0, "error": str(exc)}


def _update_thesis_tracker(run_state: Dict[str, Any]) -> Dict[str, Any]:
    if "thesis_tracker" in run_state:
        return run_state["thesis_tracker"]
    try:
        result = update_theses_from_run_state(run_state)
    except Exception as exc:
        result = {"status": "error", "message": str(exc), "active_thesis_count": 0, "changed_thesis_count": 0}
        _mark_degraded(run_state, "thesis_tracker_error", str(exc))
    run_state["thesis_tracker"] = result
    if result.get("status") != "ok":
        _mark_degraded(run_state, "thesis_tracker_error", str(result.get("message") or result))
    logger.info(
        "Thesis tracker result: status=%s active=%s changed=%s",
        result.get("status", "unknown"),
        result.get("active_thesis_count", 0),
        result.get("changed_thesis_count", 0),
    )
    return result


def _read_operator_status() -> Dict[str, Any]:
    try:
        if STATUS_FILE.exists():
            raw = STATUS_FILE.read_text(encoding="utf-8").strip()
            if raw:
                return json.loads(raw)
    except Exception as exc:
        logger.warning("Could not read operator status file: %s", exc)
    return {}


def _degradation_codes(run_state: Dict[str, Any]) -> List[str]:
    codes = [str(code) for code in (run_state.get("degradation_codes") or [])]
    if codes:
        return sorted(set(codes))
    derived = []
    for note in run_state.get("degradation_notes", []) or []:
        code = str(note).split(":", 1)[0].strip()
        if code:
            derived.append(code)
    return sorted(set(derived))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_operator_status(
    tool_state: Dict[str, Any],
    run_state: Dict[str, Any],
    telegram_result: Dict[str, Any],
) -> Dict[str, Any]:
    previous = _read_operator_status()
    run_completed_at = datetime.now(timezone.utc).isoformat()
    prices_result = tool_state.get("get_price_data", {}) or {}
    macro_result = tool_state.get("get_macro_metrics", {}) or {}
    signals_result = tool_state.get("get_latest_signals", {}) or {}
    price_timestamp = _latest_price_timestamp(prices_result.get("prices", []) or [])
    current_health = "DEGRADED" if run_state.get("degraded_mode") else "HEALTHY"
    degradation_codes = _degradation_codes(run_state)
    telegram_ok = telegram_result.get("status") == "ok"
    last_telegram_send_time = (
        run_state.get("last_telegram_send_time")
        if telegram_ok
        else previous.get("last_telegram_send_time", "")
    )
    signal_freshness = signals_result.get("freshness") or _signal_freshness(signals_result.get("signals", []) or [])
    macro_freshness = macro_result.get("freshness", {})
    price_refresh = prices_result.get("refresh", {}) or {}
    thesis_result = run_state.get("thesis_tracker") or tool_state.get("update_theses", {}) or {}
    top_theses = list(thesis_result.get("top_theses") or [])[:5]

    previous_history = previous.get("recent_runs", [])
    if not isinstance(previous_history, list):
        previous_history = []
    run_entry = {
        "run_id": run_state.get("run_id", ""),
        "completed_at": run_completed_at,
        "health": current_health,
        "degradation_codes": degradation_codes,
        "price_timestamp": price_timestamp or "unavailable",
        "price_refresh_status": price_refresh.get("status", "unknown"),
        "macro_freshness": macro_freshness.get("status", "unknown"),
        "signal_freshness": signal_freshness.get("status", "unknown"),
        "groq_status": dict(run_state.get("groq_result", {})).get("status", "unknown"),
        "telegram_status": telegram_result.get("status", "unknown"),
    }
    recent_runs = (previous_history + [run_entry])[-RUN_HISTORY_LIMIT:]
    recent_healthy_count = sum(1 for item in recent_runs if item.get("health") == "HEALTHY")
    recent_degraded_count = sum(1 for item in recent_runs if item.get("health") == "DEGRADED")

    previous_streak = _safe_int(previous.get("degraded_streak"))
    degraded_streak = previous_streak + 1 if current_health == "DEGRADED" else 0
    previous_alert = previous.get("degraded_alert") or {}
    last_alert_streak = _safe_int(previous_alert.get("last_alert_streak"))
    last_alert_time = str(previous_alert.get("last_alert_time") or "")
    if current_health == "HEALTHY":
        last_alert_streak = 0
    should_alert = (
        current_health == "DEGRADED"
        and degraded_streak >= DEGRADED_ALERT_THRESHOLD
        and (
            last_alert_streak < DEGRADED_ALERT_THRESHOLD
            or degraded_streak - last_alert_streak >= DEGRADED_ALERT_REPEAT_EVERY
        )
    )

    return {
        "updated_at": run_completed_at,
        "run_id": run_state.get("run_id", ""),
        "run_started_at": run_state.get("started_at", ""),
        "last_successful_run_time": run_completed_at if telegram_ok else previous.get("last_successful_run_time", ""),
        "last_healthy_run_time": (
            run_completed_at
            if current_health == "HEALTHY" and telegram_ok
            else previous.get("last_healthy_run_time", "")
        ),
        "last_telegram_send_time": last_telegram_send_time or "",
        "last_price_refresh_time": run_state.get("last_price_refresh_time") or previous.get("last_price_refresh_time", ""),
        "price_timestamp": price_timestamp or "unavailable",
        "price_refresh_status": price_refresh,
        "macro_freshness_status": macro_freshness,
        "signal_freshness_status": signal_freshness,
        "current_run_health": current_health,
        "degraded_streak": degraded_streak,
        "last_degradation_reasons": list(run_state.get("degradation_notes", [])),
        "last_degradation_codes": degradation_codes,
        "groq_result": dict(run_state.get("groq_result", {})),
        "telegram_result": dict(telegram_result or {}),
        "signal_count": signals_result.get("count", 0),
        "price_count": prices_result.get("count", 0),
        "thesis_tracker_result": {
            "status": thesis_result.get("status", "unknown"),
            "table": thesis_result.get("table", ""),
            "storage_path": thesis_result.get("storage_path", ""),
        },
        "active_thesis_count": thesis_result.get("active_thesis_count", 0),
        "changed_thesis_count": thesis_result.get("changed_thesis_count", 0),
        "top_theses": [
            {
                "thesis_key": thesis.get("thesis_key", ""),
                "title": thesis.get("title", ""),
                "status": thesis.get("status", ""),
                "direction": thesis.get("direction", ""),
                "confidence": thesis.get("confidence", 0),
                "last_change_reason": thesis.get("last_change_reason", ""),
            }
            for thesis in top_theses
        ],
        "recent_runs": recent_runs,
        "recent_health_summary": {
            "limit": RUN_HISTORY_LIMIT,
            "healthy": recent_healthy_count,
            "degraded": recent_degraded_count,
            "last_degradation_codes": degradation_codes,
        },
        "degraded_alert": {
            "threshold": DEGRADED_ALERT_THRESHOLD,
            "repeat_every": DEGRADED_ALERT_REPEAT_EVERY,
            "streak": degraded_streak,
            "should_alert": should_alert,
            "status": "pending" if should_alert else ("clear" if current_health == "HEALTHY" else "watching"),
            "last_alert_streak": last_alert_streak,
            "last_alert_time": last_alert_time,
            "last_alert_result": previous_alert.get("last_alert_result", {}),
        },
    }


def _operator_alert_message(status: Dict[str, Any]) -> str:
    codes = status.get("last_degradation_codes") or ["unknown"]
    groq_status = (status.get("groq_result") or {}).get("status", "unknown")
    return "\n".join(
        [
            "<b>GeoClaw Operator Alert</b>",
            f"Run Status: DEGRADED for {int(status.get('degraded_streak') or 0)} consecutive runs.",
            f"Latest codes: {html.escape(', '.join(str(code) for code in codes[:8]))}",
            f"Groq: {html.escape(str(groq_status))}",
            "Briefings remain grounded in current-run tool outputs; operator review is recommended.",
        ]
    )


def _send_degraded_alert_if_needed(status: Dict[str, Any]) -> Dict[str, Any]:
    alert = status.get("degraded_alert") or {}
    if not alert.get("should_alert"):
        return status
    attempted_at = datetime.now(timezone.utc).isoformat()
    try:
        result = tool_send_telegram_briefing(_operator_alert_message(status))
    except Exception as exc:
        result = {"status": "error", "message": str(exc)}
    alert["last_alert_streak"] = int(status.get("degraded_streak") or 0)
    alert["last_alert_time"] = attempted_at
    alert["last_alert_result"] = result
    alert["should_alert"] = False
    alert["status"] = "sent" if result.get("status") == "ok" else "failed"
    status["degraded_alert"] = alert
    if result.get("status") == "ok":
        logger.warning(
            "Degraded run alert sent streak=%s codes=%s",
            status.get("degraded_streak"),
            ",".join(status.get("last_degradation_codes") or []),
        )
    else:
        logger.warning("Degraded run alert failed streak=%s result=%s", status.get("degraded_streak"), result)
    return status


def _write_operator_report(status: Dict[str, Any]) -> None:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    def status_class(value: Any) -> str:
        normalized = str(value or "").lower()
        if normalized in {"healthy", "ok", "fresh", "sent", "clear"}:
            return "good"
        if normalized in {"degraded", "error", "failed", "unavailable", "missing", "groq_error"}:
            return "bad"
        if normalized in {"retrying", "stale", "stale-but-usable", "watching", "pending"}:
            return "warn"
        return "neutral"

    def badge(value: Any) -> str:
        text = esc(value or "unknown")
        return f'<span class="badge {status_class(value)}">{text}</span>'

    def field(label: str, value: Any) -> str:
        return f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>"

    def badge_list(items: Any) -> str:
        values = list(items or [])
        if not values:
            return '<span class="muted">None</span>'
        return " ".join(f'<span class="pill">{esc(item)}</span>' for item in values)

    def metric_list(items: Any) -> str:
        values = list(items or [])
        if not values:
            return '<span class="muted">None</span>'
        rendered = []
        for item in values:
            if isinstance(item, dict):
                metric = item.get("metric", "unknown")
                age = item.get("age_days")
                label = f"{metric} age {age}d" if age is not None else metric
                rendered.append(f'<span class="pill warn">{esc(label)}</span>')
            else:
                rendered.append(f'<span class="pill warn">{esc(item)}</span>')
        return " ".join(rendered)

    def card(title: str, body: str) -> str:
        return f'<section class="card"><h2>{esc(title)}</h2>{body}</section>'

    current_health = status.get("current_run_health", "UNKNOWN")
    summary = status.get("recent_health_summary", {}) or {}
    price_refresh = status.get("price_refresh_status", {}) or {}
    macro_freshness = status.get("macro_freshness_status", {}) or {}
    signal_freshness = status.get("signal_freshness_status", {}) or {}
    groq_result = status.get("groq_result", {}) or {}
    degradation_codes = status.get("last_degradation_codes") or []
    recent_runs = list(status.get("recent_runs", []) or [])[-RUN_HISTORY_LIMIT:]

    summary_cards = "\n".join(
        [
            card(
                "Run Health",
                f'<p class="hero-badge">{badge(current_health)}</p>'
                f'<dl>{field("Run ID", status.get("run_id", ""))}{field("Updated", status.get("updated_at", ""))}</dl>',
            ),
            card(
                "Recent Runs",
                "<div class=\"stat-row\">"
                f'<div><strong>{int(summary.get("healthy") or 0)}</strong><span>Healthy</span></div>'
                f'<div><strong>{int(summary.get("degraded") or 0)}</strong><span>Degraded</span></div>'
                f'<div><strong>{int(status.get("degraded_streak") or 0)}</strong><span>Streak</span></div>'
                "</div>",
            ),
            card(
                "Last Successful Run",
                f'<dl>{field("Run", status.get("last_successful_run_time", ""))}'
                f'{field("Telegram", status.get("last_telegram_send_time", ""))}'
                f'{field("Price", status.get("price_timestamp", ""))}</dl>',
            ),
        ]
    )

    detail_cards = "\n".join(
        [
            card(
                "Price Refresh",
                "<dl>"
                f"<dt>Status</dt><dd>{badge(price_refresh.get('status', 'unknown'))}</dd>"
                f'{field("Inserted", price_refresh.get("inserted", ""))}'
                f'{field("Message", price_refresh.get("message", ""))}'
                f'{field("Last Refresh", status.get("last_price_refresh_time", ""))}'
                "</dl>",
            ),
            card(
                "Macro Freshness",
                "<dl>"
                f"<dt>Status</dt><dd>{badge(macro_freshness.get('status', 'unknown'))}</dd>"
                f'<dt>Available</dt><dd>{badge_list(macro_freshness.get("available_metrics"))}</dd>'
                f'<dt>Missing</dt><dd>{badge_list(macro_freshness.get("missing_metrics"))}</dd>'
                f'<dt>Stale</dt><dd>{metric_list(macro_freshness.get("stale_metrics"))}</dd>'
                f'<dt>Degraded</dt><dd>{metric_list(macro_freshness.get("degraded_metrics"))}</dd>'
                "</dl>",
            ),
            card(
                "Signal Freshness",
                "<dl>"
                f"<dt>Status</dt><dd>{badge(signal_freshness.get('status', 'unknown'))}</dd>"
                f'{field("Latest Signal", signal_freshness.get("latest_signal_time", ""))}'
                f'{field("Age Hours", signal_freshness.get("age_hours", ""))}'
                f'{field("Count", signal_freshness.get("count", ""))}'
                "</dl>",
            ),
            card(
                "Groq",
                "<dl>"
                f"<dt>Status</dt><dd>{badge(groq_result.get('status', 'unknown'))}</dd>"
                f'{field("Retry Count", groq_result.get("retry_count", ""))}'
                f'{field("Last Status Code", groq_result.get("last_status_code", ""))}'
                f'{field("Message", groq_result.get("message", ""))}'
                "</dl>",
            ),
            card(
                "Last Degradation Codes",
                f'<div class="code-list">{badge_list(degradation_codes)}</div>',
            ),
        ]
    )

    recent_rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(item.get("completed_at", "")),
            badge(item.get("health", "unknown")),
            badge(item.get("macro_freshness", "unknown")),
            badge(item.get("signal_freshness", "unknown")),
            badge(item.get("groq_status", "unknown")),
            badge(item.get("telegram_status", "unknown")),
            badge_list(item.get("degradation_codes", [])),
        )
        for item in reversed(recent_runs)
    )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <title>GeoClaw Agent Brain Status</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #15201d; background: #f6f8f7; }}
    main {{ max-width: 1220px; margin: 0 auto; padding: 28px 20px 40px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }}
    h1 {{ font-size: 28px; margin: 0 0 6px; letter-spacing: 0; }}
    h2 {{ font-size: 16px; margin: 0 0 12px; letter-spacing: 0; }}
    p {{ margin: 0; }}
    table {{ border-collapse: collapse; width: 100%; background: #ffffff; border: 1px solid #d3ddd9; }}
    th, td {{ border-bottom: 1px solid #dbe4e0; padding: 10px; text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ background: #edf3f0; color: #41524c; font-weight: 650; }}
    tr:last-child td {{ border-bottom: 0; }}
    dl {{ display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 8px 12px; margin: 0; }}
    dt {{ color: #60706a; font-size: 13px; }}
    dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
    .subtle {{ color: #66756f; font-size: 14px; }}
    .refresh {{ text-align: right; color: #66756f; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin: 16px 0; }}
    .card {{ background: #ffffff; border: 1px solid #d3ddd9; border-radius: 8px; padding: 16px; box-shadow: 0 1px 2px rgba(21, 32, 29, 0.06); }}
    .badge {{ display: inline-flex; align-items: center; min-height: 24px; border-radius: 8px; padding: 3px 9px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0; border: 1px solid transparent; }}
    .badge.good {{ color: #0f5132; background: #dff3e7; border-color: #b7dfc8; }}
    .badge.warn {{ color: #664d03; background: #fff2c8; border-color: #e8d485; }}
    .badge.bad {{ color: #842029; background: #f8d7da; border-color: #e5a5ab; }}
    .badge.neutral {{ color: #34433e; background: #e8eeeb; border-color: #cbd7d2; }}
    .pill {{ display: inline-flex; margin: 0 4px 4px 0; border-radius: 8px; padding: 3px 8px; background: #edf3f0; border: 1px solid #d3ddd9; font-size: 12px; }}
    .pill.warn {{ background: #fff2c8; border-color: #e8d485; color: #664d03; }}
    .muted {{ color: #7c8a84; }}
    .hero-badge .badge {{ font-size: 18px; min-height: 34px; padding: 5px 12px; }}
    .stat-row {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
    .stat-row div {{ border: 1px solid #dbe4e0; border-radius: 8px; padding: 10px; background: #f8fbfa; }}
    .stat-row strong {{ display: block; font-size: 24px; line-height: 1.1; }}
    .stat-row span {{ color: #66756f; font-size: 13px; }}
    .table-card {{ margin-top: 16px; overflow-x: auto; }}
    .source {{ margin-top: 8px; color: #66756f; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>GeoClaw Agent Brain Status</h1>
        <p class="subtle">Read-only local operator dashboard.</p>
        <p class="source">Data source: logs/agent_brain.status.json</p>
      </div>
      <div class="refresh">Auto-refresh every 10 seconds<br>Next reload in <span id="refresh-countdown">10</span>s</div>
    </header>
    <section class="grid">
{summary_cards}
    </section>
    <section class="grid">
{detail_cards}
    </section>
    <section class="card table-card">
      <h2>Recent Run History</h2>
      <table>
        <tr><th>Completed At</th><th>Health</th><th>Macro</th><th>Signals</th><th>Groq</th><th>Telegram</th><th>Codes</th></tr>
{recent_rows}
      </table>
    </section>
  </main>
  <script>
    let remaining = 10;
    const countdown = document.getElementById("refresh-countdown");
    setInterval(() => {{
      remaining = Math.max(0, remaining - 1);
      if (countdown) countdown.textContent = String(remaining);
    }}, 1000);
  </script>
</body>
</html>
"""
    STATUS_HTML_FILE.write_text(html_doc, encoding="utf-8")


def _write_operator_status(
    tool_state: Dict[str, Any],
    run_state: Dict[str, Any],
    telegram_result: Dict[str, Any],
) -> None:
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        status = _build_operator_status(tool_state, run_state, telegram_result)
        status = _send_degraded_alert_if_needed(status)
        STATUS_FILE.write_text(json.dumps(status, indent=2, default=_json_serial) + "\n", encoding="utf-8")
        _write_operator_report(status)
        logger.info("Operator status updated: %s", STATUS_FILE)
    except Exception as exc:
        logger.warning("Could not write operator status file: %s", exc)


def _send_telegram_and_record(
    briefing: str,
    run_state: Dict[str, Any],
    log_label: str,
) -> Dict[str, Any]:
    send_result = tool_send_telegram_briefing(briefing)
    if send_result.get("status") == "ok":
        run_state["last_telegram_send_time"] = datetime.now(timezone.utc).isoformat()
    else:
        _mark_degraded(run_state, "telegram_send_failed", str(send_result.get("message") or send_result))
    logger.info("%s Telegram send result: %s", log_label, json.dumps(send_result))
    return send_result


TOOL_MAP = {
    "get_latest_signals": lambda args: tool_get_latest_signals(run_state=_CURRENT_RUN_STATE, **args),
    "run_signal_engine": lambda args: tool_run_signal_engine(run_state=_CURRENT_RUN_STATE),
    "get_price_data": lambda args: tool_get_price_data(run_state=_CURRENT_RUN_STATE),
    "get_macro_metrics": lambda args: tool_get_macro_metrics(run_state=_CURRENT_RUN_STATE),
    "assess_market_bias": lambda args: tool_assess_market_bias(run_state=_CURRENT_RUN_STATE),
    "web_search": lambda args: tool_web_search(run_state=_CURRENT_RUN_STATE, **args),
    "fetch_breaking_news": lambda args: tool_fetch_breaking_news(run_state=_CURRENT_RUN_STATE),
    "research_thesis": lambda args: tool_research_thesis(run_state=_CURRENT_RUN_STATE, **args),
    "get_active_theses": lambda args: tool_get_active_theses(run_state=_CURRENT_RUN_STATE),
}


# ─────────────────────────────────────────────
# GROQ CALLER
# ─────────────────────────────────────────────

def call_groq(messages: List[Dict], tools: Optional[List] = None) -> Dict:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in .env.geoclaw")
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.3,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    last_error = None
    retry_count = 0
    for attempt in range(3):
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code == 429:
            retry_count += 1
            if _CURRENT_RUN_STATE is not None:
                _CURRENT_RUN_STATE["groq_result"] = {
                    "status": "retrying" if attempt < 2 else "exhausted",
                    "retry_count": retry_count,
                    "last_status_code": 429,
                }
            last_error = requests.HTTPError(
                f"429 Client Error: Too Many Requests for url: {GROQ_URL}"
            )
            if attempt < 2:
                wait_seconds = 15 * (attempt + 1)
                logger.warning(
                    f"Groq rate limited (attempt {attempt + 1}/3). Sleeping {wait_seconds}s before retry."
                )
                time.sleep(wait_seconds)
                continue
        resp.raise_for_status()
        return resp.json()

    if last_error:
        raise last_error
    raise RuntimeError("Groq request failed without a JSON response")


# ─────────────────────────────────────────────
# SELF-CALIBRATION MEMORY
# ─────────────────────────────────────────────

def _build_memory_suffix(run_id: str) -> str:
    """
    Build a self-calibration block from the prediction tracker and save it as a
    snapshot so restarts don't reset learning. Returns empty string on any error.
    """
    try:
        from services.prediction_tracker import PredictionTracker
        tracker = PredictionTracker(str(DB_PATH))
        report = tracker.get_accuracy_report()

        win_rate = float(report.get("accuracy_pct") or 0.0)
        total_closed = int(report.get("verified", 0)) + int(report.get("refuted", 0))
        recent = report.get("recent") or []

        # Only inject calibration after the agent has real data to learn from
        if total_closed < 3:
            return ""

        # Directional tone based on win rate
        if win_rate < 45:
            tone = "Your recent accuracy is below 45%. Be more cautious — prefer NEUTRAL or HOLD signals when evidence is weak."
        elif win_rate >= 65:
            tone = "Your recent accuracy is above 65%. Strong track record — continue high-confidence directional calls when evidence is clear."
        else:
            tone = "Your recent accuracy is in the 45-65% range. Maintain balanced confidence — only go directional on strong evidence."

        # Collect recent wrong calls (refuted predictions)
        wrong_calls = [
            {
                "symbol": str(r.get("symbol") or ""),
                "direction": str(r.get("predicted_direction") or ""),
                "actual_change_pct": round(float(r.get("actual_change_pct") or 0.0), 2),
            }
            for r in recent
            if str(r.get("outcome") or "") == "refuted"
        ][:3]

        wrong_lines = "\n".join(
            f"  - {w['symbol']} predicted {w['direction']} but moved {w['actual_change_pct']:+.1f}%"
            for w in wrong_calls
        ) if wrong_calls else "  (none recently)"

        suffix = (
            f"\n\n--- SELF-CALIBRATION MEMORY (do not ignore) ---\n"
            f"Prediction accuracy: {win_rate:.1f}% ({total_closed} closed calls)\n"
            f"{tone}\n"
            f"Recent wrong calls:\n{wrong_lines}\n"
            f"--- END MEMORY ---"
        )

        # Persist snapshot so restarts retain learning and the backtester can audit
        try:
            save_memory_snapshot(
                run_id=run_id,
                win_rate_pct=win_rate,
                total_closed=total_closed,
                recent_errors=wrong_calls,
                prompt_suffix=suffix,
            )
        except Exception:
            pass  # snapshot persistence is best-effort — never block a run

        return suffix
    except Exception:
        return ""


# ─────────────────────────────────────────────
# AGENTIC LOOP
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are GeoClaw Agent Brain — an autonomous macroeconomic intelligence agent.

Your job:
1. Assess current market conditions using available tools
2. Run the signal engine to get fresh signals
3. Analyse signals, prices, and macro data
4. Determine market bias (BULLISH/BEARISH/NEUTRAL)
5. Finish after the tool outputs are gathered
6. Let the runner send the grounded Telegram briefing

Rules:
- Always run the signal engine first, then fetch signals
- Be concise and direct — traders need fast, clear information
- Include market bias, top 3-5 directional signals, and one key macro insight
- Format with Telegram HTML only: <b>bold</b>, <i>italic</i>, newlines with \n not <br>
- Do not make up data — only use what tools return
- Do not send Telegram directly; the runner sends a grounded briefing from current-run tool outputs
- If data is missing or errors occur, say so honestly in the briefing

Today is {date}. Execute the full agentic loop now."""


def _prepare_grounded_snapshot(run_state: Dict[str, Any]) -> Dict[str, Any]:
    tool_state: Dict[str, Any] = {}
    tool_state["get_price_data"] = tool_get_price_data(run_state=run_state)
    tool_state["get_macro_metrics"] = tool_get_macro_metrics(run_state=run_state)
    tool_state["run_signal_engine"] = tool_run_signal_engine(run_state=run_state)
    tool_state["get_latest_signals"] = tool_get_latest_signals(
        limit=SIGNAL_SNAPSHOT_LIMIT,
        run_state=run_state,
    )
    tool_state["assess_market_bias"] = tool_assess_market_bias(run_state=run_state)
    tool_state["update_theses"] = _update_thesis_tracker(run_state)
    return _attach_run_status(tool_state, run_state)


def _complete_grounded_tool_state(
    tool_state: Dict[str, Any],
    run_state: Dict[str, Any],
) -> Dict[str, Any]:
    if "get_price_data" not in tool_state:
        tool_state["get_price_data"] = tool_get_price_data(run_state=run_state)
    if "get_macro_metrics" not in tool_state:
        tool_state["get_macro_metrics"] = tool_get_macro_metrics(run_state=run_state)
    if "run_signal_engine" not in tool_state:
        tool_state["run_signal_engine"] = tool_run_signal_engine(run_state=run_state)
    if "get_latest_signals" not in tool_state:
        tool_state["get_latest_signals"] = tool_get_latest_signals(
            limit=SIGNAL_SNAPSHOT_LIMIT,
            run_state=run_state,
        )
    if "assess_market_bias" not in tool_state:
        tool_state["assess_market_bias"] = tool_assess_market_bias(run_state=run_state)
    if "update_theses" not in tool_state:
        tool_state["update_theses"] = _update_thesis_tracker(run_state)
    return _attach_run_status(tool_state, run_state)


def run_agent_loop() -> None:
    global _CURRENT_RUN_STATE
    run_state = _new_run_state()
    _CURRENT_RUN_STATE = run_state
    logger.info("GeoClaw Agent Brain starting agentic loop run_id=%s", run_state["run_id"])
    date_str = datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")
    memory_suffix = _build_memory_suffix(run_state["run_id"])
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(date=date_str) + memory_suffix},
        {"role": "user", "content": "Run the full GeoClaw morning intelligence briefing loop now."},
    ]

    max_iterations = 10
    iteration = 0
    tool_state: Dict[str, Any] = _prepare_grounded_snapshot(run_state)
    sent_briefing = False
    last_send_result: Dict[str, Any] = {}

    try:
        while iteration < max_iterations:
            iteration += 1
            logger.info(f"Agent iteration {iteration}")

            try:
                response = call_groq(messages, tools=TOOLS)
                run_state["groq_result"] = {**dict(run_state.get("groq_result", {})), "status": "ok"}
            except Exception as e:
                error_text = str(e)
                code = "groq_retry_exhausted" if "429" in error_text else "groq_error"
                retry_count = dict(run_state.get("groq_result", {})).get("retry_count", 0)
                run_state["groq_result"] = {"status": code, "retry_count": retry_count, "message": error_text}
                _mark_degraded(run_state, code, error_text)
                logger.error(f"Groq API error: {e}")
                if tool_state and not sent_briefing:
                    tool_state = _complete_grounded_tool_state(tool_state, run_state)
                    run_state["briefing_note"] = "Groq was rate-limited or unavailable. This briefing uses grounded tool outputs only."
                    briefing = build_briefing(run_state)
                    send_result = _send_telegram_and_record(briefing, run_state, "Fallback")
                    last_send_result = send_result
                    sent_briefing = bool(send_result.get("status") == "ok")
                break

            choice = response["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "")

            messages.append(message)

            if finish_reason == "stop":
                content = (message.get("content") or "").strip()
                if content:
                    logger.info("Agent completed. Using grounded current-run tool summary for Telegram.")
                if tool_state and not sent_briefing:
                    tool_state = _complete_grounded_tool_state(tool_state, run_state)
                    briefing = build_briefing(run_state)
                    send_result = _send_telegram_and_record(briefing, run_state, "Final")
                    last_send_result = send_result
                    sent_briefing = bool(send_result.get("status") == "ok")
                break

            if finish_reason == "tool_calls" or message.get("tool_calls"):
                tool_calls = message.get("tool_calls", [])
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"].get("arguments", "{}"))
                    except json.JSONDecodeError:
                        fn_args = {}

                    logger.info(f"Tool call: {fn_name}({fn_args})")

                    if fn_name in TOOL_MAP:
                        result = TOOL_MAP[fn_name](fn_args)
                        tool_state[fn_name] = result
                    else:
                        result = {"error": f"Unknown tool: {fn_name}"}

                    def _json_serial(obj):
                        if isinstance(obj, datetime):
                            return obj.isoformat()
                        raise TypeError(f"Type {type(obj)} not serializable")

                    result_str = json.dumps(result, default=_json_serial)
                    logger.info(f"Tool result: {result_str[:200]}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })
                continue

            logger.warning(f"Unexpected finish_reason: {finish_reason}")
            if tool_state and not sent_briefing:
                _mark_degraded(run_state, "unexpected_finish_reason", finish_reason or "unknown")
                tool_state = _complete_grounded_tool_state(tool_state, run_state)
                run_state["briefing_note"] = f"Unexpected agent finish state: {finish_reason or 'unknown'}"
                briefing = build_briefing(run_state)
                send_result = _send_telegram_and_record(briefing, run_state, "Unexpected-finish")
                last_send_result = send_result
                sent_briefing = bool(send_result.get("status") == "ok")
            break

        if tool_state and not sent_briefing:
            _mark_degraded(run_state, "agent_loop_incomplete", "loop ended before an explicit final response")
            tool_state = _complete_grounded_tool_state(tool_state, run_state)
            run_state["briefing_note"] = "Agent loop ended before an explicit final response. Sending grounded tool summary."
            briefing = build_briefing(run_state)
            send_result = _send_telegram_and_record(briefing, run_state, "End-of-loop")
            last_send_result = send_result
            sent_briefing = bool(send_result.get("status") == "ok")

        if run_state.get("degraded_mode"):
            logger.warning(
                "Agent loop completed degraded run_id=%s iterations=%s notes=%s",
                run_state.get("run_id"),
                iteration,
                "; ".join(run_state.get("degradation_notes", [])),
            )
        else:
            logger.info("Agent loop completed run_id=%s iterations=%s", run_state.get("run_id"), iteration)
    finally:
        _write_operator_status(_attach_run_status(tool_state, run_state), run_state, last_send_result)
        _CURRENT_RUN_STATE = None


# ─────────────────────────────────────────────
# SCHEDULED RUNNER
# ─────────────────────────────────────────────

def run_once() -> None:
    """Run the agent loop once immediately."""
    run_agent_loop()


def run_on_schedule(interval_minutes: int = 30) -> None:
    """Run the agent loop on a schedule."""
    logger.info(f"Agent Brain scheduled every {interval_minutes} minutes")
    while True:
        try:
            run_agent_loop()
        except Exception as e:
            logger.exception(f"Agent loop error: {e}")
        logger.info(f"Sleeping {interval_minutes} minutes until next run")
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GeoClaw Agent Brain")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=30, help="Schedule interval in minutes")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_on_schedule(interval_minutes=args.interval)
