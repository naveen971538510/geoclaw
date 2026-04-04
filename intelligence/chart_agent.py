"""
Candlestick pattern detection (yfinance) → Postgres chart_signals.
Runs every 15 minutes during US market hours (Mon–Fri 9:30–16:00 America/New_York).
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

from intelligence.db import ensure_intelligence_schema, get_connection, get_database_url

logger = logging.getLogger("chart_agent")

TICKERS = ["SPY", "QQQ", "BTC-USD", "GLD", "DX-Y.NYB"]
NY = ZoneInfo("America/New_York")
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(16, 0)
INTERVAL_SEC = 15 * 60
SLEEP_OFF_HOURS = 60


def _normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df


def _body_high(o: float, c: float) -> float:
    return max(o, c)


def _body_low(o: float, c: float) -> float:
    return min(o, c)


def _is_bullish(o: float, c: float) -> bool:
    return c > o


def detect_patterns(df: pd.DataFrame) -> List[Dict]:
    """Scan last rows for classic patterns; return list of dicts."""
    df = _normalize_ohlc(df)
    if df is None or len(df) < 5:
        return []
    out: List[Dict] = []
    # use last index as "current" bar where applicable
    i = len(df) - 1
    o, h, l, c = float(df["open"].iloc[i]), float(df["high"].iloc[i]), float(df["low"].iloc[i]), float(df["close"].iloc[i])
    rng = h - l if h > l else 1e-9
    body = abs(c - o)
    upper = h - _body_high(o, c)
    lower = _body_low(o, c) - l

    # Doji
    if body / rng < 0.1:
        out.append({"pattern_name": "Doji", "direction": "NEUTRAL", "confidence": 62.0, "bar_index": i})

    # Hammer / Hanging man (bullish hammer at bottom — simplified: long lower wick)
    if lower > 2 * body and upper < body * 0.3:
        direction = "BULLISH" if not _is_bullish(o, c) or lower > rng * 0.55 else "NEUTRAL"
        out.append({"pattern_name": "Hammer", "direction": direction, "confidence": 68.0, "bar_index": i})

    if i >= 1:
        po, ph, pl, pc = (
            float(df["open"].iloc[i - 1]),
            float(df["high"].iloc[i - 1]),
            float(df["low"].iloc[i - 1]),
            float(df["close"].iloc[i - 1]),
        )
        # Bullish engulfing
        if not _is_bullish(po, pc) and _is_bullish(o, c) and o <= pc and c >= po and body > abs(pc - po) * 1.05:
            out.append({"pattern_name": "Bullish Engulfing", "direction": "BULLISH", "confidence": 76.0, "bar_index": i})
        # Bearish engulfing
        if _is_bullish(po, pc) and not _is_bullish(o, c) and o >= pc and c <= po and body > abs(pc - po) * 1.05:
            out.append({"pattern_name": "Bearish Engulfing", "direction": "BEARISH", "confidence": 76.0, "bar_index": i})

    if i >= 2:
        b0 = df.iloc[i - 2]
        b1 = df.iloc[i - 1]
        b2 = df.iloc[i]
        o0, c0 = float(b0["open"]), float(b0["close"])
        o1, c1 = float(b1["open"]), float(b1["close"])
        o2, c2 = float(b2["open"]), float(b2["close"])
        h1, l1 = float(b1["high"]), float(b1["low"])

        # Morning star (simplified)
        rng0 = float(b0["high"]) - float(b0["low"]) or 1e-9
        long_red = c0 < o0 and (o0 - c0) > 0.4 * rng0
        rng1 = float(b1["high"]) - float(b1["low"]) or 1e-9
        small_mid = abs(c1 - o1) < 0.35 * max(rng0, rng1, 1e-9)
        long_green = c2 > o2 and (c2 - o2) > 0.35 * rng0 and c2 > (o0 + c0) / 2
        if long_red and small_mid and long_green and l1 <= min(float(b0["low"]), float(b2["low"])) + 1e-6:
            out.append({"pattern_name": "Morning Star", "direction": "BULLISH", "confidence": 81.0, "bar_index": i})

        # Evening star (simplified)
        long_green0 = c0 > o0
        long_red2 = c2 < o2
        if long_green0 and small_mid and long_red2 and float(b1["high"]) > max(float(b0["high"]), float(b2["high"])):
            out.append({"pattern_name": "Evening Star", "direction": "BEARISH", "confidence": 81.0, "bar_index": i})

    if i >= 2:
        # Three White Soldiers
        ok = True
        for k in range(3):
            row = df.iloc[i - 2 + k]
            ok = ok and float(row["close"]) > float(row["open"])
        if ok:
            opens = [float(df["open"].iloc[i - 2 + k]) for k in range(3)]
            closes = [float(df["close"].iloc[i - 2 + k]) for k in range(3)]
            if all(closes[j] > opens[j] for j in range(3)) and closes[1] > closes[0] and closes[2] > closes[1]:
                out.append({"pattern_name": "Three White Soldiers", "direction": "BULLISH", "confidence": 84.0, "bar_index": i})

        # Three Black Crows
        okb = True
        for k in range(3):
            row = df.iloc[i - 2 + k]
            okb = okb and float(row["close"]) < float(row["open"])
        if okb:
            closes = [float(df["close"].iloc[i - 2 + k]) for k in range(3)]
            if closes[1] < closes[0] and closes[2] < closes[1]:
                out.append({"pattern_name": "Three Black Crows", "direction": "BEARISH", "confidence": 84.0, "bar_index": i})

    # Deduplicate same pattern name (keep highest confidence)
    seen = {}
    for p in out:
        key = p["pattern_name"]
        if key not in seen or p["confidence"] > seen[key]["confidence"]:
            seen[key] = p
    return list(seen.values())


def _store_patterns(ticker: str, patterns: List[Dict]) -> int:
    if not patterns:
        return 0
    now = datetime.now(timezone.utc)
    n = 0
    with get_connection() as conn:
        cur = conn.cursor()
        for p in patterns:
            cur.execute(
                """
                INSERT INTO chart_signals (ticker, pattern_name, direction, confidence, detected_at, bar_index)
                VALUES (%s, %s, %s, %s, %s, %s);
                """,
                (
                    ticker,
                    p["pattern_name"],
                    p["direction"],
                    float(p["confidence"]),
                    now,
                    p.get("bar_index"),
                ),
            )
            n += 1
        cur.close()
    return n


def _in_market_hours() -> bool:
    now = datetime.now(NY)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def run_scan_once() -> int:
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not set")
    ensure_intelligence_schema()
    total = 0
    for sym in TICKERS:
        try:
            tkr = yf.Ticker(sym)
            df = tkr.history(period="6mo", interval="1d", auto_adjust=False)
            if df is None or df.empty:
                continue
            df = df.dropna()
            pats = detect_patterns(df)
            total += _store_patterns(sym, pats)
        except Exception as exc:
            logger.warning("chart scan failed for %s: %s", sym, exc)
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("chart_agent started")
    while True:
        try:
            if _in_market_hours():
                n = run_scan_once()
                logger.info("chart scan stored %s pattern rows", n)
                time.sleep(INTERVAL_SEC)
            else:
                time.sleep(SLEEP_OFF_HOURS)
        except Exception as exc:
            logger.exception("chart_agent loop error: %s", exc)
            time.sleep(SLEEP_OFF_HOURS)


if __name__ == "__main__":
    main()
