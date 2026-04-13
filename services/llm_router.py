"""
LLM Router — Multi-provider support for the GeoClaw Agent Brain.

Tries providers in priority order with automatic failover:
  1. Groq (fast, cheap — llama-3.3-70b-versatile)
  2. OpenAI (reliable fallback — gpt-4o-mini)
  3. Google Gemini (backup — gemini-2.0-flash)

Each provider is called with the OpenAI-compatible chat completions format.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from config import _load_local_env, ENV_FILE

_load_local_env(ENV_FILE)

logger = logging.getLogger("geoclaw.llm_router")


class LLMProvider:
    def __init__(self, name: str, api_key: str, base_url: str, model: str, timeout: int = 30):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._backoff_until = 0.0

    @property
    def available(self) -> bool:
        if not self.api_key:
            return False
        if time.time() < self._backoff_until:
            return False
        return True

    def call(self, messages: List[Dict], tools: Optional[List] = None, max_tokens: int = 1024, temperature: float = 0.3) -> Dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = requests.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )

        if resp.status_code == 429:
            self._record_failure(backoff_seconds=60)
            raise requests.HTTPError(f"429 rate limited from {self.name}")

        if resp.status_code >= 500:
            self._record_failure(backoff_seconds=30)
            raise requests.HTTPError(f"{resp.status_code} server error from {self.name}")

        resp.raise_for_status()
        self._consecutive_failures = 0
        return resp.json()

    def _record_failure(self, backoff_seconds: int = 30):
        self._consecutive_failures += 1
        self._last_failure_time = time.time()
        self._backoff_until = time.time() + min(
            backoff_seconds * self._consecutive_failures, 300
        )
        logger.warning(
            "%s failure #%d — backing off for %ds",
            self.name, self._consecutive_failures,
            min(backoff_seconds * self._consecutive_failures, 300),
        )

    def status(self) -> Dict:
        return {
            "name": self.name,
            "model": self.model,
            "available": self.available,
            "has_key": bool(self.api_key),
            "consecutive_failures": self._consecutive_failures,
            "backoff_until": datetime.fromtimestamp(self._backoff_until, tz=timezone.utc).isoformat() if self._backoff_until > time.time() else None,
        }


class LLMRouter:
    """Routes LLM calls across multiple providers with failover."""

    def __init__(self):
        self.providers: List[LLMProvider] = []
        self._last_provider = ""
        self._total_calls = 0
        self._failover_count = 0
        self._setup_providers()

    def _setup_providers(self):
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
        if groq_key:
            self.providers.append(LLMProvider(
                name="groq",
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1/chat/completions",
                model=groq_model,
                timeout=30,
            ))

        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_model = os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini").strip()
        if openai_key:
            self.providers.append(LLMProvider(
                name="openai",
                api_key=openai_key,
                base_url="https://api.openai.com/v1/chat/completions",
                model=openai_model,
                timeout=30,
            ))

        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if gemini_key:
            self.providers.append(LLMProvider(
                name="gemini",
                api_key=gemini_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                model="gemini-2.0-flash",
                timeout=30,
            ))

        if not self.providers:
            logger.error("No LLM providers configured. Set GROQ_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY.")

    def call(
        self,
        messages: List[Dict],
        tools: Optional[List] = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Dict:
        self._total_calls += 1
        errors = []

        for idx, provider in enumerate(self.providers):
            if not provider.available:
                errors.append(f"{provider.name}: unavailable (backoff or no key)")
                continue

            try:
                logger.info("LLM call via %s (%s)", provider.name, provider.model)
                result = provider.call(messages, tools=tools, max_tokens=max_tokens, temperature=temperature)
                self._last_provider = provider.name
                if idx > 0:
                    self._failover_count += 1
                    logger.info("Failover success: %s -> %s", self.providers[0].name, provider.name)
                return result
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                logger.warning("LLM provider %s failed: %s", provider.name, exc)
                continue

        error_summary = "; ".join(errors) if errors else "no providers configured"
        raise RuntimeError(f"All LLM providers failed: {error_summary}")

    def status(self) -> Dict:
        return {
            "providers": [p.status() for p in self.providers],
            "total_calls": self._total_calls,
            "failover_count": self._failover_count,
            "last_provider": self._last_provider,
            "active_providers": sum(1 for p in self.providers if p.available),
        }


_router: Optional[LLMRouter] = None


def get_llm_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
