"""
Reactive Agent — Event-driven autonomous investigation pipeline.

Listens to EventBus events (price_spike, anomaly_detected, contradiction_detected)
and autonomously investigates by searching the web, updating theses, and firing alerts.
"""

import json
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.event_bus import get_bus, publish
from services.logging_service import get_logger
from services.web_searcher import WebSearcher
from config import DB_PATH

logger = get_logger("reactive_agent")

INVESTIGATION_COOLDOWN_SECONDS = 300
MAX_INVESTIGATIONS_PER_HOUR = 12


class ReactiveAgent:
    def __init__(self, db_path: str = None):
        self._db_path = db_path or str(DB_PATH)
        self._searcher = WebSearcher(db_path=self._db_path)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._subscriber: Optional[queue.Queue] = None
        self._last_investigation: Dict[str, float] = {}
        self._hourly_count = 0
        self._hourly_reset_at = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._subscriber = get_bus().subscribe("*")
        self._thread = threading.Thread(target=self._loop, daemon=True, name="reactive-agent")
        self._thread.start()
        logger.info("Reactive agent started — listening for events")

    def stop(self):
        self._running = False
        if self._subscriber:
            get_bus().unsubscribe(self._subscriber, "*")
            self._subscriber = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Reactive agent stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def _loop(self):
        while self._running:
            try:
                event = self._subscriber.get(timeout=2.0)
            except queue.Empty:
                continue
            except Exception:
                continue

            try:
                self._handle_event(event)
            except Exception as exc:
                logger.warning("Reactive agent error handling event %s: %s", event.get("type"), exc)

    def _handle_event(self, event: Dict):
        event_type = event.get("type", "")
        data = event.get("data", {})

        if event_type == "price_spike":
            self._investigate_price_spike(data)
        elif event_type == "anomaly_detected":
            self._investigate_anomaly(data)
        elif event_type == "contradiction_detected":
            self._investigate_contradiction(data)
        elif event_type == "article_ingested":
            self._check_high_impact_article(data)

    def _should_investigate(self, key: str) -> bool:
        now = time.time()
        if now - self._hourly_reset_at > 3600:
            self._hourly_count = 0
            self._hourly_reset_at = now
        if self._hourly_count >= MAX_INVESTIGATIONS_PER_HOUR:
            logger.debug("Skipping investigation for %s: hourly limit reached", key)
            return False
        last = self._last_investigation.get(key, 0.0)
        if now - last < INVESTIGATION_COOLDOWN_SECONDS:
            logger.debug("Skipping investigation for %s: cooldown active", key)
            return False
        return True

    def _mark_investigated(self, key: str):
        self._last_investigation[key] = time.time()
        self._hourly_count += 1

    def _investigate_price_spike(self, data: Dict):
        ticker = str(data.get("ticker") or data.get("symbol") or "").upper()
        pct_change = float(data.get("pct_change") or data.get("change_pct") or 0)
        if not ticker:
            return

        investigation_key = f"price_spike:{ticker}"
        if not self._should_investigate(investigation_key):
            return
        self._mark_investigated(investigation_key)

        direction = "surged" if pct_change > 0 else "dropped"
        query = f"{ticker} {direction} {abs(pct_change):.1f}% today why"
        logger.info("Investigating price spike: %s %s %.1f%%", ticker, direction, pct_change)

        result = self._search_and_summarize(query, context_key=investigation_key)

        if result.get("articles"):
            self._update_thesis_from_investigation(
                ticker, result["articles"], f"Price spike {direction} {abs(pct_change):.1f}%"
            )
            publish("alert_fired", {
                "source": "reactive_agent",
                "trigger": "price_spike",
                "ticker": ticker,
                "pct_change": pct_change,
                "investigation_summary": result.get("summary", ""),
                "articles_found": len(result.get("articles", [])),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def _investigate_anomaly(self, data: Dict):
        description = str(data.get("description") or data.get("reason") or "")
        metric = str(data.get("metric") or data.get("metric_name") or "market anomaly")
        if not description:
            return

        investigation_key = f"anomaly:{metric}"
        if not self._should_investigate(investigation_key):
            return
        self._mark_investigated(investigation_key)

        query = f"{metric} {description[:60]} latest"
        logger.info("Investigating anomaly: %s", query[:80])

        result = self._search_and_summarize(query, context_key=investigation_key)

        if result.get("articles"):
            publish("alert_fired", {
                "source": "reactive_agent",
                "trigger": "anomaly",
                "metric": metric,
                "description": description,
                "investigation_summary": result.get("summary", ""),
                "articles_found": len(result.get("articles", [])),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def _investigate_contradiction(self, data: Dict):
        thesis_key = str(data.get("thesis_key") or data.get("headline") or "")
        reason = str(data.get("reason") or data.get("explanation") or "")
        if not thesis_key:
            return

        investigation_key = f"contradiction:{thesis_key[:60]}"
        if not self._should_investigate(investigation_key):
            return
        self._mark_investigated(investigation_key)

        logger.info("Investigating contradiction: %s", thesis_key[:80])

        try:
            from services.research_agent import research_thesis
            result = research_thesis(
                thesis_key=thesis_key[:160],
                current_claim=reason or thesis_key,
                category=str(data.get("category", "markets")),
            )
            if result.get("status") == "ok":
                publish("alert_fired", {
                    "source": "reactive_agent",
                    "trigger": "contradiction_research",
                    "thesis_key": thesis_key,
                    "support_count": result.get("support_count", 0),
                    "contradict_count": result.get("contradict_count", 0),
                    "articles_found": result.get("articles_found", 0),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as exc:
            logger.warning("Contradiction research failed for %s: %s", thesis_key[:60], exc)

    def _check_high_impact_article(self, data: Dict):
        impact_score = int(data.get("impact_score") or 0)
        alert_tags = data.get("alert_tags") or []
        if impact_score < 70 and not alert_tags:
            return

        headline = str(data.get("headline") or "")
        if not headline:
            return

        investigation_key = f"high_impact:{headline[:40]}"
        if not self._should_investigate(investigation_key):
            return
        self._mark_investigated(investigation_key)

        logger.info("High-impact article detected (score=%d): %s", impact_score, headline[:80])

        query = f"{headline[:70]} analysis impact"
        result = self._search_and_summarize(query, context_key=investigation_key)

        if result.get("articles"):
            self._update_thesis_from_investigation(
                headline[:100], result["articles"], f"High-impact article (score {impact_score})"
            )

    def _search_and_summarize(self, query: str, context_key: str = "") -> Dict:
        if not self._searcher.available():
            logger.debug("Web search unavailable for reactive investigation")
            return {"articles": [], "summary": "Search backend unavailable"}

        result = self._searcher.search_with_details(
            query, max_results=3, triggered_by="reactive_agent"
        )
        articles = result.get("results", [])
        summary = ""
        if articles:
            titles = [a.get("title", "") for a in articles[:3]]
            summary = f"Found {len(articles)} articles: {'; '.join(titles)}"
        return {"articles": articles, "summary": summary}

    def _update_thesis_from_investigation(self, key: str, articles: List[Dict], reason: str):
        try:
            from services.thesis_service import upsert_thesis
            headline = articles[0].get("title", key) if articles else key
            upsert_thesis(
                key[:160],
                current_claim=headline[:300],
                status="active",
                evidence_delta=len(articles),
                source_name=articles[0].get("source", "web") if articles else "reactive_agent",
                category="markets",
                last_update_reason=f"Reactive investigation: {reason}",
            )
        except Exception as exc:
            logger.debug("Could not update thesis from reactive investigation: %s", exc)

    def get_status(self) -> Dict:
        return {
            "running": self._running,
            "investigations_this_hour": self._hourly_count,
            "max_per_hour": MAX_INVESTIGATIONS_PER_HOUR,
            "cooldown_seconds": INVESTIGATION_COOLDOWN_SECONDS,
            "active_cooldowns": len(self._last_investigation),
        }


_reactive_agent: Optional[ReactiveAgent] = None


def get_reactive_agent() -> ReactiveAgent:
    global _reactive_agent
    if _reactive_agent is None:
        _reactive_agent = ReactiveAgent()
    return _reactive_agent


def start_reactive_agent():
    agent = get_reactive_agent()
    agent.start()
    return agent


def stop_reactive_agent():
    global _reactive_agent
    if _reactive_agent:
        _reactive_agent.stop()
        _reactive_agent = None
