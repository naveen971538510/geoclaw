"""
Reddit source — fetches posts from financial/geopolitical subreddits via JSON API.
No API key needed (uses public .json endpoints).
"""

import time
from typing import List, Optional

from models import RawArticle
from sources.base import NewsSource, clean_text, utc_now_iso

try:
    import requests
except ImportError:
    requests = None

DEFAULT_SUBREDDITS = [
    "worldnews",
    "economics",
    "wallstreetbets",
    "stocks",
    "geopolitics",
    "commodities",
]

USER_AGENT = "GeoClaw/2.0 (macro intelligence agent)"
RATE_LIMIT_SEC = 2.0


class RedditSource(NewsSource):
    name = "reddit"

    def __init__(self, subreddits: List[str] = None):
        self._subreddits = subreddits or DEFAULT_SUBREDDITS
        self._last_fetch = 0.0

    def fetch(self, query: Optional[str] = None, max_records: int = 20) -> List[RawArticle]:
        if requests is None:
            return []

        articles = []
        per_sub = max(3, max_records // len(self._subreddits))

        for subreddit in self._subreddits:
            try:
                items = self._fetch_subreddit(subreddit, query=query, limit=per_sub)
                articles.extend(items)
            except Exception:
                continue

            if len(articles) >= max_records:
                break

        return self.unique(articles[:max_records])

    def _fetch_subreddit(self, subreddit: str, query: Optional[str] = None, limit: int = 10) -> List[RawArticle]:
        self._rate_limit()

        if query:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {"q": query, "sort": "new", "limit": limit, "restrict_sr": "true", "t": "week"}
        else:
            url = f"https://www.reddit.com/r/{subreddit}/hot.json"
            params = {"limit": limit}

        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for child in (data.get("data") or {}).get("children", []):
            post = child.get("data") or {}
            title = clean_text(post.get("title") or "")
            if not title:
                continue

            # Skip non-text posts with no substance
            selftext = clean_text(post.get("selftext") or "")[:500]
            post_url = post.get("url") or f"https://reddit.com{post.get('permalink', '')}"

            articles.append(RawArticle(
                source_name=f"reddit/r/{subreddit}",
                headline=title,
                url=post_url,
                published_at=utc_now_iso(),
                summary=selftext or title,
            ))

        return articles

    def _rate_limit(self):
        elapsed = time.time() - self._last_fetch
        if elapsed < RATE_LIMIT_SEC:
            time.sleep(RATE_LIMIT_SEC - elapsed)
        self._last_fetch = time.time()
