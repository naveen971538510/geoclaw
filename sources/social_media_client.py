from __future__ import annotations

import time
import urllib.request
from typing import List
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:  # pragma: no cover - fallback keeps source optional
    requests = None

from models import RawArticle
from .base import NewsSource, clean_text


DEFAULT_SOCIAL_FEEDS = [
    {"name": "Reddit r/worldnews", "url": "https://www.reddit.com/r/worldnews/.rss"},
    {"name": "Reddit r/geopolitics", "url": "https://www.reddit.com/r/geopolitics/.rss"},
    {"name": "Reddit r/economics", "url": "https://www.reddit.com/r/Economics/.rss"},
    {"name": "Reddit r/investing", "url": "https://www.reddit.com/r/investing/.rss"},
]


class SocialMediaSource(NewsSource):
    """Public social/news RSS intake for agentic discovery without API keys."""

    name = "social"
    CACHE_TTL_SECONDS = 300
    _cache = {"fetched_at": 0.0, "items": []}

    def __init__(self, feeds: list[dict] | None = None, timeout: int = 8):
        self.feeds = feeds or DEFAULT_SOCIAL_FEEDS
        self.timeout = timeout

    def fetch(self, query: str | None = None, max_records: int = 20) -> List[RawArticle]:
        cached_items = self._read_cache()
        if cached_items:
            return self.unique(cached_items)[:max_records]

        all_items: List[RawArticle] = []
        for feed in self.feeds:
            try:
                xml_text = self._fetch_xml(feed["url"])
                all_items.extend(self._parse_feed(xml_text, feed["name"]))
            except Exception as exc:
                print(f"SocialMediaSource warning [{feed['name']}]: {exc}")

        unique_items = self.unique(all_items)
        self._write_cache(unique_items)
        return unique_items[:max_records]

    def _fetch_xml(self, url: str) -> str:
        headers = {"User-Agent": "GeoClaw/2.0 social-intake"}
        if requests is not None:
            response = requests.get(url, timeout=self.timeout, headers=headers)
            response.raise_for_status()
            return response.text
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8", errors="ignore")

    def _parse_feed(self, xml_text: str, source_name: str) -> List[RawArticle]:
        root = ET.fromstring(xml_text)
        items: List[RawArticle] = []

        for node in root.findall(".//item"):
            title = clean_text(node.findtext("title", ""))
            link = clean_text(node.findtext("link", ""))
            pub = clean_text(node.findtext("pubDate", ""))
            desc = clean_text(node.findtext("description", ""))
            if title and link:
                items.append(self._article(source_name, title, link, pub, desc))

        if items:
            return items

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "content": "http://purl.org/rss/1.0/modules/content/",
        }
        for node in root.findall(".//atom:entry", ns):
            title = clean_text(node.findtext("atom:title", "", ns))
            pub = clean_text(node.findtext("atom:updated", "", ns) or node.findtext("atom:published", "", ns))
            summary = clean_text(
                node.findtext("atom:summary", "", ns)
                or node.findtext("atom:content", "", ns)
                or node.findtext("content:encoded", "", ns)
            )
            link = ""
            for link_node in node.findall("atom:link", ns):
                href = link_node.attrib.get("href", "").strip()
                rel = link_node.attrib.get("rel", "").strip()
                if href and (not rel or rel == "alternate"):
                    link = href
                    break
            if title and link:
                items.append(self._article(source_name, title, link, pub, summary))
        return items

    def _article(self, source_name: str, title: str, link: str, published_at: str, summary: str) -> RawArticle:
        social_summary = clean_text(summary)
        if social_summary:
            social_summary = f"Social signal from {source_name}: {social_summary}"
        else:
            social_summary = f"Social signal from {source_name}."
        return RawArticle(
            source_name=source_name,
            headline=title,
            url=link,
            published_at=published_at,
            summary=social_summary,
            external_id=link,
        )

    @classmethod
    def _read_cache(cls) -> List[RawArticle]:
        fetched_at = float(cls._cache.get("fetched_at", 0.0) or 0.0)
        if not fetched_at or (time.time() - fetched_at) > cls.CACHE_TTL_SECONDS:
            return []
        return list(cls._cache.get("items", []) or [])

    @classmethod
    def _write_cache(cls, items: List[RawArticle]):
        cls._cache = {"fetched_at": time.time(), "items": list(items or [])}
