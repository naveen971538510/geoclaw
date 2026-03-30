import unittest

from services.query_engine import QueryEngine
from tests.support import make_temp_db, remove_db, seed_sample_data


class TestQueryEngine(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        seed_sample_data(self.db_path)
        self.engine = QueryEngine(self.db_path)

    def tearDown(self):
        remove_db(self.db_path)

    def test_ask_oil_question_returns_answer(self):
        result = self.engine.ask("what is driving oil right now")
        self.assertIn("oil", result["answer"].lower())
        self.assertEqual(result["data"]["asset"], "oil")
        self.assertGreater(len(result["data"]["theses"]), 0)

    def test_ask_regime_returns_dict(self):
        result = self.engine.ask("what is the current market regime")
        self.assertIsInstance(result["data"], dict)
        self.assertIn("regime", result["data"])

    def test_ask_top_theses_returns_list(self):
        result = self.engine.ask("show top thesis")
        self.assertIsInstance(result["data"]["theses"], list)
        self.assertGreaterEqual(len(result["data"]["theses"]), 1)

    def test_ask_unknown_returns_fallback(self):
        result = self.engine.ask("explain penguin fisheries in antarctica")
        self.assertIn("no strong matching thesis", result["answer"].lower())
        self.assertLessEqual(result["confidence"], 0.1)

    def test_all_patterns_match_correctly(self):
        self.assertEqual(self.engine._match_pattern("what is driving oil").__name__, "_handle_explain_asset")
        self.assertEqual(self.engine._match_pattern("show confirmed theses").__name__, "_handle_show_confirmed_theses")
        self.assertEqual(self.engine._match_pattern("any contradiction").__name__, "_handle_show_contradictions")
        self.assertEqual(self.engine._match_pattern("what is the market regime").__name__, "_handle_show_regime")

    def test_followup_suggestions_always_returned(self):
        result = self.engine.ask("summary")
        self.assertEqual(len(result["follow_up"]), 3)

    def test_answer_always_string(self):
        result = self.engine.ask("show top thesis")
        self.assertIsInstance(result["answer"], str)

    def test_answer_card_contains_direct_answer_and_points(self):
        result = self.engine.ask("what is driving oil right now")
        self.assertIn("answer_card", result)
        self.assertTrue(result["answer_card"]["direct_answer"])
        self.assertGreaterEqual(len(result["answer_card"]["supporting_points"]), 2)

    def test_answer_remains_grounded_in_retrieved_data(self):
        result = self.engine.ask("what is driving oil right now")
        points = " ".join(result.get("grounding_points", [])).lower()
        theses = " ".join(item.get("thesis_key", "") for item in result["data"].get("theses", [])).lower()
        self.assertTrue(points)
        self.assertTrue(any(term in points for term in theses.split()[:3] if term))

    def test_confidence_in_0_1_range(self):
        result = self.engine.ask("show top thesis")
        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)

    def test_country_question_returns_country_data(self):
        result = self.engine.ask("what is happening in iran")
        self.assertEqual(result["data"]["country"], "iran")
        self.assertGreaterEqual(len(result["data"]["theses"]), 1)


if __name__ == "__main__":
    unittest.main()
