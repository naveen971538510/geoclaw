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
        for item in sorted(research_queue, key=lambda value: (int(value.get("priority", 9)), -float(value.get("confidence", 0.0) or 0.0))):
            thesis_key = str(item.get("thesis_key") or "").strip()
            if not thesis_key or thesis_key in seen:
                continue
            seen.add(thesis_key)
            deduped.append(item)
        return deduped[: self.MAX_SEARCHES_PER_RUN]

    def execute_research(self, research_needs: List[Dict]) -> Dict:
        if not self.searcher.available():
            logger.warning("Web searcher not available — skipping research")
            return {"searches_done": 0, "articles_found": 0, "articles_saved": 0, "needs_processed": 0, "reason": "searcher_unavailable"}

        total_found = 0
        total_saved = 0
        searches_done = 0
        details = []

        for need in research_needs[: self.MAX_SEARCHES_PER_RUN]:
            thesis_key = str(need.get("thesis_key") or "").strip()
            if not thesis_key:
                continue
            confidence = float(need.get("confidence", 0.5) or 0.5)
            reason = str(need.get("reason") or "").strip()
            logger.info("Researching '%s' — %s", thesis_key[:80], reason)
            try:
                if confidence <= 0.55:
                    articles = self.searcher.search_for_uncertainty(thesis_key, confidence)
                else:
                    articles = self.searcher.search_for_thesis(thesis_key)
                searches_done += 1
                total_found += len(articles)
                saved = self._save_web_articles(articles, thesis_key)
                total_saved += saved
                details.append({"thesis_key": thesis_key, "reason": reason, "results": len(articles), "saved": saved})
                time.sleep(1.5)
            except Exception as exc:
                logger.error("Research failed for '%s': %s", thesis_key[:80], exc)
                details.append({"thesis_key": thesis_key, "reason": reason, "results": 0, "saved": 0, "error": str(exc)})

        return {
            "searches_done": searches_done,
            "articles_found": total_found,
            "articles_saved": total_saved,
            "needs_processed": len(research_needs or []),
            "details": details[:6],
        }

    def _save_web_articles(self, articles: List[Dict], thesis_key: str) -> int:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        saved = 0
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
            except Exception as exc:
                logger.debug("Article save failed: %s", exc)
        conn.commit()
        conn.close()
        return saved

    def run_full_research_cycle(self) -> Dict:
        needs = self.identify_research_needs()
        if not needs:
            logger.info("Research: no knowledge gaps found")
            return {"searches_done": 0, "articles_found": 0, "articles_saved": 0, "needs_found": 0}

        stats = self.execute_research(needs)
        stats["needs_found"] = len(needs)
        stats["needs"] = needs
        return stats
