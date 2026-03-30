import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
from openai import OpenAI

from config import (
    LLM_CACHE_TTL_SECONDS,
    LLM_CONTRADICTION_CACHE_TTL_SECONDS,
    LLM_PER_HOUR_CALL_CAP,
    LLM_PER_RUN_CALL_CAP,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_TIMEOUT_SECONDS,
)
from services.ai_contracts import sanitize_model_name
from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso


DEFAULT_ANALYSIS = {
    "importance": "medium",
    "category": "other",
    "why_it_matters": "",
    "contradicts_narrative": False,
    "confidence": 0.5,
    "urgency": "medium",
    "impact": "regional",
}
DEFAULT_CONTRADICTION = {
    "resolution": "nuance",
    "note": "",
    "confidence": 0.5,
}

VALID_IMPORTANCE = {"low", "medium", "high", "critical"}
VALID_CATEGORY = {"markets", "politics", "energy", "tech", "geopolitics", "other"}
VALID_URGENCY = {"low", "medium", "high", "immediate"}
VALID_IMPACT = {"local", "regional", "global", "market-specific"}
VALID_CONTRADICTION = {"contradiction", "update", "nuance", "unrelated"}
MODEL_NAME = sanitize_model_name(OPENAI_MODEL, default="gpt-5.4-mini")
LLM_FAILURE_REASONS = {
    "missing_key",
    "insufficient_quota",
    "rate_limit",
    "auth_failure",
    "timeout",
    "validation_failure",
    "budget_blocked",
    "api_error",
}


def _safe_default() -> Dict:
    return dict(DEFAULT_ANALYSIS)


def _safe_contradiction_default() -> Dict:
    return dict(DEFAULT_CONTRADICTION)


def _clamp_confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def _is_valid_enum(value, allowed) -> bool:
    return str(value or "").strip().lower() in allowed


def _strict_validate_payload(payload: Dict, allow_thesis: bool = False) -> bool:
    required = [
        "importance",
        "category",
        "why_it_matters",
        "contradicts_narrative",
        "confidence",
        "urgency",
        "impact",
    ]
    if allow_thesis:
        required.append("thesis")
    if not isinstance(payload, dict):
        return False
    if any(key not in payload for key in required):
        return False
    if not _is_valid_enum(payload.get("importance"), VALID_IMPORTANCE):
        return False
    if not _is_valid_enum(payload.get("category"), VALID_CATEGORY):
        return False
    if not _is_valid_enum(payload.get("urgency"), VALID_URGENCY):
        return False
    if not _is_valid_enum(payload.get("impact"), VALID_IMPACT):
        return False
    try:
        float(payload.get("confidence"))
    except (TypeError, ValueError):
        return False
    if not isinstance(payload.get("contradicts_narrative"), bool):
        return False
    if not isinstance(payload.get("why_it_matters"), str):
        return False
    if allow_thesis and not isinstance(payload.get("thesis"), str):
        return False
    return True


def _strict_validate_contradiction(payload: Dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if any(key not in payload for key in ("resolution", "note", "confidence")):
        return False
    if not _is_valid_enum(payload.get("resolution"), VALID_CONTRADICTION):
        return False
    if not isinstance(payload.get("note"), str):
        return False
    try:
        float(payload.get("confidence"))
    except (TypeError, ValueError):
        return False
    return True


def _clean_payload(payload: Dict, allow_thesis: bool = False) -> Dict:
    clean = _safe_default()
    clean["importance"] = str(payload.get("importance") or clean["importance"]).strip().lower()
    clean["category"] = str(payload.get("category") or clean["category"]).strip().lower()
    clean["why_it_matters"] = str(payload.get("why_it_matters") or "").strip()
    clean["contradicts_narrative"] = bool(payload.get("contradicts_narrative", False))
    clean["confidence"] = _clamp_confidence(payload.get("confidence"))
    clean["urgency"] = str(payload.get("urgency") or clean["urgency"]).strip().lower()
    clean["impact"] = str(payload.get("impact") or clean["impact"]).strip().lower()
    if allow_thesis:
        clean["thesis"] = str(payload.get("thesis") or "").strip()
    return clean


def _clean_contradiction(payload: Dict) -> Dict:
    clean = _safe_contradiction_default()
    clean["resolution"] = str(payload.get("resolution") or clean["resolution"]).strip().lower()
    clean["note"] = str(payload.get("note") or "").strip()
    clean["confidence"] = _clamp_confidence(payload.get("confidence"))
    return clean


def _custom_input(system_text: str, user_text: str):
    return [
        {"role": "system", "content": [{"type": "input_text", "text": str(system_text or "").strip()}]},
        {"role": "user", "content": [{"type": "input_text", "text": str(user_text or "").strip()}]},
    ]


def _estimate_input_size(payload) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False))
    except Exception:
        return len(str(payload or ""))


