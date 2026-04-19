from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from hashlib import sha1
from typing import Iterable, List
import re

from models import RawArticle


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def content_hash(headline: str, url: str) -> str:
    base = (headline or "").strip().lower() + "|" + (url or "").strip().lower()
    # SHA-1 here is a non-cryptographic dedup key on publicly visible
    # (headline, url) pairs — it never feeds an auth or integrity
    # decision. `usedforsecurity=False` suppresses FIPS/bandit warnings
    # while keeping the existing digest format stable across restarts.
    return sha1(base.encode("utf-8"), usedforsecurity=False).hexdigest()


class NewsSource(ABC):
    name = "base"

    @abstractmethod
    def fetch(self, query: str | None = None, max_records: int = 20) -> List[RawArticle]:
        raise NotImplementedError

    def unique(self, items: Iterable[RawArticle]) -> List[RawArticle]:
        seen = set()
        out: List[RawArticle] = []
        for item in items:
            key = (item.url or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out
