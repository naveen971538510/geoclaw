"""
Stock Backtest Engine
=====================
Replays quant signals over historical data and reports:
  win rate, Sharpe, max drawdown, profit factor vs buy-and-hold.

Usage:
    from intelligence.stock_backtest import run_backtest
    result = run_backtest("NVDA")
"""

from __future__ import annotations

import math
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import BollingerBands
from ta.volume import OnBalanceVolumeIndicator

logger = logging.getLogger("geoclaw.backtest")

TP_PCT = 0.02   # take profit  +2%
SL_PCT = 0.01   # stop loss    -1%
SIGNAL_BUY_THRESHOLD  = 62
SIGNAL_SELL_THRESHOLD = 38


# ─── Quant score on a single row (vectorised over full history) ───────────────

def _quant_scores(df: pd.DataFrame) -> pd.Series:
    """Return daily quant score series (same logic as stock_quant_engine)."""
    close  = df["Close"]
    volume = df["Volume"]

    rsi = RSIIndicator(close=close, window=14).rsi()
    macd_hist = MACD(close=close).macd_diff()
    bb = BollingerBands(close=close, window=20)
    bb_pct = (close - bb.bollinger_lband()) / (bb.bollinger_hband() - bb.bollinger_lband() + 1e-9)
    vol_ratio = volume / volume.rolling(20).mean()

    def rsi_score(v):
        if v <= 25: return 80
        if v <= 35: return 70
        if v <= 50: return 58
        if v <= 60: return 62
        if v <= 70: return 72
        if v <= 80: return 35
        return 20

    def bb_score(p):
        if p <= 0.05: return 78
        if p <= 0.25: return 65
        if p <= 0.50: return 55
        if p <= 0.75: return 58
        if p <= 0.95: return 45
        return 22

    def vol_score(r):
        if r >= 2.5: return 85
        if r >= 1.5: return 72
        if r >= 1.1: return 60
        if r >= 0.7: return 50
        return 35

    rsi_s  = rsi.apply(rsi_score)
    bb_s   = bb_pct.apply(bb_score)
    vol_s  = vol_ratio.apply(vol_score)

    # MACD score: bullish cross + expanding histogram
    macd_prev = macd_hist.shift(1)
    macd_s = pd.Series(50.0, index=df.index)
    macd_s[macd_hist > 0] = 70
    macd_s[(macd_hist > 0) & (macd_hist > macd_prev)] = 82
    macd_s[macd_hist < 0] = 32
    macd_s[(macd_hist < 0) & (macd_hist < macd_prev)] = 18

    score = rsi_s * 0.30 + macd_s * 0.35 + bb_s * 0.20 + vol_s * 0.15
    return score.round(1)


# ─── Signal simulation ────────────────────────────────────────────────────────

def _simulate_trades(df: pd.DataFrame, scores: pd.Series) -> List[Dict]:
    trades = []
    in_trade = False

    for i in range(30, len(df) - 1):  # need lookback + at least 1 future bar
        score = scores.iloc[i]
        if pd.isna(score) or in_trade:
            continue

        direction = None
        if score >= SIGNAL_BUY_THRESHOLD:
            direction = "BUY"
        elif score <= SIGNAL_SELL_THRESHOLD:
            direction = "SELL"
        if not direction:
            continue

        entry_price = float(df["Close"].iloc[i])
        entry_date  = df.index[i]
        tp = entry_price * (1 + TP_PCT) if direction == "BUY" else entry_price * (1 - TP_PCT)
        sl = entry_price * (1 - SL_PCT) if direction == "BUY" else entry_price * (1 + SL_PCT)

        outcome = "OPEN"
        close_price = None
        close_date  = None
        hold_days   = 0

        for j in range(i + 1, min(i + 6, len(df))):  # max 5 days hold
            hi = float(df["High"].iloc[j])
            lo = float(df["Low"].iloc[j])
            hold_days += 1

            if direction == "BUY":
                if lo <= sl:
                    outcome, close_price, close_date = "LOSS", sl, df.index[j]
                    break
                if hi >= tp:
                    outcome, close_price, close_date = "WIN", tp, df.index[j]
                    break
            else:
                if hi >= sl:
                    outcome, close_price, close_date = "LOSS", sl, df.index[j]
                    break
                if lo <= tp:
                    outcome, close_price, close_date = "WIN", tp, df.index[j]
                    break

        if outcome == "OPEN":
            close_price = float(df["Close"].iloc[min(i + 5, len(df) - 1)])
            close_date  = df.index[min(i + 5, len(df) - 1)]
            pnl_pct = (close_price - entry_price) / entry_price * (1 if direction == "BUY" else -1)
            outcome = "WIN" if pnl_pct > 0 else "LOSS"

        r_multiple = (abs(tp - entry_price) / abs(entry_price - sl)
                      if outcome == "WIN" else -1.0)

        trades.append({
            "ticker": df.attrs.get("ticker", "?"),
            "direction": direction,
            "entry_date": str(entry_date.date()),
            "close_date": str(close_date.date()) if close_date else None,
            "entry": round(entry_price, 4),
            "close": round(close_price, 4) if close_price else None,
            "tp": round(tp, 4), "sl": round(sl, 4),
            "outcome": outcome,
            "r_multiple": round(r_multiple, 3),
            "quant_score": score,
            "hold_days": hold_days,
        })
        in_trade = False  # allow next signal next day

    return trades


