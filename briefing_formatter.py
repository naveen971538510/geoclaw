"""
Grounded Telegram briefing formatter for agent_brain current-run state.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SIGNAL_FRESHNESS_HOURS = 48


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
        float(signal.get("confidence") or 0)
        for signal in signals
        if str(signal.get("direction", "")).upper() == "BUY"
    )
    sell_conf = sum(
        float(signal.get("confidence") or 0)
        for signal in signals
        if str(signal.get("direction", "")).upper() == "SELL"
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


def _fmt_number(value: Any, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return str(value)


def _confidence_bar(value: float, width: int = 5) -> str:
    """Render a text confidence bar like [███░░] for Telegram."""
    pct = max(0.0, min(100.0, float(value or 0)))
    filled = round(pct / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


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
    by_name = {str(metric.get("metric_name", "")).upper(): metric for metric in metrics}
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


def build_briefing(run_state: Dict[str, Any]) -> str:
    signals = _dedupe_signals(run_state.get("signals_snapshot", []) or [])
    prices_result = run_state.get("price_data", {}) or {}
    macro_result = run_state.get("macro_metrics", {}) or {}
    bias_result = run_state.get("market_bias", {}) or {}

    metrics = macro_result.get("metrics", []) or []
    prices = prices_result.get("prices", []) or []
    signal_freshness = run_state.get("signal_freshness") or _signal_freshness(signals)
    macro_freshness = macro_result.get("freshness", {}) or {}
    macro_freshness_label = _macro_freshness_label(macro_freshness)
    macro_freshness_note = _macro_freshness_line(macro_freshness)
    price_timestamp = _latest_price_timestamp(prices) or "unavailable"
    run_timestamp = run_state.get("started_at") or datetime.now(timezone.utc).isoformat()
    run_health = "DEGRADED" if run_state.get("degraded_mode") else "HEALTHY"

    buy_total, sell_total = _signal_totals(signals)
    bias = str(bias_result.get("bias") or _bias_from_totals(buy_total, sell_total)).upper()

    preferred = ["SPX", "XAUUSD", "BTCUSD", "GLD", "USO", "GBPUSD", "SPY", "QQQ"]
    price_map = {}
    for price in prices:
        ticker = str(price.get("ticker", "")).upper()
        if ticker and ticker not in price_map:
            price_map[ticker] = price

    ordered_prices = [price_map[ticker] for ticker in preferred if ticker in price_map]
    if not ordered_prices:
        ordered_prices = prices[:3]

    lines = []
    lines.append("<b>GeoClaw Intelligence Briefing</b>")
    lines.append(f"<b>Run:</b> {html.escape(str(run_timestamp))} | <b>Status:</b> {run_health}")
    lines.append("")

    # Market Overview
    bias_emoji = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "◆"}.get(bias, "◆")
    lines.append(f"<b>{bias_emoji} Market Bias: {html.escape(bias)}</b>")
    total = buy_total + sell_total
    if total > 0:
        conviction = max(buy_total, sell_total) / total * 100
        lines.append(f"  Conviction: {_fmt_number(conviction, 0)}% | BUY {_fmt_number(buy_total)} vs SELL {_fmt_number(sell_total)}")
    lines.append("")

    # Prices with change indicators
    if ordered_prices:
        lines.append("<b>Key Prices:</b>")
        for price in ordered_prices[:6]:
            ticker = html.escape(str(price.get("ticker", "")))
            price_val = _fmt_number(price.get("price"), 2)
            lines.append(f"  {ticker}: {price_val}")
        lines.append("")

    # Directional Signals as predictions
    lines.append("<b>Directional Signals:</b>")
    if signals:
        for idx, signal in enumerate(signals[:5], 1):
            direction = str(signal.get("direction", "HOLD")).upper()
            name = html.escape(str(signal.get("signal_name", "Unknown signal")))
            confidence = float(signal.get("confidence", 0) or 0)
            conf_bar = _confidence_bar(confidence)
            arrow = "▲" if direction == "BUY" else ("▼" if direction == "SELL" else "—")
            lines.append(f"  {arrow} <b>{direction}</b> {name}")
            lines.append(f"    Confidence: {conf_bar} {_fmt_number(confidence, 0)}%")
            explanation = str(signal.get("explanation_plain_english") or "").strip()
            if explanation:
                lines.append(f"    {html.escape(explanation[:120])}")
    else:
        lines.append("  No fresh BUY/SELL signals in the last 48 hours.")
    lines.append("")

    # Macro Insight
    lines.append(f"<b>Macro Insight:</b> {_pick_macro_insight(metrics)}")
    if macro_freshness_note:
        lines.append(f"  <i>({macro_freshness_label}: {html.escape(macro_freshness_note[:100])})</i>")
    lines.append("")

    # Active Theses (if available in run_state)
    theses = run_state.get("active_theses") or []
    if theses:
        lines.append("<b>Active Theses:</b>")
        for thesis in theses[:3]:
            claim = html.escape(str(thesis.get("current_claim") or thesis.get("thesis_key") or "")[:100])
            conf = float(thesis.get("confidence", 0) or 0)
            status = str(thesis.get("status", "active"))
            evidence = int(thesis.get("evidence_count", 0) or 0)
            conf_pct = round(conf * 100) if conf <= 1 else round(conf)
            lines.append(f"  • {claim}")
            lines.append(f"    {_confidence_bar(conf_pct)} {conf_pct}% | {status} | {evidence} sources")
        lines.append("")

    # Investigation findings (if agent did web searches)
    investigations = run_state.get("investigation_findings") or []
    if investigations:
        lines.append("<b>Agent Investigations:</b>")
        for finding in investigations[:3]:
            lines.append(f"  • {html.escape(str(finding)[:140])}")
        lines.append("")

    # Conservative Read
    lines.append(
        f"<b>Assessment:</b> {_conservative_read(bias, buy_total, sell_total, len(signals))}"
    )

    if run_state.get("degraded_mode"):
        lines.append("")
        lines.append("<b>⚠ Degraded:</b>")
        for note in (run_state.get("degradation_notes") or [])[:3]:
            lines.append(f"  - {html.escape(str(note)[:80])}")

    if run_state.get("briefing_note"):
        lines.append("")
        lines.append(f"<b>Note:</b> {html.escape(str(run_state['briefing_note']))}")

    return "\n".join(lines)
