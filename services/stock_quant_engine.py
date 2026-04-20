"""
Stock Quant Engine
==================
Calculates RSI, MACD, Bollinger Bands, and Volume analysis for any ticker.
Returns a structured quant score (0–100) with per-indicator breakdown.

Usage:
    from services.stock_quant_engine import run_quant_analysis
    result = run_quant_analysis("NVDA")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import BollingerBands
from ta.volume import OnBalanceVolumeIndicator

logger = logging.getLogger("geoclaw.quant")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fetch_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty or len(df) < 20:
        raise RuntimeError(f"Insufficient OHLCV data for {ticker}")
    df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close", "volume"]].dropna()


def _score_rsi(rsi_val: float) -> tuple[float, str]:
    """Score RSI 0–100. Returns (score, label)."""
    if rsi_val <= 25:
        return 80.0, "Oversold — potential bounce"
    if rsi_val <= 35:
        return 70.0, "Approaching oversold"
    if rsi_val <= 50:
        return 58.0, "Neutral-bearish territory"
    if rsi_val <= 60:
        return 62.0, "Neutral-bullish territory"
    if rsi_val <= 70:
        return 72.0, "Bullish momentum"
    if rsi_val <= 80:
        return 35.0, "Overbought — caution"
    return 20.0, "Extremely overbought"


def _score_macd(hist_now: float, hist_prev: float, macd_val: float, signal_val: float) -> tuple[float, str]:
    """Score MACD 0–100."""
    bullish_cross = macd_val > signal_val
    hist_growing = hist_now > hist_prev

    if bullish_cross and hist_growing and hist_now > 0:
        return 82.0, "Bullish crossover, histogram expanding"
    if bullish_cross and hist_now > 0:
        return 70.0, "Bullish crossover"
    if bullish_cross and hist_growing:
        return 62.0, "Bullish cross, momentum building"
    if not bullish_cross and not hist_growing and hist_now < 0:
        return 18.0, "Bearish, histogram deepening"
    if not bullish_cross:
        return 32.0, "Bearish crossover"
    return 50.0, "Neutral"


def _score_bollinger(price: float, upper: float, lower: float, mid: float) -> tuple[float, str]:
    """Score Bollinger Bands position 0–100."""
    band_width = upper - lower
    if band_width == 0:
        return 50.0, "Bands flat"
    pct_b = (price - lower) / band_width  # 0=lower band, 1=upper band

    if pct_b <= 0.05:
        return 78.0, "Price at lower band — oversold"
    if pct_b <= 0.25:
        return 65.0, "Price below midline — bullish potential"
    if pct_b <= 0.50:
        return 55.0, "Price below midline"
    if pct_b <= 0.75:
        return 58.0, "Price above midline"
    if pct_b <= 0.95:
        return 45.0, "Price near upper band"
    return 22.0, "Price at upper band — overbought"


def _score_volume(vol_now: float, vol_avg: float) -> tuple[float, str]:
    """Score volume relative to 20-day average."""
    if vol_avg == 0:
        return 50.0, "No volume baseline"
    ratio = vol_now / vol_avg
    if ratio >= 2.5:
        return 85.0, f"Volume {ratio:.1f}x avg — very high interest"
    if ratio >= 1.5:
        return 72.0, f"Volume {ratio:.1f}x avg — elevated"
    if ratio >= 1.1:
        return 60.0, f"Volume {ratio:.1f}x avg — normal-high"
    if ratio >= 0.7:
        return 50.0, f"Volume {ratio:.1f}x avg — normal"
    return 35.0, f"Volume {ratio:.1f}x avg — low interest"


# ─── Main Engine ─────────────────────────────────────────────────────────────

def run_quant_analysis(ticker: str) -> Dict[str, Any]:
    """
    Run full quant analysis on a ticker.
    Returns dict with per-indicator scores and a combined quant_score (0–100).
    """
    ticker = ticker.upper().strip()
    try:
        df = _fetch_ohlcv(ticker)
    except Exception as exc:
        logger.warning("quant fetch failed for %s: %s", ticker, exc)
        return {"ticker": ticker, "error": str(exc), "quant_score": None}

    close = df["close"]
    volume = df["volume"]

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi_series = RSIIndicator(close=close, window=14).rsi()
    rsi_val = float(rsi_series.dropna().iloc[-1])
    rsi_score, rsi_label = _score_rsi(rsi_val)

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_obj = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd_obj.macd()
    signal_line = macd_obj.macd_signal()
    hist = macd_obj.macd_diff()
    hist_now = float(hist.dropna().iloc[-1])
    hist_prev = float(hist.dropna().iloc[-2]) if len(hist.dropna()) >= 2 else hist_now
    macd_val = float(macd_line.dropna().iloc[-1])
    signal_val = float(signal_line.dropna().iloc[-1])
    macd_score, macd_label = _score_macd(hist_now, hist_prev, macd_val, signal_val)

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb = BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = float(bb.bollinger_hband().dropna().iloc[-1])
    bb_lower = float(bb.bollinger_lband().dropna().iloc[-1])
    bb_mid   = float(bb.bollinger_mavg().dropna().iloc[-1])
    price_now = float(close.iloc[-1])
    bb_score, bb_label = _score_bollinger(price_now, bb_upper, bb_lower, bb_mid)

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_now = float(volume.iloc[-1])
    vol_avg = float(volume.rolling(20).mean().dropna().iloc[-1])
    vol_score, vol_label = _score_volume(vol_now, vol_avg)

    # ── Combined Score (weighted) ─────────────────────────────────────────────
    # RSI 30% | MACD 35% | Bollinger 20% | Volume 15%
    quant_score = round(
        rsi_score * 0.30 +
        macd_score * 0.35 +
        bb_score * 0.20 +
        vol_score * 0.15,
        1,
    )

    # ── Direction label ───────────────────────────────────────────────────────
    if quant_score >= 72:
        direction = "BULLISH"
    elif quant_score >= 58:
        direction = "NEUTRAL-BULLISH"
    elif quant_score >= 42:
        direction = "NEUTRAL"
    elif quant_score >= 28:
        direction = "NEUTRAL-BEARISH"
    else:
        direction = "BEARISH"

    return {
        "ticker": ticker,
        "price": round(price_now, 4),
        "quant_score": quant_score,
        "direction": direction,
        "analysed_at": datetime.now(timezone.utc).isoformat(),
        "indicators": {
            "rsi": {
                "value": round(rsi_val, 2),
                "score": rsi_score,
                "label": rsi_label,
            },
            "macd": {
                "macd": round(macd_val, 4),
                "signal": round(signal_val, 4),
                "histogram": round(hist_now, 4),
                "score": macd_score,
                "label": macd_label,
            },
            "bollinger": {
                "upper": round(bb_upper, 4),
                "mid": round(bb_mid, 4),
                "lower": round(bb_lower, 4),
                "price": round(price_now, 4),
                "score": bb_score,
                "label": bb_label,
            },
            "volume": {
                "current": int(vol_now),
                "avg_20d": int(vol_avg),
                "score": vol_score,
                "label": vol_label,
            },
        },
    }


# ─── Batch runner ─────────────────────────────────────────────────────────────

def run_quant_batch(tickers: list[str]) -> list[Dict[str, Any]]:
    """Run quant analysis on multiple tickers."""
    results = []
    for t in tickers:
        try:
            results.append(run_quant_analysis(t))
        except Exception as exc:
            logger.warning("batch quant failed for %s: %s", t, exc)
            results.append({"ticker": t, "error": str(exc), "quant_score": None})
    return results
