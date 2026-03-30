import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from config import DB_PATH, ENV_FILE, OPENAI_API_KEY, OPENAI_MODEL
from services.ai_contracts import (
    clean_debate_argument,
    clean_query_answer_bundle,
    clean_thesis_bundle,
    default_debate_argument,
    default_query_answer_bundle,
    default_thesis_bundle,
    sanitize_model_name,
    validate_debate_argument,
    validate_query_answer_bundle,
    validate_thesis_bundle,
)
from services.db_helpers import get_conn
from services.goal_service import ensure_agent_tables
from services.logging_service import get_logger


logger = get_logger("llm_analyst")

SYSTEM_PROMPT = """You are GeoClaw, an elite geopolitical and macro-financial
intelligence analyst. You reason about news events and their market implications.
You always respond in valid JSON only. No markdown, no preamble.

Your analysis must include:
- thesis_key: one sentence identifying the core market/geopolitical thesis
- confidence_delta: float between -0.20 and +0.25 (how much this article
  should move belief in the thesis, positive = more confident, negative = contradicts)
- timeframe: "immediate" | "days" | "weeks" | "months"
- terminal_risk: "HIGH" | "MEDIUM" | "LOW"
- market_implication: which assets/sectors are affected and how
- watchlist_suggestion: what to monitor (specific tickers, pairs, commodities)
- reasoning: 2-3 sentence plain English explanation of your reasoning chain
- why_now: one sentence on why this thesis matters right now
- confidence_basis: what would change your mind (what evidence would contradict this)

Be precise. Be professional. Think like a macro hedge fund analyst."""

def _load_env_file():
    if not ENV_FILE.exists():
        return
    try:
        for raw in ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and os.getenv(key) is None:
                os.environ[key] = value
    except Exception:
        return

