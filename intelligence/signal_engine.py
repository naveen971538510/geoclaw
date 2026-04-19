"""
Score macro_signals → geoclaw_signals (0–100 impact, direction, plain-English explanation).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.db import ensure_intelligence_schema, get_connection, get_database_url, query_all

logger = logging.getLogger("geoclaw.signal_engine")


def _latest_metrics() -> Dict[str, Dict[str, Any]]:
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not set")
    rows = query_all(
        """
        SELECT DISTINCT ON (metric_name)
            metric_name, observed_at, value, previous_value, pct_change
        FROM macro_signals
        ORDER BY metric_name, observed_at DESC;
        """
    )
    return {r["metric_name"]: dict(r) for r in rows}


def _latest_prices() -> Dict[str, Dict[str, Any]]:
    rows = query_all(
        """
        SELECT DISTINCT ON (ticker)
            ticker, price, ts
        FROM price_data
        ORDER BY ticker, ts DESC;
        """
    )
    return {str(r["ticker"]).upper(): dict(r) for r in rows}


def _prev_price(ticker: str, ts_value) -> Optional[float]:
    row = query_all(
        """
        SELECT price
        FROM price_data
        WHERE ticker = %s
          AND ts < %s
        ORDER BY ts DESC
        LIMIT 1;
        """,
        (ticker, ts_value),
    )
    return float(row[0]["price"]) if row else None


def _risk_momentum_score() -> float:
    """
    Aggregate momentum from BTC, XAUUSD, SPX:
      positive -> risk-on
      negative -> risk-off
    """
    prices = _latest_prices()
    tickers = ["BTCUSD", "XAUUSD", "SPX"]
    score = 0.0
    cnt = 0
    for t in tickers:
        p = prices.get(t)
        if not p:
            continue
        cur = float(p["price"])
        prev = _prev_price(t, p["ts"])
        if prev is None or prev == 0:
            continue
        ret = (cur - prev) / abs(prev)
        score += ret
        cnt += 1
    if cnt == 0:
        return 0.0
    return score / cnt


def _insert_signal(
    name: str,
    value: Optional[float],
    direction: str,
    confidence: float,
    explanation: str,
) -> None:
    confidence = max(0.0, min(100.0, float(confidence)))
    direction = direction.upper()
    if direction not in ("BUY", "SELL", "HOLD"):
        direction = "HOLD"
    ts = datetime.now(timezone.utc)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO geoclaw_signals (

                signal_name,
                value,
                direction,
                confidence,
                explanation_plain_english,
                ts,
                signal_day
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                (%s::timestamptz AT TIME ZONE 'UTC')::date
            )
            ON CONFLICT (signal_name, direction, signal_day)
            DO UPDATE SET
                value = EXCLUDED.value,
                confidence = EXCLUDED.confidence,
                explanation_plain_english = EXCLUDED.explanation_plain_english,
                ts = EXCLUDED.ts;
            """,
            (name, value, direction, confidence, explanation[:2000], ts, ts),
        )
        cur.close()


def _fed_rule(m: Dict[str, Any], momentum: float) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("FEDFUNDS")
    if not v:
        return None
    cur = float(v["value"])
    if cur > 5.0 and momentum <= 0:
        return (
            "Federal Reserve policy rate",
            cur,
            "SELL",
            92.0,
            "Policy rates are above 5% while risk momentum is weak, a setup that often pressures equities.",
        )
    if cur < 3.0 and momentum >= 0:
        return (
            "Federal Reserve policy rate",
            cur,
            "BUY",
            88.0,
            "Policy rates are below 3% and momentum is supportive, which tends to favor risk assets.",
        )
    return (
        "Federal Reserve policy rate",
        cur,
        "HOLD",
        45.0,
        "Policy rates are in a middle zone relative to momentum, so this is not a strong directional setup.",
    )


def _cpi_rule(m: Dict[str, Any], momentum: float) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("CPI_YOY_PCT")
    if not v:
        return None
    yoy = float(v["value"])
    prev = v.get("previous_value")
    rising = (prev is not None and yoy > float(prev))
    falling = (prev is not None and yoy < float(prev))
    if yoy > 3.5 and rising and momentum <= 0:
        return (
            "Consumer inflation (CPI, year over year)",
            yoy,
            "SELL",
            85.0,
            "Inflation is above 3.5% and rising while momentum is weak, increasing rate-risk pressure on markets.",
        )
    if yoy < 2.5 and falling and momentum >= 0:
        return (
            "Consumer inflation (CPI, year over year)",
            yoy,
            "BUY",
            80.0,
            "Inflation is below 2.5% and falling with supportive momentum, a friendlier setup for equities.",
        )
    return (
        "Consumer inflation (CPI, year over year)",
        yoy,
        "HOLD",
        50.0,
        "Inflation and momentum do not align for a high-conviction trade direction.",
    )


