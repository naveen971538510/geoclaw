from pathlib import Path
import os

from services.ai_contracts import sanitize_model_name

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "geoclaw.db"

APP_STATE_DIR = BASE_DIR / ".state"
APP_STATE_DIR.mkdir(exist_ok=True)
UI_DIR = BASE_DIR / "ui"
UI_DIR.mkdir(exist_ok=True)

ENV_FILE = BASE_DIR / ".env.geoclaw"
PROVIDER_STATE_FILE = APP_STATE_DIR / "provider_state.json"
OPERATOR_STATE_FILE = APP_STATE_DIR / "operator_state.json"
AGENT_STATE_FILE = APP_STATE_DIR / "agent_state.json"
PROVIDER_SELF_TEST_MIN_INTERVAL_SECONDS = 900


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


def _clean_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    upper = value.upper()
    placeholder_markers = [
        "PUT_YOUR_",
        "YOUR_REAL_",
        "_KEY_HERE",
        "CHANGE_ME",
        "REPLACE_ME",
        "PASTE_",
        "EXAMPLE",
    ]
    if not value:
        return ""
    if any(marker in upper for marker in placeholder_markers):
        return ""
    return value


_load_local_env(ENV_FILE)

NEWSAPI_KEY = _clean_env("NEWSAPI_KEY")
GUARDIAN_API_KEY = _clean_env("GUARDIAN_API_KEY")
ALPHAVANTAGE_KEY = _clean_env("ALPHAVANTAGE_KEY")
# Add OPENAI_API_KEY to .env.geoclaw to enable article analysis.
OPENAI_API_KEY = _clean_env("OPENAI_API_KEY")
OPENAI_MODEL = sanitize_model_name(_clean_env("OPENAI_MODEL"), default="gpt-5.4-mini")
OPENAI_TIMEOUT_SECONDS = 12
LLM_PER_RUN_CALL_CAP = 6
LLM_PER_HOUR_CALL_CAP = 24
LLM_CACHE_TTL_SECONDS = 1800
LLM_CONTRADICTION_CACHE_TTL_SECONDS = 900
MAX_REASONING_CHAINS_PER_CLUSTER = 1
MAX_RESEARCH_RUNS_PER_DAY = 8
MAX_AUTONOMOUS_GOALS_PER_DAY = 6
MAX_THESIS_UPDATES_PER_RUN = 20
THESIS_COOLDOWN_MINUTES = 20
CLUSTER_COOLDOWN_MINUTES = 30
MAX_ACTION_PROPOSALS_PER_RUN = 5
ACTION_PROPOSAL_MIN_CONFIDENCE = 0.55
ACTION_CRITICAL_MIN_CONFIDENCE = 0.85
ACTION_COOLDOWN_MINUTES = 120
ALLOW_AUTO_APPROVED_ACTIONS = False
GEOCLAW_LOCAL_TOKEN = _clean_env("GEOCLAW_LOCAL_TOKEN")
GEOCLAW_AUTO_SCHEDULE = _clean_env("GEOCLAW_AUTO_SCHEDULE").lower() in {"1", "true", "yes"}
SCHEDULER_INTERVAL_MINUTES = int(_clean_env("SCHEDULER_INTERVAL_MINUTES") or "30")
AGENT_REFLECTION_INTERVAL_RUNS = 5
AGENT_AUTONOMOUS_GOAL_INTERVAL_RUNS = 3
AGENT_BRIEFING_INTERVAL_HOURS = 23

ENABLE_RSS = True
ENABLE_GDELT = True
ENABLE_NEWSAPI = bool(NEWSAPI_KEY)
ENABLE_GUARDIAN = bool(GUARDIAN_API_KEY)
ENABLE_REDDIT = _clean_env("ENABLE_REDDIT").lower() in {"1", "true", "yes", ""}  # on by default
ENABLE_SEC = _clean_env("ENABLE_SEC").lower() in {"1", "true", "yes", ""}  # on by default
ENABLE_TWITTER = _clean_env("ENABLE_TWITTER").lower() in {"1", "true", "yes"}  # off by default (needs nitter)

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
    {"symbol": "GLD", "label": "Gold", "kind": "equity"},
    {"symbol": "USO", "label": "Oil", "kind": "equity"},
    {"symbol": "GBPUSD", "label": "GBP/USD", "kind": "fx"},
    {"symbol": "EURUSD", "label": "EUR/USD", "kind": "fx"},
    {"symbol": "SPY", "label": "S&P 500 proxy", "kind": "equity"},
    {"symbol": "QQQ", "label": "Nasdaq proxy", "kind": "equity"},
]
