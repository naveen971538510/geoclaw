from __future__ import annotations

from typing import List
import urllib.request
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:  # pragma: no cover - exercised through fallback imports
    requests = None

from models import RawArticle
from .base import NewsSource, clean_text


DEFAULT_RSS_FEEDS = [
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
        ]


class RSSSource(NewsSource):
    name = "rss"

    def __init__(self, feeds: list[dict] | None = None, timeout: int = 20):
        self.feeds = feeds or DEFAULT_RSS_FEEDS
        self.timeout = timeout

    def _parse_rss(self, xml_text: str, source_name: str) -> List[RawArticle]:
        items: List[RawArticle] = []
        root = ET.fromstring(xml_text)

        # RSS
        for node in root.findall(".//item"):
            title = clean_text(node.findtext("title", ""))
            link = clean_text(node.findtext("link", ""))
            pub = clean_text(node.findtext("pubDate", ""))
            desc = clean_text(node.findtext("description", ""))
            if title and link:
                items.append(
                    RawArticle(
                        source_name=source_name,
                        headline=title,
                        url=link,
                        published_at=pub,
                        summary=desc,
                    )
                )

        # Atom fallback
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for node in root.findall(".//atom:entry", ns):
                title = clean_text(node.findtext("atom:title", "", ns))
                pub = clean_text(node.findtext("atom:updated", "", ns) or node.findtext("atom:published", "", ns))
                summary = clean_text(node.findtext("atom:summary", "", ns))
                link = ""
                for link_node in node.findall("atom:link", ns):
                    href = link_node.attrib.get("href", "").strip()
                    rel = link_node.attrib.get("rel", "").strip()
                    if href and (not rel or rel == "alternate"):
                        link = href
                        break
                if title and link:
                    items.append(
                        RawArticle(
                            source_name=source_name,
                            headline=title,
                            url=link,
                            published_at=pub,
                            summary=summary,
                        )
                    )
        return items

    def fetch(self, query: str | None = None, max_records: int = 20) -> List[RawArticle]:
        all_items: List[RawArticle] = []
        for feed in self.feeds:
            try:
                if requests is not None:
                    response = requests.get(feed["url"], timeout=self.timeout, headers={"User-Agent": "GeoClaw/2.0"})
                    response.raise_for_status()
                    xml_text = response.text
                else:
                    request = urllib.request.Request(feed["url"], headers={"User-Agent": "GeoClaw/2.0"}, method="GET")
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        xml_text = response.read().decode("utf-8", errors="ignore")
                parsed = self._parse_rss(xml_text, feed["name"])
                all_items.extend(parsed)
            except Exception as exc:
                print(f"RSSSource warning [{feed['name']}]: {exc}")
        return self.unique(all_items)[:max_records]
