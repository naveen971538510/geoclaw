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

_load_local_env(ENV_FILE)

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


def _fmt_number(value: Any, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


def _pick_macro_insight(metrics: List[Dict[str, Any]]) -> str:
    if not metrics:
        return "No macro metrics available."

    priority = [
        "CPI_YOY_PCT",
        "FEDFUNDS",
        "UNRATE",
        "GDP_QOQ",
        "GDP_YOY",
        "NFP",
        "PAYROLLS",
    ]
    by_name = {str(m.get("metric_name", "")).upper(): m for m in metrics}
    chosen = None
    for key in priority:
        if key in by_name:
            chosen = by_name[key]
            break
    if chosen is None:
        chosen = metrics[0]

    name = str(chosen.get("metric_name", "Unknown metric"))
    value = chosen.get("value")
    previous = chosen.get("previous_value")
    pct_change = chosen.get("pct_change")

    parts = [f"{html.escape(name)} = {_fmt_number(value)}"]
    if previous is not None:
        parts.append(f"prev {_fmt_number(previous)}")
    if pct_change is not None:
        parts.append(f"change {_fmt_number(pct_change, 2)}%")
    observed_at = chosen.get("observed_at")
    if observed_at:
        parts.append(f"as of {html.escape(str(observed_at))}")
    return "; ".join(parts)


def _conservative_read(bias: str, buy_total: float, sell_total: float, signal_count: int) -> str:
    if signal_count == 0:
        return "No fresh directional signal set is available from this run."
    if bias == "BULLISH" and buy_total > sell_total:
        return "Bias leans bullish on the deduplicated signal totals; treat it as context, not a standalone trade call."
    if bias == "BEARISH" and sell_total > buy_total:
        return "Bias leans bearish on the deduplicated signal totals; treat it as context, not a standalone trade call."
    return "Signal totals are mixed or low-conviction; treat the evidence as partial context."


def _macro_freshness_line(freshness: Dict[str, Any]) -> str:
    if freshness.get("status") in {"ok", "fresh"}:
        return ""
    details = []
    missing = freshness.get("missing_metrics") or []
    stale = freshness.get("stale_metrics") or []
    reason = freshness.get("reason")
    if reason:
        details.append(str(reason))
    if missing:
        details.append("missing " + ", ".join(str(item) for item in missing[:5]))
    if stale:
        stale_bits = []
        for item in stale[:5]:
            metric = str(item.get("metric", "unknown"))
            age = item.get("age_days")
            if age is None:
                stale_bits.append(metric)
            else:
                stale_bits.append(f"{metric} age {age}d")
        details.append("stale " + ", ".join(stale_bits))
    return "; ".join(details) or "freshness check failed"


def _macro_freshness_label(freshness: Dict[str, Any]) -> str:
    status = str(freshness.get("status") or "unknown").lower()
    if status in {"ok", "fresh"}:
        return "FRESH"
    if status == "stale-but-usable":
        return "STALE-BUT-USABLE"
    if status == "unavailable":
        return "UNAVAILABLE"
    if status in {"stale", "degraded"}:
        return "DEGRADED"
    return status.upper()


def _signal_freshness_line(freshness: Dict[str, Any]) -> str:
    status = str(freshness.get("status") or "unknown").upper()
    latest = freshness.get("latest_signal_time") or "unavailable"
    age = freshness.get("age_hours")
    if age is None:
        return f"{status} - latest {latest}"
    return f"{status} - latest {latest}, age {age}h"


def build_grounded_briefing(tool_state: Dict[str, Any], error_note: str = "") -> str:
    signals_result = tool_state.get("get_latest_signals", {}) or {}
    bias_result = tool_state.get("assess_market_bias", {}) or {}
    prices_result = tool_state.get("get_price_data", {}) or {}
    macro_result = tool_state.get("get_macro_metrics", {}) or {}
    run_info = tool_state.get("_run", {}) or {}

    signals = _dedupe_signals(signals_result.get("signals", []) or [])
    metrics = macro_result.get("metrics", []) or []
    prices = prices_result.get("prices", []) or []
    signal_freshness = signals_result.get("freshness") or _signal_freshness(signals)
    macro_freshness = macro_result.get("freshness", {}) or {}
    macro_freshness_label = _macro_freshness_label(macro_freshness)
    macro_freshness_note = _macro_freshness_line(macro_freshness)
    price_timestamp = _latest_price_timestamp(prices) or "unavailable"
    run_timestamp = run_info.get("started_at") or datetime.now(timezone.utc).isoformat()
    run_health = "DEGRADED" if run_info.get("degraded_mode") else "HEALTHY"

    buy_total, sell_total = _signal_totals(signals)
    bias = str(bias_result.get("bias") or _bias_from_totals(buy_total, sell_total)).upper()

    preferred = ["SPX", "XAUUSD", "BTCUSD", "GLD", "USO", "GBPUSD", "SPY", "QQQ"]
    price_map = {}
    for p in prices:
        ticker = str(p.get("ticker", "")).upper()
        if ticker and ticker not in price_map:
            price_map[ticker] = p

    ordered_prices = [price_map[t] for t in preferred if t in price_map]
    if not ordered_prices:
        ordered_prices = prices[:3]

    lines = []
    lines.append("<b>GeoClaw Briefing</b>")
    lines.append(f"<b>Run Timestamp:</b> {html.escape(str(run_timestamp))}")
    lines.append(f"<b>Price Timestamp:</b> {html.escape(str(price_timestamp))}")
    if macro_freshness_note:
        lines.append(f"<b>Macro Freshness:</b> {macro_freshness_label} - {html.escape(macro_freshness_note)}")
    else:
        lines.append(f"<b>Macro Freshness:</b> {macro_freshness_label}")
    lines.append(f"<b>Signal Freshness:</b> {html.escape(_signal_freshness_line(signal_freshness))}")
    lines.append(f"<b>Run Status:</b> {run_health}")
    lines.append("")
    lines.append(f"<b>Market Bias:</b> <i>{html.escape(bias)}</i>")
    lines.append(
        f"<b>Signal Totals:</b> BUY {_fmt_number(buy_total)} | SELL {_fmt_number(sell_total)}"
    )

    if ordered_prices:
        price_bits = []
        for p in ordered_prices[:4]:
            ticker = html.escape(str(p.get("ticker", "")))
            price_bits.append(f"{ticker} {_fmt_number(p.get('price'), 2)}")
        lines.append(f"<b>Prices:</b> {' | '.join(price_bits)}")

    lines.append("")
    lines.append("<b>Directional Signals:</b>")
    if signals:
        for idx, s in enumerate(signals[:5], 1):
            direction = html.escape(str(s.get("direction", "HOLD")).upper())
            name = html.escape(str(s.get("signal_name", "Unknown signal")))
            confidence = _fmt_number(s.get("confidence", 0), 0)
            lines.append(f"{idx}. <b>{direction}</b> {name} ({confidence})")
    else:
        lines.append("No fresh BUY/SELL signals in the last 48 hours.")

    lines.append("")
    lines.append(f"<b>Macro Insight:</b> {_pick_macro_insight(metrics)}")
    lines.append("")
    lines.append(
        f"<b>Conservative Read:</b> {_conservative_read(bias, buy_total, sell_total, len(signals))}"
    )

    if run_info.get("degraded_mode"):
        lines.append("")
        lines.append("<b>Degraded Notes:</b>")
        for note in (run_info.get("degradation_notes") or [])[:5]:
            lines.append(f"- {html.escape(str(note))}")

    if error_note:
        lines.append("")
        lines.append(f"<b>Agent Note:</b> {html.escape(error_note)}")

    return "\n".join(lines)


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


def _compact_status_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=_json_serial, sort_keys=True)
    return "" if value is None else str(value)


