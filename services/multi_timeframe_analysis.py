from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional


TIMEFRAME_ORDER = ("5m", "1h", "12h")
BASE_TIMEFRAME_WEIGHTS = {"5m": 0.25, "1h": 0.35, "12h": 0.40}

NEWS_BULLISH_TERMS = {
    "risk on",
    "stocks rise",
    "stocks rally",
    "nikkei rises",
    "nikkei rally",
    "nikkei gains",
    "yen weakens",
    "dollar strengthens",
    "boj holds",
    "soft landing",
    "ceasefire",
    "deal",
    "oil falls",
    "oil eases",
    "semiconductor demand",
    "chip demand",
    "stimulus",
}

NEWS_BEARISH_TERMS = {
    "risk off",
    "stocks fall",
    "stocks slide",
    "nikkei falls",
    "nikkei drops",
    "nikkei plunges",
    "yen strengthens",
    "boj hike",
    "oil spike",
    "oil surges",
    "escalation",
    "selloff",
    "tariff",
    "recession",
    "tightening",
    "tech selloff",
}

NEWS_THEME_MAP = {
    "boj": ("boj", "bank of japan", "policy", "rate hike", "rate cut"),
    "yen": ("yen", "usd/jpy", "dollar", "currency"),
    "oil": ("oil", "crude", "energy"),
    "geopolitics": ("iran", "war", "tariff", "ceasefire", "talks", "sanction"),
    "chips": ("chip", "semiconductor", "ai", "tech"),
    "macro": ("gdp", "inflation", "cpi", "payroll", "yield", "treasury"),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = parsedate_to_datetime(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalise_candles(candles: Iterable[Dict[str, Any]]) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for candle in candles or []:
        o = _to_float(candle.get("o"))
        h = _to_float(candle.get("h"))
        l = _to_float(candle.get("l"))
        c = _to_float(candle.get("c"))
        if not all(value > 0 for value in (o, h, l, c)):
            continue
        rows.append({"o": o, "h": h, "l": l, "c": c})
    return rows


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    period = max(1, min(period, len(values)))
    multiplier = 2.0 / (period + 1.0)
    ema_value = float(values[0])
    for value in values[1:]:
        ema_value = (float(value) - ema_value) * multiplier + ema_value
    return ema_value


def _rsi(values: List[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    period = max(2, min(period, len(values) - 1))
    gains = []
    losses = []
    for prev, current in zip(values[-period - 1 : -1], values[-period:]):
        delta = float(current) - float(prev)
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / max(len(gains), 1)
    avg_loss = sum(losses) / max(len(losses), 1)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _pct_change(current: float, base: float) -> float:
    if not base:
        return 0.0
    return (float(current) - float(base)) / float(base) * 100.0


def aggregate_candles(candles: Iterable[Dict[str, Any]], group_size: int) -> List[Dict[str, Any]]:
    rows = list(candles or [])
    if group_size <= 1:
        return rows
    out: List[Dict[str, Any]] = []
    bucket: List[Dict[str, Any]] = []
    for candle in rows:
        bucket.append(candle)
        if len(bucket) < group_size:
            continue
        out.append(
            {
                "t": bucket[0].get("t") or bucket[0].get("quote_timestamp"),
                "quote_timestamp": bucket[-1].get("quote_timestamp") or bucket[-1].get("t"),
                "quote_minute": bucket[0].get("quote_minute") or bucket[0].get("t"),
                "o": _to_float(bucket[0].get("o")),
                "h": max(_to_float(item.get("h")) for item in bucket),
                "l": min(_to_float(item.get("l")) for item in bucket),
                "c": _to_float(bucket[-1].get("c")),
            }
        )
        bucket = []
    return out


def analyse_timeframe(label: str, candles: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    bars = _normalise_candles(candles)
    if len(bars) < 30:
        return {
            "label": label,
            "ready": False,
            "bar_count": len(bars),
            "bias": "waiting",
            "score": 0.0,
            "confidence": 0,
            "summary": f"{label} is waiting for more candles.",
        }

    closes = [row["c"] for row in bars]
    highs = [row["h"] for row in bars]
    lows = [row["l"] for row in bars]
    last_price = closes[-1]

    ema_fast = _ema(closes, 9)
    ema_slow = _ema(closes, 21)
    ema_anchor = _ema(closes, 50)
    rsi_value = _rsi(closes, 14)
    momentum_lookback = min(max(4, len(closes) // 10), len(closes) - 1)
    momentum_pct = _pct_change(last_price, closes[-momentum_lookback - 1])
    volatility_pct = _pct_change(max(highs[-20:]), min(lows[-20:]))
    support = min(lows[-20:])
    resistance = max(highs[-20:])

    score = 0.0
    score += 20.0 if last_price >= ema_fast else -20.0
    score += 26.0 if ema_fast >= ema_slow else -26.0
    score += 18.0 if ema_slow >= ema_anchor else -18.0
    score += _clamp(momentum_pct * 12.0, -18.0, 18.0)
    if rsi_value >= 72:
        score -= 8.0
    elif rsi_value <= 28:
        score += 8.0
    if volatility_pct >= 4.0:
        score *= 0.92
    score = round(_clamp(score, -100.0, 100.0), 1)

    if score >= 20:
        bias = "bullish"
    elif score <= -20:
        bias = "bearish"
    else:
        bias = "neutral"

    confidence = int(
        round(
            _clamp(
                45.0 + abs(score) * 0.42 + min(len(bars), 120) / 12.0 - (6.0 if volatility_pct > 5.5 else 0.0),
                25.0,
                92.0,
            )
        )
    )

    relationship = "above" if last_price >= ema_slow else "below"
    summary = (
        f"{label} is {bias} with price {relationship} the 21 EMA, RSI {rsi_value:.1f}, "
        f"and {momentum_pct:+.2f}% momentum over the last {momentum_lookback} bars."
    )

    return {
        "label": label,
        "ready": True,
        "bar_count": len(bars),
        "bias": bias,
        "score": score,
        "confidence": confidence,
        "last_price": round(last_price, 2),
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "ema_anchor": round(ema_anchor, 2),
        "rsi": round(rsi_value, 1),
        "momentum_pct": round(momentum_pct, 2),
        "volatility_pct": round(volatility_pct, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "summary": summary,
    }


def _headline_score(text: str) -> int:
    lower = str(text or "").lower()
    bullish = sum(1 for term in NEWS_BULLISH_TERMS if term in lower)
    bearish = sum(1 for term in NEWS_BEARISH_TERMS if term in lower)
    return (bullish - bearish) * 12


def analyse_news(news_items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(news_items or [])
    if len(rows) < 3:
        return {
            "ready": False,
            "headline_count": len(rows),
            "bias": "waiting",
            "score": 0.0,
            "confidence": 0,
            "dominant_themes": [],
            "top_headlines": rows[:3],
            "summary": "Waiting for at least 3 recent headlines before using news context.",
        }

    now = datetime.now(timezone.utc)
    weighted_score = 0.0
    theme_counts = {key: 0 for key in NEWS_THEME_MAP}
    ranked_rows = []

    for item in rows:
        headline = str(item.get("headline") or item.get("title") or "").strip()
        detail = str(item.get("reason") or item.get("summary") or "").strip()
        text = f"{headline} {detail}".strip()
        item_score = _headline_score(text)
        published_at = _parse_time(item.get("ts") or item.get("published_at") or item.get("fetched_at"))
        if published_at is None:
            recency_weight = 0.8
        else:
            hours_old = max((now - published_at).total_seconds() / 3600.0, 0.0)
            recency_weight = 1.25 if hours_old <= 2 else (1.0 if hours_old <= 12 else 0.7)
        weighted_score += item_score * recency_weight
        for theme, terms in NEWS_THEME_MAP.items():
            if any(term in text.lower() for term in terms):
                theme_counts[theme] += 1
        ranked_rows.append(
            {
                "headline": headline,
                "source": item.get("source"),
                "url": item.get("url"),
                "ts": item.get("ts") or item.get("published_at") or item.get("fetched_at"),
                "score": round(item_score * recency_weight, 1),
            }
        )

    ranked_rows.sort(key=lambda item: abs(_to_float(item.get("score"))), reverse=True)
    score = round(_clamp(weighted_score, -100.0, 100.0), 1)
    if score >= 15:
        bias = "bullish"
    elif score <= -15:
        bias = "bearish"
    else:
        bias = "neutral"

    confidence = int(round(_clamp(38.0 + abs(score) * 0.55 + min(len(rows), 12) * 2.5, 30.0, 90.0)))
    dominant_themes = [theme for theme, count in sorted(theme_counts.items(), key=lambda item: item[1], reverse=True) if count > 0][:3]
    if not dominant_themes:
        dominant_themes = ["broad market"]

    summary = (
        f"News flow is {bias}. {len(rows)} headlines scanned with focus on "
        + ", ".join(dominant_themes)
        + "."
    )

    return {
        "ready": True,
        "headline_count": len(rows),
        "bias": bias,
        "score": score,
        "confidence": confidence,
        "dominant_themes": dominant_themes,
        "top_headlines": ranked_rows[:4],
        "summary": summary,
    }


def analyse_message(message: str) -> Dict[str, Any]:
    text = str(message or "").strip()
    lower = text.lower()
    weights = dict(BASE_TIMEFRAME_WEIGHTS)
    mode = "balanced"
    risk = "balanced"
    emphasis = "confluence"

    if any(term in lower for term in ("scalp", "scalping", "quick", "fast", "entry now")):
        mode = "execution"
        weights = {"5m": 0.40, "1h": 0.35, "12h": 0.25}
    elif any(term in lower for term in ("swing", "position", "hold", "higher timeframe")):
        mode = "swing"
        weights = {"5m": 0.15, "1h": 0.35, "12h": 0.50}

    if any(term in lower for term in ("safe", "conservative", "careful", "low risk")):
        risk = "conservative"
    elif any(term in lower for term in ("aggressive", "breakout", "high risk")):
        risk = "aggressive"

    if any(term in lower for term in ("news", "headline", "macro", "event")):
        emphasis = "news"

    return {
        "text": text,
        "mode": mode,
        "risk": risk,
        "emphasis": emphasis,
        "weights": weights,
    }


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _adaptive_plan(
    bias: str,
    message_profile: Dict[str, Any],
    timeframe_map: Dict[str, Dict[str, Any]],
    news_state: Dict[str, Any],
) -> str:
    fast = timeframe_map["5m"]
    mid = timeframe_map["1h"]
    slow = timeframe_map["12h"]
    risk = message_profile.get("risk")
    mode = message_profile.get("mode")

    if bias == "BULLISH":
        if risk == "conservative":
            return (
                f"Bullish structure is intact, but wait for 5m pullbacks to hold above {mid.get('support', 0):,.0f} "
                f"before trusting continuation. The 12h trend stays constructive while price holds above {slow.get('support', 0):,.0f}."
            )
        if mode == "execution":
            return (
                f"Use 5m breaks through {fast.get('resistance', 0):,.0f} only when the 1h trend stays above its 21 EMA. "
                f"News is currently {news_state.get('bias', 'neutral')}, so failed breakouts matter."
            )
        return (
            f"The higher-timeframe path is up. Keep the bullish case while 1h support at {mid.get('support', 0):,.0f} "
            f"and 12h support at {slow.get('support', 0):,.0f} continue to hold."
        )

    if bias == "BEARISH":
        if risk == "conservative":
            return (
                f"Bearish structure is in control, but avoid chasing weakness into support. A safer plan is to wait for failed 5m rebounds below "
                f"{mid.get('resistance', 0):,.0f} while 12h resistance holds near {slow.get('resistance', 0):,.0f}."
            )
        if mode == "execution":
            return (
                f"Use 5m rejection from {fast.get('resistance', 0):,.0f} only while the 1h trend remains below its 21 EMA. "
                f"News is {news_state.get('bias', 'neutral')}, so sudden squeezes remain live."
            )
        return (
            f"Pressure stays down while the 1h chart trades below {mid.get('resistance', 0):,.0f}. "
            f"The bigger invalidation is a reclaim of 12h resistance near {slow.get('resistance', 0):,.0f}."
        )

    return (
        f"The tape is mixed. Wait for either a 5m reclaim above {fast.get('resistance', 0):,.0f} with 1h confirmation, "
        f"or a clean loss of {fast.get('support', 0):,.0f} with news follow-through."
    )


def _time_horizon(message_profile: Dict[str, Any], ready: bool = True) -> str:
    if not ready:
        return "Wait for full confluence"
    mode = str(message_profile.get("mode") or "balanced")
    if mode == "execution":
        return "1-4 hours"
    if mode == "swing":
        return "4-12 hours"
    return "1-6 hours"


def _build_forecast(
    bias: str,
    price: float,
    timeframe_map: Dict[str, Dict[str, Any]],
    news_state: Dict[str, Any],
    message_profile: Dict[str, Any],
    confidence: int,
) -> Dict[str, Any]:
    fast = timeframe_map["5m"]
    mid = timeframe_map["1h"]
    slow = timeframe_map["12h"]
    trend_range = max(
        _to_float(slow.get("resistance")) - _to_float(slow.get("support")),
        _to_float(mid.get("resistance")) - _to_float(mid.get("support")),
        _to_float(fast.get("resistance")) - _to_float(fast.get("support")),
        1.0,
    )

    if bias == "BULLISH":
        trigger_level = max(_to_float(fast.get("support")), _to_float(mid.get("support")))
        invalidation_level = min(_to_float(fast.get("support")), _to_float(mid.get("support")))
        target_level = min(
            _to_float(slow.get("resistance")),
            max(_to_float(mid.get("resistance")), float(price) + trend_range * 0.25),
        )
        secondary_target_level = max(_to_float(slow.get("resistance")), target_level + trend_range * 0.12)
        direction = "LONG"
        setup = "Pullback continuation"
        summary = (
            f"Buy bias stays valid above {trigger_level:,.0f}. "
            f"First target is {target_level:,.0f} with invalidation below {invalidation_level:,.0f}."
        )
        risk = max(trigger_level - invalidation_level, 1.0)
        reward = max(target_level - trigger_level, 1.0)
        trigger_text = f"Hold above {trigger_level:,.0f} on 5m/1h pullbacks"
        if message_profile.get("risk") == "conservative":
            trigger_text = f"Wait for 5m support to hold above {trigger_level:,.0f}"
    elif bias == "BEARISH":
        trigger_level = min(_to_float(fast.get("resistance")), _to_float(mid.get("resistance")))
        invalidation_level = max(_to_float(fast.get("resistance")), _to_float(mid.get("resistance")))
        target_level = max(
            _to_float(slow.get("support")),
            min(_to_float(mid.get("support")), float(price) - trend_range * 0.25),
        )
        secondary_target_level = min(_to_float(slow.get("support")), target_level - trend_range * 0.12)
        direction = "SHORT"
        setup = "Pullback continuation"
        summary = (
            f"Sell bias stays valid below {trigger_level:,.0f}. "
            f"First target is {target_level:,.0f} with invalidation above {invalidation_level:,.0f}."
        )
        risk = max(invalidation_level - trigger_level, 1.0)
        reward = max(trigger_level - target_level, 1.0)
        trigger_text = f"Reject below {trigger_level:,.0f} on 5m/1h rallies"
        if message_profile.get("risk") == "conservative":
            trigger_text = f"Wait for failed bounce below {trigger_level:,.0f}"
    else:
        upper_trigger = max(_to_float(fast.get("resistance")), _to_float(mid.get("resistance")))
        lower_trigger = min(_to_float(fast.get("support")), _to_float(mid.get("support")))
        return {
            "direction": "WAIT",
            "setup": "Range breakout",
            "entry_low": None,
            "entry_high": None,
            "trigger_text": f"Upside above {upper_trigger:,.0f} / downside below {lower_trigger:,.0f}",
            "trigger_level": round(upper_trigger, 2),
            "secondary_trigger_level": round(lower_trigger, 2),
            "target_level": None,
            "secondary_target_level": None,
            "invalidation_level": None,
            "reward_to_risk": 0.0,
            "confidence": confidence,
            "time_horizon": _time_horizon(message_profile),
            "summary": (
                f"No clean directional edge yet. A break above {upper_trigger:,.0f} favors upside, "
                f"while a drop below {lower_trigger:,.0f} opens downside."
            ),
            "news_alignment": news_state.get("bias", "neutral"),
        }

    reward_to_risk = round(reward / risk, 2) if risk else 0.0
    entry_band = max(risk * 0.18, trend_range * 0.04, float(price or 0.0) * 0.00035, 10.0)
    entry_low = trigger_level - entry_band
    entry_high = trigger_level + entry_band
    forecast_confidence = int(
        _clamp(
            confidence + (4 if str(news_state.get("bias") or "") in {"bullish", "bearish"} else 0),
            35.0,
            95.0,
        )
    )
    return {
        "direction": direction,
        "setup": setup,
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "trigger_text": trigger_text,
        "trigger_level": round(trigger_level, 2),
        "secondary_trigger_level": None,
        "target_level": round(target_level, 2),
        "secondary_target_level": round(secondary_target_level, 2),
        "invalidation_level": round(invalidation_level, 2),
        "reward_to_risk": reward_to_risk,
        "confidence": forecast_confidence,
        "time_horizon": _time_horizon(message_profile),
        "summary": summary,
        "news_alignment": news_state.get("bias", "neutral"),
    }


def _build_regime(
    bias: str,
    timeframe_map: Dict[str, Dict[str, Any]],
    news_state: Dict[str, Any],
    forecast: Dict[str, Any],
    confidence: int,
) -> Dict[str, Any]:
    states = [str(timeframe_map[label].get("bias") or "neutral") for label in TIMEFRAME_ORDER]
    bullish_count = sum(1 for state in states if state == "bullish")
    bearish_count = sum(1 for state in states if state == "bearish")
    news_bias = str(news_state.get("bias") or "neutral")
    news_score = abs(_to_float(news_state.get("score")))
    direction = str(forecast.get("direction") or "WAIT").upper()

    if confidence < 48:
        label = "Low Conviction"
        summary = "Signal quality is still thin, so wait for cleaner alignment before leaning into a trade."
    elif news_score >= 28 and news_bias in {"bullish", "bearish"}:
        label = "News-Driven"
        summary = f"Headline flow is steering the session, with news bias currently {news_bias}."
    elif direction == "LONG" and bullish_count >= 2 and bearish_count == 0:
        label = "Trend Day"
        summary = "Higher-timeframe structure and intraday confirmation are both aligned to the upside."
    elif direction == "SHORT" and bearish_count >= 2 and bullish_count == 0:
        label = "Breakdown Risk"
        summary = "Selling pressure is aligned across the stack, so failed rebounds remain vulnerable."
    elif direction == "SHORT" and bullish_count >= 1 and news_bias == "bullish":
        label = "Short Squeeze Risk"
        summary = "The broader structure is heavy, but bullish catalysts can still force fast squeezes."
    elif bias == "NEUTRAL":
        label = "Range Day"
        summary = "Timeframes are mixed enough that breakout confirmation matters more than anticipation."
    else:
        label = "Range Day"
        summary = "The market is tradable, but the edge still depends on trigger quality rather than broad alignment."

    return {
        "label": label,
        "summary": summary,
        "bullish_timeframes": bullish_count,
        "bearish_timeframes": bearish_count,
    }


def _build_quant_strategy(
    bias: str,
    timeframe_map: Dict[str, Dict[str, Any]],
    news_state: Dict[str, Any],
    forecast: Dict[str, Any],
    regime: Dict[str, Any],
    message_profile: Dict[str, Any],
    confidence: int,
) -> Dict[str, Any]:
    risk_mode = str(message_profile.get("risk") or "balanced")
    trade_mode = str(message_profile.get("mode") or "balanced")
    direction = str(forecast.get("direction") or "WAIT").upper()
    regime_label = str(regime.get("label") or "Low Conviction")
    news_bias = str(news_state.get("bias") or "neutral")
    rr = _to_float(forecast.get("reward_to_risk"))
    risk_budget = 0.35 if risk_mode == "conservative" else (0.75 if risk_mode == "aggressive" else 0.50)
    size_fraction = 0.50 if risk_mode == "conservative" else (1.00 if risk_mode == "aggressive" else 0.75)

    if confidence < 58 or rr < 1.35:
        size_fraction = max(0.25, size_fraction - 0.25)

    size_label = "Quarter size" if size_fraction <= 0.25 else ("Half size" if size_fraction <= 0.50 else ("Three-quarter size" if size_fraction < 1.0 else "Full size"))
    entry_level = _to_float(forecast.get("trigger_level"))
    invalidation_level = _to_float(forecast.get("invalidation_level"))
    target_level = _to_float(forecast.get("target_level"))
    stop_distance = abs(entry_level - invalidation_level) if entry_level and invalidation_level else 0.0
    target_distance = abs(target_level - entry_level) if entry_level and target_level else 0.0

    if direction == "WAIT":
        upper_trigger = _to_float(forecast.get("trigger_level"))
        lower_trigger = _to_float(forecast.get("secondary_trigger_level"))
        return {
            "name": "Stand Aside",
            "family": "stand_aside",
            "state": "WAIT",
            "side": "WAIT",
            "entry_model": "Breakout confirmation only",
            "exit_model": "No trade until price leaves the range",
            "position_size": "No position",
            "position_size_fraction": 0.0,
            "risk_per_trade_pct": round(risk_budget, 2),
            "edge_score": max(20, int(round(confidence * 0.55))),
            "time_horizon": _time_horizon(message_profile),
            "summary": (
                f"Quant strategy is flat for now. Buy only above {upper_trigger:,.0f} or sell only below {lower_trigger:,.0f}."
                if upper_trigger and lower_trigger
                else "Quant strategy is flat for now while the market stays mixed."
            ),
            "rules": [
                f"Long only above {upper_trigger:,.0f}" if upper_trigger else "Wait for upside breakout",
                f"Short only below {lower_trigger:,.0f}" if lower_trigger else "Wait for downside breakout",
                "Do not trade in the middle of the range.",
            ],
            "guardrails": [
                f"Risk no more than {risk_budget:.2f}% on one idea.",
                "One active position at a time.",
                "Skip trades when price chops around the trigger.",
            ],
            "why_now": "The timeframes are not aligned enough to justify a live quant entry.",
            "stop_distance_points": 0.0,
            "target_distance_points": 0.0,
        }

    if direction == "LONG":
        name = "Trend Pullback Long" if regime_label == "Trend Day" else ("News Breakout Long" if regime_label == "News-Driven" else "Long Continuation")
        state = "REDUCED" if regime_label in {"News-Driven", "Short Squeeze Risk"} or confidence < 64 else "ACTIVE"
        entry_model = (
            "Buy the 5m pullback after support holds"
            if trade_mode != "execution"
            else "Buy the 5m reclaim after the trigger breaks"
        )
        exit_model = f"Scale out into {target_level:,.0f} then trail the stop." if target_level else "Take partial profit into resistance."
        rules = [
            f"Only go long above {entry_level:,.0f}.",
            f"5m and 1h must stay bullish into entry.",
            f"News should stay neutral to bullish, not sharply bearish ({news_bias}).",
        ]
        guardrails = [
            f"Stop below {invalidation_level:,.0f}.",
            f"Risk no more than {risk_budget:.2f}% on one trade.",
            "Cut the trade if the trigger fails quickly.",
        ]
        why_now = (
            f"The model sees upside continuation because the short, medium, and higher timeframe stack is supporting a long setup."
            if regime_label == "Trend Day"
            else f"The model is still long-biased, but {regime_label.lower()} means position size should stay controlled."
        )
    else:
        name = "Trend Pullback Short" if regime_label == "Breakdown Risk" else ("News Breakout Short" if regime_label == "News-Driven" else "Short Continuation")
        state = "REDUCED" if regime_label in {"News-Driven", "Short Squeeze Risk"} or confidence < 64 else "ACTIVE"
        entry_model = (
            "Sell the 5m bounce after resistance rejects price"
            if trade_mode != "execution"
            else "Sell the 5m breakdown after the trigger fails"
        )
        exit_model = f"Scale out into {target_level:,.0f} then trail the stop." if target_level else "Take partial profit into support."
        rules = [
            f"Only go short below {entry_level:,.0f}.",
            f"5m and 1h must stay bearish into entry.",
            f"News should stay neutral to bearish, not sharply bullish ({news_bias}).",
        ]
        guardrails = [
            f"Stop above {invalidation_level:,.0f}.",
            f"Risk no more than {risk_budget:.2f}% on one trade.",
            "Cut the trade if the breakdown snaps back quickly.",
        ]
        why_now = (
            "The model sees downside continuation because sellers are aligned across the chart stack."
            if regime_label == "Breakdown Risk"
            else f"The model is still short-biased, but {regime_label.lower()} means position size should stay controlled."
        )

    active_rules = 0
    dominant_bias = bias.lower()
    for label in TIMEFRAME_ORDER:
        if str(timeframe_map[label].get("bias") or "neutral") == dominant_bias:
            active_rules += 1
    if news_bias == dominant_bias:
        active_rules += 1

    edge_score = int(round(_clamp(confidence * 0.72 + rr * 14.0 + active_rules * 5.0, 25.0, 96.0)))
    return {
        "name": name,
        "family": name.lower().replace(" ", "_"),
        "state": state,
        "side": direction,
        "entry_model": entry_model,
        "exit_model": exit_model,
        "position_size": size_label,
        "position_size_fraction": round(size_fraction, 2),
        "risk_per_trade_pct": round(risk_budget, 2),
        "edge_score": edge_score,
        "time_horizon": _time_horizon(message_profile),
        "summary": forecast.get("summary") or f"{name} is active while the trigger holds.",
        "rules": rules,
        "guardrails": guardrails,
        "why_now": why_now,
        "stop_distance_points": round(stop_distance, 2),
        "target_distance_points": round(target_distance, 2),
    }

def build_multi_timeframe_analysis(
    *,
    price: float,
    quote_timestamp: str,
    timeframes: Dict[str, Iterable[Dict[str, Any]]],
    news_items: Iterable[Dict[str, Any]],
    message: str = "",
) -> Dict[str, Any]:
    message_profile = analyse_message(message)
    timeframe_states = {label: analyse_timeframe(label, timeframes.get(label, [])) for label in TIMEFRAME_ORDER}
    news_state = analyse_news(news_items)
    requirements = {
        "5m": timeframe_states["5m"].get("ready", False),
        "1h": timeframe_states["1h"].get("ready", False),
        "12h": timeframe_states["12h"].get("ready", False),
        "news": news_state.get("ready", False),
    }
    ready = all(requirements.values())

    if not ready:
        missing = [label for label, is_ready in requirements.items() if not is_ready]
        return {
            "status": "ok",
            "ready": False,
            "price": round(float(price or 0.0), 2),
            "quote_timestamp": quote_timestamp,
            "requirements": requirements,
            "timeframes": [timeframe_states[label] for label in TIMEFRAME_ORDER],
            "news": news_state,
            "message_profile": message_profile,
            "confluence": {
                "bias": "WAITING",
                "confidence": 0,
                "score": 0.0,
                "summary": "Waiting for all three chart states and fresh news before producing an adaptive chart call.",
            },
            "forecast": {
                "direction": "WAIT",
                "setup": "Waiting for full confluence",
                "entry_low": None,
                "entry_high": None,
                "trigger_text": "Need 5m, 1h, 12h, and news before a precise forecast is published.",
                "trigger_level": None,
                "secondary_trigger_level": None,
                "target_level": None,
                "secondary_target_level": None,
                "invalidation_level": None,
                "reward_to_risk": 0.0,
                "confidence": 0,
                "time_horizon": _time_horizon(message_profile, ready=False),
                "summary": "Waiting for all three chart states and fresh news before producing an adaptive chart call.",
                "news_alignment": news_state.get("bias", "neutral"),
            },
            "regime": {
                "label": "Low Conviction",
                "summary": "Waiting for 5m, 1h, 12h, and news before labeling the market regime.",
                "bullish_timeframes": 0,
                "bearish_timeframes": 0,
            },
            "adaptive_plan": {
                "message": message_profile.get("text", ""),
                "playbook": "Still collecting the required 5m, 1h, 12h, and news inputs.",
            },
            "quant_strategy": {
                "name": "Stand Aside",
                "family": "stand_aside",
                "state": "WAIT",
                "side": "WAIT",
                "entry_model": "Wait for full confluence",
                "exit_model": "No trade yet",
                "position_size": "No position",
                "position_size_fraction": 0.0,
                "risk_per_trade_pct": 0.35,
                "edge_score": 20,
                "time_horizon": _time_horizon(message_profile, ready=False),
                "summary": "No quant strategy is live until all chart states and fresh news are ready.",
                "rules": ["Need 5m, 1h, 12h, and news before activating the strategy."],
                "guardrails": ["Stay flat until all required inputs are ready."],
                "why_now": "The model is still waiting for a complete data stack.",
                "stop_distance_points": 0.0,
                "target_distance_points": 0.0,
            },
            "missing_requirements": missing,
        }

    weights = dict(message_profile.get("weights") or BASE_TIMEFRAME_WEIGHTS)
    timeframe_score = sum(_to_float(timeframe_states[label]["score"]) * _to_float(weights.get(label, 0.0)) for label in TIMEFRAME_ORDER)
    news_weight = 0.24 if message_profile.get("emphasis") == "news" else 0.18
    combined_score = timeframe_score * (1.0 - news_weight) + _to_float(news_state.get("score")) * news_weight

    dominant_sign = _sign(combined_score)
    timeframe_alignment = sum(
        1
        for label in TIMEFRAME_ORDER
        if _sign(_to_float(timeframe_states[label].get("score"))) == dominant_sign and abs(_to_float(timeframe_states[label].get("score"))) >= 15
    )
    news_alignment = dominant_sign != 0 and _sign(_to_float(news_state.get("score"))) == dominant_sign

    if combined_score >= 18:
        bias = "BULLISH"
    elif combined_score <= -18:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    confidence = 42.0 + abs(combined_score) * 0.44 + timeframe_alignment * 8.0 + (6.0 if news_alignment else 0.0)
    if message_profile.get("risk") == "conservative":
        confidence -= 4.0
    if bias == "NEUTRAL":
        confidence -= 8.0
    confidence = int(round(_clamp(confidence, 35.0, 95.0)))

    timeframe_summary = ", ".join(f"{label} {timeframe_states[label]['bias']}" for label in TIMEFRAME_ORDER)
    news_fragment = f"news is {news_state.get('bias', 'neutral')}"
    confluence_summary = (
        f"{timeframe_summary}; {news_fragment}. "
        f"{timeframe_alignment}/3 chart states align with the current {bias.lower()} read."
    )
    forecast = _build_forecast(
        bias,
        price,
        timeframe_states,
        news_state,
        message_profile,
        confidence,
    )
    regime = _build_regime(
        bias,
        timeframe_states,
        news_state,
        forecast,
        confidence,
    )
    quant_strategy = _build_quant_strategy(
        bias,
        timeframe_states,
        news_state,
        forecast,
        regime,
        message_profile,
        confidence,
    )

    return {
        "status": "ok",
        "ready": True,
        "price": round(float(price or 0.0), 2),
        "quote_timestamp": quote_timestamp,
        "requirements": requirements,
        "timeframes": [timeframe_states[label] for label in TIMEFRAME_ORDER],
        "news": news_state,
        "message_profile": message_profile,
        "confluence": {
            "bias": bias,
            "confidence": confidence,
            "score": round(combined_score, 1),
            "alignment_count": timeframe_alignment,
            "summary": confluence_summary,
        },
        "forecast": forecast,
        "regime": regime,
        "adaptive_plan": {
            "message": message_profile.get("text", ""),
            "playbook": _adaptive_plan(bias, message_profile, timeframe_states, news_state),
        },
        "quant_strategy": quant_strategy,
    }