def _unemployment_rule(m: Dict[str, Any], momentum: float) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("UNRATE")
    if not v:
        return None
    u = float(v["value"])
    prev = v.get("previous_value")
    rising = prev is not None and u > float(prev)
    falling = prev is not None and u < float(prev)
    if rising and momentum <= 0:
        return (
            "US unemployment rate",
            u,
            "SELL",
            70.0,
            "Unemployment is rising and momentum is weak, a warning sign for growth-sensitive assets.",
        )
    if falling and momentum >= 0:
        return (
            "US unemployment rate",
            u,
            "BUY",
            68.0,
            "Unemployment is falling and momentum is positive, supporting a risk-on view.",
        )
    return (
        "US unemployment rate",
        u,
        "HOLD",
        48.0,
        "Unemployment is in a normal range — neither a clear boom nor a clear bust signal.",
    )


def _curve_rule(m: Dict[str, Any], momentum: float) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    t10 = m.get("TREASURY_10Y")
    t2 = m.get("TREASURY_2Y")
    if not t10 or not t2:
        return None
    y10 = float(t10["value"])
    y2 = float(t2["value"])
    spread = y10 - y2
    if spread < -0.5 and momentum <= 0:
        return (
            "Treasury yield curve (10Y vs 2Y)",
            spread,
            "SELL",
            95.0,
            "The yield curve is deeply inverted and momentum is weak, a classic recession-risk signal.",
        )
    if spread > 0.5 and momentum >= 0:
        return (
            "Treasury yield curve (10Y vs 2Y)",
            spread,
            "BUY",
            72.0,
            "A positive yield curve with supportive momentum suggests a healthier growth backdrop.",
        )
    return (
        "Treasury yield curve (10Y vs 2Y)",
        spread,
        "HOLD",
        42.0,
        "The curve is not deeply inverted or very steep — no extreme bond-market stress signal today.",
    )


def _nfp_rule(m: Dict[str, Any], momentum: float) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("NFP_MOM_THOUSANDS")
    if not v:
        return None
    mom = float(v["value"])
    prev_mom = v.get("previous_value")
    # Prioritize real month-over-month acceleration when available.
    if prev_mom is not None:
        accel = mom - float(prev_mom)
        if accel > 100 and momentum >= 0:
            return (
                "Nonfarm payrolls momentum vs previous month",
                accel,
                "BUY",
                82.0,
                "Payroll momentum accelerated by more than 100k with supportive price momentum, a strong pro-growth signal.",
            )
        if accel < -100 and momentum <= 0:
            return (
                "Nonfarm payrolls momentum vs previous month",
                accel,
                "SELL",
                78.0,
                "Payroll momentum dropped by more than 100k with weak price momentum, pointing to downside macro risk.",
            )
        return (
            "Nonfarm payrolls momentum vs previous month",
            accel,
            "HOLD",
            52.0,
            "Payroll momentum is close to last month, so labor data is not sending a strong directional signal.",
        )

    return None


def _gdp_rule(m: Dict[str, Any], momentum: float) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("GDP_GROWTH")
    if not v:
        return None
    g = float(v["value"])
    prev = v.get("previous_value")
    if g < 0 and prev is not None and float(prev) < 0 and momentum <= 0:
        return (
            "Real GDP growth (quarterly % change)",
            g,
            "SELL",
            78.0,
            "GDP has been negative for two consecutive periods with weak momentum, consistent with recession risk.",
        )
    if g > 2.5 and momentum >= 0:
        return (
            "Real GDP growth (quarterly % change)",
            g,
            "BUY",
            70.0,
            "GDP growth is above 2.5% and momentum is positive, which supports a risk-on stance.",
        )
    return (
        "Real GDP growth (quarterly % change)",
        g,
        "HOLD",
        44.0,
        "GDP growth is moderate — neither a boom nor a contraction signal from this print.",
    )


def run_signal_engine() -> int:
    ensure_intelligence_schema()
    m = _latest_metrics()
    momentum = _risk_momentum_score()
    rules = (_fed_rule, _cpi_rule, _unemployment_rule, _curve_rule, _nfp_rule, _gdp_rule)
    buy_count = 0
    sell_count = 0
    n = 0
    seen = set()
    for fn in rules:
        out = fn(m, momentum)
        if not out:
            continue
        name, val, direction, conf, expl = out
        key = (str(name), str(direction).upper())
        if key in seen:
            logger.warning(
                "Duplicate signal key skipped in run_signal_engine: key=%s rule=%s",
                key,
                getattr(fn, "__name__", "unknown_rule"),
            )
            continue
        seen.add(key)
        if direction == "BUY":
            buy_count += 1
        elif direction == "SELL":
            sell_count += 1
        _insert_signal(name, val, direction, conf, expl)
        n += 1
    if buy_count >= 4:
        _insert_signal(
            "Composite macro regime",
            float(buy_count - sell_count),
            "BUY",
            84.0,
            "At least four macro rules are bullish and confirmed by momentum, producing a strong BUY composite regime.",
        )
        n += 1
    elif sell_count >= 4:
        _insert_signal(
            "Composite macro regime",
            float(buy_count - sell_count),
            "SELL",
            84.0,
            "At least four macro rules are bearish and confirmed by momentum, producing a strong SELL composite regime.",
        )
        n += 1
    else:
        _insert_signal(
            "Composite macro regime",
            float(buy_count - sell_count),
            "HOLD",
            55.0,
            "Macro signals are mixed, so the composite regime remains HOLD.",
        )
        n += 1
    return n


if __name__ == "__main__":
    print(run_signal_engine(), "signals written")
