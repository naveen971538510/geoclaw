"""
Score macro_signals → geoclaw_signals (0–100 impact, direction, plain-English explanation).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.db import ensure_intelligence_schema, get_connection, get_database_url, query_all

NFP_EXPECTED_MOM_K = float(os.environ.get("NFP_EXPECTED_MOM_K", "175") or 175)


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


def _insert_signal(
    name: str,
    value: Optional[float],
    direction: str,
    confidence: float,
    explanation: str,
) -> None:
    confidence = max(0.0, min(100.0, float(confidence)))
    direction = direction.upper()
    if direction not in ("BULLISH", "BEARISH", "NEUTRAL"):
        direction = "NEUTRAL"
    ts = datetime.now(timezone.utc)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO geoclaw_signals (signal_name, value, direction, confidence, explanation_plain_english, ts)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (name, value, direction, confidence, explanation[:2000], ts),
        )
        cur.close()


def _fed_rule(m: Dict[str, Any]) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("FEDFUNDS")
    if not v:
        return None
    cur = float(v["value"])
    prev = v.get("previous_value")
    if prev is None:
        return None
    delta = cur - float(prev)
    if delta > 0.25:
        return (
            "Federal Reserve policy rate",
            delta,
            "BEARISH",
            92.0,
            "The Fed pushed its policy rate up by more than a quarter point — that makes borrowing costlier and usually pressures stocks and growth.",
        )
    if delta < -0.08:
        return (
            "Federal Reserve policy rate",
            delta,
            "BULLISH",
            88.0,
            "The Fed cut interest rates — cheaper borrowing tends to support stocks and economic activity.",
        )
    return (
        "Federal Reserve policy rate",
        delta,
        "NEUTRAL",
        45.0,
        "Fed funds were roughly steady — no big rate shock for markets right now.",
    )


def _cpi_rule(m: Dict[str, Any]) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("CPI_YOY_PCT")
    if not v:
        return None
    yoy = float(v["value"])
    if yoy > 3.5:
        return (
            "Consumer inflation (CPI, year over year)",
            yoy,
            "BEARISH",
            85.0,
            "Inflation is running above about 3.5% year over year — that can keep the Fed hawkish and hurt valuations.",
        )
    if yoy < 2.0:
        return (
            "Consumer inflation (CPI, year over year)",
            yoy,
            "BULLISH",
            80.0,
            "Inflation is tame (under 2% YoY) — that gives the Fed room to ease and tends to support risk assets.",
        )
    return (
        "Consumer inflation (CPI, year over year)",
        yoy,
        "NEUTRAL",
        50.0,
        "Inflation is in a middle zone — not hot enough to panic, not cold enough to declare victory.",
    )


def _unemployment_rule(m: Dict[str, Any]) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("UNRATE")
    if not v:
        return None
    u = float(v["value"])
    if u > 5.0:
        return (
            "US unemployment rate",
            u,
            "BEARISH",
            70.0,
            "Unemployment is above 5% — a softer job market usually means weaker consumer spending and recession worries.",
        )
    if u < 4.0:
        return (
            "US unemployment rate",
            u,
            "BULLISH",
            68.0,
            "Unemployment is low — a tight labor market supports spending and can lift corporate earnings.",
        )
    return (
        "US unemployment rate",
        u,
        "NEUTRAL",
        48.0,
        "Unemployment is in a normal range — neither a clear boom nor a clear bust signal.",
    )


def _curve_rule(m: Dict[str, Any]) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    t10 = m.get("TREASURY_10Y")
    t2 = m.get("TREASURY_2Y")
    if not t10 or not t2:
        return None
    y10 = float(t10["value"])
    y2 = float(t2["value"])
    spread = y10 - y2
    if y10 < y2:
        return (
            "Treasury yield curve (10Y vs 2Y)",
            spread,
            "BEARISH",
            95.0,
            "Short-term rates are above long-term rates (inverted curve) — markets often read that as a recession warning.",
        )
    if spread > 0.75:
        return (
            "Treasury yield curve (10Y vs 2Y)",
            spread,
            "BULLISH",
            72.0,
            "The yield curve is steep — long rates well above short rates usually signals growth expectations, not imminent recession.",
        )
    return (
        "Treasury yield curve (10Y vs 2Y)",
        spread,
        "NEUTRAL",
        42.0,
        "The curve is not deeply inverted or very steep — no extreme bond-market stress signal today.",
    )


def _nfp_rule(m: Dict[str, Any]) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("NFP_MOM_THOUSANDS")
    if not v:
        return None
    mom = float(v["value"])
    surprise = mom - NFP_EXPECTED_MOM_K
    if surprise < -50:
        return (
            "Nonfarm payrolls surprise vs typical expectation",
            surprise,
            "BEARISH",
            75.0,
            "Job growth missed expectations by a wide margin (roughly 50k+ jobs short) — that points to a cooling labor market and growth worries.",
        )
    if surprise > 50:
        return (
            "Nonfarm payrolls surprise vs typical expectation",
            surprise,
            "BULLISH",
            74.0,
            "Job growth beat expectations strongly — a hotter labor market supports spending but can keep the Fed cautious on cuts.",
        )
    return (
        "Nonfarm payrolls surprise vs typical expectation",
        surprise,
        "NEUTRAL",
        46.0,
        "Payrolls were close to what markets typically expect — no big jobs shock this print.",
    )


def _gdp_rule(m: Dict[str, Any]) -> Optional[Tuple[str, Optional[float], str, float, str]]:
    v = m.get("GDP_GROWTH")
    if not v:
        return None
    g = float(v["value"])
    if g < 0:
        return (
            "Real GDP growth (quarterly % change)",
            g,
            "BEARISH",
            78.0,
            "The economy shrank last quarter — negative GDP growth is a classic recession red flag for investors.",
        )
    if g > 3.0:
        return (
            "Real GDP growth (quarterly % change)",
            g,
            "BULLISH",
            70.0,
            "GDP is growing briskly — a strong economy supports earnings, though it can keep inflation pressure in focus.",
        )
    return (
        "Real GDP growth (quarterly % change)",
        g,
        "NEUTRAL",
        44.0,
        "GDP growth is moderate — neither a boom nor a contraction signal from this print.",
    )


def run_signal_engine() -> int:
    ensure_intelligence_schema()
    m = _latest_metrics()
    rules = (_fed_rule, _cpi_rule, _unemployment_rule, _curve_rule, _nfp_rule, _gdp_rule)
    n = 0
    for fn in rules:
        out = fn(m)
        if not out:
            continue
        name, val, direction, conf, expl = out
        _insert_signal(name, val, direction, conf, expl)
        n += 1
    return n


if __name__ == "__main__":
    print(run_signal_engine(), "signals written")
