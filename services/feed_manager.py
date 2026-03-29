from typing import Dict, List

from config import ENABLE_GDELT, ENABLE_GUARDIAN, ENABLE_NEWSAPI, ENABLE_RSS
from intelligence import normalize_article
from services.ingest_service import DEFAULT_QUERY
from sources import GDELTSource, GuardianSource, NewsAPISource, RSSSource

SOURCE_CREDIBILITY = {
    "Reuters": 1.0,
    "Bloomberg": 1.0,
    "FT": 0.9,
    "WSJ": 0.9,
    "ECB": 1.0,
    "Fed Reserve": 1.0,
    "IMF": 0.95,
    "BBC": 0.85,
    "CNBC": 0.80,
    "MarketWatch": 0.75,
    "Al Jazeera": 0.80,
    "Foreign Policy": 0.85,
    "Oil Price": 0.70,
    "Economist": 0.90,
    "Seeking Alpha": 0.50,
}


def get_source_weight(source_name: str) -> float:
    clean = str(source_name or "").strip().lower()
    for key, weight in SOURCE_CREDIBILITY.items():
        if key.lower() in clean:
            return float(weight)
    return 0.65


class FeedManager:
    def __init__(self, enabled_sources: List[str] = None):
        self.enabled_sources = {str(item or "").strip().lower() for item in (enabled_sources or []) if str(item or "").strip()}

    def _allow(self, name: str) -> bool:
        return not self.enabled_sources or str(name or "").strip().lower() in self.enabled_sources

    def _sources(self):
        sources = []
        if ENABLE_RSS and self._allow("rss"):
            sources.append(("rss", RSSSource()))
        if ENABLE_GDELT and self._allow("gdelt"):
            sources.append(("gdelt", GDELTSource()))
        if ENABLE_NEWSAPI and self._allow("newsapi"):
            sources.append(("newsapi", NewsAPISource()))
        if ENABLE_GUARDIAN and self._allow("guardian"):
            sources.append(("guardian", GuardianSource()))
        return sources

    def fetch_all(self, query: str = None, max_records: int = 10) -> List[Dict]:
        items: List[Dict] = []
        for _, source in self._sources():
            try:
                fetched = source.fetch(query=query or DEFAULT_QUERY, max_records=int(max_records or 10))
            except Exception:
                fetched = []
            for raw in fetched or []:
                article = normalize_article(raw)
                if article.get("headline") and article.get("url"):
                    items.append(article)
        return items
