"""
JP225 Neural Intelligence Schema
=================================
Multi-layer intelligence engine for Nikkei 225 CFD.

Architecture:
  Layer 1 — Factor Ingestion   : fetch 7 correlated instruments in parallel
  Layer 2 — Signal Extraction  : score each factor -100 → +100 for JP225 impact
  Layer 3 — News NLP           : scan headlines, extract JP225-relevant signals
  Layer 4 — LLM Synthesis      : Groq fast-model reasons across all layers
  Layer 5 — Composite Output   : single JP225 direction score + trade bias
"""

from __future__ import annotations

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.logging_service import get_logger

logger = get_logger("jp225_neural")

# ─────────────────────────────────────────────
# Layer 1: Factor definitions
# ─────────────────────────────────────────────

FACTORS: List[Dict[str, Any]] = [
    {
        "id": "usdjpy",
        "label": "USD/JPY",
        "symbol": "USDJPY=X",
        "weight": 0.30,
        "relation": "inverse",   # yen strengthens → JP225 falls
        "threshold_pct": 0.3,
        "description": "Yen strength pressures exporter earnings; weak yen lifts JP225.",
    },
    {
        "id": "sp500_futures",
        "label": "S&P500 Futures",
        "symbol": "ES=F",
        "weight": 0.25,
        "relation": "positive",
        "threshold_pct": 0.5,
        "description": "US equity risk appetite leads Asia open direction.",
    },
    {
        "id": "vix",
        "label": "VIX Fear Index",
        "symbol": "^VIX",
        "weight": 0.15,
        "relation": "inverse",
        "threshold_pct": 5.0,    # VIX moves in larger %
        "description": "Elevated fear suppresses JP225; VIX falling is bullish.",
    },
    {
        "id": "oil",
        "label": "Brent Crude",
        "symbol": "CL=F",
        "weight": 0.10,
        "relation": "inverse",   # Japan is an oil importer
        "threshold_pct": 1.0,
        "description": "Higher oil raises import costs; lower oil is net positive for Japan.",
    },
    {
        "id": "sox",
        "label": "Semis (SOXX)",
        "symbol": "SOXX",
        "weight": 0.10,
        "relation": "positive",
        "threshold_pct": 1.0,
        "description": "Japan tech/chip names (Advantest, Tokyo Electron) track global semi cycle.",
    },
    {
        "id": "us10y",
        "label": "US 10Y Yield",
        "symbol": "^TNX",
        "weight": 0.05,
        "relation": "inverse",
        "threshold_pct": 2.0,
        "description": "Rising US yields pressure equity valuations and can strengthen USD vs JPY.",
    },
    {
        "id": "jp225_self",
        "label": "JP225 Momentum",
        "symbol": "^N225",
        "weight": 0.05,
        "relation": "positive",
        "threshold_pct": 0.5,
        "description": "Own price momentum — trend continuation bias.",
    },
]

# ─────────────────────────────────────────────
# Layer 1: Factor Fetcher
# ─────────────────────────────────────────────

