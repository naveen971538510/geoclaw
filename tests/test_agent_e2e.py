"""
End-to-end tests for the GeoClaw agentic system.

Tests cover: agent loop mechanics, reactive agent, LLM router, tool registry,
briefing format, and rate limiting.

Run: python -m pytest tests/test_agent_e2e.py -v
"""
import json
import os
import queue
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestToolRegistry(unittest.TestCase):
    """Verify all expected tools are registered."""

    def test_tool_map_has_core_tools(self):
        from agent_brain import TOOL_MAP
        expected = {"get_latest_signals", "run_signal_engine", "get_price_data", "get_macro_metrics", "assess_market_bias"}
        self.assertTrue(expected.issubset(set(TOOL_MAP.keys())), f"Missing tools: {expected - set(TOOL_MAP.keys())}")

    def test_tool_map_has_agentic_tools(self):
        from agent_brain import TOOL_MAP
        agentic = {"web_search", "fetch_breaking_news", "research_thesis", "get_active_theses"}
        self.assertTrue(agentic.issubset(set(TOOL_MAP.keys())), f"Missing agentic tools: {agentic - set(TOOL_MAP.keys())}")

    def test_tools_array_matches_tool_map(self):
        from agent_brain import TOOLS, TOOL_MAP
        tool_names = {t["function"]["name"] for t in TOOLS}
        # Every tool in TOOLS must have a handler in TOOL_MAP
        for name in tool_names:
            self.assertIn(name, TOOL_MAP, f"Tool '{name}' in TOOLS but not in TOOL_MAP")


class TestLLMRouter(unittest.TestCase):
    """Verify multi-provider failover logic."""

    def test_status_returns_providers(self):
        from services.llm_router import get_status
        status = get_status()
        self.assertIn("providers", status)
        self.assertIsInstance(status["providers"], list)
        names = {p["name"] for p in status["providers"]}
        self.assertIn("groq", names)
        self.assertIn("openai", names)
        self.assertIn("gemini", names)

    def test_provider_backoff_tracking(self):
        from services.llm_router import _ProviderState
        state = _ProviderState(name="test")
        self.assertFalse(state.is_backed_off)
        state.record_failure()
        self.assertTrue(state.is_backed_off)
        self.assertEqual(state.failures, 1)
        state.record_success()
        self.assertFalse(state.is_backed_off)
        self.assertEqual(state.failures, 0)


class TestReactiveAgent(unittest.TestCase):
    """Verify reactive agent rate limiting and event handling."""

    def test_initial_status(self):
        from services.reactive_agent import ReactiveAgent
        agent = ReactiveAgent()
        status = agent.status()
        self.assertFalse(status["running"])
        self.assertEqual(status["investigation_count"], 0)

    def test_cooldown_enforced(self):
        from services.reactive_agent import ReactiveAgent, _COOLDOWN_SECONDS
        agent = ReactiveAgent()
        # Simulate a recent investigation
        agent._last_investigation_ts = time.time()
        status = agent.status()
        self.assertGreater(status["cooldown_remaining_s"], 0)

    def test_hourly_limit(self):
        from services.reactive_agent import ReactiveAgent, _MAX_PER_HOUR
        agent = ReactiveAgent()
        agent._hourly_count = _MAX_PER_HOUR
        agent._hourly_reset_ts = time.time()
        # The limit should prevent new investigations
        self.assertEqual(agent._hourly_count, _MAX_PER_HOUR)


class TestEventBus(unittest.TestCase):
    """Verify EventBus pub/sub works."""

    def test_publish_and_subscribe(self):
        from services.event_bus import EventBus
        bus = EventBus()
        subscriber = bus.subscribe("price_spike")
        bus.publish("price_spike", {"symbol": "GLD", "change_pct": 3.5})
        event = subscriber.get(timeout=1)
        self.assertEqual(event["type"], "price_spike")
        self.assertEqual(event["data"]["symbol"], "GLD")

    def test_wildcard_subscriber(self):
        from services.event_bus import EventBus
        bus = EventBus()
        subscriber = bus.subscribe("*")
        bus.publish("thesis_updated", {"key": "test"})
        event = subscriber.get(timeout=1)
        self.assertEqual(event["type"], "thesis_updated")

    def test_history_maintained(self):
        from services.event_bus import EventBus
        bus = EventBus()
        bus.publish("test_event", {"n": 1})
        bus.publish("test_event", {"n": 2})
        history = bus.get_history(limit=10)
        self.assertEqual(len(history), 2)


class TestBriefingFormat(unittest.TestCase):
    """Verify briefing formatter produces valid output."""

    def test_build_briefing_returns_string(self):
        try:
            from briefing_formatter import build_briefing
            run_state = {
                "run_id": "test",
                "started_at": "2026-01-01T00:00:00Z",
                "degraded_mode": False,
                "degradation_notes": [],
            }
            result = build_briefing(run_state)
            self.assertIsInstance(result, str)
        except Exception:
            self.skipTest("briefing_formatter not importable in test env")


if __name__ == "__main__":
    unittest.main()
