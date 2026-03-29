from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "geoclaw.db"

APP_STATE_DIR = BASE_DIR / ".state"
APP_STATE_DIR.mkdir(exist_ok=True)

ENV_FILE = BASE_DIR / ".env.geoclaw"


def _load_local_env(path: Path):
    if not path.exists():
        return
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            if key and os.getenv(key) is None:
                os.environ[key] = value
    except Exception as exc:
        print(f"WARN: failed to load {path.name}: {exc}")


_load_local_env(ENV_FILE)

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

GDELT_TIMEOUT_SECONDS = 12
GDELT_COOLDOWN_SECONDS = 900
GDELT_MAX_RECORDS_DEFAULT = 8
GDELT_STATE_FILE = APP_STATE_DIR / "gdelt_state.json"

ALERT_MIN_IMPACT_SCORE = 25
ALERT_MIN_ALERT_TAGS = 1
ALERT_MIN_WATCHLIST_HITS = 2

AGENT_MAX_RECORDS_PER_SOURCE = 8

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
