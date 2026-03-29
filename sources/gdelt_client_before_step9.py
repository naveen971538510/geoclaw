from __future__ import annotations

from typing import List
import requests

from models import RawArticle
from .base import NewsSource, clean_text


class GDELTSource(NewsSource):
    name = "gdelt"
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def fetch(self, query: str | None = None, max_records: int = 20) -> List[RawArticle]:
        q = query or '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency)'
        params = {
            "query": q,
            "mode": "ArtList",
            "format": "json",
            "sort": "DateDesc",
            "maxrecords": max_records,
        }
        res = requests.get(self.endpoint, params=params, timeout=self.timeout, headers={"User-Agent": "GeoClaw/2.0"})
        res.raise_for_status()
        data = res.json()

        articles = data.get("articles", []) or data.get("results", []) or []
        out: List[RawArticle] = []
        for item in articles:
            title = clean_text(item.get("title", ""))
            url = clean_text(item.get("url", ""))
            source_name = clean_text(item.get("domain", "") or "GDELT")
            published_at = clean_text(item.get("seendate", "") or item.get("date", ""))
            summary = clean_text(item.get("snippet", "") or item.get("socialimage", ""))
            if title and url:
                out.append(
                    RawArticle(
                        source_name=source_name,
                        headline=title,
                        url=url,
                        published_at=published_at,
                        summary=summary,
                    )
                )
        return self.unique(out)
