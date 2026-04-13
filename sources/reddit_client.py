"""
Reddit public JSON API client — no API keys required.

Fetches hot posts from macro-relevant subreddits via Reddit's public .json
endpoints. Rate-limited with User-Agent to stay within Reddit's ToS.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

_SUBREDDITS = ["worldnews", "economics", "wallstreetbets", "stocks", "geopolitics"]
_USER_AGENT = "GeoClaw/1.0 (macro-intelligence; +https://github.com/geoclaw)"
_TIMEOUT = 10
_MAX_PER_SUB = 8


def fetch_reddit_articles(
    subreddits: Optional[List[str]] = None,
    limit_per_sub: int = _MAX_PER_SUB,
    delay: float = 1.0,
) -> List[Dict[str, Any]]:
    """
    Fetch hot posts from Reddit. Returns article dicts compatible with the
    existing ingestion pipeline. Gated by ENABLE_REDDIT env var (default true).
    """
    if os.environ.get("ENABLE_REDDIT", "true").strip().lower() not in {"1", "true", "yes"}:
        return []

    targets = subreddits or _SUBREDDITS
    seen: set = set()
    results: List[Dict[str, Any]] = []

    for sub in targets:
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json"
            resp = requests.get(
                url,
                params={"limit": min(limit_per_sub, 25), "raw_json": 1},
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
            for child in children:
                post = child.get("data") or {}
                title = str(post.get("title") or "").strip()
                permalink = str(post.get("permalink") or "")
                post_url = f"https://www.reddit.com{permalink}" if permalink else ""
                if not title or post_url in seen:
                    continue
                seen.add(post_url)
                created_utc = float(post.get("created_utc") or 0)
                published_at = (
                    datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
                    if created_utc
                    else datetime.now(timezone.utc).isoformat()
                )
                results.append(
                    {
                        "headline": title[:300],
                        "source": f"reddit:r/{sub}",
                        "url": post_url,
                        "published_at": published_at,
                        "content": str(post.get("selftext") or "")[:2000],
                        "score": int(post.get("score") or 0),
                    }
                )
        except Exception:
            pass
        if delay > 0:
            time.sleep(delay)

    return results


if __name__ == "__main__":
    articles = fetch_reddit_articles(subreddits=["worldnews"], limit_per_sub=3)
    for a in articles:
        print(f"[{a['source']}] {a['headline'][:100]}")
    print(f"\nFetched {len(articles)} posts")
