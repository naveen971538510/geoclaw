from __future__ import annotations

from typing import List
import requests

from config import GUARDIAN_API_KEY
from models import RawArticle
from services.provider_state_service import (
    mark_provider_invalid,
    mark_provider_limited,
    mark_provider_temp_issue,
    provider_ready,
    record_provider_success,
)
from .base import NewsSource, clean_text


class GuardianSource(NewsSource):
    name = "guardian"
    endpoint = "https://content.guardianapis.com/search"

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        self.api_key = (api_key or GUARDIAN_API_KEY or "").strip()
        self.timeout = timeout

    def fetch(self, query: str | None = None, max_records: int = 20) -> List[RawArticle]:
        if not self.api_key:
            return []
        if not provider_ready("guardian", bool(self.api_key)):
            return []

        q = query or '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency)'
        params = {
            "q": q,
            "api-key": self.api_key,
            "page-size": max_records,
            "order-by": "newest",
            "show-fields": "trailText",
        }

        try:
            res = requests.get(
                self.endpoint,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "GeoClaw/2.0"},
            )

            if res.status_code in (401, 403):
                mark_provider_invalid("guardian", "unauthorized key")
                raise RuntimeError("guardian unauthorized key")
            if res.status_code == 429:
                mark_provider_limited("guardian", "rate limited", retry_after_seconds=900)
                raise RuntimeError("guardian rate limited")

            res.raise_for_status()
            data = res.json()
            response = data.get("response") or {}

            if response.get("status") == "error":
                mark_provider_temp_issue("guardian", "api error", retry_after_seconds=300)
                raise RuntimeError("guardian api error")

            record_provider_success("guardian")

            results = response.get("results") or []
            out: List[RawArticle] = []
            for item in results:
                fields = item.get("fields", {}) or {}
                title = clean_text(item.get("webTitle", ""))
                url = clean_text(item.get("webUrl", ""))
                published_at = clean_text(item.get("webPublicationDate", ""))
                summary = clean_text(fields.get("trailText", ""))
                if title and url:
                    out.append(
                        RawArticle(
                            source_name="The Guardian",
                            headline=title,
                            url=url,
                            published_at=published_at,
                            summary=summary,
                        )
                    )
            return self.unique(out)
        except requests.exceptions.Timeout:
            mark_provider_temp_issue("guardian", "timeout", retry_after_seconds=300)
            raise RuntimeError("guardian timeout")
