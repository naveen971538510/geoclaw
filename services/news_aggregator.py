"""
News Aggregator
===============
Pulls live news from multiple free sources:
  - RSS feeds: Reuters, CNBC, MarketWatch, Yahoo Finance, Seeking Alpha
  - Reddit: r/investing, r/wallstreetbets, r/stocks (no auth — public JSON API)
  - StockTwits: trending + per-ticker stream (no auth)

No paid API keys required.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger("geoclaw.news")

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; GeoClaw/2.0; research bot)",
    "Accept": "application/json, text/html, */*",
})

# ─── Known tracked tickers for mention extraction ────────────────────────────

_TRACKED = {
    "SPY","QQQ","GLD","USO","GBPUSD","EURUSD",
    "NVDA","AAPL","MSFT","GOOGL","GOOG","META","AMZN","AMD",
    "JPM","BAC","XOM","MSTR","COIN","TSLA","NFLX","DIS",
    "BTC","ETH","SOL","OIL","GOLD","SILVER",
}

_COMPANY_TO_TICKER = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT",
    "alphabet": "GOOGL", "google": "GOOGL", "meta": "META",
    "amazon": "AMZN", "amd": "AMD", "jpmorgan": "JPM",
    "bank of america": "BAC", "exxon": "XOM", "microstrategy": "MSTR",
    "coinbase": "COIN", "tesla": "TSLA", "netflix": "NFLX",
    "disney": "DIS", "bitcoin": "BTC", "ethereum": "ETH",
    "solana": "SOL", "crude oil": "OIL", "gold": "GOLD",
    "s&p 500": "SPY", "nasdaq": "QQQ", "s&p500": "SPY",
}

_TICKER_RE = re.compile(r'\b([A-Z]{2,5})\b')


def extract_tickers(text: str) -> List[str]:
    """Extract known ticker symbols from free text."""
    found = set()
    lower = text.lower()
    for company, ticker in _COMPANY_TO_TICKER.items():
        if company in lower:
            found.add(ticker)
    for m in _TICKER_RE.finditer(text):
        sym = m.group(1)
        if sym in _TRACKED:
            found.add(sym)
    return sorted(found)


def _item_id(source: str, url: str, title: str) -> str:
    raw = f"{source}:{url or title}"
    return hashlib.md5(raw.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_get(url: str, timeout: int = 8, **kwargs) -> Optional[requests.Response]:
    try:
        r = _SESSION.get(url, timeout=timeout, **kwargs)
        r.raise_for_status()
        return r
    except Exception as exc:
        logger.debug("fetch failed %s: %s", url, exc)
        return None


# ─── RSS sources ─────────────────────────────────────────────────────────────

RSS_FEEDS = [
    ("reuters",     "https://feeds.reuters.com/reuters/businessNews"),
    ("reuters",     "https://feeds.reuters.com/reuters/technologyNews"),
    ("cnbc",        "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("cnbc",        "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("marketwatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("marketwatch", "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
    ("yahoo",       "https://finance.yahoo.com/news/rssindex"),
    ("seeking_alpha","https://seekingalpha.com/market_currents.xml"),
    ("investing",   "https://www.investing.com/rss/news.rss"),
    ("ft",          "https://www.ft.com/rss/home/us"),
]


def _parse_rss_date(date_str: Optional[str]) -> str:
    if not date_str:
        return _now_iso()
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return _now_iso()


def fetch_rss() -> List[Dict[str, Any]]:
    """Fetch all RSS feeds. Returns list of raw news items."""
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed — skipping RSS")
        return []

    items = []
    for source, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in (feed.entries or [])[:15]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "")
                body  = entry.get("summary", entry.get("description", ""))
                # strip HTML tags
                body  = re.sub(r"<[^>]+>", " ", body).strip()
                pub   = _parse_rss_date(entry.get("published") or entry.get("updated"))
                text  = f"{title} {body}"
                items.append({
                    "id":           _item_id(source, link, title),
                    "source":       source,
                    "title":        title[:300],
                    "url":          link,
                    "body":         body[:800],
                    "tickers":      extract_tickers(text),
                    "published_at": pub,
                    "fetched_at":   _now_iso(),
                })
            logger.debug("rss %s: %d items", source, len(feed.entries or []))
        except Exception as exc:
            logger.warning("rss failed %s: %s", url, exc)

    return items


# ─── Reddit (no auth — public JSON API) ──────────────────────────────────────

REDDIT_FEEDS = [
    ("reddit_investing",     "https://www.reddit.com/r/investing/hot.json?limit=25"),
    ("reddit_wsb",           "https://www.reddit.com/r/wallstreetbets/hot.json?limit=25"),
    ("reddit_stocks",        "https://www.reddit.com/r/stocks/hot.json?limit=25"),
    ("reddit_stockmarket",   "https://www.reddit.com/r/StockMarket/hot.json?limit=25"),
    ("reddit_finance",       "https://www.reddit.com/r/finance/hot.json?limit=15"),
]


def fetch_reddit() -> List[Dict[str, Any]]:
    """Fetch Reddit posts from finance subreddits (no auth)."""
    items = []
    for source, url in REDDIT_FEEDS:
        resp = _safe_get(url, headers={"User-Agent": "GeoClaw:research:v2.0"})
        if not resp:
            continue
        try:
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            for p in posts:
                d = p.get("data", {})
                title = d.get("title", "").strip()
                body  = d.get("selftext", "")[:600]
                url_  = f"https://reddit.com{d.get('permalink', '')}"
                score = d.get("score", 0)
                created = datetime.fromtimestamp(
                    d.get("created_utc", time.time()), tz=timezone.utc
                ).isoformat()
                text = f"{title} {body}"
                items.append({
                    "id":           _item_id(source, url_, title),
                    "source":       source,
                    "title":        title[:300],
                    "url":          url_,
                    "body":         body,
                    "tickers":      extract_tickers(text),
                    "published_at": created,
                    "fetched_at":   _now_iso(),
                    "meta":         {"reddit_score": score, "subreddit": d.get("subreddit")},
                })
            logger.debug("reddit %s: %d posts", source, len(posts))
        except Exception as exc:
            logger.warning("reddit parse failed %s: %s", url, exc)
        time.sleep(0.5)  # Reddit rate limit — be polite

    return items


# ─── StockTwits ───────────────────────────────────────────────────────────────

STOCKTWITS_SYMBOLS = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "AMD",
    "SPY", "QQQ", "TSLA", "COIN", "MSTR", "BTC.X", "ETH.X",
]


def fetch_stocktwits(symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Fetch StockTwits messages for tracked symbols (no auth, free public API)."""
    items = []
    syms = symbols or STOCKTWITS_SYMBOLS[:8]  # limit to avoid rate limiting
    for sym in syms:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
        resp = _safe_get(url, timeout=6)
        if not resp:
            continue
        try:
            data = resp.json()
            messages = data.get("messages", [])
            ticker = sym.replace(".X", "")  # BTC.X → BTC
            for msg in messages[:10]:
                body    = msg.get("body", "").strip()
                created = msg.get("created_at", _now_iso())
                sentiment_raw = msg.get("entities", {}).get("sentiment", {})
                st_sentiment  = (sentiment_raw or {}).get("basic", "")
                items.append({
                    "id":           _item_id("stocktwits", str(msg.get("id", "")), body),
                    "source":       "stocktwits",
                    "title":        body[:200],
                    "url":          f"https://stocktwits.com/symbol/{sym}",
                    "body":         body,
                    "tickers":      [ticker] + extract_tickers(body),
                    "published_at": created,
                    "fetched_at":   _now_iso(),
                    "meta":         {"stocktwits_sentiment": st_sentiment},
                })
            logger.debug("stocktwits %s: %d msgs", sym, len(messages))
        except Exception as exc:
            logger.warning("stocktwits failed %s: %s", sym, exc)
        time.sleep(0.3)

    return items


# ─── Public entry point ───────────────────────────────────────────────────────

def fetch_all_news() -> List[Dict[str, Any]]:
    """
    Fetch from all sources. Returns deduplicated list sorted newest first.
    Called by the background worker every 3 minutes.
    """
    logger.info("news_aggregator: starting fetch cycle")
    all_items: List[Dict[str, Any]] = []

    rss = fetch_rss()
    logger.info("rss: %d items", len(rss))
    all_items.extend(rss)

    reddit = fetch_reddit()
    logger.info("reddit: %d items", len(reddit))
    all_items.extend(reddit)

    stocktwits = fetch_stocktwits()
    logger.info("stocktwits: %d items", len(stocktwits))
    all_items.extend(stocktwits)

    # Deduplicate by id
    seen = set()
    deduped = []
    for item in all_items:
        if item["id"] not in seen:
            seen.add(item["id"])
            deduped.append(item)

    deduped.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    logger.info("news_aggregator: %d unique items total", len(deduped))
    return deduped