def _write_operator_report(status: Dict[str, Any]) -> None:
    rows = [
        ("Last Successful Run", status.get("last_successful_run_time", "")),
        ("Last Telegram Send", status.get("last_telegram_send_time", "")),
        ("Current Health", status.get("current_run_health", "")),
        ("Degraded Streak", status.get("degraded_streak", 0)),
        ("Price Timestamp", status.get("price_timestamp", "")),
        ("Price Refresh", status.get("price_refresh_status", {})),
        ("Macro Freshness", status.get("macro_freshness_status", {})),
        ("Signal Freshness", status.get("signal_freshness_status", {})),
        ("Groq", status.get("groq_result", {})),
        ("Last Degradation Codes", ", ".join(status.get("last_degradation_codes") or [])),
    ]
    row_html = "\n".join(
        "<tr><th>{}</th><td>{}</td></tr>".format(
            html.escape(label),
            html.escape(_compact_status_value(value)),
        )
        for label, value in rows
    )
    recent_rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(item.get("completed_at", ""))),
            html.escape(str(item.get("health", ""))),
            html.escape(",".join(str(code) for code in item.get("degradation_codes", []) or [])),
            html.escape(str(item.get("groq_status", ""))),
        )
        for item in status.get("recent_runs", [])[-RUN_HISTORY_LIMIT:]
    )
    summary = status.get("recent_health_summary", {}) or {}
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GeoClaw Agent Brain Status</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #172026; background: #f8faf9; }}
    h1, h2 {{ font-size: 20px; margin: 0 0 12px; }}
    h2 {{ margin-top: 24px; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 1100px; background: #ffffff; }}
    th, td {{ border: 1px solid #c9d3d0; padding: 8px; text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ width: 220px; background: #eef3f1; }}
    .status {{ font-weight: 700; }}
  </style>
</head>
<body>
  <h1>GeoClaw Agent Brain Status</h1>
  <p class="status">Current health: {html.escape(str(status.get("current_run_health", "")))}</p>
  <p>Recent runs: {int(summary.get("healthy") or 0)} healthy, {int(summary.get("degraded") or 0)} degraded.</p>
  <table>
{row_html}
  </table>
  <h2>Recent Runs</h2>
  <table>
    <tr><th>Completed At</th><th>Health</th><th>Codes</th><th>Groq</th></tr>
{recent_rows}
  </table>
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
    return _attach_run_status(tool_state, run_state)


def run_agent_loop() -> None:
    global _CURRENT_RUN_STATE
    run_state = _new_run_state()
    _CURRENT_RUN_STATE = run_state
    logger.info("GeoClaw Agent Brain starting agentic loop run_id=%s", run_state["run_id"])
    date_str = datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(date=date_str)},
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
                    briefing = build_grounded_briefing(
                        tool_state,
                        error_note="Groq was rate-limited or unavailable. This briefing uses grounded tool outputs only.",
                    )
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
                    briefing = build_grounded_briefing(tool_state)
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
                briefing = build_grounded_briefing(
                    tool_state,
                    error_note=f"Unexpected agent finish state: {finish_reason or 'unknown'}",
                )
                send_result = _send_telegram_and_record(briefing, run_state, "Unexpected-finish")
                last_send_result = send_result
                sent_briefing = bool(send_result.get("status") == "ok")
            break

        if tool_state and not sent_briefing:
            _mark_degraded(run_state, "agent_loop_incomplete", "loop ended before an explicit final response")
            tool_state = _complete_grounded_tool_state(tool_state, run_state)
            briefing = build_grounded_briefing(
                tool_state,
                error_note="Agent loop ended before an explicit final response. Sending grounded tool summary.",
            )
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
