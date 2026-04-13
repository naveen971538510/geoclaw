"""
Multi-provider LLM Router — Groq → OpenAI → Gemini automatic failover.

Each provider tracks consecutive failures and backs off automatically.
Backoff formula: min(base_seconds * consecutive_failures, 300).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("geoclaw.llm_router")


@dataclass
class _ProviderState:
    name: str
    failures: int = 0
    total_calls: int = 0
    total_failures: int = 0
    last_failure_ts: float = 0.0
    backoff_seconds: float = 5.0

    @property
    def is_backed_off(self) -> bool:
        if self.failures == 0:
            return False
        return (time.time() - self.last_failure_ts) < min(self.backoff_seconds * self.failures, 300)

    def record_success(self):
        self.failures = 0
        self.total_calls += 1

    def record_failure(self):
        self.failures += 1
        self.total_failures += 1
        self.total_calls += 1
        self.last_failure_ts = time.time()

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "healthy": self.failures == 0,
            "backed_off": self.is_backed_off,
            "consecutive_failures": self.failures,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
        }


# ---------------------------------------------------------------------------
# Provider configs
# ---------------------------------------------------------------------------

_PROVIDERS: List[Dict[str, str]] = [
    {
        "name": "groq",
        "env_key": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
    },
    {
        "name": "openai",
        "env_key": "OPENAI_API_KEY",
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
    {
        "name": "gemini",
        "env_key": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.0-flash",
    },
]

_states: Dict[str, _ProviderState] = {}
_failover_count: int = 0


def _get_state(name: str) -> _ProviderState:
    if name not in _states:
        _states[name] = _ProviderState(name=name)
    return _states[name]


def _call_provider(provider: Dict[str, str], messages: List[Dict], tools: Optional[List] = None, timeout: int = 30) -> Dict[str, Any]:
    api_key = os.environ.get(provider["env_key"], "").strip()
    if not api_key:
        raise ValueError(f"{provider['env_key']} not set")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: Dict[str, Any] = {
        "model": provider["model"],
        "messages": messages,
        "temperature": 0.4,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    resp = requests.post(provider["url"], headers=headers, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def chat(messages: List[Dict], tools: Optional[List] = None, timeout: int = 30) -> Dict[str, Any]:
    """
    Send a chat completion to the first available provider.
    Falls through Groq → OpenAI → Gemini on failure.
    """
    global _failover_count
    errors: List[str] = []

    for provider in _PROVIDERS:
        api_key = os.environ.get(provider["env_key"], "").strip()
        if not api_key:
            continue
        state = _get_state(provider["name"])
        if state.is_backed_off:
            errors.append(f"{provider['name']}: backed off ({state.failures} failures)")
            continue
        try:
            result = _call_provider(provider, messages, tools=tools, timeout=timeout)
            state.record_success()
            logger.info("LLM call succeeded via %s", provider["name"])
            return result
        except Exception as exc:
            state.record_failure()
            errors.append(f"{provider['name']}: {exc}")
            logger.warning("LLM call failed on %s: %s — trying next provider", provider["name"], exc)
            _failover_count += 1

    raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")


def get_status() -> Dict[str, Any]:
    """Return health status for all providers."""
    available = []
    for p in _PROVIDERS:
        key = os.environ.get(p["env_key"], "").strip()
        state = _get_state(p["name"])
        available.append({
            **state.status(),
            "configured": bool(key),
            "model": p["model"],
        })
    return {
        "providers": available,
        "failover_count": _failover_count,
        "active_provider": next(
            (p["name"] for p in _PROVIDERS
             if os.environ.get(p["env_key"], "").strip() and not _get_state(p["name"]).is_backed_off),
            None,
        ),
    }
