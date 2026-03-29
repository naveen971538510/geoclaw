import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

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


def new_llm_run_state(per_run_cap: int = None) -> Dict:
    return {
        "per_run_call_cap": int(per_run_cap or LLM_PER_RUN_CALL_CAP),
        "per_hour_call_cap": int(LLM_PER_HOUR_CALL_CAP),
        "llm_calls_made": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "budget_blocked_calls": 0,
        "hourly_blocked_calls": 0,
    }


def summarize_llm_run_state(state: Dict = None) -> Dict:
    state = state or {}
    return {
        "llm_calls_made": int(state.get("llm_calls_made", 0) or 0),
        "cache_hits": int(state.get("cache_hits", 0) or 0),
        "cache_misses": int(state.get("cache_misses", 0) or 0),
        "budget_blocked_calls": int(state.get("budget_blocked_calls", 0) or 0),
        "hourly_blocked_calls": int(state.get("hourly_blocked_calls", 0) or 0),
        "per_run_call_cap": int(state.get("per_run_call_cap", LLM_PER_RUN_CALL_CAP) or LLM_PER_RUN_CALL_CAP),
        "per_hour_call_cap": int(state.get("per_hour_call_cap", LLM_PER_HOUR_CALL_CAP) or LLM_PER_HOUR_CALL_CAP),
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


def _record_usage(cache_key: str, mode: str, outcome: str):
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO llm_usage_log (cache_key, mode, outcome, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (_compound_cache_key(cache_key, mode), str(mode or ""), str(outcome or "call"), utc_now_iso()),
    )
    conn.commit()
    conn.close()


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


def _cached_meta(cache_key: str, mode: str, run_state: Dict = None):
    cached = _cache_lookup(cache_key, mode)
    if not cached:
        if run_state is not None:
            run_state["cache_misses"] = int(run_state.get("cache_misses", 0) or 0) + 1
        return None
    if run_state is not None:
        run_state["cache_hits"] = int(run_state.get("cache_hits", 0) or 0) + 1
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
) -> Dict:
    cached = _cached_meta(cache_key, mode, run_state=run_state)
    if cached:
        return cached

    if not OPENAI_API_KEY:
        return {
            "analysis": _safe_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "missing_key",
            "cache_hit": False,
            "mode": mode,
        }

    if _budget_blocked(run_state):
        return {
            "analysis": _safe_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "budget_blocked",
            "cache_hit": False,
            "mode": mode,
        }

    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=input_payload,
            max_output_tokens=260 if allow_thesis else 220,
        )
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _record_usage(cache_key, mode, "call")
        raw_text = getattr(response, "output_text", "") or ""
        if not raw_text:
            _cache_store(cache_key, mode, _safe_default(), "validation_error", ttl_seconds)
            return {
                "analysis": _safe_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_error",
                "cache_hit": False,
                "mode": mode,
            }
        payload = json.loads(raw_text)
        if not _strict_validate_payload(payload, allow_thesis=allow_thesis):
            _cache_store(cache_key, mode, _safe_default(), "validation_error", ttl_seconds)
            return {
                "analysis": _safe_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_error",
                "cache_hit": False,
                "mode": mode,
            }
        clean = _clean_payload(payload, allow_thesis=allow_thesis)
        _cache_store(cache_key, mode, clean, "", ttl_seconds)
        return {
            "analysis": clean,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception:
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _record_usage(cache_key, mode, "api_error")
        _cache_store(cache_key, mode, _safe_default(), "api_error", ttl_seconds)
        return {
            "analysis": _safe_default(),
            "call_made": True,
            "used_fallback": True,
            "fallback_reason": "api_error",
            "cache_hit": False,
            "mode": mode,
        }


def _invoke_contradiction(input_payload, cache_key: str, run_state: Dict = None) -> Dict:
    mode = "contradiction"
    cached = _cached_meta(cache_key, mode, run_state=run_state)
    if cached:
        return cached

    if not OPENAI_API_KEY:
        return {
            "analysis": _safe_contradiction_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "missing_key",
            "cache_hit": False,
            "mode": mode,
        }

    if _budget_blocked(run_state):
        return {
            "analysis": _safe_contradiction_default(),
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "budget_blocked",
            "cache_hit": False,
            "mode": mode,
        }

    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=input_payload,
            max_output_tokens=180,
        )
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _record_usage(cache_key, mode, "call")
        raw_text = getattr(response, "output_text", "") or ""
        if not raw_text:
            _cache_store(cache_key, mode, _safe_contradiction_default(), "validation_error", LLM_CONTRADICTION_CACHE_TTL_SECONDS)
            return {
                "analysis": _safe_contradiction_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_error",
                "cache_hit": False,
                "mode": mode,
            }
        payload = json.loads(raw_text)
        if not _strict_validate_contradiction(payload):
            _cache_store(cache_key, mode, _safe_contradiction_default(), "validation_error", LLM_CONTRADICTION_CACHE_TTL_SECONDS)
            return {
                "analysis": _safe_contradiction_default(),
                "call_made": True,
                "used_fallback": True,
                "fallback_reason": "validation_error",
                "cache_hit": False,
                "mode": mode,
            }
        clean = _clean_contradiction(payload)
        _cache_store(cache_key, mode, clean, "", LLM_CONTRADICTION_CACHE_TTL_SECONDS)
        return {
            "analysis": clean,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception:
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _record_usage(cache_key, mode, "api_error")
        _cache_store(cache_key, mode, _safe_contradiction_default(), "api_error", LLM_CONTRADICTION_CACHE_TTL_SECONDS)
        return {
            "analysis": _safe_contradiction_default(),
            "call_made": True,
            "used_fallback": True,
            "fallback_reason": "api_error",
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
) -> Dict:
    cached = _cached_meta(cache_key, mode, run_state=run_state)
    if cached:
        return cached

    fallback = fallback if isinstance(fallback, dict) else {}
    ttl_seconds = int(ttl_seconds or LLM_CACHE_TTL_SECONDS)

    if not OPENAI_API_KEY:
        return {
            "analysis": fallback,
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "missing_key",
            "cache_hit": False,
            "mode": mode,
        }

    if _budget_blocked(run_state):
        return {
            "analysis": fallback,
            "call_made": False,
            "used_fallback": True,
            "fallback_reason": "budget_blocked",
            "cache_hit": False,
            "mode": mode,
        }

    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=_custom_input(system_text, user_text),
            max_output_tokens=int(max_output_tokens or 320),
        )
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _record_usage(cache_key, mode, "call")
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
        return {
            "analysis": clean,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception as exc:
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        reason = "validation_error" if isinstance(exc, ValueError) else "api_error"
        _record_usage(cache_key, mode, reason)
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
) -> Dict:
    cached = _cached_meta(cache_key, mode, run_state=run_state)
    if cached:
        analysis = cached.get("analysis", {}) or {}
        return {**cached, "text": str(analysis.get("text", fallback_text or "") or "")}

    ttl_seconds = int(ttl_seconds or LLM_CACHE_TTL_SECONDS)
    fallback_payload = {"text": str(fallback_text or "")}

    if not OPENAI_API_KEY:
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
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=httpx.Timeout(float(OPENAI_TIMEOUT_SECONDS), connect=4.0, read=float(OPENAI_TIMEOUT_SECONDS), write=float(OPENAI_TIMEOUT_SECONDS)),
        )
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=_custom_input(system_text, user_text),
            max_output_tokens=int(max_output_tokens or 420),
        )
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _record_usage(cache_key, mode, "call")
        text = str(getattr(response, "output_text", "") or "").strip() or str(fallback_text or "")
        payload = {"text": text}
        _cache_store(cache_key, mode, payload, "", ttl_seconds)
        return {
            "analysis": payload,
            "text": text,
            "call_made": True,
            "used_fallback": False,
            "fallback_reason": "",
            "cache_hit": False,
            "mode": mode,
        }
    except Exception:
        if run_state is not None:
            run_state["llm_calls_made"] = int(run_state.get("llm_calls_made", 0) or 0) + 1
        _record_usage(cache_key, mode, "api_error")
        _cache_store(cache_key, mode, fallback_payload, "api_error", ttl_seconds)
        return {
            "analysis": fallback_payload,
            "text": fallback_payload["text"],
            "call_made": True,
            "used_fallback": True,
            "fallback_reason": "api_error",
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
    )


def analyse_article(headline, summary, source_name) -> Dict:
    return analyse_article_meta(headline, summary, source_name)["analysis"]
