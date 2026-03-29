import json
import re
import time
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
from services.provider_state_service import get_provider_state, provider_ready
from services.db_helpers import get_conn as shared_get_conn

STARTED_AT = time.time()


def get_conn():
    return shared_get_conn(DB_PATH)


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


def _provider_note(name: str, configured: bool, state_entry: Dict) -> str:
    if not configured:
        env_name = {
            "newsapi": "NEWSAPI_KEY",
            "guardian": "GUARDIAN_API_KEY",
            "alphavantage": "ALPHAVANTAGE_KEY",
        }.get(str(name or "").lower(), f"{str(name or '').upper()}_KEY")
        return f"Missing or invalid {env_name}"
    status = str(state_entry.get("status", "") or "")
    reason = str(state_entry.get("reason", "") or "")
    retry_after = int(state_entry.get("retry_after", 0) or 0)

    if status == "invalid":
        return "Invalid key cached"
    if status == "limited":
        if retry_after and int(time.time()) < retry_after:
            return f"Limited: {reason or 'temporary issue'}"
        return "Retry allowed"
    if status == "ok":
        return "Configured"
    return "Configured"


def _provider_status(configured: bool, state_entry: Dict) -> str:
    if not configured:
        return "missing"
    status = str(state_entry.get("status", "") or "")
    retry_after = int(state_entry.get("retry_after", 0) or 0)
    now = int(time.time())

    if status == "invalid":
        return "invalid"
    if status == "limited" and retry_after and now < retry_after:
        return "limited"
    if status == "ok":
        return "ok"
    return "ok"