def _fetch_single_factor(factor: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch a single yfinance instrument and return enriched factor dict."""
    try:
        import yfinance as yf
        info = yf.Ticker(factor["symbol"]).fast_info
        price = float(info.last_price or 0)
        prev  = float(info.previous_close or price)
        change_pct = ((price - prev) / prev * 100) if prev else 0.0
        return {**factor, "price": round(price, 4), "prev_close": round(prev, 4),
                "change_pct": round(change_pct, 4), "fetch_ok": True}
    except Exception as exc:
        logger.warning("Factor fetch failed %s: %s", factor["symbol"], exc)
        return {**factor, "price": None, "prev_close": None,
                "change_pct": None, "fetch_ok": False}


def fetch_all_factors() -> List[Dict[str, Any]]:
    """Fetch all 7 factors in parallel. Returns in ~1s."""
    results = []
    with ThreadPoolExecutor(max_workers=7) as ex:
        futures = {ex.submit(_fetch_single_factor, f): f for f in FACTORS}
        for fut in as_completed(futures):
            results.append(fut.result())
    # preserve canonical order
    order = {f["id"]: i for i, f in enumerate(FACTORS)}
    results.sort(key=lambda r: order.get(r["id"], 99))
    return results


# ─────────────────────────────────────────────
# Layer 2: Signal Extraction
# ─────────────────────────────────────────────

def score_factor(factor: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a factor's change_pct into a JP225 signal score [-100, +100].
    Positive score = bullish for JP225.
    """
    if not factor.get("fetch_ok") or factor.get("change_pct") is None:
        return {**factor, "score": 0, "signal": "unavailable", "direction": "neutral"}

    pct   = float(factor["change_pct"])
    thr   = float(factor.get("threshold_pct", 0.5))
    rel   = factor.get("relation", "positive")
    sign  = 1 if rel == "positive" else -1

    # Raw score: how many threshold multiples did it move, capped at ±100
    raw = sign * min(abs(pct) / thr * 50, 100) * (1 if pct >= 0 else -1)
    score = round(max(-100, min(100, raw)))

    if abs(score) >= 60:
        signal = "strong_bullish" if score > 0 else "strong_bearish"
    elif abs(score) >= 25:
        signal = "bullish" if score > 0 else "bearish"
    else:
        signal = "neutral"

    direction = "bullish" if score > 0 else ("bearish" if score < 0 else "neutral")

    return {
        **factor,
        "score": score,
        "signal": signal,
        "direction": direction,
        "score_contribution": round(score * factor["weight"], 2),
    }


def extract_signals(factors: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float]:
    """Score all factors. Returns (scored_factors, composite_score)."""
    scored = [score_factor(f) for f in factors]
    composite = sum(f["score_contribution"] for f in scored if f.get("fetch_ok"))
    return scored, round(composite, 2)


# ─────────────────────────────────────────────
# Layer 3: News NLP scoring
# ─────────────────────────────────────────────

JP225_BULLISH_TERMS = [
    "japan stocks rise", "nikkei gains", "nikkei rally", "boj holds",
    "yen weakens", "dollar strengthens", "risk on", "asia equities rise",
    "us-iran talks", "oil falls", "oil eases", "ceasefire", "trade deal",
    "semiconductor", "chip demand", "export growth", "japan gdp",
    "bank of japan hold", "stimulus", "reflation",
]

JP225_BEARISH_TERMS = [
    "japan stocks fall", "nikkei drops", "nikkei plunges", "boj hike",
    "yen strengthens", "yen surges", "risk off", "asia selloff",
    "oil spike", "oil surges", "strait of hormuz", "escalation",
    "tariff", "trade war", "japan recession", "japan gdp falls",
    "rate hike", "policy tightening", "tech selloff",
]


def score_news_headlines(headlines: List[str]) -> Dict[str, Any]:
    """Scan headlines for JP225 bullish/bearish terms. Returns NLP signal."""
    bull_hits, bear_hits = [], []
    text = " ".join(h.lower() for h in headlines)

    for term in JP225_BULLISH_TERMS:
        if term in text:
            bull_hits.append(term)
    for term in JP225_BEARISH_TERMS:
        if term in text:
            bear_hits.append(term)

    bull_score = min(len(bull_hits) * 15, 100)
    bear_score = min(len(bear_hits) * 15, 100)
    net = bull_score - bear_score

    return {
        "bullish_hits": bull_hits,
        "bearish_hits": bear_hits,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "net_score": net,
        "direction": "bullish" if net > 10 else ("bearish" if net < -10 else "neutral"),
        "headlines_scanned": len(headlines),
    }


# ─────────────────────────────────────────────
# Layer 4: LLM Synthesis
# ─────────────────────────────────────────────

_SYNTHESIS_PROMPT = """You are a quantitative analyst specialising in the Nikkei 225 CFD (^N225).

You will receive:
- FACTOR SIGNALS: 7 correlated instruments scored for JP225 impact
- COMPOSITE SCORE: weighted sum of factor scores (-100 bearish → +100 bullish)
- NEWS NLP: headline scan results
- JP225 PRICE: current and change

Your job: synthesise all layers into a precise, actionable JP225 intelligence brief.

Output a JSON object with exactly these keys:
{{
  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": 0-100,
  "composite_score": <number>,
  "short_thesis": "<one sentence — why is JP225 moving right now>",
  "key_driver": "<the single most important factor>",
  "risk_factor": "<the single biggest risk to the thesis>",
  "signals": [
    {{"factor": "<name>", "direction": "bullish|bearish|neutral", "score": <int>, "note": "<10 words>"}}
  ],
  "trade_note": "<2-3 sentences — what a trader should watch for the next 4 hours>",
  "horizon": "intraday",
  "generated_at": "<iso timestamp>"
}}

FACTOR SIGNALS:
{factor_json}

COMPOSITE SCORE: {composite}

NEWS NLP:
{news_json}

JP225: {jp225_price} ({jp225_change:+.2f}%)

Respond with only the JSON object. No markdown, no explanation."""


def synthesise_with_llm(
    scored_factors: List[Dict[str, Any]],
    composite: float,
    news_signal: Dict[str, Any],
    jp225_price: float,
    jp225_change_pct: float,
) -> Optional[Dict[str, Any]]:
    """Layer 4 — call Groq (fast) or OpenAI to synthesise all signals."""
    factor_summary = [
        {
            "factor": f["label"],
            "symbol": f["symbol"],
            "change_pct": f.get("change_pct"),
            "score": f.get("score", 0),
            "direction": f.get("direction", "neutral"),
            "weight": f["weight"],
        }
        for f in scored_factors
    ]
    prompt = _SYNTHESIS_PROMPT.format(
        factor_json=json.dumps(factor_summary, indent=2),
        composite=composite,
        news_json=json.dumps(news_signal, indent=2),
        jp225_price=jp225_price,
        jp225_change=jp225_change_pct,
    )

    # Try Groq first (fastest, cheapest)
    try:
        import os
        from groq import Groq
        client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        result = json.loads(raw)
        result["llm_provider"] = "groq"
        return result
    except Exception as exc:
        logger.warning("Groq synthesis failed: %s", exc)

    # Fallback: OpenAI
    try:
        import os
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        result["llm_provider"] = "openai"
        return result
    except Exception as exc:
        logger.warning("OpenAI synthesis failed: %s", exc)

    return None


# ─────────────────────────────────────────────
# Layer 5: Composite output + cache
# ─────────────────────────────────────────────

_SCHEMA_CACHE: Dict[str, Any] = {"result": None, "ts": 0}
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_TTL = 60  # seconds — refresh every minute


def run_neural_schema(
    headlines: Optional[List[str]] = None,
    jp225_price: float = 0.0,
    jp225_change_pct: float = 0.0,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Run the full 5-layer neural schema. Returns cached result if < TTL.
    Safe to call on every API request.
    """
    now = time.time()
    with _SCHEMA_LOCK:
        cached = _SCHEMA_CACHE.get("result")
        if cached and not force and (now - _SCHEMA_CACHE["ts"]) < _SCHEMA_TTL:
            return {**cached, "cached": True}

    try:
        t0 = time.time()

        # L1: fetch factors
        factors = fetch_all_factors()

        # L2: extract signals
        scored, composite = extract_signals(factors)

        # L3: news NLP
        news_signal = score_news_headlines(headlines or [])

        # L4: LLM synthesis
        synthesis = synthesise_with_llm(
            scored, composite, news_signal, jp225_price, jp225_change_pct
        )

        # L5: assemble output
        if synthesis:
            bias       = str(synthesis.get("bias") or "NEUTRAL").upper()
            confidence = int(synthesis.get("confidence") or 50)
            short_thesis = str(synthesis.get("short_thesis") or "")
            key_driver   = str(synthesis.get("key_driver") or "")
            risk_factor  = str(synthesis.get("risk_factor") or "")
            trade_note   = str(synthesis.get("trade_note") or "")
            llm_signals  = synthesis.get("signals") or []
            llm_provider = str(synthesis.get("llm_provider") or "unknown")
        else:
            # Pure quant fallback — no LLM
            bias = "BULLISH" if composite > 15 else ("BEARISH" if composite < -15 else "NEUTRAL")
            confidence = min(90, max(10, int(abs(composite))))
            short_thesis = f"JP225 composite factor score: {composite:+.1f}"
            key_driver = max(scored, key=lambda f: abs(f.get("score_contribution", 0)), default={}).get("label", "—")
            risk_factor = "LLM synthesis unavailable"
            trade_note = ""
            llm_signals = []
            llm_provider = "quant_only"

        elapsed = round(time.time() - t0, 2)

        result = {
            "bias": bias,
            "confidence": confidence,
            "composite_score": composite,
            "short_thesis": short_thesis,
            "key_driver": key_driver,
            "risk_factor": risk_factor,
            "trade_note": trade_note,
            "factors": [
                {
                    "id": f["id"],
                    "label": f["label"],
                    "symbol": f["symbol"],
                    "price": f.get("price"),
                    "change_pct": f.get("change_pct"),
                    "score": f.get("score", 0),
                    "direction": f.get("direction", "neutral"),
                    "weight": f["weight"],
                    "description": f["description"],
                }
                for f in scored
            ],
            "news_signal": news_signal,
            "llm_signals": llm_signals,
            "llm_provider": llm_provider,
            "elapsed_seconds": elapsed,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
        }

        with _SCHEMA_LOCK:
            _SCHEMA_CACHE["result"] = result
            _SCHEMA_CACHE["ts"] = time.time()

        logger.info(
            "Neural schema: bias=%s confidence=%s composite=%.1f elapsed=%.2fs llm=%s",
            bias, confidence, composite, elapsed, llm_provider,
        )
        return result

    except Exception as exc:
        logger.error("Neural schema failed: %s", exc)
        return {
            "bias": "NEUTRAL",
            "confidence": 0,
            "composite_score": 0,
            "short_thesis": f"Schema error: {exc}",
            "key_driver": "—",
            "risk_factor": "—",
            "trade_note": "",
            "factors": [],
            "news_signal": {},
            "llm_signals": [],
            "llm_provider": "error",
            "elapsed_seconds": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "error": str(exc),
        }
