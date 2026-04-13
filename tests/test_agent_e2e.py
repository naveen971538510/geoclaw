"""
End-to-end agent loop test — exercises the full agentic pipeline
without requiring live API keys. Uses mock LLM responses and mock search.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.support import make_temp_db, remove_db, seed_sample_data

try:
    import requests as _requests_check
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ---------------------------------------------------------------------------
# Mock Groq response that exercises the ReAct loop:
#   1. Calls run_signal_engine + get_price_data + get_active_theses (baseline)
#   2. Calls web_search to investigate (agentic step)
#   3. Calls assess_market_bias (synthesize)
#   4. Stops with a final message
# ---------------------------------------------------------------------------

def _tool_call(call_id, name, args=None):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    }


MOCK_GROQ_RESPONSES = [
    # Step 1: Agent gathers baseline — calls 3 tools
    {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("tc1", "run_signal_engine"),
                    _tool_call("tc2", "get_price_data"),
                    _tool_call("tc3", "get_active_theses", {"limit": 5}),
                ],
            },
            "finish_reason": "tool_calls",
        }]
    },
    # Step 2: Agent investigates — web search on a thesis
    {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("tc4", "web_search", {"query": "Iran oil strait of hormuz latest"}),
                ],
            },
            "finish_reason": "tool_calls",
        }]
    },
    # Step 3: Agent synthesizes — assess bias
    {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tool_call("tc5", "assess_market_bias"),
                ],
            },
            "finish_reason": "tool_calls",
        }]
    },
    # Step 4: Agent done
    {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Investigation complete. Market bias is NEUTRAL with bearish undertones from geopolitical risk.",
            },
            "finish_reason": "stop",
        }]
    },
]


class TestAgentE2E(unittest.TestCase):
    """Full agent loop dry-run with mocked external services."""

    @classmethod
    def setUpClass(cls):
        cls.db_path = make_temp_db()
        seed_sample_data(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        remove_db(cls.db_path)

    @unittest.skipUnless(HAS_REQUESTS, "requires 'requests' package")
    def test_full_agent_loop_dry_run(self):
        """The agent loop runs through all 4 steps without error."""
        call_idx = {"i": 0}

        def mock_call_groq(messages, tools=None):
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx < len(MOCK_GROQ_RESPONSES):
                return MOCK_GROQ_RESPONSES[idx]
            return MOCK_GROQ_RESPONSES[-1]

        mock_telegram = MagicMock(return_value={"status": "ok", "message": "Sent"})
        mock_web_search = MagicMock(return_value={
            "query": "Iran oil strait of hormuz latest",
            "results_found": 2,
            "articles": [
                {"title": "Iran warns of Hormuz blockade", "url": "https://example.com/1", "body": "Iran...", "source": "reuters.com"},
                {"title": "Oil prices steady amid tensions", "url": "https://example.com/2", "body": "Oil...", "source": "bloomberg.com"},
            ],
        })

        with patch("agent_brain.call_groq", side_effect=mock_call_groq), \
             patch("agent_brain.tool_send_telegram_briefing", mock_telegram), \
             patch("agent_brain.tool_web_search", mock_web_search), \
             patch("agent_brain.tool_get_price_data", return_value={"prices": [{"ticker": "SPX", "price": 5200, "ts": "2026-04-13"}], "count": 1, "refresh": {"status": "ok"}}), \
             patch("agent_brain.tool_get_macro_metrics", return_value={"metrics": [{"metric_name": "CPI_YOY_PCT", "value": 3.2}], "count": 1, "freshness": {"status": "fresh"}}), \
             patch("agent_brain.tool_run_signal_engine", return_value={"status": "ok"}), \
             patch("agent_brain.tool_get_latest_signals", return_value={"signals": [{"signal_name": "Gold", "direction": "BUY", "confidence": 70, "ts": "2026-04-13"}], "count": 1, "freshness": {"status": "ok"}}), \
             patch("agent_brain.tool_assess_market_bias", return_value={"bias": "NEUTRAL", "buy_confidence_total": 70, "sell_confidence_total": 65}), \
             patch("agent_brain.tool_get_active_theses", return_value={"theses": [{"thesis_key": "Iran oil", "current_claim": "Iran tensions", "confidence": 0.93, "status": "confirmed", "evidence_count": 5, "category": "geopolitics"}], "count": 1}), \
             patch("agent_brain._write_operator_status"):

            from agent_brain import run_agent_loop
            run_agent_loop()

        # Groq was called 4 times (baseline, investigate, synthesize, done)
        self.assertEqual(call_idx["i"], 4)

        # Telegram was called to send the briefing
        mock_telegram.assert_called_once()
        briefing_text = mock_telegram.call_args[0][0]
        self.assertIn("GeoClaw", briefing_text)

        # Web search was called for investigation
        mock_web_search.assert_called_once()

    def test_briefing_contains_investigation_findings(self):
        """The briefing includes agent investigation results."""
        from briefing_formatter import build_briefing

        run_state = {
            "started_at": "2026-04-13T10:00:00Z",
            "degraded_mode": False,
            "signals_snapshot": [
                {"signal_name": "Gold", "direction": "BUY", "confidence": 72, "ts": "2026-04-13T09:00:00Z"},
            ],
            "price_data": {"prices": [{"ticker": "SPX", "price": 5200, "ts": "2026-04-13"}]},
            "macro_metrics": {
                "metrics": [{"metric_name": "CPI_YOY_PCT", "value": 3.2}],
                "freshness": {"status": "fresh"},
            },
            "market_bias": {"bias": "BULLISH"},
            "active_theses": [
                {"thesis_key": "gold rally", "current_claim": "Gold rally on rate pause", "confidence": 0.72, "status": "active", "evidence_count": 4, "category": "markets"},
            ],
            "investigation_findings": [
                "Searched 'gold price rally': 3 results — Gold surges; Precious metals rise",
            ],
        }

        briefing = build_briefing(run_state)
        self.assertIn("Agent Investigations", briefing)
        self.assertIn("gold price rally", briefing)
        self.assertIn("Active Theses", briefing)
        self.assertIn("gold rally", briefing.lower())
        self.assertIn("[████░]", briefing)  # confidence bar

    def test_confidence_bar(self):
        from briefing_formatter import _confidence_bar
        self.assertEqual(_confidence_bar(0), "[░░░░░]")
        self.assertEqual(_confidence_bar(50), "[██░░░]")  # 2.5 rounds to 2
        self.assertEqual(_confidence_bar(100), "[█████]")
        self.assertEqual(_confidence_bar(80), "[████░]")

    def test_reactive_agent_handles_price_spike(self):
        """ReactiveAgent processes a price_spike event without crashing."""
        from services.reactive_agent import ReactiveAgent

        agent = ReactiveAgent(db_path=self.db_path)

        mock_searcher = MagicMock()
        mock_searcher.available.return_value = True
        mock_searcher.search_with_details.return_value = {
            "results": [
                {"title": "Gold spikes on Fed news", "url": "https://example.com/gold", "body": "Gold...", "source": "reuters.com"},
            ]
        }
        agent._searcher = mock_searcher

        agent._handle_event({
            "type": "price_spike",
            "data": {
                "ticker": "GC=F",
                "pct_change": 3.5,
                "price": 2400,
                "previous_price": 2320,
            },
            "timestamp": 1234567890,
        })

        mock_searcher.search_with_details.assert_called_once()
        query_used = mock_searcher.search_with_details.call_args[0][0]
        self.assertIn("GC=F", query_used)
        self.assertIn("surged", query_used)

    def test_reactive_agent_respects_cooldown(self):
        """ReactiveAgent skips duplicate investigations within cooldown."""
        from services.reactive_agent import ReactiveAgent

        agent = ReactiveAgent(db_path=self.db_path)
        mock_searcher = MagicMock()
        mock_searcher.available.return_value = True
        mock_searcher.search_with_details.return_value = {"results": []}
        agent._searcher = mock_searcher

        event = {
            "type": "price_spike",
            "data": {"ticker": "SPY", "pct_change": 2.5},
            "timestamp": 1234567890,
        }

        agent._handle_event(event)
        agent._handle_event(event)  # same event again

        # Should only search once due to cooldown
        self.assertEqual(mock_searcher.search_with_details.call_count, 1)

    def test_reactive_agent_hourly_limit(self):
        """ReactiveAgent stops investigating after hourly limit."""
        import time
        from services.reactive_agent import ReactiveAgent, MAX_INVESTIGATIONS_PER_HOUR

        agent = ReactiveAgent(db_path=self.db_path)
        # Set the hourly limit AND reset time to now so it doesn't auto-reset
        agent._hourly_count = MAX_INVESTIGATIONS_PER_HOUR
        agent._hourly_reset_at = time.time()

        mock_searcher = MagicMock()
        mock_searcher.available.return_value = True
        agent._searcher = mock_searcher

        agent._handle_event({
            "type": "price_spike",
            "data": {"ticker": "NEW_TICKER", "pct_change": 5.0},
            "timestamp": 1234567890,
        })

        mock_searcher.search_with_details.assert_not_called()

    @unittest.skipUnless(HAS_REQUESTS, "requires 'requests' package")
    def test_new_tools_in_brain(self):
        """All new tools are defined in TOOLS and TOOL_MAP."""
        from agent_brain import TOOLS, TOOL_MAP

        tool_names = {t["function"]["name"] for t in TOOLS}
        expected = {"web_search", "fetch_breaking_news", "research_thesis", "get_active_theses"}

        for name in expected:
            self.assertIn(name, tool_names, f"{name} missing from TOOLS")
            self.assertIn(name, TOOL_MAP, f"{name} missing from TOOL_MAP")


if __name__ == "__main__":
    unittest.main()
