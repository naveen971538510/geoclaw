import sqlite3
import unittest

from services.debate_engine import DebateEngine
from tests.support import make_temp_db, remove_db, seed_sample_data


class TestDebateEngine(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        seed_sample_data(self.db_path)
        self.engine = DebateEngine(self.db_path, llm_analyst=None)

    def tearDown(self):
        remove_db(self.db_path)

    def test_debate_returns_bull_and_bear(self):
        result = self.engine.debate_thesis("Iran tensions affect Strait of Hormuz oil flow")
        self.assertIn("bull", result)
        self.assertIn("bear", result)
        self.assertIn("argument", result["bull"])
        self.assertIn("argument", result["bear"])

    def test_debate_returns_verdict(self):
        result = self.engine.debate_thesis("Iran tensions affect Strait of Hormuz oil flow")
        self.assertIn("verdict", result)
        self.assertIn(result["verdict_winner"], {"bull", "bear", "neutral"})

    def test_debate_rule_fallback_no_llm(self):
        result = self.engine.debate_thesis("Iran tensions affect Strait of Hormuz oil flow")
        self.assertEqual(result["mode"], "rule_based")

    def test_debate_unknown_thesis_returns_error(self):
        result = self.engine.debate_thesis("Missing thesis")
        self.assertEqual(result, {"error": "Thesis not found"})

    def test_bull_positive_framing(self):
        result = self.engine.debate_thesis("Iran tensions affect Strait of Hormuz oil flow")
        text = result["bull"]["argument"].lower()
        self.assertTrue("risk" in text or "threat" in text or "underpriced" in text)

    def test_bear_skeptical_framing(self):
        result = self.engine.debate_thesis("Iran tensions affect Strait of Hormuz oil flow")
        text = result["bear"]["argument"].lower()
        self.assertTrue("markets have seen" in text or "fade quickly" in text or "headline risk" in text)

    def test_low_confidence_thesis_can_favor_bear(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO agent_theses (
                thesis_key, confidence, status, evidence_count, title
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("Minor diplomatic rumor in Europe", 0.20, "active", 1, "Low Conviction"),
        )
        conn.commit()
        conn.close()
        result = self.engine.debate_thesis("Minor diplomatic rumor in Europe")
        self.assertEqual(result["verdict_winner"], "bear")


if __name__ == "__main__":
    unittest.main()
