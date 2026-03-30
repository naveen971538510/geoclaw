import logging
import time
from datetime import datetime, timezone
from typing import Dict, List


logger = logging.getLogger("geoclaw.search")


class WebSearcher:
    """
    The agent's active eyes. Searches the web for specific information
    when RSS alone is insufficient to answer a question.
    """

    MAX_RESULTS = 5
    MAX_BODY_CHARS = 1500
    RATE_LIMIT_SEC = 2.0

    def __init__(self, db_path: str = None):
        self.db_path = db_path
        self._last_search = 0.0
        try:
            from duckduckgo_search import DDGS

            self._ddgs = DDGS()
            self._available = True
        except Exception as exc:
            self._ddgs = None
            self._available = False
            logger.warning("duckduckgo-search unavailable: %s", exc)

    def available(self) -> bool:
        return bool(self._available)

    def search(
        self,
        query: str,
        max_results: int = None,
        triggered_by: str = "agent",
        thesis_key: str = "",
    ) -> List[Dict]:
        """
        Search the web. Returns list of:
          {title, url, body, source, published_at, relevance_score}
        """
        clean_query = str(query or "").strip()
        if not clean_query:
            return []
        if not self._available:
            self._log_search(clean_query, 0, triggered_by=triggered_by, thesis_key=thesis_key)
            return []

        elapsed = time.time() - self._last_search
        if elapsed < self.RATE_LIMIT_SEC:
            time.sleep(self.RATE_LIMIT_SEC - elapsed)
        self._last_search = time.time()

        results = []
        try:
            raw = list(self._ddgs.text(clean_query, max_results=int(max_results or self.MAX_RESULTS)))
            for row in raw:
                url = str(row.get("href") or "").strip()
                body = self._extract_body(url, str(row.get("body") or ""))
                results.append(
                    {
                        "title": str(row.get("title") or "").strip(),
                        "url": url,
                        "body": body[: self.MAX_BODY_CHARS],
                        "source": self._extract_domain(url),
                        "published_at": datetime.now(timezone.utc).isoformat(),
                        "relevance_score": 0.7,
                        "search_query": clean_query,
                    }
                )
            logger.info("Web search '%s': %s results", clean_query[:60], len(results))
        except Exception as exc:
            logger.warning("Web search failed for '%s': %s", clean_query[:80], exc)

        self._log_search(clean_query, len(results), triggered_by=triggered_by, thesis_key=thesis_key)
        return results

    def search_for_thesis(self, thesis_key: str) -> List[Dict]:
        words = str(thesis_key or "").split()
        skip = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "may",
            "will",
            "if",
            "this",
            "that",
            "and",
            "or",
            "to",
            "of",
            "in",
            "on",
            "for",
            "with",
            "by",
            "from",
            "monitor",
            "watch",
            "confirm",
            "follow",
            "context",
            "unclear",
        }
        meaningful = [word for word in words if word.lower() not in skip and len(word) > 3]
        query = " ".join(meaningful[:8]) or str(thesis_key or "")[:80]
        query = f"{query} latest news 2025 2026".strip()
        return self.search(query, triggered_by="thesis_research", thesis_key=thesis_key)

    def search_for_uncertainty(self, thesis_key: str, confidence: float) -> List[Dict]:
        if float(confidence or 0.0) > 0.55:
            return []
        primary = self.search_for_thesis(thesis_key)
        contra_query = f"{' '.join(str(thesis_key or '').split()[:5])} analysis contradiction alternative view"
        contra = self.search(contra_query, max_results=2, triggered_by="uncertainty", thesis_key=thesis_key)
        return (primary + contra)[: self.MAX_RESULTS]

    def search_breaking_news(self, topic: str) -> List[Dict]:
        query = f"{str(topic or '').strip()} breaking news latest developments"
        return self.search(query, max_results=3, triggered_by="breaking_news", thesis_key=str(topic or ""))

    def _extract_body(self, url: str, fallback: str) -> str:
        if not url:
            return fallback or ""
        try:
            import trafilatura

            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded)
                if text and len(text) > 100:
                    return text[: self.MAX_BODY_CHARS]
        except Exception:
            pass
        return fallback or ""

    def _extract_domain(self, url: str) -> str:
        try:
            from urllib.parse import urlparse

            return urlparse(url).netloc.replace("www.", "") or "web"
        except Exception:
            return "web"

    def _log_search(self, query: str, result_count: int, triggered_by: str = "agent", thesis_key: str = ""):
        if not self.db_path:
            return
        try:
            import sqlite3

            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                INSERT INTO web_search_log (query, result_count, searched_at, triggered_by, thesis_key)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(query or "")[:200],
                    int(result_count or 0),
                    datetime.now(timezone.utc).isoformat(),
                    str(triggered_by or "agent")[:50],
                    str(thesis_key or "")[:200],
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.debug("Could not write web_search_log: %s", exc)
