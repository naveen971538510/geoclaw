import unittest
import sqlite3
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services.ai_contracts import (
    clean_briefing_bundle,
    clean_query_answer_bundle,
    clean_thesis_bundle,
    default_query_answer_bundle,
    render_briefing_bundle,
    validate_briefing_bundle,
    validate_debate_argument,
    validate_query_answer_bundle,
    validate_thesis_bundle,
)
from services.llm_service import analyse_contradiction_meta
from services.llm_service import recent_usage_summary
from services.query_engine import QueryEngine
from services.thesis_service import build_thesis_claim
from tests.eval_cases import (
    ASK_EVAL_CASES,
    BRIEFING_EVAL_CASES,
    CONTRADICTION_EVAL_CASES,
    THESIS_GENERATION_EVAL_CASES,
)
from tests.support import make_temp_db, remove_db, seed_sample_data


class TestAIGuardrails(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        seed_sample_data(self.db_path)
        self.engine = QueryEngine(self.db_path, llm_analyst=None)

    def tearDown(self):
        remove_db(self.db_path)

    def test_eval_sets_have_expected_sizes(self):
        self.assertEqual(len(ASK_EVAL_CASES), 10)
        self.assertEqual(len(THESIS_GENERATION_EVAL_CASES), 10)
        self.assertEqual(len(CONTRADICTION_EVAL_CASES), 10)
        self.assertEqual(len(BRIEFING_EVAL_CASES), 10)

    def test_ask_eval_set_returns_non_empty_structured_answers(self):
        for question in ASK_EVAL_CASES:
            with self.subTest(question=question):
                result = self.engine.ask(question)
                self.assertTrue(result["answer"].strip())
                self.assertTrue(result["answer_card"]["direct_answer"].strip())
                self.assertGreaterEqual(len(result["answer_card"]["supporting_points"]), 1)
                self.assertGreaterEqual(result["confidence"], 0.0)
                self.assertLessEqual(result["confidence"], 1.0)

    def test_query_answer_validation_and_cleaning(self):
        payload = {
            "direct_answer": "Oil risk is being driven by Iran shipping tension.",
            "supporting_points": ["Brent-sensitive thesis is top ranked.", "Reuters article refreshed the risk premium."],
            "follow_up": ["What should I watch tomorrow?"],
            "caveat": "",
        }
        self.assertTrue(validate_query_answer_bundle(payload))
        cleaned = clean_query_answer_bundle(payload, default_query_answer_bundle({"answer": "Fallback", "follow_up": []}))
        self.assertEqual(cleaned["direct_answer"], payload["direct_answer"])

    def test_thesis_bundle_validation_rejects_bad_payload(self):
        self.assertFalse(validate_thesis_bundle({"thesis_key": "bad"}))
        clean = clean_thesis_bundle(
            {
                "thesis_key": "Iran tensions lift oil risk premium",
                "confidence_delta": 0.18,
                "timeframe": "days",
                "terminal_risk": "HIGH",
                "market_implication": "Oil and volatility are exposed.",
                "watchlist_suggestion": "CL=F, ^VIX",
                "reasoning": "Shipping risk is repricing.",
                "confidence_basis": "No disruption would weaken the thesis.",
            },
            headline="Iran tensions lift oil risk premium",
        )
        self.assertTrue(validate_thesis_bundle(clean))

    def test_debate_argument_schema_validation(self):
        self.assertTrue(validate_debate_argument({"argument": "Risk is underpriced.", "key_point": "Tail risk remains live"}))
        self.assertFalse(validate_debate_argument({"argument": "", "key_point": ""}))

    @patch("services.llm_service.OPENAI_API_KEY", "")
    def test_thesis_generation_eval_cases_return_safe_claim_bundle(self):
        for case in THESIS_GENERATION_EVAL_CASES:
            with self.subTest(headline=case["headline"]):
                bundle = build_thesis_claim(case["headline"], [], case["source"], case["category"])
                self.assertTrue(bundle["title"].strip())
                self.assertTrue(bundle["current_claim"].strip())
                self.assertIn("watch_for_next", bundle)

    @patch("services.llm_service.OPENAI_API_KEY", "")
    def test_contradiction_eval_cases_return_safe_fallback_shape(self):
        for case in CONTRADICTION_EVAL_CASES:
            with self.subTest(prior_claim=case["prior_claim"][:40]):
                result = analyse_contradiction_meta(case["current_text"], case["prior_claim"])
                payload = result["analysis"]
                self.assertIn(payload["resolution"], {"contradiction", "update", "nuance", "unrelated"})
                self.assertGreaterEqual(float(payload["confidence"]), 0.0)
                self.assertLessEqual(float(payload["confidence"]), 1.0)

    def test_briefing_eval_cases_render_non_empty_markdown(self):
        for case in BRIEFING_EVAL_CASES:
            with self.subTest(headline=case["headline"]):
                self.assertTrue(validate_briefing_bundle(case))
                clean = clean_briefing_bundle(case, case)
                text = render_briefing_bundle(clean)
                self.assertIn("##", text)
                self.assertTrue(text.strip())

    def test_recent_usage_summary_handles_grouped_rows_without_mode_column(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE llm_usage_log (
                lane TEXT,
                task_type TEXT,
                outcome TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO llm_usage_log (lane, task_type, outcome, created_at)
            VALUES ('reason', 'thesis_generation', 'fallback:api_error', '2099-01-01T00:00:00+00:00')
            """
        )
        conn.commit()
        try:
            with patch("services.llm_service.ensure_agent_tables"), patch("services.llm_service.get_conn", return_value=conn):
                summary = recent_usage_summary(hours=6)
        finally:
            conn.close()
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["by_task"]["thesis_generation"], 1)
        self.assertEqual(summary["fallbacks"]["fallback:api_error"], 1)

    def test_dashboard_pages_still_render(self):
        client = TestClient(app)
        dashboard = client.get("/dashboard")
        ask_page = client.get("/ask")
        live_page = client.get("/live")
        terminal_page = client.get("/terminal")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("What Changed Since Last Run", dashboard.text)
        self.assertIn("Ask GeoClaw", dashboard.text)
        self.assertEqual(ask_page.status_code, 200)
        self.assertEqual(live_page.status_code, 200)
        self.assertEqual(terminal_page.status_code, 200)


if __name__ == "__main__":
    unittest.main()
