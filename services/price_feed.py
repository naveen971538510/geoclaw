import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH
from services.logging_service import get_logger


logger = get_logger("price_feed")

WATCHLIST_SYMBOLS = {
    "^N225": {"name": "Japan 225 CFD", "category": "equity"},
}


class PriceFeed:
    def __init__(self):
        try:
            import yfinance as yf

            self._yf = yf
            self._available = True
        except ImportError:
            self._yf = None
            self._available = False
            logger.warning("yfinance not installed — price feed disabled. pip install yfinance")

    def get_snapshot(self, symbols: list = None) -> dict:
        if not self._available:
            return {}
        target = symbols or list(WATCHLIST_SYMBOLS.keys())
        result = {}
        try:
            tickers = self._yf.Tickers(" ".join(target))
            for symbol in target:
                try:
                    info = tickers.tickers[symbol].fast_info
                    meta = WATCHLIST_SYMBOLS.get(symbol, {"name": symbol, "category": "unknown"})
                    price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
                    prev = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)
                    change_pct = ((price - prev) / prev) * 100 if price and prev and prev > 0 else 0.0
                    result[symbol] = {
                        "symbol": symbol,
                        "name": meta["name"],
                        "category": meta["category"],
                        "price": round(float(price), 4) if price is not None else None,
                        "change_pct": round(float(change_pct), 2),
                        "direction": "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                except Exception as exc:
                    logger.debug("Price fetch failed for %s: %s", symbol, exc)
        except Exception as exc:
            logger.error("Batch price fetch failed: %s", exc)
        return result

    def get_price(self, symbol: str) -> Optional[dict]:
        return self.get_snapshot([symbol]).get(symbol)

    def save_snapshot(self, db_path: str = None):
        snapshot = self.get_snapshot()
        if not snapshot:
            return 0
        conn = sqlite3.connect(str(db_path or DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        count = 0
        for symbol, data in snapshot.items():
            try:
                conn.execute(
                    """
                    INSERT INTO price_snapshots (symbol, name, category, price, change_pct, direction, captured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        data["name"],
                        data["category"],
                        data["price"],
                        data["change_pct"],
                        data["direction"],
                        data["timestamp"],
                    ),
                )
                count += 1
            except Exception as exc:
                logger.debug("Skipping snapshot for %s: %s", symbol, exc)
        conn.commit()
        conn.close()
        return count

    def get_thesis_relevant_prices(self, thesis_key: str) -> list:
        key = str(thesis_key or "").lower()
        keyword_symbol_map = {
            "oil": ["CL=F", "XLE", "GC=F"],
            "gold": ["GC=F", "GLD"],
            "iran": ["CL=F", "GC=F", "^VIX"],
            "dollar": ["DX-Y.NYB", "EURUSD=X", "GC=F"],
            "fed": ["^TNX", "DX-Y.NYB", "SPY"],
            "rate": ["^TNX", "^TYX", "XLF"],
            "china": ["USDCNH=X", "EEM", "CL=F"],
            "war": ["^VIX", "GC=F", "CL=F"],
            "recession": ["^VIX", "^TNX", "SPY", "EEM"],
            "inflation": ["GC=F", "CL=F", "^TNX"],
            "crypto": ["BTC-USD"],
            "emerging": ["EEM", "DX-Y.NYB"],
            "sanction": ["DX-Y.NYB", "EEM", "CL=F"],
        }
        symbols_needed = set()
        for keyword, symbols in keyword_symbol_map.items():
            if keyword in key:
                symbols_needed.update(symbols)
        if not symbols_needed:
            symbols_needed = {"^VIX", "GC=F", "SPY"}
        snapshot = self.get_snapshot(list(symbols_needed))
        return list(snapshot.values())
