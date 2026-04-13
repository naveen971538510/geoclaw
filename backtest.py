"""
GeoClaw Backtesting Engine
Reads historical predictions from thesis_predictions table and computes
P&L, win rate, Sharpe ratio, and per-asset/per-confidence breakdowns.
"""
import math
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List

from config import DB_PATH


def _query(sql: str, params: tuple = ()) -> List[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_backtest(
    min_confidence: float = 0.0,
    symbol_filter: str = "",
    limit: int = 500,
) -> Dict:
    """
    Core backtest: compute P&L curve and stats from closed predictions.

    Args:
        min_confidence: only include predictions >= this confidence (0.0–1.0)
        symbol_filter: restrict to a specific symbol (e.g. 'GLD'), empty = all
        limit: max rows to analyse

    Returns:
        dict with stats, equity_curve, per_asset, per_confidence_bucket breakdowns
    """
    sql = """
        SELECT
            id, thesis_key, symbol, predicted_direction,
            confidence_at_prediction, price_at_prediction, price_at_check,
            actual_change_pct, outcome, outcome_note, predicted_at, checked_at
        FROM thesis_predictions
        WHERE COALESCE(outcome, 'pending') NOT IN ('pending', '')
          AND confidence_at_prediction >= ?
    """
    params: list = [float(min_confidence)]
    if symbol_filter:
        sql += " AND symbol = ?"
        params.append(symbol_filter.upper())
    sql += " ORDER BY predicted_at ASC, id ASC LIMIT ?"
    params.append(int(limit))

    rows = _query(sql, tuple(params))
    if not rows:
        return _empty_result()

    # ── Core stats ────────────────────────────────────────────────────
    verified = sum(1 for r in rows if r.get("outcome") == "verified")
    refuted  = sum(1 for r in rows if r.get("outcome") == "refuted")
    neutral  = sum(1 for r in rows if r.get("outcome") == "neutral")
    closed   = verified + refuted        # exclude neutrals from win rate
    win_rate = (verified / closed * 100) if closed else 0.0

    # Simulated returns: +actual_change_pct for correct, -actual_change_pct for wrong
    returns: List[float] = []
    equity  = 100.0
    equity_curve: List[dict] = []

    for r in rows:
        chg = float(r.get("actual_change_pct") or 0.0)
        direction = str(r.get("predicted_direction") or "risk_up")
        outcome   = str(r.get("outcome") or "neutral")

        if outcome == "neutral":
            ret = 0.0
        elif outcome == "verified":
            ret = abs(chg)
        else:  # refuted
            ret = -abs(chg)

        returns.append(ret)
        equity = equity * (1 + ret / 100)
        equity_curve.append({
            "id": r.get("id"),
            "date": str(r.get("checked_at") or r.get("predicted_at") or ""),
            "symbol": r.get("symbol", ""),
            "outcome": outcome,
            "ret_pct": round(ret, 2),
            "equity": round(equity, 2),
        })

    total_return_pct = round(equity - 100, 2)
    avg_return       = round(sum(returns) / len(returns), 3) if returns else 0.0
    avg_win          = round(
        sum(r for r in returns if r > 0) / max(sum(1 for r in returns if r > 0), 1), 3
    )
    avg_loss         = round(
        sum(r for r in returns if r < 0) / max(sum(1 for r in returns if r < 0), 1), 3
    )
    profit_factor    = round(
        abs(sum(r for r in returns if r > 0)) / max(abs(sum(r for r in returns if r < 0)), 0.001), 2
    )

    # Sharpe (annualised, assume 1 trade/day)
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.001
        sharpe = round((mean / std) * math.sqrt(252), 2)
    else:
        sharpe = 0.0

    # Max drawdown
    peak = 100.0
    max_dd = 0.0
    running = 100.0
    for r in returns:
        running = running * (1 + r / 100)
        if running > peak:
            peak = running
        dd = (peak - running) / peak * 100
        if dd > max_dd:
            max_dd = dd
    max_dd = round(max_dd, 2)

    # ── Per-asset breakdown ───────────────────────────────────────────
    asset_stats: Dict[str, dict] = {}
    for r in rows:
        sym = str(r.get("symbol") or "UNKNOWN")
        if sym not in asset_stats:
            asset_stats[sym] = {"symbol": sym, "total": 0, "verified": 0, "refuted": 0, "neutral": 0}
        asset_stats[sym]["total"] += 1
        outcome = str(r.get("outcome") or "neutral")
        asset_stats[sym][outcome] = asset_stats[sym].get(outcome, 0) + 1
    for v in asset_stats.values():
        c = v["verified"] + v["refuted"]
        v["win_rate"] = round(v["verified"] / c * 100, 1) if c else 0.0
    per_asset = sorted(asset_stats.values(), key=lambda x: x["total"], reverse=True)

    # ── Per-confidence-bucket breakdown ──────────────────────────────
    buckets = {"65-70": [], "70-80": [], "80-90": [], "90+": []}
    for r in rows:
        conf = float(r.get("confidence_at_prediction") or 0) * 100
        outcome = str(r.get("outcome") or "neutral")
        if conf < 70:
            buckets["65-70"].append(outcome)
        elif conf < 80:
            buckets["70-80"].append(outcome)
        elif conf < 90:
            buckets["80-90"].append(outcome)
        else:
            buckets["90+"].append(outcome)

    per_confidence = []
    for label, outcomes in buckets.items():
        v = outcomes.count("verified")
        rf = outcomes.count("refuted")
        c = v + rf
        per_confidence.append({
            "bucket": label,
            "total": len(outcomes),
            "verified": v,
            "refuted": rf,
            "neutral": outcomes.count("neutral"),
            "win_rate": round(v / c * 100, 1) if c else 0.0,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {"min_confidence": min_confidence, "symbol": symbol_filter or "all"},
        "stats": {
            "total_predictions": len(rows),
            "verified": verified,
            "refuted": refuted,
            "neutral": neutral,
            "closed": closed,
            "win_rate_pct": round(win_rate, 1),
            "total_return_pct": total_return_pct,
            "avg_return_pct": avg_return,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": max_dd,
        },
        "equity_curve": equity_curve,
        "per_asset": per_asset,
        "per_confidence": per_confidence,
    }


def _empty_result() -> Dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "total_predictions": 0, "verified": 0, "refuted": 0, "neutral": 0,
            "closed": 0, "win_rate_pct": 0.0, "total_return_pct": 0.0,
            "avg_return_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "profit_factor": 0.0, "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0,
        },
        "equity_curve": [],
        "per_asset": [],
        "per_confidence": [],
        "message": "No closed predictions yet — run the agent a few cycles to build history.",
    }


if __name__ == "__main__":
    import json
    result = run_backtest()
    print(json.dumps(result, indent=2, default=str))
