"""
News Store
==========
SQLite persistence for news items + Groq-powered sentiment scoring.

Schema:
  news_items   — raw articles/posts from all sources
  news_signals — per-ticker aggregated sentiment scores (updated each cycle)

Sentiment scoring uses Groq (llama3-8b-8192) — fast and cheap.
Falls back to keyword scoring if Groq is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("geoclaw.news_store")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "news.db")


# ─── Schema ───────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS news_items (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT,
    body         TEXT,
    tickers      TEXT DEFAULT '[]',
    sentiment    TEXT DEFAULT 'NEUTRAL',
    sentiment_score REAL DEFAULT 0.0,
    published_at TEXT,
    fetched_at   TEXT,
    meta         TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_source    ON news_items(source);

CREATE TABLE IF NOT EXISTS news_signals (
    ticker       TEXT PRIMARY KEY,
    sentiment    TEXT DEFAULT 'NEUTRAL',
    score        REAL DEFAULT 0.0,
    article_count INTEGER DEFAULT 0,
    top_headlines TEXT DEFAULT '[]',
    updated_at   TEXT
);
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _get_conn() as conn:
        conn.executescript(_DDL)
    logger.info("news_store: DB initialised at %s", DB_PATH)


# ─── Sentiment scoring ────────────────────────────────────────────────────────

_GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
_GROQ_MODEL = "llama3-8b-8192"

_POS_WORDS = {"surge", "soar", "rally", "beat", "record", "gain", "profit",
              "bullish", "buy", "upgrade", "strong", "growth", "rise", "up",
              "breakout", "outperform", "positive", "win", "boom"}
_NEG_WORDS = {"crash", "fall", "drop", "plunge", "loss", "miss", "bearish",
              "sell", "downgrade", "weak", "decline", "down", "risk", "cut",
              "warn", "layoff", "fraud", "debt", "fear", "recession"}


def _keyword_sentiment(text: str) -> Tuple[str, float]:
    """Fallback keyword-based sentiment."""
    lower = text.lower()
    pos = sum(1 for w in _POS_WORDS if w in lower)
    neg = sum(1 for w in _NEG_WORDS if w in lower)
    total = pos + neg
    if total == 0:
        return "NEUTRAL", 0.0
    score = (pos - neg) / total
    if score > 0.2:
        return "POSITIVE", round(score, 2)
    if score < -0.2:
        return "NEGATIVE", round(score, 2)
    return "NEUTRAL", round(score, 2)


def _groq_sentiment(text: str) -> Tuple[str, float]:
    """Score sentiment using Groq llama3 — fast and cheap."""
    if not _GROQ_KEY:
        return _keyword_sentiment(text)
    try:
        from groq import Groq
        client = Groq(api_key=_GROQ_KEY)
        prompt = (
            "You are a financial sentiment classifier. "
            "Reply with EXACTLY one line: SENTIMENT: [POSITIVE|NEGATIVE|NEUTRAL] SCORE: [0.0 to 1.0 for positive, -1.0 to 0.0 for negative, 0.0 for neutral]\n\n"
            f"Text: {text[:400]}"
        )
        resp = client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=30,
        )
        line = resp.choices[0].message.content.strip()
        # Parse: "SENTIMENT: POSITIVE SCORE: 0.8"
        import re
        sm = re.search(r'SENTIMENT:\s*(POSITIVE|NEGATIVE|NEUTRAL)', line, re.I)
        sc = re.search(r'SCORE:\s*([-\d.]+)', line)
        sentiment = sm.group(1).upper() if sm else "NEUTRAL"
        score = float(sc.group(1)) if sc else 0.0
        return sentiment, round(max(-1.0, min(1.0, score)), 2)
    except Exception as exc:
        logger.debug("groq sentiment failed: %s", exc)
        return _keyword_sentiment(text)


def score_sentiment(title: str, body: str = "") -> Tuple[str, float]:
    text = f"{title}. {body}"[:500]
    return _groq_sentiment(text)


# ─── Storage ──────────────────────────────────────────────────────────────────

def upsert_news_items(items: List[Dict[str, Any]]) -> int:
    """
    Insert new items (skip existing by id).
    Scores sentiment for items that don't have it yet.
    Returns count of newly inserted items.
    """
    if not items:
        return 0
    conn = _get_conn()
    inserted = 0
    for item in items:
        # Check if exists
        existing = conn.execute(
            "SELECT id FROM news_items WHERE id = ?", (item["id"],)
        ).fetchone()
        if existing:
            continue

        # Score sentiment
        sentiment, score = score_sentiment(item.get("title", ""), item.get("body", ""))
        tickers_json = json.dumps(list(set(item.get("tickers", []))))
        meta_json    = json.dumps(item.get("meta", {}))

        conn.execute("""
            INSERT OR IGNORE INTO news_items
              (id, source, title, url, body, tickers, sentiment, sentiment_score,
               published_at, fetched_at, meta)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            item["id"], item["source"], item.get("title", ""),
            item.get("url", ""), item.get("body", ""),
            tickers_json, sentiment, score,
            item.get("published_at", ""), item.get("fetched_at", ""),
            meta_json,
        ))
        inserted += 1

    conn.commit()
    conn.close()
    logger.info("news_store: inserted %d new items", inserted)
    return inserted


