"""
Bluesky AT Protocol client — public search API, no credentials required.
Replaces Nitter/Twitter RSS proxies which are unreliable.

Public endpoint: https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts
Rate limit: ~100 req/min unauthenticated.

Enable: ENABLE_BLUESKY=true (off by default to avoid accidental rate-limit burn)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

BLUESKY_SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
BLUESKY_TIMEOUT = 10
BLUESKY_MAX_PER_QUERY = 15

# Macro-relevant search terms — kept focused to avoid noise
_MACRO_QUERIES = [
    "federal reserve rate",
    "inflation cpi",
    "oil price opec",
    "tariff trade war",
    "recession gdp",
    "gold xau",
    "geopolitics sanctions",
]


def _fetch_posts(query: str, limit: int = BLUESKY_MAX_PER_QUERY) -> List[Dict[str, Any]]:
    """
    Fetch posts for a single query. Returns a list of normalised article dicts.
    Raises requests.RequestException on network failure (caller handles).
    """
    resp = requests.get(
        BLUESKY_SEARCH_URL,
        params={"q": query, "limit": min(limit, 25), "sort": "latest"},
        timeout=BLUESKY_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    posts = data.get("posts") or []
    articles = []
    for post in posts:
        record = post.get("record") or {}
        author = post.get("author") or {}
        text = str(record.get("text") or "").strip()
        if not text:
            continue
        created_at_raw = str(record.get("createdAt") or "")
        try:
            created_dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except Exception:
            created_dt = datetime.now(timezone.utc)
        articles.append(
            {
                "headline": text[:280],
                "source": f"bsky:{str(author.get('handle') or 'unknown')}",
                "url": f"https://bsky.app/profile/{author.get('handle', '')}/post/{post.get('uri', '').split('/')[-1]}",
                "published_at": created_dt.isoformat(),
                "content": text,
                "query": query,
            }
        )
    return articles


def fetch_bluesky_articles(
    queries: Optional[List[str]] = None,
    limit_per_query: int = BLUESKY_MAX_PER_QUERY,
    delay_between_queries: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Fetch macro-relevant posts from Bluesky across all configured queries.
    Deduplicates by URL. Silently skips queries that fail.

    Returns list of article dicts compatible with existing news ingestion pipeline.
    """
    if not bool(os.environ.get("ENABLE_BLUESKY", "").strip().lower() in {"1", "true", "yes"}):
        return []

    targets = queries or _MACRO_QUERIES
    seen_urls: set = set()
    results: List[Dict[str, Any]] = []

    for query in targets:
        try:
            posts = _fetch_posts(query, limit=limit_per_query)
            for post in posts:
                url = str(post.get("url") or "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(post)
        except Exception:
            pass  # one failed query must not abort the whole fetch
        if delay_between_queries > 0:
            time.sleep(delay_between_queries)

    return results


if __name__ == "__main__":
    os.environ["ENABLE_BLUESKY"] = "true"
    articles = fetch_bluesky_articles(queries=["federal reserve rate"], limit_per_query=5)
    for a in articles:
        print(f"[{a['source']}] {a['headline'][:100]}")
    print(f"\nFetched {len(articles)} posts")