def _market_snapshot_count() -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT symbol, MAX(id)
            FROM market_snapshots
            GROUP BY symbol
        )
        """
    )
    count = int(cur.fetchone()[0] or 0)
    conn.close()
    return count


def _gdelt_issue(gdelt_state: Dict) -> str:
    reason = str(gdelt_state.get("reason", "") or "").lower()
    if "429" in reason or "rate" in reason:
        return "rate_limit"
    if "timeout" in reason:
        return "timeout"
    return "limited"


def _issue_from_reason(reason: str) -> str:
    low = str(reason or "").lower()
    if "429" in low or "rate" in low:
        return "rate_limit"
    if "timeout" in low:
        return "timeout"
    return ""


def _current_banner_notes(sources: List[Dict]) -> List[str]:
    rate_limited = []
    timed_out = []
    invalid = []
    degraded = []

    for source in sources:
        if not source.get("enabled"):
            continue
        name = str(source.get("name", "") or "").upper()
        status = str(source.get("status", "") or "")
        issue = str(source.get("issue", "") or "")

        if status == "invalid":
            invalid.append(name)
        elif status == "limited":
            if issue == "rate_limit":
                rate_limited.append(name)
            elif issue == "timeout":
                timed_out.append(name)
            else:
                degraded.append(name)
        elif status not in ("ok", "missing", "disabled", "cached"):
            degraded.append(name)

    notes = []
    if invalid:
        notes.append("Invalid or unauthorized: <strong>" + ", ".join(invalid) + "</strong>")
    if rate_limited:
        notes.append("Rate limited: <strong>" + ", ".join(rate_limited) + "</strong>")
    if timed_out:
        notes.append("Timeouts: <strong>" + ", ".join(timed_out) + "</strong>")
    if degraded:
        notes.append("Degraded: <strong>" + ", ".join(degraded) + "</strong>")
    return notes


def get_source_health() -> Dict:
    gdelt_state = _read_gdelt_state()
    cooldown_until = int(float(gdelt_state.get("cooldown_until", 0) or 0))
    cooldown_active = bool(cooldown_until and int(time.time()) < cooldown_until)
    market_snapshot_count = _market_snapshot_count()

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

    provider_state = get_provider_state()
    providers = provider_state.get("providers", {}) or {}
    gdelt_entry = providers.get("gdelt", {}) or {}
    gdelt_reason = str(gdelt_entry.get("reason", "") or "")
    gdelt_updated = int(gdelt_entry.get("updated_at", 0) or 0)
    gdelt_state_updated = int(gdelt_state.get("updated_at", 0) or 0)
    use_provider_gdelt = gdelt_updated >= gdelt_state_updated and str(gdelt_entry.get("status", "") or "") != ""

    newsapi_status = _provider_status(bool(NEWSAPI_KEY), providers.get("newsapi", {}))
    guardian_status = _provider_status(bool(GUARDIAN_API_KEY), providers.get("guardian", {}))
    market_status = _provider_status(bool(ALPHAVANTAGE_KEY), providers.get("alphavantage", {}))
    gdelt_status = _provider_status(True, gdelt_entry) if use_provider_gdelt else ("limited" if cooldown_active else "ok")
    gdelt_issue = _issue_from_reason(gdelt_reason) if use_provider_gdelt else (_gdelt_issue(gdelt_state) if cooldown_active else "")
    gdelt_ready = gdelt_status in ("ok", "cached")
    gdelt_note = (
        _provider_note("gdelt", True, gdelt_entry)
        if use_provider_gdelt
        else ("Cooldown active" if cooldown_active else "Broad discovery enabled")
    )

    if not ALPHAVANTAGE_KEY and market_snapshot_count:
        market_status = "cached"

    sources = [
        {
            "name": "rss",
            "enabled": bool(ENABLE_RSS),
            "ready": bool(ENABLE_RSS),
            "status": "ok" if ENABLE_RSS else "disabled",
            "issue": "",
            "note": "BBC RSS active",
        },
        {
            "name": "gdelt",
            "enabled": bool(ENABLE_GDELT),
            "ready": bool(ENABLE_GDELT and gdelt_ready),
            "status": gdelt_status if ENABLE_GDELT else "disabled",
            "issue": gdelt_issue,
            "note": gdelt_note,
        },
        {
            "name": "newsapi",
            "enabled": bool(ENABLE_NEWSAPI),
            "ready": provider_ready("newsapi", bool(NEWSAPI_KEY)),
            "status": newsapi_status,
            "issue": "rate_limit" if newsapi_status == "limited" and "rate" in str((providers.get("newsapi", {}) or {}).get("reason", "")).lower() else ("timeout" if newsapi_status == "limited" and "timeout" in str((providers.get("newsapi", {}) or {}).get("reason", "")).lower() else ""),
            "note": _provider_note("newsapi", bool(NEWSAPI_KEY), providers.get("newsapi", {})),
        },
        {
            "name": "guardian",
            "enabled": bool(ENABLE_GUARDIAN),
            "ready": provider_ready("guardian", bool(GUARDIAN_API_KEY)),
            "status": guardian_status,
            "issue": "rate_limit" if guardian_status == "limited" and "rate" in str((providers.get("guardian", {}) or {}).get("reason", "")).lower() else ("timeout" if guardian_status == "limited" and "timeout" in str((providers.get("guardian", {}) or {}).get("reason", "")).lower() else ""),
            "note": _provider_note("guardian", bool(GUARDIAN_API_KEY), providers.get("guardian", {})),
        },
        {
            "name": "market",
            "enabled": bool(ALPHAVANTAGE_KEY),
            "ready": provider_ready("alphavantage", bool(ALPHAVANTAGE_KEY)) or bool((not ALPHAVANTAGE_KEY) and market_snapshot_count),
            "status": market_status,
            "issue": "rate_limit" if market_status == "limited" and "rate" in str((providers.get("alphavantage", {}) or {}).get("reason", "")).lower() else ("timeout" if market_status == "limited" and "timeout" in str((providers.get("alphavantage", {}) or {}).get("reason", "")).lower() else ""),
            "note": (
                f"Provider missing; cached values available ({market_snapshot_count})"
                if (not ALPHAVANTAGE_KEY and market_snapshot_count)
                else _provider_note("alphavantage", bool(ALPHAVANTAGE_KEY), providers.get("alphavantage", {}))
            ),
        },
    ]

    recent_errors = _recent_run_errors(limit=8)
    banner_notes = _current_banner_notes(sources)

    summary = {
        "wide_news_ready": any(
            x.get("ready")
            for x in sources
            if x.get("name") in {"rss", "gdelt", "newsapi", "guardian"}
        ),
        "market_data_ready": market_status in ("ok", "cached"),
        "market_data_mode": "cached" if market_status == "cached" else ("live" if market_status == "ok" else "missing"),
        "enabled_sources": [x["name"] for x in sources if x.get("enabled")],
        "banner_notes": banner_notes,
        "auth_issues": any(x.get("status") == "invalid" and x.get("enabled") for x in sources),
        "rate_limit_issues": any(x.get("issue") == "rate_limit" and x.get("enabled") for x in sources),
        "timeout_issues": any(x.get("issue") == "timeout" and x.get("enabled") for x in sources),
    }

    return {
        "status": "ok",
        "keys": keys,
        "missing_keys": missing_keys,
        "sources": sources,
        "gdelt_state": gdelt_state,
        "provider_state": provider_state,
        "recent_errors": recent_errors,
        "summary": summary,
    }


def get_health() -> Dict:
    conn = get_conn()
    cur = conn.cursor()
    db_status = "ok"
    thesis_count = 0
    article_count = 0
    article_count_24h = 0
    avg_confidence = 0.0
    last_run_time = ""
    error = ""
    try:
        cur.execute("SELECT COUNT(*) FROM agent_theses")
        thesis_count = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM ingested_articles")
        article_count = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM ingested_articles WHERE fetched_at >= datetime('now', '-24 hours')")
        article_count_24h = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COALESCE(AVG(confidence), 0) FROM agent_theses WHERE COALESCE(status, '') != 'superseded'")
        avg_confidence = float(cur.fetchone()[0] or 0.0)
        cur.execute("SELECT COALESCE(MAX(started_at), '') FROM agent_runs")
        row = cur.fetchone()
        last_run_time = str(row[0] or "") if row else ""
    except Exception as exc:
        db_status = "error"
        error = str(exc)
    finally:
        conn.close()
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "error": error,
        "last_run_time": last_run_time,
        "thesis_count": thesis_count,
        "article_count": article_count,
        "article_count_24h": article_count_24h,
        "avg_confidence": round(avg_confidence, 4),
        "uptime_seconds": round(max(0.0, time.time() - STARTED_AT), 3),
    }


def get_deep_health() -> Dict:
    from services.action_service import pending_action_count
    from services.agent_loop_service import list_journal
    from services.agent_state_service import get_agent_state
    from services.scheduler_service import get_scheduler_status

    base = get_health()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [str(row[0]) for row in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM agent_theses WHERE COALESCE(contradiction_count, 0) > 0")
    contradiction_count = int(cur.fetchone()[0] or 0)
    conn.close()
    latest_journal = (list_journal(limit=1) or [{}])[0]
    metrics = latest_journal.get("metrics", {}) or {}
    llm_metrics = metrics.get("llm_metrics", {}) or {}
    contradiction_llm = metrics.get("contradiction_llm_metrics", {}) or {}
    return {
        **base,
        "db": {
            "status": base.get("db", "error"),
            "error": base.get("error", ""),
            "tables": tables,
        },
        "agent": {
            "last_run_at": base.get("last_run_time", ""),
            "thesis_count": int(base.get("thesis_count", 0) or 0),
            "avg_confidence": float(base.get("avg_confidence", 0.0) or 0.0),
        },
        "ingestion": {
            "article_count_total": int(base.get("article_count", 0) or 0),
            "article_count_24h": int(base.get("article_count_24h", 0) or 0),
        },
        "actions": {
            "pending_count": pending_action_count(),
        },
        "scheduler": get_scheduler_status(),
        "tables": {
            "count": len(tables),
            "items": tables,
        },
        "agent_loop_state": get_agent_state(),
        "llm_usage": {
            "calls_made": int(llm_metrics.get("llm_calls_made", 0) or 0) + int(contradiction_llm.get("llm_calls_made", 0) or 0),
            "cache_hits": int(llm_metrics.get("cache_hits", 0) or 0),
            "cache_misses": int(llm_metrics.get("cache_misses", 0) or 0),
            "budget_blocked_calls": int(llm_metrics.get("budget_blocked_calls", 0) or 0),
            "hourly_blocked_calls": int(llm_metrics.get("hourly_blocked_calls", 0) or 0),
        },
        "contradiction_count": contradiction_count,
        "pending_actions": pending_action_count(),
        "latest_journal": {
            "created_at": latest_journal.get("created_at", ""),
            "summary": latest_journal.get("summary", ""),
            "metrics": metrics,
        },
    }