def rebuild_ticker_signals():
    """
    Aggregate news sentiment per ticker from last 24h.
    Updates news_signals table.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc).replace(microsecond=0) -
              timedelta(hours=24)).isoformat()
    conn = _get_conn()
    rows = conn.execute("""
        SELECT tickers, sentiment, sentiment_score, title, published_at
        FROM news_items
        WHERE published_at >= ?
        ORDER BY published_at DESC
    """, (cutoff,)).fetchall()

    ticker_data: Dict[str, List] = {}
    for row in rows:
        try:
            tickers = json.loads(row["tickers"])
        except Exception:
            continue
        for t in tickers:
            if t not in ticker_data:
                ticker_data[t] = []
            ticker_data[t].append({
                "sentiment": row["sentiment"],
                "score": row["sentiment_score"],
                "title": row["title"],
                "published_at": row["published_at"],
            })

    now = datetime.now(timezone.utc).isoformat()
    for ticker, data in ticker_data.items():
        if not data:
            continue
        avg_score = sum(d["score"] for d in data) / len(data)
        pos = sum(1 for d in data if d["sentiment"] == "POSITIVE")
        neg = sum(1 for d in data if d["sentiment"] == "NEGATIVE")
        if pos > neg and avg_score > 0.1:
            agg_sentiment = "POSITIVE"
        elif neg > pos and avg_score < -0.1:
            agg_sentiment = "NEGATIVE"
        else:
            agg_sentiment = "NEUTRAL"
        headlines = json.dumps([d["title"][:120] for d in data[:5]])
        conn.execute("""
            INSERT OR REPLACE INTO news_signals
              (ticker, sentiment, score, article_count, top_headlines, updated_at)
            VALUES (?,?,?,?,?,?)
        """, (ticker, agg_sentiment, round(avg_score, 3),
              len(data), headlines, now))

    conn.commit()
    conn.close()
    logger.info("news_store: rebuilt signals for %d tickers", len(ticker_data))


# ─── Query helpers ────────────────────────────────────────────────────────────

def get_recent_news(limit: int = 60, source: Optional[str] = None,
                    ticker: Optional[str] = None) -> List[Dict]:
    conn = _get_conn()
    if ticker:
        rows = conn.execute("""
            SELECT * FROM news_items
            WHERE tickers LIKE ?
            ORDER BY published_at DESC LIMIT ?
        """, (f'%"{ticker}"%', limit)).fetchall()
    elif source:
        rows = conn.execute("""
            SELECT * FROM news_items WHERE source LIKE ?
            ORDER BY published_at DESC LIMIT ?
        """, (f"%{source}%", limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM news_items
            ORDER BY published_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticker_signal(ticker: str) -> Optional[Dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM news_signals WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_signals() -> List[Dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM news_signals ORDER BY ABS(score) DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_news_stats() -> Dict:
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) as n FROM news_items GROUP BY source ORDER BY n DESC"
    ).fetchall()
    conn.close()
    return {
        "total_items": total,
        "by_source": {r["source"]: r["n"] for r in by_source},
    }
