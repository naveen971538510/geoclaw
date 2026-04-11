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
from datetime import datetime, timezone
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

def tool_get_latest_signals(limit: int = 10) -> Dict:
    try:
        from intelligence.db import ensure_intelligence_schema, query_all, get_database_url
        from datetime import timedelta
        if not get_database_url():
            return {"error": "DATABASE_URL not set", "signals": []}
        ensure_intelligence_schema()
        since = datetime.now(timezone.utc) - timedelta(hours=48)
        requested_limit = max(1, int(limit))
        fetch_limit = requested_limit * 3
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
        signals = _dedupe_signals([dict(r) for r in rows])[:requested_limit]
        return {"signals": signals, "count": len(signals)}
    except Exception as e:
        return {"error": str(e), "signals": []}


def tool_run_signal_engine() -> Dict:
    try:
        from intelligence.signal_engine import run_signal_engine
        run_signal_engine()
        return {"status": "ok", "message": "Signal engine completed successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def tool_get_price_data() -> Dict:
    try:
        from intelligence.db import query_all
        rows = query_all(
            "SELECT ticker, price, ts FROM price_data ORDER BY ts DESC LIMIT 50;"
        )
        seen = {}
        for r in rows:
            t = str(r["ticker"]).upper()
            if t not in seen:
                seen[t] = {"ticker": t, "price": float(r["price"]), "ts": str(r["ts"])}
        return {"prices": list(seen.values()), "count": len(seen)}
    except Exception as e:
        return {"error": str(e), "prices": []}


def tool_get_macro_metrics() -> Dict:
    try:
        from intelligence.db import query_all
        rows = query_all(
            """
            SELECT DISTINCT ON (metric_name)
                metric_name, observed_at, value, previous_value, pct_change
            FROM macro_signals
            ORDER BY metric_name, observed_at DESC
            LIMIT 30;
            """
        )
        return {"metrics": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": str(e), "metrics": []}


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


def tool_assess_market_bias() -> Dict:
    signals = tool_get_latest_signals(limit=20)
    unique_signals = _dedupe_signals(signals.get("signals", []) or [])
    prices = tool_get_price_data()
    buy_conf, sell_conf = _signal_totals(unique_signals)
    bias = _bias_from_totals(buy_conf, sell_conf)
    return {
        "bias": bias,
        "buy_confidence_total": buy_conf,
        "sell_confidence_total": sell_conf,
        "signal_count": len(unique_signals),
        "price_count": prices.get("count", 0),
    }


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
    return "Signal totals are mixed or low-conviction; wait for confirmation before acting."


def build_grounded_briefing(tool_state: Dict[str, Any], error_note: str = "") -> str:
    signals_result = tool_state.get("get_latest_signals", {}) or {}
    bias_result = tool_state.get("assess_market_bias", {}) or {}
    prices_result = tool_state.get("get_price_data", {}) or {}
    macro_result = tool_state.get("get_macro_metrics", {}) or {}

    signals = _dedupe_signals(signals_result.get("signals", []) or [])
    metrics = macro_result.get("metrics", []) or []
    prices = prices_result.get("prices", []) or []

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
    lines.append(f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>")
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
    lines.append("<b>Top Signals:</b>")
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

    if error_note:
        lines.append("")
        lines.append(f"<b>Agent Note:</b> {html.escape(error_note)}")

    return "\n".join(lines)


TOOL_MAP = {
    "get_latest_signals": lambda args: tool_get_latest_signals(**args),
    "run_signal_engine": lambda args: tool_run_signal_engine(),
    "get_price_data": lambda args: tool_get_price_data(),
    "get_macro_metrics": lambda args: tool_get_macro_metrics(),
    "assess_market_bias": lambda args: tool_assess_market_bias(),
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
- Include market bias, top 3-5 actionable signals, and one key macro insight
- Format with Telegram HTML only: <b>bold</b>, <i>italic</i>, newlines with \n not <br>
- Do not make up data — only use what tools return
- Do not send Telegram directly; the runner sends a grounded briefing from current-run tool outputs
- If data is missing or errors occur, say so honestly in the briefing

Today is {date}. Execute the full agentic loop now."""


def run_agent_loop() -> None:
    logger.info("GeoClaw Agent Brain starting agentic loop")
    date_str = datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(date=date_str)},
        {"role": "user", "content": "Run the full GeoClaw morning intelligence briefing loop now."},
    ]

    max_iterations = 10
    iteration = 0
    tool_state: Dict[str, Any] = {}
    sent_briefing = False

    while iteration < max_iterations:
        iteration += 1
        logger.info(f"Agent iteration {iteration}")

        try:
            response = call_groq(messages, tools=TOOLS)
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            if tool_state and not sent_briefing:
                briefing = build_grounded_briefing(
                    tool_state,
                    error_note="Groq was rate-limited or unavailable. This briefing uses grounded tool outputs only.",
                )
                send_result = tool_send_telegram_briefing(briefing)
                logger.info(f"Fallback Telegram send result: {json.dumps(send_result)}")
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
                briefing = build_grounded_briefing(tool_state)
                send_result = tool_send_telegram_briefing(briefing)
                logger.info(f"Final Telegram send result: {json.dumps(send_result)}")
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
            briefing = build_grounded_briefing(
                tool_state,
                error_note=f"Unexpected agent finish state: {finish_reason or 'unknown'}",
            )
            send_result = tool_send_telegram_briefing(briefing)
            logger.info(f"Unexpected-finish Telegram send result: {json.dumps(send_result)}")
            sent_briefing = bool(send_result.get("status") == "ok")
        break

    if tool_state and not sent_briefing:
        briefing = build_grounded_briefing(
            tool_state,
            error_note="Agent loop ended before an explicit final response. Sending grounded tool summary.",
        )
        send_result = tool_send_telegram_briefing(briefing)
        logger.info(f"End-of-loop Telegram send result: {json.dumps(send_result)}")
        sent_briefing = bool(send_result.get("status") == "ok")

    logger.info(f"Agent loop completed in {iteration} iterations")


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