# ─── Stats ───────────────────────────────────────────────────────────────────

def _stats(trades: List[Dict], df: pd.DataFrame) -> Dict:
    if not trades:
        return {"error": "No trades generated"}

    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS")]
    total  = len(closed)
    wins   = sum(1 for t in closed if t["outcome"] == "WIN")
    win_rate = round(wins / total * 100, 1) if total else 0

    r_series = [t["r_multiple"] for t in closed]

    # Equity curve (1% risk per trade)
    equity = [100.0]
    for r in r_series:
        equity.append(equity[-1] * (1 + r * 0.01))

    # Sharpe
    mean_r = np.mean(r_series) if r_series else 0
    std_r  = np.std(r_series)  if r_series else 1e-9
    avg_hold = np.mean([t["hold_days"] for t in closed]) or 1
    trades_per_year = 252 / avg_hold
    sharpe = round((mean_r / std_r) * math.sqrt(trades_per_year), 2) if std_r > 0 else 0

    # Max drawdown
    peak, max_dd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak * 100)

    # Profit factor
    gross_win  = sum(r for r in r_series if r > 0)
    gross_loss = abs(sum(r for r in r_series if r < 0))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0

    # Buy-and-hold benchmark
    bh_return = round(
        (float(df["Close"].iloc[-1]) - float(df["Close"].iloc[30])) /
        float(df["Close"].iloc[30]) * 100, 1)

    # Strategy return
    strat_return = round(equity[-1] - 100, 1)

    return {
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "max_drawdown": round(max_dd, 1),
        "profit_factor": profit_factor,
        "strategy_return_pct": strat_return,
        "buyhold_return_pct": bh_return,
        "avg_hold_days": round(avg_hold, 1),
        # Grade on profit factor + Sharpe (win rate alone misleads on 2:1 R:R)
        "grade": (
            "A" if profit_factor >= 1.5 and sharpe >= 1.0 else
            "B" if profit_factor >= 1.2 and sharpe >= 0.5 else
            "C" if profit_factor >= 1.0 else "D"
        ),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_backtest(ticker: str, period: str = "2y") -> Dict[str, Any]:
    ticker = ticker.upper()
    try:
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if df is None or len(df) < 60:
            return {"ticker": ticker, "error": "Insufficient data"}
        df.attrs["ticker"] = ticker

        scores = _quant_scores(df)
        trades = _simulate_trades(df, scores)
        stats  = _stats(trades, df)

        return {
            "ticker": ticker,
            "period": period,
            "backtested_at": datetime.now(timezone.utc).isoformat(),
            **stats,
            "recent_trades": trades[-10:],
        }
    except Exception as exc:
        logger.warning("backtest failed for %s: %s", ticker, exc)
        return {"ticker": ticker, "error": str(exc)}
