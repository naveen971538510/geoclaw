import logging
import queue
import threading
import time
from typing import Dict, List


logger = logging.getLogger("geoclaw.events")


EVENT_TYPES = {
    "article_ingested": "New article saved to DB",
    "thesis_updated": "Thesis confidence changed",
    "thesis_confirmed": "Thesis promoted to confirmed",
    "thesis_superseded": "Thesis expired or was superseded",
    "action_proposed": "New action proposed",
    "action_approved": "Action approved by user",
    "alert_fired": "Alert condition triggered",
    "agent_run_started": "Agent run began",
    "agent_run_complete": "Agent run finished",
    "contradiction_detected": "New contradiction logged",
    "regime_changed": "Market regime changed",
    "price_spike": "Price moved more than 2 percent",
    "briefing_generated": "New intelligence brief created",
    "prediction_checked": "Prediction outcome batch checked",
    "anomaly_detected": "Anomaly detector found an unusual condition",
}


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[queue.Queue]] = {}
        self._lock = threading.Lock()
        self._history: List[Dict] = []
        self._max_history = 200

    def subscribe(self, event_type: str = "*") -> queue.Queue:
        subscriber = queue.Queue(maxsize=50)
        with self._lock:
            self._subscribers.setdefault(str(event_type or "*"), []).append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue, event_type: str = "*"):
        with self._lock:
            listeners = self._subscribers.get(str(event_type or "*"), [])
            try:
                listeners.remove(subscriber)
            except ValueError:
                pass

    def publish(self, event_type: str, data: Dict):
        event = {
            "type": str(event_type or "").strip() or "unknown",
            "data": data or {},
            "timestamp": time.time(),
            "description": EVENT_TYPES.get(str(event_type or "").strip(), str(event_type or "").strip()),
        }
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            targets = list(self._subscribers.get(event["type"], [])) + list(self._subscribers.get("*", []))

        for subscriber in targets:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                pass

        logger.debug("Event published: %s %s", event["type"], str(event["data"])[:120])

    def get_history(self, limit: int = 50) -> List[Dict]:
        return list(self._history[-int(limit or 50) :])

    def get_recent(self, since_timestamp: float) -> List[Dict]:
        cutoff = float(since_timestamp or 0.0)
        return [event for event in self._history if float(event.get("timestamp", 0.0) or 0.0) > cutoff]


_bus = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def publish(event_type: str, data: Dict):
    get_bus().publish(event_type, data)
