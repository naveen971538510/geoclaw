import pandas as pd
import yfinance as yf

# Load your signals
signals = pd.read_csv("signals.csv")
signals["timestamp"] = pd.to_datetime(signals["timestamp"])

results = []

for _, row in signals.iterrows():
    ticker = row["ticker"]
    entry = float(row["entry"])
    tp = float(row["tp"])
    sl = float(row["sl"])
    direction = row["signal"]  # "BUY" or "SELL"
    ts = row["timestamp"]

    # Fetch price data after signal
    df = yf.download(ticker, start=ts, period="5d", interval="1h", progress=False)
    if df.empty:
        continue

    outcome = "OPEN"
    for _, candle in df.iterrows():
        high = candle["High"]
        low = candle["Low"]

        if direction == "BUY":
            if low <= sl:
                outcome = "LOSS"; break
            if high >= tp:
                outcome = "WIN"; break
        elif direction == "SELL":
            if high >= sl:
                outcome = "LOSS"; break
            if low <= tp:
                outcome = "WIN"; break

    results.append({
        "ticker": ticker,
        "signal": direction,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "timestamp": ts,
        "outcome": outcome
    })

df_results = pd.DataFrame(results)
df_results.to_csv("backtest_results.csv", index=False)

# Summary
total = len(df_results[df_results["outcome"] != "OPEN"])
wins = len(df_results[df_results["outcome"] == "WIN"])
losses = len(df_results[df_results["outcome"] == "LOSS"])
win_rate = (wins / total * 100) if total > 0 else 0

print(f"\n=== BACKTEST RESULTS ===")
print(f"Total closed: {total}")
print(f"Wins:         {wins}")
print(f"Losses:       {losses}")
print(f"Win rate:     {win_rate:.1f}%")
print(f"Open:         {len(df_results[df_results['outcome'] == 'OPEN'])}")
print(f"\nTarget: 55%+ usable | 60%+ strong")
print(f"Saved to backtest_results.csv")
