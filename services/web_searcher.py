import logging
import re
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
    MAX_QUERY_CHARS = 90
    MAX_VARIANTS = 3
    MAX_EXTRACTION_ATTEMPTS = 2
    RATE_LIMIT_SEC = 0.4
    NOISE_PATTERNS = [
        r"^\s*monitor:\s*",
        r"context unclear.*$",
        r"watch for follow-?up confirmation.*$",
        r"follow-?up confirmation.*$",
        r"latest news 2025 2026",
        r"\b(?:days|weeks|immediate)\s+horizon\b",
        r"\blow[- ]medium\b.*$",
        r"\blow\b.*$",
        r"\bhigh\b.*$",
    ]

    def __init__(self, db_path: str = None):
        self.db_path = db_path
        self._last_search = 0.0
        self._backend = ""
        self._availability_reason = ""
        self._ddgs = self._load_backend()

    def _load_backend(self):
        try:
            from ddgs import DDGS

            self._backend = "ddgs"
            self._availability_reason = ""
            return DDGS()
        except Exception as exc:
            self._availability_reason = str(exc)
        try:
            from duckduckgo_search import DDGS

            self._backend = "duckduckgo_search"
            self._availability_reason = ""
            return DDGS()
        except Exception as exc:
            self._backend = ""
            self._availability_reason = str(exc)
            logger.warning("duckduckgo-search unavailable: %s", exc)
            return None

    def available(self) -> bool:
        return bool(self._ddgs is not None)

    def search(
        self,
        query: str,
        max_results: int = None,
        triggered_by: str = "agent",
        thesis_key: str = "",
    ) -> List[Dict]:
        return self.search_with_details(
            query,
            max_results=max_results,
            triggered_by=triggered_by,
            thesis_key=thesis_key,
        )["results"]

    def search_with_details(
        self,
        query: str,
        max_results: int = None,
        triggered_by: str = "agent",
        thesis_key: str = "",
        variant: str = "plain",
    ) -> Dict:
        clean_query = self._clean_query(query)
        summary = {
            "query": clean_query,
            "variant": str(variant or "plain"),
            "backend": self._backend or "",
            "attempted": 0,
            "succeeded": 0,
            "raw_results_found": 0,
            "usable_results": 0,
            "extraction_failures": 0,
            "wait_seconds": 0.0,
            "extraction_seconds": 0.0,
            "zero_reason": "",
            "results": [],
        }
        if not clean_query:
            summary["zero_reason"] = "empty_query_after_cleanup"
            logger.info("Web search skipped: empty query after cleanup")
            self._log_search("", 0, triggered_by=triggered_by, thesis_key=thesis_key)
            return summary
        if not self.available():
            summary["zero_reason"] = "search_backend_unavailable"
            logger.warning("Web search unavailable for '%s': %s", clean_query[:80], self._availability_reason or "no backend")
            self._log_search(clean_query, 0, triggered_by=triggered_by, thesis_key=thesis_key)
            return summary

        wait_seconds = self._apply_rate_limit()
        summary["wait_seconds"] = round(wait_seconds, 3)
        summary["attempted"] = 1

        try:
            raw_rows = list(self._ddgs.text(clean_query, max_results=int(max_results or self.MAX_RESULTS)))
        except Exception as exc:
            summary["zero_reason"] = "backend_error:" + exc.__class__.__name__
            logger.warning("Web search failed for '%s': %s", clean_query[:80], exc)
            self._log_search(clean_query, 0, triggered_by=triggered_by, thesis_key=thesis_key)
            return summary

        summary["raw_results_found"] = len(raw_rows)
        extraction_attempts = 0
        extraction_started = time.time()
        results = []
        seen_urls = set()
        for row in raw_rows:
            title = str(row.get("title") or "").strip()
            url = str(row.get("href") or "").strip()
            snippet = self._normalize_snippet(row.get("body") or "")
            if not title and not url:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            allow_extract = bool(url) and extraction_attempts < self.MAX_EXTRACTION_ATTEMPTS
            if allow_extract:
                extraction_attempts += 1
            body, extracted, extraction_failed = self._extract_body(url, snippet, allow_extract=allow_extract)
            if extraction_failed:
                summary["extraction_failures"] += 1
            results.append(
                {
                    "title": title or snippet[:140] or clean_query,
                    "url": url,
                    "body": (body or snippet or title)[: self.MAX_BODY_CHARS],
                    "source": self._extract_domain(url),
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "relevance_score": 0.7 if extracted else 0.55,
                    "search_query": clean_query,
                    "search_variant": summary["variant"],
                }
            )
        summary["extraction_seconds"] = round(max(0.0, time.time() - extraction_started), 3)
        summary["results"] = results
        summary["usable_results"] = len(results)
        summary["succeeded"] = 1 if results else 0
        if not results:
            if summary["raw_results_found"] == 0:
                summary["zero_reason"] = "no_results_from_backend"
            else:
                summary["zero_reason"] = "results_filtered_or_unusable"
            logger.info(
                "Web search returned 0 usable results for '%s' (raw=%s, extraction_failures=%s, reason=%s)",
                clean_query[:80],
                summary["raw_results_found"],
                summary["extraction_failures"],
                summary["zero_reason"],
            )
        else:
            logger.info(
                "Web search '%s' produced %s usable result(s) from %s raw result(s)",
                clean_query[:80],
                summary["usable_results"],
                summary["raw_results_found"],
            )
        self._log_search(clean_query, len(results), triggered_by=triggered_by, thesis_key=thesis_key)
        return summary

    def build_query_variants(self, thesis_key: str, confidence: float = 0.5, reason: str = "") -> List[Dict]:
        base_query = self._thesis_to_query(thesis_key, reason=reason)
        if not base_query:
            return []
        candidates = [
            ("plain", base_query),
            ("latest", f"{base_query} latest"),
        ]
        if float(confidence or 0.0) <= 0.55 or "contradiction" in str(reason or "").lower():
            candidates.append(("alternative", f"{base_query} alternative view"))
        variants = []
        seen = set()
        for variant_name, query in candidates:
            clean = self._clean_query(query)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            variants.append({"variant": variant_name, "query": clean})
        return variants[: self.MAX_VARIANTS]

    def search_queries(
        self,
        queries: List[Dict],
        thesis_key: str = "",
        triggered_by: str = "thesis_research",
        max_results: int = None,
    ) -> Dict:
        combined = {
            "backend": self._backend or "",
            "query_variants": queries[: self.MAX_VARIANTS],
            "searches_attempted": 0,
            "searches_succeeded": 0,
            "raw_results_found": 0,
            "articles_found": 0,
            "extraction_failures": 0,
            "wait_seconds": 0.0,
            "extraction_seconds": 0.0,
            "duplicate_urls_skipped": 0,
            "zero_reasons": [],
            "details": [],
            "results": [],
        }
        seen_urls = set()
        for item in queries[: self.MAX_VARIANTS]:
            detail = self.search_with_details(
                item.get("query", ""),
                max_results=max_results,
                triggered_by=triggered_by,
                thesis_key=thesis_key,
                variant=item.get("variant", "plain"),
            )
            combined["searches_attempted"] += int(detail.get("attempted", 0) or 0)
            combined["searches_succeeded"] += int(detail.get("succeeded", 0) or 0)
            combined["raw_results_found"] += int(detail.get("raw_results_found", 0) or 0)
            combined["extraction_failures"] += int(detail.get("extraction_failures", 0) or 0)
            combined["wait_seconds"] += float(detail.get("wait_seconds", 0.0) or 0.0)
            combined["extraction_seconds"] += float(detail.get("extraction_seconds", 0.0) or 0.0)
            if detail.get("zero_reason"):
                combined["zero_reasons"].append(str(detail["zero_reason"]))
            deduped_results = []
            for result in detail.get("results", []) or []:
                url = str(result.get("url") or "").strip()
                if url and url in seen_urls:
                    combined["duplicate_urls_skipped"] += 1
                    continue
                if url:
                    seen_urls.add(url)
                deduped_results.append(result)
            combined["details"].append(
                {
                    "variant": detail.get("variant", item.get("variant", "plain")),
                    "query": detail.get("query", item.get("query", "")),
                    "raw_results_found": int(detail.get("raw_results_found", 0) or 0),
                    "usable_results": len(deduped_results),
                    "zero_reason": detail.get("zero_reason", ""),
                }
            )
            combined["results"].extend(deduped_results)
            if len(combined["results"]) >= int(max_results or self.MAX_RESULTS):
                break
            if deduped_results and item.get("variant") == "plain":
                break
        combined["results"] = combined["results"][: int(max_results or self.MAX_RESULTS)]
        combined["articles_found"] = len(combined["results"])
        combined["wait_seconds"] = round(combined["wait_seconds"], 3)
        combined["extraction_seconds"] = round(combined["extraction_seconds"], 3)
        if not combined["articles_found"] and not combined["zero_reasons"]:
            combined["zero_reasons"] = ["no_usable_results"]
        return combined

    def search_for_thesis(self, thesis_key: str, reason: str = "") -> Dict:
        return self.search_queries(
            self.build_query_variants(thesis_key, confidence=0.7, reason=reason),
            thesis_key=thesis_key,
            triggered_by="thesis_research",
            max_results=3,
        )

    def search_for_uncertainty(self, thesis_key: str, confidence: float, reason: str = "") -> Dict:
        if float(confidence or 0.0) > 0.55:
            return {
                "searches_attempted": 0,
                "searches_succeeded": 0,
                "raw_results_found": 0,
                "articles_found": 0,
                "articles_saved": 0,
                "extraction_failures": 0,
                "duplicate_urls_skipped": 0,
                "wait_seconds": 0.0,
                "extraction_seconds": 0.0,
                "zero_reasons": ["confidence_not_low_enough"],
                "details": [],
                "results": [],
                "query_variants": [],
            }
        return self.search_queries(
            self.build_query_variants(thesis_key, confidence=confidence, reason=reason),
            thesis_key=thesis_key,
            triggered_by="uncertainty",
            max_results=4,
        )

    def search_breaking_news(self, topic: str) -> Dict:
        query = self._clean_query(f"{str(topic or '').strip()} breaking news")
        return self.search_queries(
            [{"variant": "breaking", "query": query}],
            thesis_key=str(topic or ""),
            triggered_by="breaking_news",
            max_results=3,
        )

    def _apply_rate_limit(self) -> float:
        elapsed = time.time() - self._last_search
        waited = 0.0
        if elapsed < self.RATE_LIMIT_SEC:
            waited = max(0.0, self.RATE_LIMIT_SEC - elapsed)
            time.sleep(waited)
        self._last_search = time.time()
        return waited

    def _thesis_to_query(self, thesis_key: str, reason: str = "") -> str:
        text = str(thesis_key or "").strip()
        if not text:
            return ""
        lowered = text
        for pattern in self.NOISE_PATTERNS:
            lowered = re.sub(pattern, " ", lowered, flags=re.IGNORECASE)
        lowered = lowered.replace("—", " ").replace("|", " ").replace("•", " ")
        clauses = [part.strip() for part in re.split(r"[.;:!?]+", lowered) if part.strip()]
        best_clause = clauses[0] if clauses else lowered.strip()
        words = []
        for token in re.split(r"\s+", best_clause):
            clean = token.strip(" ,()[]{}\"'`")
            if not clean:
                continue
            words.append(clean)
        if words and sum(1 for word in words if re.search(r"[A-Za-z]", word)) >= 2:
            stopwords = {
                "the",
                "a",
                "an",
                "is",
                "are",
                "may",
                "will",
                "this",
                "that",
                "with",
                "from",
                "into",
                "global",
                "days",
                "weeks",
                "horizon",
                "signal",
                "watch",
                "follow",
                "confirmation",
                "monitor",
                "context",
                "unclear",
                "developing",
            }
            filtered = [word for word in words if word.lower() not in stopwords]
            base = " ".join(filtered[:7] or words[:7])
        else:
            base = best_clause[: self.MAX_QUERY_CHARS]
        base = self._clean_query(base)
        if reason and "contradiction" in str(reason or "").lower() and "alternative" not in base.lower():
            return self._clean_query(base)
        return base

    def _clean_query(self, query: str) -> str:
        text = str(query or "").strip()
        for pattern in self.NOISE_PATTERNS:
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text.replace("—", " ").replace("|", " ")).strip(" .,:;")
        return text[: self.MAX_QUERY_CHARS].strip()

    def _normalize_snippet(self, snippet: str) -> str:
        text = re.sub(r"\s+", " ", str(snippet or "").strip())
        return text[: self.MAX_BODY_CHARS]

    def _extract_body(self, url: str, fallback: str, allow_extract: bool = True):
        snippet = self._normalize_snippet(fallback)
        if not url or not allow_extract:
            return snippet, False, False
        try:
            import trafilatura

            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded)
                if text and len(text) > 120:
                    return str(text).strip()[: self.MAX_BODY_CHARS], True, False
        except Exception as exc:
            logger.debug("Body extraction failed for %s: %s", url, exc)
            return snippet or url, False, True
        return snippet or url, False, False

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
