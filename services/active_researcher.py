import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, List

from services.web_searcher import WebSearcher


logger = logging.getLogger("geoclaw.researcher")


class ActiveResearcher:
    """
    The agent's research loop.
    Decides what to search for based on its own knowledge gaps.
    """

    MAX_SEARCHES_PER_RUN = 4

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.searcher = WebSearcher(db_path)

    def identify_research_needs(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        research_queue = []

        risky_low_evidence = conn.execute(
            """
            SELECT thesis_key, confidence, evidence_count, terminal_risk, confidence_velocity
            FROM agent_theses
            WHERE terminal_risk LIKE 'HIGH%'
              AND COALESCE(evidence_count, 0) < 3
              AND status NOT IN ('superseded')
            ORDER BY confidence DESC
            LIMIT 2
            """
        ).fetchall()
        for row in risky_low_evidence:
            research_queue.append(
                {
                    "thesis_key": row["thesis_key"],
                    "reason": f"HIGH risk but only {int(row['evidence_count'] or 0)} evidence articles — need verification",
                    "priority": 1,
                    "confidence": float(row["confidence"] or 0.0),
                }
            )

        falling = conn.execute(
            """
            SELECT thesis_key, confidence, confidence_velocity
            FROM agent_theses
            WHERE COALESCE(confidence_velocity, 0) < -0.04
              AND status NOT IN ('superseded')
            ORDER BY confidence_velocity ASC
            LIMIT 2
            """
        ).fetchall()
        for row in falling:
            research_queue.append(
                {
                    "thesis_key": row["thesis_key"],
                    "reason": f"Confidence falling (velocity={float(row['confidence_velocity'] or 0.0):.3f}) — search for cause",
                    "priority": 2,
                    "confidence": float(row["confidence"] or 0.0),
                }
            )

        uncertain = conn.execute(
            """
            SELECT thesis_key, confidence
            FROM agent_theses
            WHERE confidence < 0.42
              AND confidence > 0.20
              AND status = 'active'
            ORDER BY evidence_count ASC, confidence ASC
            LIMIT 2
            """
        ).fetchall()
        for row in uncertain:
            research_queue.append(
                {
                    "thesis_key": row["thesis_key"],
                    "reason": f"Low confidence ({float(row['confidence'] or 0.0):.0%}) — search for confirming or contradicting evidence",
                    "priority": 3,
                    "confidence": float(row["confidence"] or 0.0),
                }
            )

        contradictions = conn.execute(
            """
            SELECT thesis_key, explanation
            FROM contradictions
            WHERE resolved = 0
              AND created_at >= datetime('now', '-48 hours')
            LIMIT 1
            """
        ).fetchall()
        for row in contradictions:
            research_queue.append(
                {
                    "thesis_key": row["thesis_key"],
                    "reason": "Active contradiction — search for resolution: "
                    + str(row["explanation"] or "")[:80],
                    "priority": 2,
                    "confidence": 0.5,
                }
            )

        conn.close()

        deduped = []
        seen = set()
        for item in sorted(
            research_queue,
            key=lambda value: (int(value.get("priority", 9)), -float(value.get("confidence", 0.0) or 0.0)),
        ):
            thesis_key = str(item.get("thesis_key") or "").strip()
            if not thesis_key or thesis_key in seen:
                continue
            seen.add(thesis_key)
            deduped.append(item)
        return deduped[: self.MAX_SEARCHES_PER_RUN]

    def execute_research(self, research_needs: List[Dict]) -> Dict:
        started = time.time()
        if not self.searcher.available():
            logger.warning("Web searcher not available — skipping research")
            return {
                "searches_done": 0,
                "search_cycles": 0,
                "searches_attempted": 0,
                "searches_succeeded": 0,
                "raw_results_found": 0,
                "articles_found": 0,
                "articles_saved": 0,
                "extraction_failures": 0,
                "duplicate_urls_skipped": 0,
                "needs_processed": 0,
                "reason": "searcher_unavailable",
                "zero_reasons": ["searcher_unavailable"],
                "details": [],
                "duration_seconds": 0.0,
            }

        totals = {
            "searches_done": 0,
            "search_cycles": 0,
            "searches_attempted": 0,
            "searches_succeeded": 0,
            "raw_results_found": 0,
            "articles_found": 0,
            "articles_saved": 0,
            "extraction_failures": 0,
            "duplicate_urls_skipped": 0,
            "wait_seconds": 0.0,
            "extraction_seconds": 0.0,
            "needs_processed": 0,
            "zero_reasons": [],
            "details": [],
            "backend": getattr(self.searcher, "_backend", ""),
        }
        seen_theses = set()

        for need in (research_needs or [])[: self.MAX_SEARCHES_PER_RUN]:
            thesis_key = str(need.get("thesis_key") or "").strip()
            if not thesis_key or thesis_key in seen_theses:
                continue
            seen_theses.add(thesis_key)
            totals["needs_processed"] += 1
            totals["search_cycles"] += 1
            confidence = float(need.get("confidence", 0.5) or 0.5)
            reason = str(need.get("reason") or "").strip()
            logger.info("Researching '%s' — %s", thesis_key[:80], reason)
            try:
                if confidence <= 0.55:
                    result = self.searcher.search_for_uncertainty(thesis_key, confidence, reason=reason)
                else:
                    result = self.searcher.search_for_thesis(thesis_key, reason=reason)
                save_stats = self._save_web_articles(result.get("results", []) or [], thesis_key)
                totals["searches_done"] += int(result.get("searches_attempted", 0) or 0)
                totals["searches_attempted"] += int(result.get("searches_attempted", 0) or 0)
                totals["searches_succeeded"] += int(result.get("searches_succeeded", 0) or 0)
                totals["raw_results_found"] += int(result.get("raw_results_found", 0) or 0)
                totals["articles_found"] += int(result.get("articles_found", 0) or 0)
                totals["articles_saved"] += int(save_stats.get("saved", 0) or 0)
                totals["extraction_failures"] += int(result.get("extraction_failures", 0) or 0)
                totals["duplicate_urls_skipped"] += int(result.get("duplicate_urls_skipped", 0) or 0)
                totals["duplicate_urls_skipped"] += int(save_stats.get("duplicate_urls_skipped", 0) or 0)
                totals["wait_seconds"] += float(result.get("wait_seconds", 0.0) or 0.0)
                totals["extraction_seconds"] += float(result.get("extraction_seconds", 0.0) or 0.0)
                zero_reasons = list(result.get("zero_reasons", []) or [])
                totals["zero_reasons"].extend(zero_reasons)
                detail = {
                    "thesis_key": thesis_key,
                    "reason": reason,
                    "searches_attempted": int(result.get("searches_attempted", 0) or 0),
                    "searches_succeeded": int(result.get("searches_succeeded", 0) or 0),
                    "raw_results_found": int(result.get("raw_results_found", 0) or 0),
                    "articles_found": int(result.get("articles_found", 0) or 0),
                    "articles_saved": int(save_stats.get("saved", 0) or 0),
                    "extraction_failures": int(result.get("extraction_failures", 0) or 0),
                    "duplicate_urls_skipped": int(result.get("duplicate_urls_skipped", 0) or 0)
                    + int(save_stats.get("duplicate_urls_skipped", 0) or 0),
                    "zero_reason": zero_reasons[0] if zero_reasons else "",
                    "queries": list(result.get("details", []) or [])[:3],
                }
                if not detail["articles_saved"] and detail["articles_found"] <= 0:
                    logger.info(
                        "Research found no usable evidence for '%s' (reason=%s)",
                        thesis_key[:80],
                        detail["zero_reason"] or "unknown",
                    )
                totals["details"].append(detail)
            except Exception as exc:
                logger.error("Research failed for '%s': %s", thesis_key[:80], exc)
                totals["zero_reasons"].append("research_error:" + exc.__class__.__name__)
                totals["details"].append(
                    {
                        "thesis_key": thesis_key,
                        "reason": reason,
                        "searches_attempted": 0,
                        "searches_succeeded": 0,
                        "raw_results_found": 0,
                        "articles_found": 0,
                        "articles_saved": 0,
                        "extraction_failures": 0,
                        "duplicate_urls_skipped": 0,
                        "zero_reason": "research_error:" + exc.__class__.__name__,
                        "error": str(exc),
                        "queries": [],
                    }
                )

        totals["wait_seconds"] = round(float(totals.get("wait_seconds", 0.0) or 0.0), 3)
        totals["extraction_seconds"] = round(float(totals.get("extraction_seconds", 0.0) or 0.0), 3)
        totals["duration_seconds"] = round(max(0.0, time.time() - started), 3)
        return totals

    def _save_web_articles(self, articles: List[Dict], thesis_key: str) -> Dict:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        saved = 0
        duplicates = 0
        for article in articles:
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO web_sourced_articles (
                        headline, url, body, source, search_query, published_at,
                        fetched_at, is_reasoned, thesis_key
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        str(article.get("title") or "")[:300],
                        str(article.get("url") or ""),
                        str(article.get("body") or "")[:2000],
                        str(article.get("source") or "web")[:120],
                        str(article.get("search_query") or "")[:200],
                        str(article.get("published_at") or ""),
                        datetime.now(timezone.utc).isoformat(),
                        str(thesis_key or "")[:200],
                    ),
                )
                if int(cursor.rowcount or 0) > 0:
                    saved += 1
                else:
                    duplicates += 1
            except Exception as exc:
                logger.debug("Article save failed: %s", exc)
        conn.commit()
        conn.close()
        return {"saved": saved, "duplicate_urls_skipped": duplicates}

    def run_full_research_cycle(self) -> Dict:
        needs = self.identify_research_needs()
        if not needs:
            logger.info("Research: no knowledge gaps found")
            return {
                "searches_done": 0,
                "search_cycles": 0,
                "searches_attempted": 0,
                "searches_succeeded": 0,
                "raw_results_found": 0,
                "articles_found": 0,
                "articles_saved": 0,
                "extraction_failures": 0,
                "duplicate_urls_skipped": 0,
                "needs_found": 0,
                "needs_processed": 0,
                "zero_reasons": ["no_research_needs"],
                "duration_seconds": 0.0,
            }

        stats = self.execute_research(needs)
        stats["needs_found"] = len(needs)
        stats["needs"] = needs
        return stats
