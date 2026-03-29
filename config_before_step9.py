from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "geoclaw.db"

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()
GUARDIAN_API_KEY = os.getenv("GUARDIAN_API_KEY", "").strip()
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY", "").strip()

ENABLE_RSS = True
ENABLE_GDELT = True
ENABLE_NEWSAPI = bool(NEWSAPI_KEY)
ENABLE_GUARDIAN = bool(GUARDIAN_API_KEY)

AUTO_NEWS_REFRESH_SECONDS = 600
AUTO_PRICE_REFRESH_SECONDS = 60
AUTO_AGENT_REFRESH_SECONDS = 900

DEFAULT_WATCHLIST = [
    "oil",
    "gold",
    "fed",
    "boe",
    "ecb",
    "inflation",
    "sanctions",
    "china",
    "pound",
    "usd",
    "recession",
    "tariff",
    "opec",
    "gbp"
]

TRACKED_SYMBOLS = [
    {"symbol": "GOLD", "label": "Gold"},
    {"symbol": "OIL", "label": "Oil"},
    {"symbol": "GBPUSD", "label": "GBP/USD"},
    {"symbol": "EURUSD", "label": "EUR/USD"},
    {"symbol": "SPY", "label": "S&P 500 proxy"},
    {"symbol": "QQQ", "label": "Nasdaq proxy"},
]
