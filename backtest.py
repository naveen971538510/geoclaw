"""
GeoClaw backtest engine.

Reads signals.csv (timestamp, ticker, entry, tp, sl, signal) and validates
each trade against 5-day 1-hour yfinance bars.

Metrics computed:
  - Win / loss / open counts and win rate
  - Realised Sharpe ratio from actual close timestamps (not synthetic 1-trade/day)
  - Max drawdown on the running equity curve
  - Profit factor (gross wins / gross losses)
"""
import math
import sys

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Load signals
# ---------------------------------------------------------------------------
signals = pd.read_csv("signals.csv")
signals["timestamp"] = pd.to_datetime(signals["timestamp"], utc=True)

results = []

for _, row in signals.iterrows():
    ticker = row["ticker"]
    entry = float(row["entry"])
    tp = float(row["tp"])
    sl = float(row["sl"])
    direction = row["signal"]  # "BUY" or "SELL"
    ts = row["timestamp"]

    df = yf.download(ticker, start=ts, period="5d", interval="1h", progress=False)
    if df is None or df.empty:
        continue

    outcome = "OPEN"
    close_ts = None
    close_price = None

    for bar_ts, candle in df.iterrows():
        high = float(candle["High"])
        low = float(candle["Low"])
        close = float(candle["Close"])

        if direction == "BUY":
            if low <= sl:
                outcome = "LOSS"
                close_ts = bar_ts
                close_price = sl
                break
            if high >= tp:
                outcome = "WIN"
                close_ts = bar_ts
                close_price = tp
                break
        elif direction == "SELL":
            if high >= sl:
                outcome = "LOSS"
                close_ts = bar_ts
                close_price = sl
                break
            if low <= tp:
                outcome = "WIN"
                close_ts = bar_ts
                close_price = tp
                break

    # R-multiple: +1 for win (TP hit), -1 for loss (SL hit), 0 for open
    if outcome == "WIN":
        r_multiple = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 1.0
    elif outcome == "LOSS":
        r_multiple = -1.0
    else:
        r_multiple = 0.0

    results.append(
        {
            "ticker": ticker,
            "signal": direction,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "open_ts": ts,
            "close_ts": close_ts,
            "close_price": close_price,
            "outcome": outcome,
            "r_multiple": r_multiple,
        }
    )

df_results = pd.DataFrame(results)
df_results.to_csv("backtest_results.csv", index=False)

# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------
closed = df_results[df_results["outcome"] != "OPEN"]
total = len(closed)
wins = int((closed["outcome"] == "WIN").sum())
losses = int((closed["outcome"] == "LOSS").sum())
win_rate = (wins / total * 100) if total > 0 else 0.0

# --- Realised Sharpe from actual close timestamps ---
# Use R-multiples as the return series, scaled by actual elapsed hours so
# frequency differences don't inflate/deflate annualised vol.
sharpe = float("nan")
max_dd_pct = 0.0
profit_factor = 0.0

if total >= 3:
    closed_sorted = closed.dropna(subset=["close_ts"]).sort_values("close_ts")
    r_series = closed_sorted["r_multiple"].tolist()

    # Equity curve (100-base)
    equity = [100.0]
    for r in r_series:
        equity.append(equity[-1] * (1 + r * 0.01))  # 1% risk per trade

    # Realised Sharpe: annualise using actual average hold time
    if len(closed_sorted) >= 2 and closed_sorted["open_ts"].notna().all() and closed_sorted["close_ts"].notna().all():
        hold_hours = (
            (pd.to_datetime(closed_sorted["close_ts"]) - pd.to_datetime(closed_sorted["open_ts"]))
            .dt.total_seconds()
            .div(3600)
            .clip(lower=0.1)
        )
        avg_hold_hours = float(hold_hours.mean())
        trades_per_year = 8760 / avg_hold_hours  # hours in a year / avg hold
        mean_r = sum(r_series) / len(r_series)
        var_r = sum((r - mean_r) ** 2 for r in r_series) / len(r_series)
        std_r = math.sqrt(var_r) if var_r > 0 else 1e-9
        sharpe = (mean_r / std_r) * math.sqrt(trades_per_year)

    # Max drawdown
    peak = equity[0]
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd_pct:
            max_dd_pct = dd

    # Profit factor
    gross_wins = sum(r for r in r_series if r > 0)
    gross_losses = abs(sum(r for r in r_series if r < 0))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

print(f"\n=== BACKTEST RESULTS ===")
print(f"Total closed:   {total}")
print(f"Wins:           {wins}")
print(f"Losses:         {losses}")
print(f"Win rate:       {win_rate:.1f}%")
print(f"Open:           {len(df_results[df_results['outcome'] == 'OPEN'])}")
print(f"Sharpe (real):  {sharpe:.2f}" if not math.isnan(sharpe) else "Sharpe:         n/a (need ≥3 closed trades with timestamps)")
print(f"Max drawdown:   {max_dd_pct:.1f}%")
print(f"Profit factor:  {profit_factor:.2f}" if profit_factor != float("inf") else "Profit factor:  ∞ (no losses)")
print(f"\nTarget: 55%+ usable | 60%+ strong | Sharpe > 1.0 desirable")
print(f"Saved to backtest_results.csv")