class LLMAnalyst:
    def __init__(self, db_path=None):
        _load_env_file()
        self.api_key = OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "").strip()
        model_hint = os.environ.get("GEOCLAW_LLM_MODEL", "").strip() or OPENAI_MODEL or "gpt-5.4-mini"
        self.model = sanitize_model_name(model_hint, default="gpt-5.4-mini")
        self.db_path = str(db_path or DB_PATH)
        self._calls_this_hour = 0
        self._hour_window_start = time.time()
        self.per_hour_cap = int(os.environ.get("LLM_BUDGET_PER_HOUR", "20") or 20)
        self.per_run_cap = int(os.environ.get("LLM_CALLS_PER_RUN", "5") or 5)
        self._run_calls = 0
        self._recent_calls = []

    def reset_run_counter(self):
        self._run_calls = 0

    def _budget_ok(self) -> bool:
        now = time.time()
        if now - self._hour_window_start > 3600:
            self._calls_this_hour = 0
            self._hour_window_start = now
        return self._calls_this_hour < self.per_hour_cap and self._run_calls < self.per_run_cap

    def available(self) -> bool:
        return bool(self.api_key) and self._budget_ok()

    def analyse_article(self, headline: str, body: str, existing_thesis: Optional[dict] = None) -> Optional[dict]:
        fallback = default_thesis_bundle(headline=headline, existing_thesis=existing_thesis)
        context = ""
        if existing_thesis:
            context = (
                f"\nExisting thesis to update: {existing_thesis.get('thesis_key', '')}\n"
                f"Current confidence: {float(existing_thesis.get('confidence', 0.5) or 0.5):.0%}\n"
                f"Confidence velocity: {float(existing_thesis.get('confidence_velocity', 0.0) or 0.0):+.3f}\n"
                f"Contradiction count: {int(existing_thesis.get('contradiction_count', 0) or 0)}\n"
                f"Confidence history: {existing_thesis.get('history', [])}\n"
                f"Source reliability: {float(existing_thesis.get('source_reliability', 0.65) or 0.65):.2f}\n"
                f"Prediction accuracy: {float(existing_thesis.get('prediction_accuracy', 0.0) or 0.0):.1f}%\n"
                f"Linked recent articles: {existing_thesis.get('recent_articles', [])}"
            )
        user_msg = (
            f"Analyse this news article:{context}\n\n"
            f"HEADLINE: {headline or ''}\n\n"
            f"BODY: {(body or '')[:1200] if body else '(no body)'}\n\n"
            "Return JSON analysis."
        )
        result = self._request_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_msg,
            validator=validate_thesis_bundle,
            cleaner=lambda payload: clean_thesis_bundle(payload, headline=headline, existing_thesis=existing_thesis),
            fallback=fallback,
            task_type="thesis_generation",
            lane="reason",
            max_tokens=700,
            temperature=0.2,
            context_snippet=str(headline or "")[:80],
        )
        if result["used_fallback"]:
            logger.warning("LLM call fell back for thesis_generation: %s", result["fallback_reason"])
            return None
        cleaned = result["analysis"]
        cleaned["llm_generated"] = True
        cleaned["model"] = self.model
        cleaned["tokens_used"] = int(result.get("tokens_used", 0) or 0)
        return cleaned

    def polish_query_answer(self, question: str, fallback_bundle: Dict, context_text: str) -> Dict:
        fallback = default_query_answer_bundle(fallback_bundle)
        system_prompt = (
            "You rewrite grounded operator answers. Return strict JSON with keys: "
            "direct_answer, supporting_points, follow_up, caveat. "
            "Use only the supplied facts. Do not add new facts, numbers, dates, or entities. "
            "direct_answer must be 1-2 sentences. supporting_points must have 2-4 short bullets."
        )
        user_prompt = (
            f"Question: {question}\n\n"
            f"Grounded answer draft: {json.dumps(fallback, ensure_ascii=False)}\n\n"
            f"Grounded facts: {context_text[:2200]}"
        )
        result = self._request_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            validator=validate_query_answer_bundle,
            cleaner=lambda payload: clean_query_answer_bundle(payload, fallback),
            fallback=fallback,
            task_type="query_answer",
            lane="polish",
            max_tokens=420,
            temperature=0.1,
            context_snippet=str(question or "")[:120],
        )
        return clean_query_answer_bundle(result["analysis"], fallback)

    def debate_argument(self, persona: str, system_prompt: str, context: str, fallback: Dict) -> Dict:
        clean_fallback = default_debate_argument(
            persona=persona,
            argument=fallback.get("argument", ""),
            key_point=fallback.get("key_point", ""),
        )
        result = self._request_json(
            system_prompt=system_prompt,
            user_prompt=context,
            validator=validate_debate_argument,
            cleaner=lambda payload: clean_debate_argument(payload, clean_fallback),
            fallback=clean_fallback,
            task_type="debate_argument",
            lane="reason",
            max_tokens=240,
            temperature=0.5,
            context_snippet=context[:120],
        )
        return clean_debate_argument(result["analysis"], clean_fallback)

    def recent_summary(self) -> Dict:
        total = len(self._recent_calls)
        fallbacks = {}
        by_task = {}
        for item in self._recent_calls:
            by_task[item["task_type"]] = int(by_task.get(item["task_type"], 0) or 0) + 1
            if item["fallback_reason"]:
                fallbacks[item["fallback_reason"]] = int(fallbacks.get(item["fallback_reason"], 0) or 0) + 1
        return {
            "total": total,
            "by_task": by_task,
            "fallbacks": fallbacks,
        }

    def _request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        validator,
        cleaner,
        fallback: Dict,
        task_type: str,
        lane: str,
        max_tokens: int,
        temperature: float,
        context_snippet: str,
    ) -> Dict:
        fallback = fallback if isinstance(fallback, dict) else {}
        input_size_estimate = len(str(system_prompt or "")) + len(str(user_prompt or ""))
        if not self.available():
            fallback_reason = "missing_key" if not self.api_key else "budget_blocked"
            self._record_observability(
                task_type=task_type,
                lane=lane,
                success=False,
                fallback_reason=fallback_reason,
                latency_ms=0,
                input_size_estimate=input_size_estimate,
                validation_error="",
                context_snippet=context_snippet,
                tokens_used=0,
            )
            return {"analysis": fallback, "used_fallback": True, "fallback_reason": fallback_reason, "tokens_used": 0}
        try:
            from openai import OpenAI

            started = time.perf_counter()
            client = OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=int(max_tokens or 320),
                temperature=float(temperature or 0.1),
                response_format={"type": "json_object"},
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            raw = response.choices[0].message.content
            result = json.loads(raw or "{}")
            if not callable(validator) or not validator(result):
                raise ValueError("invalid_schema")
            cleaned = cleaner(result) if callable(cleaner) else result
            if not isinstance(cleaned, dict):
                raise ValueError("invalid_cleaner")
            self._calls_this_hour += 1
            self._run_calls += 1
            tokens_used = int(getattr(response.usage, "total_tokens", 0) or 0)
            self._record_observability(
                task_type=task_type,
                lane=lane,
                success=True,
                fallback_reason="",
                latency_ms=latency_ms,
                input_size_estimate=input_size_estimate,
                validation_error="",
                context_snippet=context_snippet,
                tokens_used=tokens_used,
            )
            return {"analysis": cleaned, "used_fallback": False, "fallback_reason": "", "tokens_used": tokens_used}
        except ValueError as exc:
            self._record_observability(
                task_type=task_type,
                lane=lane,
                success=False,
                fallback_reason="validation_error",
                latency_ms=0,
                input_size_estimate=input_size_estimate,
                validation_error=str(exc),
                context_snippet=context_snippet,
                tokens_used=0,
            )
            return {"analysis": fallback, "used_fallback": True, "fallback_reason": "validation_error", "tokens_used": 0}
        except Exception as exc:
            logger.warning("LLM call failed (%s): %s — falling back safely", type(exc).__name__, exc)
            self._record_observability(
                task_type=task_type,
                lane=lane,
                success=False,
                fallback_reason="api_error",
                latency_ms=0,
                input_size_estimate=input_size_estimate,
                validation_error=type(exc).__name__,
                context_snippet=context_snippet,
                tokens_used=0,
            )
            return {"analysis": fallback, "used_fallback": True, "fallback_reason": "api_error", "tokens_used": 0}

    def _record_observability(
        self,
        *,
        task_type: str,
        lane: str,
        success: bool,
        fallback_reason: str,
        latency_ms: int,
        input_size_estimate: int,
        validation_error: str,
        context_snippet: str,
        tokens_used: int,
    ):
        try:
            ensure_agent_tables()
            conn = get_conn(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO llm_usage (model, tokens, context_snippet, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (self.model, int(tokens_used or 0), str(context_snippet or "")[:160], datetime.now(timezone.utc).isoformat()),
            )
            cur.execute(
                """
                INSERT INTO llm_usage_log (
                    cache_key, mode, outcome, created_at,
                    task_type, lane, model, success, fallback_reason,
                    latency_ms, input_size_estimate, validation_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{lane}::{task_type}::{str(context_snippet or '')[:80].lower()}",
                    task_type,
                    "call" if success else (fallback_reason or "fallback"),
                    datetime.now(timezone.utc).isoformat(),
                    task_type,
                    lane,
                    self.model,
                    1 if success else 0,
                    fallback_reason,
                    int(latency_ms or 0),
                    int(input_size_estimate or 0),
                    validation_error,
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        self._recent_calls.append(
            {
                "task_type": task_type,
                "lane": lane,
                "success": bool(success),
                "fallback_reason": str(fallback_reason or ""),
            }
        )
        self._recent_calls = self._recent_calls[-40:]

    def budget_status(self) -> dict:
        return {
            "available": self.available(),
            "api_key_set": bool(self.api_key),
            "calls_this_hour": self._calls_this_hour,
            "per_hour_cap": self.per_hour_cap,
            "run_calls": self._run_calls,
            "per_run_cap": self.per_run_cap,
            "model": self.model,
            "recent_summary": self.recent_summary(),
        }
