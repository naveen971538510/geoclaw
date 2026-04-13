"""
Twitter/X source — fetches financial commentary via RSS bridge services.
Uses public Nitter instances or RSS bridges that proxy Twitter content.

No API key required — falls back gracefully if no bridge is available.
"""

import time
from typing import List, Optional

from models import RawArticle
from sources.base import NewsSource, clean_text, utc_now_iso

try:
    import feedparser
except ImportError:
    feedparser = None

# Financial/macro Twitter accounts that move markets
DEFAULT_ACCOUNTS = [
    "zaborhedge",       # ZeroHedge
    "DeItaone",         # First Squawk
    "unusual_whales",   # Unusual Whales
    "MacroAlf",         # Alfonso Peccatiello
    "lisaabramowicz1",  # Lisa Abramowicz
    "NorthmanTrader",   # Sven Henrich
    "fed",              # Federal Reserve
]

# Nitter instances (public Twitter RSS proxies)
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

RATE_LIMIT_SEC = 1.5


class TwitterSource(NewsSource):
    name = "twitter"

    def __init__(self, accounts: List[str] = None, nitter_base: str = None):
        self._accounts = accounts or DEFAULT_ACCOUNTS
        self._nitter_base = nitter_base or self._find_working_instance()
        self._last_fetch = 0.0

    def _find_working_instance(self) -> str:
        if feedparser is None:
            return ""
        for instance in NITTER_INSTANCES:
            try:
                import requests
                resp = requests.get(f"{instance}/zaborhedge/rss", timeout=5)
                if resp.status_code == 200 and len(resp.text) > 100:
                    return instance
            except Exception:
                continue
        return ""

    def fetch(self, query: Optional[str] = None, max_records: int = 20) -> List[RawArticle]:
        if feedparser is None or not self._nitter_base:
            return []

        articles = []
        per_account = max(2, max_records // len(self._accounts))

        for account in self._accounts:
            try:
                items = self._fetch_account(account, limit=per_account)
                articles.extend(items)
            except Exception:
                continue
            if len(articles) >= max_records:
                break

        # Filter by query if provided
        if query:
            query_lower = query.lower()
            articles = [a for a in articles if query_lower in (a.headline + " " + a.summary).lower()]

        return self.unique(articles[:max_records])

    def _fetch_account(self, account: str, limit: int = 5) -> List[RawArticle]:
        self._rate_limit()

        rss_url = f"{self._nitter_base}/{account}/rss"
        feed = feedparser.parse(rss_url)

        articles = []
        for entry in feed.entries[:limit]:
            title = clean_text(entry.get("title", ""))
            if not title or len(title) < 15:
                continue

            link = entry.get("link", f"https://twitter.com/{account}")
            published = entry.get("published", utc_now_iso())
            summary = clean_text(entry.get("summary", ""))[:500]

            articles.append(RawArticle(
                source_name=f"twitter/@{account}",
                headline=title[:300],
                url=link,
                published_at=published,
                summary=summary or title,
            ))

        return articles

    def _rate_limit(self):
        elapsed = time.time() - self._last_fetch
        if elapsed < RATE_LIMIT_SEC:
            time.sleep(RATE_LIMIT_SEC - elapsed)
        self._last_fetch = time.time()
