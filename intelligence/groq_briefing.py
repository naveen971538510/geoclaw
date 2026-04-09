"""
Groq Cloud API (OpenAI-compatible) for market briefings.
Falls back to Google Gemini 1.5 Flash if Groq fails or returns non-200.
Default model: llama-3.1-8b-instant (override with GROQ_MODEL).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

DEFAULT_MODEL = "llama-3.1-8b-instant"


def _gemini_completion(messages: List[Dict[str, str]]) -> str:
    import google.generativeai as genai

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key or not str(gemini_key).strip():
        raise RuntimeError("GEMINI_API_KEY is not set")
    os.environ["GEMINI_API_KEY"] = str(gemini_key).strip()
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = messages[-1]["content"]
    response = model.generate_content(prompt)
    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None):
        parts = response.candidates[0].content.parts
        text = "".join(getattr(p, "text", "") for p in parts)
    return str(text).strip()


def groq_chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.5,
    max_tokens: int = 1200,
) -> str:
    groq_err: Optional[str] = None
    api_key = os.environ.get("GROQ_API_KEY")
    if api_key and str(api_key).strip():
        api_key = str(api_key).strip()
        model = (model or os.environ.get("GROQ_MODEL") or DEFAULT_MODEL).strip()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=90,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:800]}")
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError(f"Groq empty response: {data}")
            msg = choices[0].get("message") or {}
            return str(msg.get("content") or "").strip()
        except Exception as exc:
            groq_err = str(exc)
    else:
        groq_err = "GROQ_API_KEY is not set"

    gemini_err: Optional[str] = None
    try:
        return _gemini_completion(messages)
    except Exception as exc:
        gemini_err = str(exc)

    raise RuntimeError(f"Groq failed: {groq_err}; Gemini failed: {gemini_err}")


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