def classify_llm_failure(exc: Exception) -> Dict[str, str]:
    if isinstance(exc, ValueError):
        return {
            "reason": "validation_failure",
            "detail": str(exc or "validation failure")[:240],
        }

    name = type(exc).__name__
    message = str(exc or "")
    combined = f"{name}: {message}".lower()
    status_code = getattr(exc, "status_code", None)

    if "insufficient_quota" in combined or ("quota" in combined and int(status_code or 0) == 429):
        return {"reason": "insufficient_quota", "detail": message[:240]}
    if int(status_code or 0) in (401, 403) or any(
        token in combined for token in ("authentication", "unauthorized", "invalid api key", "incorrect api key")
    ):
        return {"reason": "auth_failure", "detail": message[:240]}
    if "ratelimiterror" in combined or int(status_code or 0) == 429 or "rate limit" in combined or "too many requests" in combined:
        return {"reason": "rate_limit", "detail": message[:240]}
    if any(
        token in combined
        for token in (
            "apitimeouterror",
            "timeout",
            "timed out",
            "readtimeout",
            "connecttimeout",
            "writetimeout",
        )
    ):
        return {"reason": "timeout", "detail": message[:240]}
    return {"reason": "api_error", "detail": message[:240]}


def _build_article_input(headline: str, summary: str, source_name: str):
    system_text = (
        "You classify financial and geopolitical news for an operator terminal. "
        "Return strict JSON only with these keys: importance, category, why_it_matters, "
        "contradicts_narrative, confidence, urgency, impact. "
        "importance must be one of: low, medium, high, critical. "
        "category must be one of: markets, politics, energy, tech, geopolitics, other. "
        "urgency must be one of: low, medium, high, immediate. "
        "impact must be one of: local, regional, global, market-specific. "
        "why_it_matters must be one short sentence."
    )
    user_text = (
        f"Headline: {headline or ''}\n"
        f"Summary: {summary or ''}\n"
        f"Source: {source_name or ''}\n"
    )
    return [
        {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
        {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
    ]


def _build_cluster_input(cluster_items: List[Dict]):
    system_text = (
        "You cluster related financial and geopolitical headlines for an operator terminal. "
        "Return strict JSON only with these keys: thesis, importance, category, why_it_matters, "
        "contradicts_narrative, confidence, urgency, impact. "
        "thesis must be one concise sentence that captures the shared story. "
        "importance must be one of: low, medium, high, critical. "
        "category must be one of: markets, politics, energy, tech, geopolitics, other. "
        "urgency must be one of: low, medium, high, immediate. "
        "impact must be one of: local, regional, global, market-specific. "
        "why_it_matters must be one short sentence."
    )
    lines = []
    for idx, item in enumerate(cluster_items[:5], start=1):
        lines.append(
            f"{idx}. Headline: {str(item.get('headline') or '').strip()} | "
            f"Summary: {str(item.get('summary') or '').strip()} | "
            f"Source: {str(item.get('source_name') or item.get('source') or '').strip()}"
        )
    user_text = "Related story cluster:\n" + "\n".join(lines)
    return [
        {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
        {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
    ]


def _build_contradiction_input(current_text: str, prior_claim: str):
    system_text = (
        "You evaluate whether a new article truly contradicts an existing market thesis. "
        "Return strict JSON only with these keys: resolution, note, confidence. "
        "resolution must be one of: contradiction, update, nuance, unrelated. "
        "note must be one concise sentence."
    )
    user_text = (
        f"Stored thesis claim: {prior_claim or ''}\n"
        f"New article text: {current_text or ''}\n"
    )
    return [
        {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
        {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
    ]


def new_llm_run_state(per_run_cap: int = None, buffer_usage_logs: bool = False) -> Dict:
    return {
        "per_run_call_cap": int(per_run_cap or LLM_PER_RUN_CALL_CAP),
        "per_hour_call_cap": int(LLM_PER_HOUR_CALL_CAP),
        "llm_calls_made": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "llm_latency_total_ms": 0,
        "llm_latency_samples": 0,
        "budget_blocked_calls": 0,
        "hourly_blocked_calls": 0,
        "lane_counts": {},
        "task_counts": {},
        "lane_metrics": {},
        "fallback_reasons": {},
        "validation_failures": 0,
        "buffer_usage_logs": bool(buffer_usage_logs),
        "usage_log_buffer": [],
    }


def summarize_llm_run_state(state: Dict = None) -> Dict:
    state = state or {}
    lane_metrics = {}
    for lane, payload in dict(state.get("lane_metrics", {}) or {}).items():
        calls = int(payload.get("calls", 0) or 0)
        latency_ms = int(payload.get("latency_ms", 0) or 0)
        lane_metrics[str(lane)] = {
            "calls": calls,
            "cache_hits": int(payload.get("cache_hits", 0) or 0),
            "cache_misses": int(payload.get("cache_misses", 0) or 0),
            "latency_ms": latency_ms,
            "avg_latency_ms": round(latency_ms / max(calls, 1), 1) if calls else 0.0,
            "fallbacks": dict(payload.get("fallbacks", {}) or {}),
        }
    llm_calls_made = int(state.get("llm_calls_made", 0) or 0)
    total_latency_ms = int(state.get("llm_latency_total_ms", 0) or 0)
    return {
        "llm_calls_made": llm_calls_made,
        "cache_hits": int(state.get("cache_hits", 0) or 0),
        "cache_misses": int(state.get("cache_misses", 0) or 0),
        "total_llm_latency_ms": total_latency_ms,
        "average_llm_latency_ms": round(total_latency_ms / max(llm_calls_made, 1), 1) if llm_calls_made else 0.0,
        "budget_blocked_calls": int(state.get("budget_blocked_calls", 0) or 0),
        "hourly_blocked_calls": int(state.get("hourly_blocked_calls", 0) or 0),
        "per_run_call_cap": int(state.get("per_run_call_cap", LLM_PER_RUN_CALL_CAP) or LLM_PER_RUN_CALL_CAP),
        "per_hour_call_cap": int(state.get("per_hour_call_cap", LLM_PER_HOUR_CALL_CAP) or LLM_PER_HOUR_CALL_CAP),
        "lane_counts": dict(state.get("lane_counts", {}) or {}),
        "task_counts": dict(state.get("task_counts", {}) or {}),
        "lane_metrics": lane_metrics,
        "fallback_reasons": dict(state.get("fallback_reasons", {}) or {}),
        "validation_failures": int(state.get("validation_failures", 0) or 0),
    }


def _parse_iso(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _compound_cache_key(cache_key: str, mode: str) -> str:
    return f"{str(mode or '').strip().lower()}::{str(cache_key or '').strip().lower()}"


def _hour_cutoff_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _hours_back_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours or 1)))).isoformat()


def _record_usage(
    cache_key: str,
    mode: str,
    outcome: str,
    *,
    task_type: str = "",
    lane: str = "reason",
    success: bool = False,
    fallback_reason: str = "",
    latency_ms: int = 0,
    input_size_estimate: int = 0,
    validation_error: str = "",
    model: str = "",
    run_state: Dict = None,
):
    row = (
        _compound_cache_key(cache_key, mode),
        str(mode or ""),
        str(outcome or "call"),
        utc_now_iso(),
        str(task_type or mode or ""),
        str(lane or "reason"),
        sanitize_model_name(model or MODEL_NAME, default=MODEL_NAME),
        1 if success else 0,
        str(fallback_reason or ""),
        int(latency_ms or 0),
        int(input_size_estimate or 0),
        str(validation_error or ""),
    )
    if run_state is not None and run_state.get("buffer_usage_logs"):
        run_state.setdefault("usage_log_buffer", []).append(row)
        return
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO llm_usage_log (
            cache_key, mode, outcome, created_at,
            task_type, lane, model, success, fallback_reason,
            latency_ms, input_size_estimate, validation_error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row,
    )
    conn.commit()
    conn.close()


def flush_usage_log_buffer(run_state: Dict = None) -> Dict:
    if not run_state:
        return {"flushed": 0}
    rows = list(run_state.get("usage_log_buffer") or [])
    if not rows:
        return {"flushed": 0}
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO llm_usage_log (
            cache_key, mode, outcome, created_at,
            task_type, lane, model, success, fallback_reason,
            latency_ms, input_size_estimate, validation_error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    run_state["usage_log_buffer"] = []
    return {"flushed": len(rows)}


def _mark_run_state(state: Dict = None, lane: str = "", task_type: str = "", fallback_reason: str = ""):
    if state is None:
        return
    lane_key = str(lane or "reason")
    task_key = str(task_type or "unknown")
    lane_counts = state.setdefault("lane_counts", {})
    lane_counts[lane_key] = int(lane_counts.get(lane_key, 0) or 0) + 1
    task_counts = state.setdefault("task_counts", {})
    task_counts[task_key] = int(task_counts.get(task_key, 0) or 0) + 1
    if fallback_reason:
        fallback_counts = state.setdefault("fallback_reasons", {})
        fallback_counts[str(fallback_reason)] = int(fallback_counts.get(str(fallback_reason), 0) or 0) + 1
        lane_metrics = state.setdefault("lane_metrics", {})
        lane_bucket = lane_metrics.setdefault(lane_key, {"calls": 0, "latency_ms": 0, "cache_hits": 0, "cache_misses": 0, "fallbacks": {}})
        lane_bucket["fallbacks"][str(fallback_reason)] = int(lane_bucket["fallbacks"].get(str(fallback_reason), 0) or 0) + 1
        if str(fallback_reason) == "validation_failure":
            state["validation_failures"] = int(state.get("validation_failures", 0) or 0) + 1


def _mark_cache_state(state: Dict = None, lane: str = "", cache_hit: bool = False):
    if state is None:
        return
    lane_key = str(lane or "reason")
    lane_metrics = state.setdefault("lane_metrics", {})
    lane_bucket = lane_metrics.setdefault(lane_key, {"calls": 0, "latency_ms": 0, "cache_hits": 0, "cache_misses": 0, "fallbacks": {}})
    metric_key = "cache_hits" if cache_hit else "cache_misses"
    lane_bucket[metric_key] = int(lane_bucket.get(metric_key, 0) or 0) + 1


def _mark_call_latency(state: Dict = None, lane: str = "", latency_ms: int = 0):
    if state is None:
        return
    lane_key = str(lane or "reason")
    lane_metrics = state.setdefault("lane_metrics", {})
    lane_bucket = lane_metrics.setdefault(lane_key, {"calls": 0, "latency_ms": 0, "cache_hits": 0, "cache_misses": 0, "fallbacks": {}})
    lane_bucket["calls"] = int(lane_bucket.get("calls", 0) or 0) + 1
    lane_bucket["latency_ms"] = int(lane_bucket.get("latency_ms", 0) or 0) + int(latency_ms or 0)
    state["llm_latency_total_ms"] = int(state.get("llm_latency_total_ms", 0) or 0) + int(latency_ms or 0)
    state["llm_latency_samples"] = int(state.get("llm_latency_samples", 0) or 0) + 1


def _hourly_calls() -> int:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM llm_usage_log
        WHERE created_at >= ?
        """,
        (_hour_cutoff_iso(),),
    )
    row = cur.fetchone()
    conn.close()
    return int(row[0] or 0) if row else 0


def _cache_lookup(cache_key: str, mode: str):
    if not cache_key:
        return None
    compound_key = _compound_cache_key(cache_key, mode)
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT analysis_json, fallback_reason, expires_at
        FROM llm_cache
        WHERE cache_key = ? AND mode = ?
        LIMIT 1
        """,
        (compound_key, str(mode)),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    expires_at = _parse_iso(row["expires_at"])
    if expires_at and expires_at < datetime.now(timezone.utc):
        return None
    try:
        analysis = json.loads(row["analysis_json"] or "{}")
    except Exception:
        return None
    return {
        "analysis": analysis,
        "fallback_reason": str(row["fallback_reason"] or ""),
    }


def _cache_store(cache_key: str, mode: str, analysis: Dict, fallback_reason: str, ttl_seconds: int):
    if not cache_key:
        return
    compound_key = _compound_cache_key(cache_key, mode)
    ensure_agent_tables()
    now = utc_now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(60, int(ttl_seconds or 60)))).isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO llm_cache (cache_key, mode, analysis_json, fallback_reason, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            mode = excluded.mode,
            analysis_json = excluded.analysis_json,
            fallback_reason = excluded.fallback_reason,
            created_at = excluded.created_at,
            expires_at = excluded.expires_at
        """,
        (
            compound_key,
            str(mode),
            json.dumps(analysis or {}, ensure_ascii=False),
            str(fallback_reason or ""),
            now,
            expires_at,
        ),
    )
    conn.commit()
    conn.close()


def _budget_blocked(state: Dict = None) -> bool:
    state = state or {}
    if int(state.get("llm_calls_made", 0) or 0) >= int(state.get("per_run_call_cap", LLM_PER_RUN_CALL_CAP) or LLM_PER_RUN_CALL_CAP):
        state["budget_blocked_calls"] = int(state.get("budget_blocked_calls", 0) or 0) + 1
        return True
    if _hourly_calls() >= int(state.get("per_hour_call_cap", LLM_PER_HOUR_CALL_CAP) or LLM_PER_HOUR_CALL_CAP):
        state["hourly_blocked_calls"] = int(state.get("hourly_blocked_calls", 0) or 0) + 1
        state["budget_blocked_calls"] = int(state.get("budget_blocked_calls", 0) or 0) + 1
        return True
    return False


def _cached_meta(cache_key: str, mode: str, run_state: Dict = None, lane: str = "", task_type: str = ""):
    cached = _cache_lookup(cache_key, mode)
    if not cached:
        if run_state is not None:
            run_state["cache_misses"] = int(run_state.get("cache_misses", 0) or 0) + 1
            _mark_cache_state(run_state, lane=lane, cache_hit=False)
        return None
    if run_state is not None:
        run_state["cache_hits"] = int(run_state.get("cache_hits", 0) or 0) + 1
        _mark_cache_state(run_state, lane=lane, cache_hit=True)
        _mark_run_state(run_state, lane=lane, task_type=task_type or mode or "cache_hit")
    return {
        "analysis": cached["analysis"],
        "call_made": False,
        "used_fallback": False,
        "fallback_reason": str(cached.get("fallback_reason", "") or ""),
        "cache_hit": True,
        "mode": mode,
    }


def _invoke_json(
    input_payload,
    mode: str,
    cache_key: str,
    ttl_seconds: int,
    run_state: Dict = None,
    allow_thesis: bool = False,
    lane: str = "classify",
    task_type: str = "",
) -> Dict:
    task_type = str(task_type or mode or "json_task")
    cached = _cached_meta(cache_key, mode, run_state=run_state, lane=lane, task_type=task_type)
    if cached:
        return cached

    input_size_estimate = _estimate_input_size(input_payload)

    if not OPENAI_API_KEY:
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="missing_key")
        _record_usage(cache_key, mode, "missing_key", task_type=task_type, lane=lane, success=False, fallback_reason="missing_key", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": _safe_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "missing_key",
            "cache_hit": False,
            "mode": mode,
        }

    if _budget_blocked(run_state):
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="budget_blocked")
        _record_usage(cache_key, mode, "budget_blocked", task_type=task_type, lane=lane, success=False, fallback_reason="budget_blocked", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": _safe_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "budget_blocked",
            "cache_hit": False,
            "mode": mode,
        }

    try:
        started = time.perf_counter()
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=MODEL_NAME,
            input=input_payload,
            max_output_tokens=260 if allow_thesis else 220,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        _mark_run_state(run_state, lane=lane, task_type=task_type)
        raw_text = getattr(response, "output_text", "") or ""
        if not raw_text:
            _cache_store(cache_key, mode, _safe_default(), "validation_error", ttl_seconds)
            _record_usage(cache_key, mode, "validation_failure", task_type=task_type, lane=lane, success=False, fallback_reason="validation_failure", latency_ms=latency_ms, input_size_estimate=input_size_estimate, validation_error="empty_output", model=MODEL_NAME, run_state=run_state)
            _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="validation_failure")
            return {
                "analysis": _safe_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_failure",
                "cache_hit": False,
                "mode": mode,
            }
        payload = json.loads(raw_text)
        if not _strict_validate_payload(payload, allow_thesis=allow_thesis):
            _cache_store(cache_key, mode, _safe_default(), "validation_error", ttl_seconds)
            _record_usage(cache_key, mode, "validation_failure", task_type=task_type, lane=lane, success=False, fallback_reason="validation_failure", latency_ms=latency_ms, input_size_estimate=input_size_estimate, validation_error="invalid_schema", model=MODEL_NAME, run_state=run_state)
            _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="validation_failure")
            return {
                "analysis": _safe_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_failure",
                "cache_hit": False,
                "mode": mode,
            }
        clean = _clean_payload(payload, allow_thesis=allow_thesis)
        _cache_store(cache_key, mode, clean, "", ttl_seconds)
        _record_usage(cache_key, mode, "call", task_type=task_type, lane=lane, success=True, latency_ms=latency_ms, input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": clean,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception as exc:
        failure = classify_llm_failure(exc)
        latency_ms = int((time.perf_counter() - started) * 1000) if "started" in locals() else 0
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason=failure["reason"])
        _record_usage(cache_key, mode, failure["reason"], task_type=task_type, lane=lane, success=False, fallback_reason=failure["reason"], latency_ms=latency_ms, input_size_estimate=input_size_estimate, validation_error=failure["detail"], model=MODEL_NAME, run_state=run_state)
        _cache_store(cache_key, mode, _safe_default(), failure["reason"], ttl_seconds)
        return {
            "analysis": _safe_default(),
            "call_made": True,
            "used_fallback": True,
            "fallback_reason": failure["reason"],
            "cache_hit": False,
            "mode": mode,
        }


def _invoke_contradiction(input_payload, cache_key: str, run_state: Dict = None, lane: str = "reason", task_type: str = "contradiction") -> Dict:
    mode = "contradiction"
    cached = _cached_meta(cache_key, mode, run_state=run_state, lane=lane, task_type=task_type)
    if cached:
        return cached

    input_size_estimate = _estimate_input_size(input_payload)

    if not OPENAI_API_KEY:
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="missing_key")
        _record_usage(cache_key, mode, "missing_key", task_type=task_type, lane=lane, success=False, fallback_reason="missing_key", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": _safe_contradiction_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "missing_key",
            "cache_hit": False,
            "mode": mode,
        }

    if _budget_blocked(run_state):
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="budget_blocked")
        _record_usage(cache_key, mode, "budget_blocked", task_type=task_type, lane=lane, success=False, fallback_reason="budget_blocked", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": _safe_contradiction_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "budget_blocked",
            "cache_hit": False,
            "mode": mode,
        }

    try:
        started = time.perf_counter()
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=MODEL_NAME,
            input=input_payload,
            max_output_tokens=180,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        _mark_run_state(run_state, lane=lane, task_type=task_type)
        raw_text = getattr(response, "output_text", "") or ""
        if not raw_text:
            _cache_store(cache_key, mode, _safe_contradiction_default(), "validation_error", LLM_CONTRADICTION_CACHE_TTL_SECONDS)
            _record_usage(cache_key, mode, "validation_failure", task_type=task_type, lane=lane, success=False, fallback_reason="validation_failure", latency_ms=latency_ms, input_size_estimate=input_size_estimate, validation_error="empty_output", model=MODEL_NAME, run_state=run_state)
            _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="validation_failure")
            return {
                "analysis": _safe_contradiction_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_failure",
                "cache_hit": False,
                "mode": mode,
            }
        payload = json.loads(raw_text)
        if not _strict_validate_contradiction(payload):
            _cache_store(cache_key, mode, _safe_contradiction_default(), "validation_error", LLM_CONTRADICTION_CACHE_TTL_SECONDS)
            _record_usage(cache_key, mode, "validation_failure", task_type=task_type, lane=lane, success=False, fallback_reason="validation_failure", latency_ms=latency_ms, input_size_estimate=input_size_estimate, validation_error="invalid_schema", model=MODEL_NAME, run_state=run_state)
            _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="validation_failure")
            return {
                "analysis": _safe_contradiction_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_failure",
                "cache_hit": False,
                "mode": mode,
            }
        clean = _clean_contradiction(payload)
        _cache_store(cache_key, mode, clean, "", LLM_CONTRADICTION_CACHE_TTL_SECONDS)
        _record_usage(cache_key, mode, "call", task_type=task_type, lane=lane, success=True, latency_ms=latency_ms, input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": clean,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception as exc:
        failure = classify_llm_failure(exc)
        latency_ms = int((time.perf_counter() - started) * 1000) if "started" in locals() else 0
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason=failure["reason"])
        _record_usage(cache_key, mode, failure["reason"], task_type=task_type, lane=lane, success=False, fallback_reason=failure["reason"], latency_ms=latency_ms, input_size_estimate=input_size_estimate, validation_error=failure["detail"], model=MODEL_NAME, run_state=run_state)
        _cache_store(cache_key, mode, _safe_contradiction_default(), failure["reason"], LLM_CONTRADICTION_CACHE_TTL_SECONDS)
        return {
            "analysis": _safe_contradiction_default(),
            "call_made": True,
            "used_fallback": True,
            "fallback_reason": failure["reason"],
            "cache_hit": False,
            "mode": mode,
        }


def analyse_custom_json(
    system_text: str,
    user_text: str,
    fallback: Dict,
    mode: str,
    cache_key: str,
    validator=None,
    cleaner=None,
    run_state: Dict = None,
    ttl_seconds: int = None,
    max_output_tokens: int = 320,
    lane: str = "reason",
    task_type: str = "",
) -> Dict:
    task_type = str(task_type or mode or "custom_json")
    cached = _cached_meta(cache_key, mode, run_state=run_state, lane=lane, task_type=task_type)
    if cached:
        return cached

    fallback = fallback if isinstance(fallback, dict) else {}
    ttl_seconds = int(ttl_seconds or LLM_CACHE_TTL_SECONDS)
    input_payload = _custom_input(system_text, user_text)
    input_size_estimate = _estimate_input_size(input_payload)

    if not OPENAI_API_KEY:
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="missing_key")
        _record_usage(cache_key, mode, "missing_key", task_type=task_type, lane=lane, success=False, fallback_reason="missing_key", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": fallback,
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "missing_key",
            "cache_hit": False,
            "mode": mode,
        }

    if _budget_blocked(run_state):
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="budget_blocked")
        _record_usage(cache_key, mode, "budget_blocked", task_type=task_type, lane=lane, success=False, fallback_reason="budget_blocked", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": fallback,
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "budget_blocked",
            "cache_hit": False,
            "mode": mode,
        }

    try:
        started = time.perf_counter()
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=MODEL_NAME,
            input=input_payload,
            max_output_tokens=int(max_output_tokens or 320),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        _mark_run_state(run_state, lane=lane, task_type=task_type)
        raw_text = getattr(response, "output_text", "") or ""
        payload = json.loads(raw_text) if raw_text else None
        if not isinstance(payload, dict):
            raise ValueError("invalid_json")
        if callable(validator) and not validator(payload):
            raise ValueError("invalid_schema")
        clean = cleaner(payload) if callable(cleaner) else payload
        if not isinstance(clean, dict):
            raise ValueError("invalid_clean")
        _cache_store(cache_key, mode, clean, "", ttl_seconds)
        _record_usage(cache_key, mode, "call", task_type=task_type, lane=lane, success=True, latency_ms=latency_ms, input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": clean,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000) if "started" in locals() else 0
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        failure = classify_llm_failure(exc)
        reason = failure["reason"]
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason=reason)
        _record_usage(
            cache_key,
            mode,
            reason,
            task_type=task_type,
            lane=lane,
            success=False,
            fallback_reason=reason,
            latency_ms=latency_ms,
            input_size_estimate=input_size_estimate,
            validation_error=failure["detail"],
            model=MODEL_NAME,
            run_state=run_state,
        )
        _cache_store(cache_key, mode, fallback, reason, ttl_seconds)
        return {
            "analysis": fallback,
            "call_made": True,
            "used_fallback": True,
            "fallback_reason": reason,
            "cache_hit": False,
            "mode": mode,
        }


def analyse_custom_text(
    system_text: str,
    user_text: str,
    fallback_text: str,
    mode: str,
    cache_key: str,
    run_state: Dict = None,
    ttl_seconds: int = None,
    max_output_tokens: int = 420,
    lane: str = "polish",
    task_type: str = "",
) -> Dict:
    task_type = str(task_type or mode or "custom_text")
    cached = _cached_meta(cache_key, mode, run_state=run_state, lane=lane, task_type=task_type)
    if cached:
        analysis = cached.get("analysis", {}) or {}
        return {**cached, "text": str(analysis.get("text", fallback_text or "") or "")}

    ttl_seconds = int(ttl_seconds or LLM_CACHE_TTL_SECONDS)
    fallback_payload = {"text": str(fallback_text or "")}
    input_payload = _custom_input(system_text, user_text)
    input_size_estimate = _estimate_input_size(input_payload)

    if not OPENAI_API_KEY:
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="missing_key")
        _record_usage(cache_key, mode, "missing_key", task_type=task_type, lane=lane, success=False, fallback_reason="missing_key", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": fallback_payload,
            "text": fallback_payload["text"],
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "missing_key",
            "cache_hit": False,
            "mode": mode,
        }

    if _budget_blocked(run_state):
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason="budget_blocked")
        _record_usage(cache_key, mode, "budget_blocked", task_type=task_type, lane=lane, success=False, fallback_reason="budget_blocked", input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": fallback_payload,
            "text": fallback_payload["text"],
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "budget_blocked",
            "cache_hit": False,
            "mode": mode,
        }

    try:
        started = time.perf_counter()
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=MODEL_NAME,
            input=input_payload,
            max_output_tokens=int(max_output_tokens or 420),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        _mark_run_state(run_state, lane=lane, task_type=task_type)
        text = str(getattr(response, "output_text", "") or "").strip() or str(fallback_text or "")
        payload = {"text": text}
        _cache_store(cache_key, mode, payload, "", ttl_seconds)
        _record_usage(cache_key, mode, "call", task_type=task_type, lane=lane, success=True, latency_ms=latency_ms, input_size_estimate=input_size_estimate, model=MODEL_NAME, run_state=run_state)
        return {
            "analysis": payload,
            "text": text,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception as exc:
        failure = classify_llm_failure(exc)
        latency_ms = int((time.perf_counter() - started) * 1000) if "started" in locals() else 0
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _mark_call_latency(run_state, lane=lane, latency_ms=latency_ms)
        _mark_run_state(run_state, lane=lane, task_type=task_type, fallback_reason=failure["reason"])
        _record_usage(cache_key, mode, failure["reason"], task_type=task_type, lane=lane, success=False, fallback_reason=failure["reason"], latency_ms=latency_ms, input_size_estimate=input_size_estimate, validation_error=failure["detail"], model=MODEL_NAME, run_state=run_state)
        _cache_store(cache_key, mode, fallback_payload, failure["reason"], ttl_seconds)
        return {
            "analysis": fallback_payload,
            "text": fallback_payload["text"],
            "call_made": True,
            "used_fallback": True,
            "fallback_reason": failure["reason"],
            "cache_hit": False,
            "mode": mode,
        }


def analyse_article_meta(headline, summary, source_name, cache_key: str = "", run_state: Dict = None) -> Dict:
    headline = str(headline or "").strip()
    summary = str(summary or "").strip()
    source_name = str(source_name or "").strip()

    if not headline:
        return {
            "analysis": _safe_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "validation_error",
            "cache_hit": False,
            "mode": "article",
        }

    return _invoke_json(
        _build_article_input(headline, summary, source_name),
        mode="article",
        cache_key=str(cache_key or headline[:120]).strip().lower(),
        ttl_seconds=LLM_CACHE_TTL_SECONDS,
        run_state=run_state,
        allow_thesis=False,
        lane="classify",
        task_type="article_meta",
    )


def analyse_cluster_meta(cluster_items: List[Dict], cluster_key: str = "", run_state: Dict = None) -> Dict:
    usable = []
    for item in cluster_items or []:
        headline = str(item.get("headline") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if headline:
            usable.append(
                {
                    "headline": headline,
                    "summary": summary,
                    "source_name": str(item.get("source_name") or item.get("source") or "").strip(),
                }
            )
    if not usable:
        return {
            "analysis": _safe_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "validation_error",
            "cache_hit": False,
            "mode": "cluster",
        }

    return _invoke_json(
        _build_cluster_input(usable),
        mode="cluster",
        cache_key=str(cluster_key or usable[0]["headline"][:120]).strip().lower(),
        ttl_seconds=LLM_CACHE_TTL_SECONDS,
        run_state=run_state,
        allow_thesis=True,
        lane="reason",
        task_type="cluster_meta",
    )


def analyse_contradiction_meta(current_text: str, prior_claim: str, cluster_key: str = "", run_state: Dict = None) -> Dict:
    current_text = str(current_text or "").strip()
    prior_claim = str(prior_claim or "").strip()
    if not current_text or not prior_claim:
        return {
            "analysis": _safe_contradiction_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "validation_error",
            "cache_hit": False,
            "mode": "contradiction",
        }
    return _invoke_contradiction(
        _build_contradiction_input(current_text, prior_claim),
        cache_key=str(cluster_key or prior_claim[:120]).strip().lower(),
        run_state=run_state,
        lane="reason",
        task_type="contradiction_check",
    )


def recent_usage_summary(hours: int = 6, since_iso: Optional[str] = None, until_iso: Optional[str] = None) -> Dict:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    usage_columns = set()
    try:
        usage_columns = {str(row[1] if not hasattr(row, "keys") else row["name"]) for row in conn.execute("PRAGMA table_info(llm_usage_log)").fetchall()}
    except Exception:
        usage_columns = set()
    latency_sum_expr = "SUM(latency_ms) AS latency_ms" if "latency_ms" in usage_columns else "0 AS latency_ms"
    latency_total_expr = "SUM(latency_ms) AS latency_total" if "latency_ms" in usage_columns else "0 AS latency_total"
    avg_latency_expr = "AVG(CASE WHEN outcome = 'call' THEN latency_ms END) AS avg_latency_ms" if "latency_ms" in usage_columns else "0 AS avg_latency_ms"
    since_value = str(since_iso or _hours_back_iso(hours))
    until_clause = ""
    params = [since_value]
    if until_iso:
        until_clause = " AND created_at <= ?"
        params.append(str(until_iso))
    cur.execute(
        f"""
        SELECT lane, task_type, outcome, COUNT(*) AS count, {latency_sum_expr}
        FROM llm_usage_log
        WHERE created_at >= ?{until_clause}
        GROUP BY lane, task_type, outcome
        """,
        tuple(params),
    )
    rows = cur.fetchall()
    cur.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN outcome = 'call' THEN 1 ELSE 0 END) AS llm_calls_made,
            {latency_total_expr},
            {avg_latency_expr}
        FROM llm_usage_log
        WHERE created_at >= ?{until_clause}
        """,
        tuple(params),
    )
    aggregate = cur.fetchone()
    conn.close()
    summary = {
        "total": int((aggregate["total"] if aggregate else 0) or 0),
        "llm_calls_made": int((aggregate["llm_calls_made"] if aggregate else 0) or 0),
        "total_llm_latency_ms": int((aggregate["latency_total"] if aggregate else 0) or 0),
        "average_llm_latency_ms": round(float((aggregate["avg_latency_ms"] if aggregate else 0.0) or 0.0), 1),
        "by_lane": {},
        "by_task": {},
        "fallbacks": {},
        "lane_metrics": {},
    }
    for row in rows:
        row_keys = set(row.keys()) if hasattr(row, "keys") else set()
        lane_value = row["lane"] if "lane" in row_keys else None
        task_value = row["task_type"] if "task_type" in row_keys else None
        mode_value = row["mode"] if "mode" in row_keys else None
        outcome_value = row["outcome"] if "outcome" in row_keys else None
        count_value = row["count"] if "count" in row_keys else 0
        lane = str(lane_value or "reason")
        task = str(task_value or mode_value or "unknown")
        outcome = str(outcome_value or "unknown")
        count = int(count_value or 0)
        latency_ms = int((row["latency_ms"] if "latency_ms" in row_keys else 0) or 0)
        summary["by_lane"][lane] = int(summary["by_lane"].get(lane, 0) or 0) + count
        summary["by_task"][task] = int(summary["by_task"].get(task, 0) or 0) + count
        lane_bucket = summary["lane_metrics"].setdefault(
            lane,
            {"events": 0, "calls": 0, "latency_ms": 0, "avg_latency_ms": 0.0},
        )
        lane_bucket["events"] = int(lane_bucket.get("events", 0) or 0) + count
        if outcome == "call":
            lane_bucket["calls"] = int(lane_bucket.get("calls", 0) or 0) + count
            lane_bucket["latency_ms"] = int(lane_bucket.get("latency_ms", 0) or 0) + latency_ms
        if outcome != "call":
            summary["fallbacks"][outcome] = int(summary["fallbacks"].get(outcome, 0) or 0) + count
    for lane, payload in summary["lane_metrics"].items():
        calls = int(payload.get("calls", 0) or 0)
        payload["avg_latency_ms"] = round(int(payload.get("latency_ms", 0) or 0) / max(calls, 1), 1) if calls else 0.0
    return summary


def analyse_article(headline, summary, source_name) -> Dict:
    return analyse_article_meta(headline, summary, source_name)["analysis"]
