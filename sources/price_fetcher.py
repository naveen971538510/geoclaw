"""
Price fetcher for BTC/USD, XAU/USD (proxy), and S&P 500.
Stores prices in Postgres table price_data.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.db import ensure_intelligence_schema, get_connection, get_database_url

logger = logging.getLogger("price_fetcher")

TICKER_MAP = {
    "BTCUSD": "BTC-USD",
    "XAUUSD": "GC=F",   # Gold futures proxy
    "SPX": "^GSPC",
}


def fetch_latest_price(symbol: str) -> float:
    hist = yf.Ticker(symbol).history(period="2d", interval="1m", auto_adjust=True)
    if hist is None or hist.empty:
        raise RuntimeError(f"No price history for {symbol}")
    return float(hist["Close"].dropna().iloc[-1])


def store_price(ticker: str, price: float) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO price_data (ticker, price, ts)
            VALUES (%s, %s, %s);
            """,
            (ticker, float(price), datetime.now(timezone.utc)),
        )
        cur.close()


def fetch_and_store_prices() -> int:
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not set")
    ensure_intelligence_schema()
    n = 0
    for ticker, yf_symbol in TICKER_MAP.items():
        try:
            px = fetch_latest_price(yf_symbol)
            store_price(ticker, px)
            n += 1
        except Exception as exc:
            logger.warning("price fetch failed for %s (%s): %s", ticker, yf_symbol, exc)
    return n

