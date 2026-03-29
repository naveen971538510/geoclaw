from __future__ import annotations

from typing import List
import json
import time
import requests

from config import GDELT_TIMEOUT_SECONDS, GDELT_COOLDOWN_SECONDS, GDELT_MAX_RECORDS_DEFAULT, GDELT_STATE_FILE
from models import RawArticle
from .base import NewsSource, clean_text


class GDELTSource(NewsSource):
    name = "gdelt"
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, timeout: int = None):
        self.timeout = timeout or GDELT_TIMEOUT_SECONDS

    def _load_state(self) -> dict:
        try:
            if GDELT_STATE_FILE.exists():
                return json.loads(GDELT_STATE_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save_state(self, state: dict):
        try:
            GDELT_STATE_FILE.write_text(json.dumps(state))
        except Exception:
            pass

    def _cooldown_active(self) -> bool:
        state = self._load_state()
        until = float(state.get("cooldown_until", 0) or 0)
        return time.time() < until

    def _set_cooldown(self, reason: str):
        state = {
            "cooldown_until": time.time() + int(GDELT_COOLDOWN_SECONDS),
            "reason": reason,
            "updated_at": int(time.time()),
        }
        self._save_state(state)

    def fetch(self, query: str | None = None, max_records: int = 20) -> List[RawArticle]:
        if self._cooldown_active():
            print("GDELTSource cooldown active: skipping request")
            return []

        q = query or '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency)'
        params = {
            "query": q,
            "mode": "ArtList",
            "format": "json",
            "sort": "DateDesc",
            "maxrecords": min(int(max_records), int(GDELT_MAX_RECORDS_DEFAULT)),
        }

        try:
            res = requests.get(
                self.endpoint,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "GeoClaw/2.0"},
            )

            if res.status_code == 429:
                self._set_cooldown("429")
                print("GDELTSource warning: 429 rate limit, cooldown started")
                return []

            res.raise_for_status()
            data = res.json()
        except requests.exceptions.Timeout:
            self._set_cooldown("timeout")
            print("GDELTSource warning: timeout, cooldown started")
            return []
        except Exception as exc:
            if "429" in str(exc):
                self._set_cooldown("429-exception")
            print(f"GDELTSource warning: {exc}")
            return []

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
