"""
Reactive Event Agent — subscribes to the EventBus in a background thread and
autonomously investigates price spikes, contradictions, and high-impact articles.

Rate limits:
  - 5 min cooldown between investigations
  - Max 12 investigations per hour
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("geoclaw.reactive_agent")

_COOLDOWN_SECONDS = 300  # 5 minutes
_MAX_PER_HOUR = 12

# Event types that trigger an investigation
_TRIGGERS = {"price_spike", "contradiction_detected", "anomaly_detected"}


class ReactiveAgent:
    def __init__(self):
        self._investigations: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_investigation_ts: float = 0.0
        self._hourly_count: int = 0
        self._hourly_reset_ts: float = time.time()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._subscriber: Optional[queue.Queue] = None

    # --- public status ---

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> Dict[str, Any]:
        cooldown_remaining = max(0.0, _COOLDOWN_SECONDS - (time.time() - self._last_investigation_ts))
        return {
            "running": self._running,
            "investigation_count": len(self._investigations),
            "hourly_count": self._hourly_count,
            "hourly_limit": _MAX_PER_HOUR,
            "cooldown_remaining_s": round(cooldown_remaining, 1),
            "last_investigation": self._investigations[-1] if self._investigations else None,
        }

    def get_investigations(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._investigations[-limit:])

    # --- lifecycle ---

    def start(self):
        if self._running:
            return
        try:
            from services.event_bus import get_bus
            bus = get_bus()
            self._subscriber = bus.subscribe("*")
        except Exception as exc:
            logger.error("Cannot start reactive agent — EventBus unavailable: %s", exc)
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="reactive-agent")
        self._thread.start()
        logger.info("Reactive agent started")

    def stop(self):
        self._running = False
        if self._subscriber:
            try:
                from services.event_bus import get_bus
                get_bus().unsubscribe(self._subscriber, "*")
            except Exception:
                pass
        logger.info("Reactive agent stopped")

    # --- core loop ---

    def _loop(self):
        while self._running:
            try:
                event = self._subscriber.get(timeout=2)
            except queue.Empty:
                continue
            except Exception:
                continue

            event_type = str(event.get("type") or "")
            if event_type not in _TRIGGERS:
                continue

            # Rate limiting
            now = time.time()
            if now - self._hourly_reset_ts > 3600:
                self._hourly_count = 0
                self._hourly_reset_ts = now
            if self._hourly_count >= _MAX_PER_HOUR:
                logger.debug("Hourly investigation limit reached, skipping %s", event_type)
                continue
            if now - self._last_investigation_ts < _COOLDOWN_SECONDS:
                logger.debug("Cooldown active, skipping %s", event_type)
                continue

            self._investigate(event)

    def _investigate(self, event: Dict[str, Any]):
        event_type = str(event.get("type") or "")
        event_data = event.get("data") or {}
        now = time.time()
        self._last_investigation_ts = now
        self._hourly_count += 1

        investigation: Dict[str, Any] = {
            "trigger": event_type,
            "trigger_data": {k: str(v)[:200] for k, v in list(event_data.items())[:5]},
            "started_at": datetime.now(timezone.utc).isoformat(),
            "findings": [],
            "summary": "",
        }

        try:
            if event_type == "price_spike":
                investigation["findings"] = self._investigate_price_spike(event_data)
                investigation["summary"] = f"Price spike investigation: {event_data.get('symbol', 'unknown')} moved {event_data.get('change_pct', '?')}%"
            elif event_type == "contradiction_detected":
                investigation["findings"] = self._investigate_contradiction(event_data)
                investigation["summary"] = f"Contradiction investigation: {str(event_data.get('description', ''))[:100]}"
            elif event_type == "anomaly_detected":
                investigation["findings"] = self._investigate_anomaly(event_data)
                investigation["summary"] = f"Anomaly investigation: {str(event_data.get('description', ''))[:100]}"
            else:
                investigation["summary"] = f"Unhandled trigger: {event_type}"
        except Exception as exc:
            investigation["summary"] = f"Investigation failed: {exc}"
            investigation["findings"] = [{"error": str(exc)}]

        investigation["completed_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._investigations.append(investigation)
            if len(self._investigations) > 100:
                self._investigations = self._investigations[-100:]
        logger.info("Investigation complete: %s", investigation["summary"])

    # --- investigation strategies ---

    def _investigate_price_spike(self, data: Dict) -> List[Dict]:
        """Search for news that might explain the price spike."""
        findings = []
        symbol = str(data.get("symbol") or "unknown")
        try:
            from services.db_helpers import query
            rows = query(
                """
                SELECT headline, source, url, sentiment
                FROM ingested_articles
                WHERE headline LIKE ?
                ORDER BY fetched_at DESC
                LIMIT 5
                """,
                (f"%{symbol}%",),
            )
            for r in rows:
                findings.append({"type": "related_article", "headline": str(dict(r).get("headline", "")), "source": str(dict(r).get("source", ""))})
        except Exception:
            pass
        if not findings:
            findings.append({"type": "no_related_news", "note": f"No recent articles found mentioning {symbol}"})
        return findings

    def _investigate_contradiction(self, data: Dict) -> List[Dict]:
        """Pull the contradicting theses for context."""
        findings = []
        try:
            from services.db_helpers import query
            rows = query(
                """
                SELECT thesis_key, confidence, terminal_risk
                FROM agent_theses
                WHERE COALESCE(status, '') != 'superseded'
                ORDER BY confidence DESC
                LIMIT 5
                """
            )
            for r in rows:
                d = dict(r)
                findings.append({"type": "active_thesis", "thesis": str(d.get("thesis_key", ""))[:120], "confidence": d.get("confidence")})
        except Exception:
            pass
        return findings

    def _investigate_anomaly(self, data: Dict) -> List[Dict]:
        """Pull recent signals around the anomaly."""
        findings = []
        try:
            from services.db_helpers import query
            rows = query(
                """
                SELECT signal_name, direction, confidence
                FROM geoclaw_signals
                ORDER BY ts DESC
                LIMIT 5
                """
            )
            for r in rows:
                d = dict(r)
                findings.append({"type": "recent_signal", "name": str(d.get("signal_name", "")), "direction": str(d.get("direction", ""))})
        except Exception:
            pass
        return findings


# Singleton
_agent: Optional[ReactiveAgent] = None


def get_reactive_agent() -> ReactiveAgent:
    global _agent
    if _agent is None:
        _agent = ReactiveAgent()
    return _agent


def start_reactive_agent():
    get_reactive_agent().start()
