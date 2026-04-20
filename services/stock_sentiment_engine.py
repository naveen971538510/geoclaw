"""
Stock Sentiment Engine
======================
Per-ticker sentiment scoring using FinGPT-style instruction prompts
routed through GeoClaw's LLM router (Groq → OpenAI → Gemini).

No GPU needed. Uses existing infrastructure.

Usage:
    from services.stock_sentiment_engine import run_sentiment_analysis
    result = run_sentiment_analysis("NVDA")
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yfinance as yf

from services.llm_router import chat, extract_message_content

logger = logging.getLogger("geoclaw.sentiment")

# FinGPT-style prompt (their instruction format, proven on financial benchmarks)
_SYSTEM_PROMPT = (
    "You are a financial sentiment classifier. "
    "For each news headline and summary provided, classify the sentiment "
    "as POSITIVE, NEUTRAL, or NEGATIVE from an investor's perspective. "
    "Consider market impact, not just tone."
)

_BATCH_PROMPT_TEMPLATE = """Analyze the sentiment of each news item below for stock {ticker}.
Respond ONLY with a JSON array. Each item: {{"id": N, "sentiment": "POSITIVE"|"NEUTRAL"|"NEGATIVE", "confidence": 0-100, "reason": "brief reason"}}.

News items:
{news_block}

JSON array:"""


# ─── News Fetcher ─────────────────────────────────────────────────────────────

def _fetch_ticker_news(ticker: str, max_items: int = 10) -> List[Dict[str, str]]:
    """Fetch recent headlines for ticker via yfinance."""
    try:
        raw = yf.Ticker(ticker).news or []
        items = []
        for item in raw[:max_items]:
            content = item.get("content", {})
            title = str(content.get("title", "")).strip()
            summary = str(content.get("summary", "")).strip()
            pub = str(content.get("pubDate", "")).strip()
            if title:
                items.append({"title": title, "summary": summary[:200], "published": pub})
        return items
    except Exception as exc:
        logger.warning("news fetch failed for %s: %s", ticker, exc)
        return []


# ─── LLM Scoring ─────────────────────────────────────────────────────────────

def _score_headlines(ticker: str, headlines: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Run FinGPT-style batch sentiment via LLM router."""
    if not headlines:
        return []

    news_block = "\n".join(
        f"{i+1}. Title: {h['title']}\n   Summary: {h['summary'] or 'N/A'}"
        for i, h in enumerate(headlines)
    )

    prompt = _BATCH_PROMPT_TEMPLATE.format(ticker=ticker, news_block=news_block)

    try:
        response = chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        raw_text = extract_message_content(response)

        # Extract JSON array from response
        import json, re
        match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if not match:
            logger.warning("no JSON array in LLM response for %s", ticker)
            return []

        scored = json.loads(match.group())
        # Attach original headline text
        for item in scored:
            idx = int(item.get("id", 1)) - 1
            if 0 <= idx < len(headlines):
                item["title"] = headlines[idx]["title"]
                item["published"] = headlines[idx].get("published", "")
        return scored

    except Exception as exc:
        logger.warning("LLM sentiment failed for %s: %s", ticker, exc)
        return []


# ─── Score Aggregation ────────────────────────────────────────────────────────

def _aggregate_scores(scored_items: List[Dict[str, Any]]) -> tuple[float, str, Dict]:
    """Convert per-headline results → single 0-100 sentiment score."""
    if not scored_items:
        return 50.0, "NEUTRAL", {}

    label_map = {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1}
    counts = {"POSITIVE": 0, "NEUTRAL": 0, "NEGATIVE": 0}
    weighted_sum = 0.0
    total_confidence = 0.0

    for item in scored_items:
        label = str(item.get("sentiment", "NEUTRAL")).upper()
        conf = float(item.get("confidence", 50)) / 100.0
        if label not in label_map:
            label = "NEUTRAL"
        counts[label] += 1
        weighted_sum += label_map[label] * conf
        total_confidence += conf

    if total_confidence == 0:
        return 50.0, "NEUTRAL", counts

    # Normalize to -1 → +1 then map to 0 → 100
    raw_score = weighted_sum / total_confidence          # -1.0 to +1.0
    sentiment_score = round((raw_score + 1.0) / 2.0 * 100, 1)  # 0-100

    # Direction label
    if sentiment_score >= 72:
        direction = "VERY BULLISH"
    elif sentiment_score >= 58:
        direction = "BULLISH"
    elif sentiment_score >= 42:
        direction = "NEUTRAL"
    elif sentiment_score >= 28:
        direction = "BEARISH"
    else:
        direction = "VERY BEARISH"

    return sentiment_score, direction, counts


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def run_sentiment_analysis(ticker: str, max_news: int = 10) -> Dict[str, Any]:
    """
    Full per-ticker sentiment analysis.
    Returns sentiment_score (0-100), direction, and per-headline breakdown.
    """
    ticker = ticker.upper().strip()

    headlines = _fetch_ticker_news(ticker, max_items=max_news)
    if not headlines:
        return {
            "ticker": ticker,
            "sentiment_score": 50.0,
            "direction": "NEUTRAL",
            "note": "No news found",
            "headlines_scored": 0,
            "analysed_at": datetime.now(timezone.utc).isoformat(),
        }

    scored = _score_headlines(ticker, headlines)

    sentiment_score, direction, counts = _aggregate_scores(scored)

    return {
        "ticker": ticker,
        "sentiment_score": sentiment_score,
        "direction": direction,
        "headlines_scored": len(scored),
        "breakdown": counts,
        "top_headlines": scored[:5],
        "analysed_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Batch Runner ─────────────────────────────────────────────────────────────

def run_sentiment_batch(tickers: List[str], delay: float = 1.0) -> List[Dict[str, Any]]:
    """Run sentiment analysis for multiple tickers with rate-limit delay."""
    results = []
    for t in tickers:
        try:
            results.append(run_sentiment_analysis(t))
            time.sleep(delay)
        except Exception as exc:
            logger.warning("batch sentiment failed for %s: %s", t, exc)
            results.append({"ticker": t, "error": str(exc), "sentiment_score": 50.0})
    return results
