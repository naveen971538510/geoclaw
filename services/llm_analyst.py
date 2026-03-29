import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH, ENV_FILE, OPENAI_API_KEY, OPENAI_MODEL
from services.db_helpers import get_conn
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
- confidence_basis: what would change your mind (what evidence would contradict this)

Be precise. Be professional. Think like a macro hedge fund analyst."""

VALID_TIMEFRAMES = {"immediate", "days", "weeks", "months"}
VALID_RISKS = {"HIGH", "MEDIUM", "LOW"}


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


def _clamp_delta(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.05
    return max(-0.20, min(0.25, number))


def _clean_text(value, default: str = "") -> str:
    return str(value or default).strip()


class LLMAnalyst:
    def __init__(self, db_path=None):
        _load_env_file()
        self.api_key = OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "").strip()
        self.model = os.environ.get("GEOCLAW_LLM_MODEL", "").strip() or OPENAI_MODEL or "gpt-4o-mini"
        self.db_path = str(db_path or DB_PATH)
        self._calls_this_hour = 0
        self._hour_window_start = time.time()
        self.per_hour_cap = int(os.environ.get("LLM_BUDGET_PER_HOUR", "20") or 20)
        self.per_run_cap = int(os.environ.get("LLM_CALLS_PER_RUN", "5") or 5)
        self._run_calls = 0

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
        if not self.available():
            return None
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.api_key)
            context = ""
            if existing_thesis:
                context = (
                    f"\nExisting thesis to update: {existing_thesis.get('thesis_key', '')}\n"
                    f"Current confidence: {float(existing_thesis.get('confidence', 0.5) or 0.5):.0%}"
                )
            user_msg = (
                f"Analyse this news article:{context}\n\n"
                f"HEADLINE: {headline or ''}\n\n"
                f"BODY: {(body or '')[:800] if body else '(no body)'}\n\n"
                "Return JSON analysis."
            )
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=600,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
            result = json.loads(raw or "{}")
            cleaned = {
                "thesis_key": _clean_text(result.get("thesis_key"), headline),
                "confidence_delta": _clamp_delta(result.get("confidence_delta")),
                "timeframe": _clean_text(result.get("timeframe"), "days").lower(),
                "terminal_risk": _clean_text(result.get("terminal_risk"), "LOW").upper(),
                "market_implication": _clean_text(result.get("market_implication")),
                "watchlist_suggestion": _clean_text(result.get("watchlist_suggestion")),
                "reasoning": _clean_text(result.get("reasoning")),
                "confidence_basis": _clean_text(result.get("confidence_basis")),
                "llm_generated": True,
                "model": self.model,
                "tokens_used": int(getattr(response.usage, "total_tokens", 0) or 0),
            }
            if cleaned["timeframe"] not in VALID_TIMEFRAMES:
                cleaned["timeframe"] = "days"
            if cleaned["terminal_risk"] not in VALID_RISKS:
                cleaned["terminal_risk"] = "LOW"
            self._calls_this_hour += 1
            self._run_calls += 1
            if self.db_path:
                self._log_usage(cleaned["tokens_used"], headline[:80])
            logger.info(
                "LLM analysis: delta=%.3f risk=%s tokens=%s",
                cleaned["confidence_delta"],
                cleaned["terminal_risk"],
                cleaned["tokens_used"],
            )
            return cleaned
        except Exception as exc:
            logger.warning("LLM call failed (%s): %s — falling back to rule engine", type(exc).__name__, exc)
            return None

    def _log_usage(self, tokens: int, context: str):
        try:
            conn = get_conn(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO llm_usage (model, tokens, context_snippet, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (self.model, int(tokens or 0), str(context or "")[:160], datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception:
            return

    def budget_status(self) -> dict:
        return {
            "available": self.available(),
            "api_key_set": bool(self.api_key),
            "calls_this_hour": self._calls_this_hour,
            "per_hour_cap": self.per_hour_cap,
            "run_calls": self._run_calls,
            "per_run_cap": self.per_run_cap,
            "model": self.model,
        }
