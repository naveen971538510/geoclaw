"""
Forward-looking market scenarios from macro signal context (Groq + Gemini fallback).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from intelligence.groq_briefing import groq_chat_completion

SCENARIO_MODEL = "llama-3.1-8b-instant"


def generate_scenarios(signals: List[Dict[str, Any]]) -> str:
    """
    `signals` is a list of macro-style dicts (e.g. metric_name, value, previous_value, pct_change).
    Returns plain-text scenarios from the LLM.
    """
    lines = []
    for s in signals:
        lines.append(json.dumps(s, default=str))
    blob = "\n".join(lines) if lines else "(no macro signals provided)"

    prompt = (
        "Given these macro signals, generate 3 forward-looking market scenarios for the next 30 days. "
        "Label them BULL CASE, BASE CASE, BEAR CASE. For each give: trigger conditions, likely market impact, "
        "probability estimate. Format as plain text.\n\nSignals:\n"
        f"{blob}"
    )
    return groq_chat_completion(
        [{"role": "user", "content": prompt}],
        model=SCENARIO_MODEL,
        temperature=0.5,
        max_tokens=1200,
    )
