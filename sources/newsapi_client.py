from __future__ import annotations

from typing import List
import requests

from config import NEWSAPI_KEY
from models import RawArticle
from services.provider_state_service import (
    mark_provider_invalid,
    mark_provider_limited,
    mark_provider_temp_issue,
    provider_ready,
    record_provider_success,
)
from .base import NewsSource, clean_text


class NewsAPISource(NewsSource):
    name = "newsapi"
    endpoint = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        self.api_key = (api_key or NEWSAPI_KEY or "").strip()
        self.timeout = timeout

    def fetch(self, query: str | None = None, max_records: int = 20) -> List[RawArticle]:
        if not self.api_key:
            return []
        if not provider_ready("newsapi", bool(self.api_key)):
            return []

        q = query or '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency)'
        params = {
            "q": q,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": max_records,
            "apiKey": self.api_key,
        }

        try:
            res = requests.get(
                self.endpoint,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "GeoClaw/2.0"},
            )

            if res.status_code in (401, 403):
                mark_provider_invalid("newsapi", "unauthorized key")
                raise RuntimeError("newsapi unauthorized key")
            if res.status_code == 429:
                mark_provider_limited("newsapi", "rate limited", retry_after_seconds=900)
                raise RuntimeError("newsapi rate limited")

            res.raise_for_status()
            data = res.json()

            if data.get("status") == "error":
                code = str(data.get("code", "")).strip()
                msg = str(data.get("message", "")).strip()
                low = (code + " " + msg).lower()
                if "api key" in low or "apikey" in low or "unauthorized" in low:
                    mark_provider_invalid("newsapi", "unauthorized key")
                    raise RuntimeError("newsapi unauthorized key")
                if "rate" in low or "limit" in low:
                    mark_provider_limited("newsapi", "rate limited", retry_after_seconds=900)
                    raise RuntimeError("newsapi rate limited")
                mark_provider_temp_issue("newsapi", "api error", retry_after_seconds=300)
                raise RuntimeError("newsapi api error")

            record_provider_success("newsapi")

            out: List[RawArticle] = []
            for item in data.get("articles", []):
                source_obj = item.get("source", {}) or {}
                source_name = clean_text(source_obj.get("name", "") or "NewsAPI")
                title = clean_text(item.get("title", ""))
                url = clean_text(item.get("url", ""))
                published_at = clean_text(item.get("publishedAt", ""))
                summary = clean_text(item.get("description", "") or item.get("content", ""))
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
        except requests.exceptions.Timeout:
            mark_provider_temp_issue("newsapi", "timeout", retry_after_seconds=300)
            raise RuntimeError("newsapi timeout")
