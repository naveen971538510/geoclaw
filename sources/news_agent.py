"""
GeoClaw news & sentiment agent: RSS → Groq JSON sentiment → Postgres news_signals.
Cycle every 30 minutes (1800 s).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import feedparser
import psycopg2
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence.groq_briefing import groq_chat_completion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("news_agent")

RSS_FEEDS: Dict[str, str] = {
    
    "BBC Business": "http://feeds.bbci.co.uk/news/business/rss.xml",
    "FT Markets": "https://www.ft.com/rss/home",
}

CYCLE_SECONDS = 1800
GROQ_MODEL_NEWS = "llama-3.1-8b-instant"


def get_database_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def ensure_news_signals_table(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS news_signals (
            id SERIAL PRIMARY KEY,
            headline TEXT NOT NULL,
            source VARCHAR(100) NOT NULL,
            url TEXT,
            sentiment VARCHAR(20) NOT NULL,
            confidence INT NOT NULL,
            reason TEXT,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_news_signals_ts ON news_signals (ts DESC);
        """
    )
    cur.close()


def _parse_sentiment_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


def score_headline(headline: str) -> Tuple[str, int, str]:
    prompt = (
        "Score this financial headline as BULLISH, BEARISH, or NEUTRAL with a confidence score 0-100. "
        'Reply in JSON only: {"sentiment": string, "confidence": int, "reason": string}. '
        f"Headline: {headline}"
    )
    raw = groq_chat_completion(
        [{"role": "user", "content": prompt}],
        model=GROQ_MODEL_NEWS,
        temperature=0.2,
        max_tokens=400,
    )
    data = _parse_sentiment_json(raw)
    sentiment = str(data.get("sentiment", "NEUTRAL")).upper()
    if sentiment not in ("BULLISH", "BEARISH", "NEUTRAL"):
        sentiment = "NEUTRAL"
    conf = int(data.get("confidence", 50))
    conf = max(0, min(100, conf))
    reason = str(data.get("reason", ""))[:2000]
    return sentiment, conf, reason


def fetch_entries(source_name: str, url: str) -> List[Dict[str, Any]]:
    logger.info("Fetching feed %s (%s)", source_name, url)
    r = requests.get(url, timeout=45)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    entries = getattr(parsed, "entries", []) or []
    out = []
    for e in entries[:5]:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or e.get("id") or "").strip()
        if title:
            out.append({"title": title, "link": link})
    return out


def store_signal(conn, headline: str, source: str, url: str, sentiment: str, confidence: int, reason: str) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO news_signals (headline, source, url, sentiment, confidence, reason, ts)
        VALUES (%s, %s, %s, %s, %s, %s, %s);
        """,
        (headline, source[:100], url or None, sentiment[:20], int(confidence), reason, datetime.now(timezone.utc)),
    )
    cur.close()


def run_cycle() -> None:
    url = get_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    try:
        ensure_news_signals_table(conn)
        conn.commit()
        for source_name, feed_url in RSS_FEEDS.items():
            try:
                entries = fetch_entries(source_name, feed_url)
                logger.info("Feed %s: %s entries (top 5)", source_name, len(entries))
                for ent in entries:
                    headline = ent["title"]
                    link = ent["link"]
                    try:
                        sentiment, confidence, reason = score_headline(headline)
                        store_signal(conn, headline, source_name, link, sentiment, confidence, reason)
                        conn.commit()
                        logger.info("Stored: %s | %s %s%%", headline[:60], sentiment, confidence)
                    except Exception as exc:
                        conn.rollback()
                        logger.exception("Failed headline %s: %s", headline[:80], exc)
            except Exception as exc:
                logger.exception("Feed failed %s: %s", source_name, exc)
    finally:
        conn.close()


def main() -> None:
    logger.info("news_agent started; cycle every %s s", CYCLE_SECONDS)
    while True:
        try:
            run_cycle()
        except Exception as exc:
            logger.exception("Cycle error: %s", exc)
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
