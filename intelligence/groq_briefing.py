"""
Groq Cloud API (OpenAI-compatible) for market briefings.
Default model: llama3-8b-8192 (override with GROQ_MODEL).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama3-8b-8192"


def groq_chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.5,
    max_tokens: int = 1200,
) -> str:
    key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    model = (model or os.environ.get("GROQ_MODEL") or DEFAULT_MODEL).strip()
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GROQ_CHAT_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Groq HTTP {exc.code}: {err[:800]}") from exc
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Groq empty response: {data}")
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def build_signals_context(signals: List[Dict[str, Any]], macro: List[Dict[str, Any]], charts: List[Dict[str, Any]]) -> str:
    parts = ["=== SCORED SIGNALS ==="]
    for s in signals[:12]:
        parts.append(
            f"- {s.get('signal_name')}: {s.get('direction')} conf={s.get('confidence')} | {s.get('explanation_plain_english')}"
        )
    parts.append("\n=== MACRO LATEST ===")
    for m in macro[:20]:
        parts.append(
            f"- {m.get('metric_name')}: value={m.get('value')} prev={m.get('previous_value')} pct_ch={m.get('pct_change')}"
        )
    parts.append("\n=== CHART PATTERNS ===")
    for c in charts[:15]:
        parts.append(f"- {c.get('ticker')} {c.get('pattern_name')} {c.get('direction')} conf={c.get('confidence')}")
    return "\n".join(parts)


def generate_dashboard_briefing(context: str) -> str:
    """Three paragraphs plain English for dashboard."""
    system = (
        "You are GeoClaw, an economic intelligence assistant. Write exactly three short paragraphs "
        "in plain English for a professional investor. No markdown headers, no bullet lists inside paragraphs. "
        "Cover macro tone, key risks, and what to watch next based only on the data provided."
    )
    user = f"Data:\n{context}\n\nWrite the three paragraphs now."
    return groq_chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.45,
        max_tokens=900,
    )


def generate_morning_briefing_block(context: str) -> str:
    """Structured morning briefing for Telegram."""
    system = (
        "You are GeoClaw morning briefing. Output with EXACT section headers in this order, each on its own line:\n"
        "MACRO_OVERVIEW: (exactly 3 sentences)\n"
        "TOP_SIGNALS: (numbered 1-3, each one sentence plain English)\n"
        "CHART_WATCH: (one sentence naming one pattern/ticker worth watching)\n"
        "MARKET_BIAS: (one word: BULLISH, BEARISH, or NEUTRAL, then one short sentence)\n"
        "Use only the provided data. Plain English for non-experts."
    )
    user = f"Data:\n{context}"
    return groq_chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.4,
        max_tokens=700,
    )
