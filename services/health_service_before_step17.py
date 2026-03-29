import json
import re
import sqlite3
from typing import Dict, List

from config import (
    DB_PATH,
    NEWSAPI_KEY,
    GUARDIAN_API_KEY,
    ALPHAVANTAGE_KEY,
    ENABLE_RSS,
    ENABLE_GDELT,
    ENABLE_NEWSAPI,
    ENABLE_GUARDIAN,
    GDELT_STATE_FILE,
)


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _read_gdelt_state() -> Dict:
    try:
        if GDELT_STATE_FILE.exists():
            return json.loads(GDELT_STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _mask(text: str) -> str:
    s = str(text or "")
    s = re.sub(r'(api[Kk]ey=)[^&\s]+', r'\1***', s)
    s = re.sub(r'(apikey=)[^&\s]+', r'\1***', s)
    s = re.sub(r'(api-key=)[^&\s]+', r'\1***', s)
    s = re.sub(r'(key=)[^&\s]+', r'\1***', s)
    s = re.sub(r'https?://([^/?\s]+)[^\s]*', r'https://\1/...', s)
    return s


def _recent_run_errors(limit: int = 12) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, started_at, status, error_text
        FROM agent_runs
        WHERE error_text IS NOT NULL AND TRIM(error_text) != ''
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "started_at": r["started_at"],
                "status": r["status"],
                "error_text": _mask(r["error_text"]),
            }
        )
    return out


def get_source_health() -> Dict:
    gdelt_state = _read_gdelt_state()
    cooldown_active = bool(gdelt_state.get("cooldown_until"))

    keys = {
        "newsapi_configured": bool(NEWSAPI_KEY),
        "guardian_configured": bool(GUARDIAN_API_KEY),
        "alphavantage_configured": bool(ALPHAVANTAGE_KEY),
    }

    missing_keys = []
    if not keys["newsapi_configured"]:
        missing_keys.append("NEWSAPI_KEY")
    if not keys["guardian_configured"]:
        missing_keys.append("GUARDIAN_API_KEY")
    if not keys["alphavantage_configured"]:
        missing_keys.append("ALPHAVANTAGE_KEY")

    sources = [
        {
            "name": "rss",
            "enabled": bool(ENABLE_RSS),
            "ready": bool(ENABLE_RSS),
            "note": "BBC RSS active",
        },
        {
            "name": "gdelt",
            "enabled": bool(ENABLE_GDELT),
            "ready": bool(ENABLE_GDELT and not cooldown_active),
            "note": "Cooldown active" if cooldown_active else "Broad discovery enabled",
        },
        {
            "name": "newsapi",
            "enabled": bool(ENABLE_NEWSAPI),
            "ready": bool(ENABLE_NEWSAPI),
            "note": "Configured" if ENABLE_NEWSAPI else "Missing or invalid NEWSAPI_KEY",
        },
        {
            "name": "guardian",
            "enabled": bool(ENABLE_GUARDIAN),
            "ready": bool(ENABLE_GUARDIAN),
            "note": "Configured" if ENABLE_GUARDIAN else "Missing or invalid GUARDIAN_API_KEY",
        },
        {
            "name": "market",
            "enabled": bool(ALPHAVANTAGE_KEY),
            "ready": bool(ALPHAVANTAGE_KEY),
            "note": "Configured" if ALPHAVANTAGE_KEY else "Missing or invalid ALPHAVANTAGE_KEY",
        },
    ]

    recent_errors = _recent_run_errors(limit=8)
    joined = " | ".join(x["error_text"] for x in recent_errors).lower()

    summary = {
        "wide_news_ready": bool(ENABLE_RSS) and (bool(ENABLE_NEWSAPI) or bool(ENABLE_GUARDIAN) or bool(ENABLE_GDELT)),
        "market_data_ready": bool(ALPHAVANTAGE_KEY),
        "auth_issues": ("unauthorized" in joined) or ("invalid key" in joined),
        "rate_limit_issues": ("rate limited" in joined) or ("429" in joined),
        "timeout_issues": ("timed out" in joined) or ("timeout" in joined),
    }

    return {
        "status": "ok",
        "keys": keys,
        "missing_keys": missing_keys,
        "sources": sources,
        "gdelt_state": gdelt_state,
        "recent_errors": recent_errors,
        "summary": summary,
    }
