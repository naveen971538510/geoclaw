import sqlite3
import unittest

from services.sentiment_index import SentimentIndex
from tests.support import iso_now, make_temp_db, remove_db


class TestSentimentIndex(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        self.index = SentimentIndex()

    def tearDown(self):
        remove_db(self.db_path)

    def test_compute_returns_score_0_to_100(self):
        self._seed_case()
        result = self.index.compute(self.db_path)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)

    def test_extreme_fear_with_many_high_risk(self):
        self._seed_case(negative_articles=6, positive_articles=0, neutral_articles=0, avg_conf=0.92, high_risk=4, contradictions=3)
        result = self.index.compute(self.db_path)
        self.assertLess(result["score"], 45)
        self.assertIn(result["label"], {"Fear", "Extreme Fear"})

    def test_greed_with_positive_articles(self):
        self._seed_case(negative_articles=0, positive_articles=6, neutral_articles=1, avg_conf=0.20, high_risk=0, contradictions=0)
        result = self.index.compute(self.db_path)
        self.assertGreater(result["score"], 55)
        self.assertIn(result["label"], {"Greed", "Extreme Greed"})

    def test_label_matches_score_range(self):
        self._seed_case()
        result = self.index.compute(self.db_path)
        score = result["score"]
        if score >= 75:
            self.assertEqual(result["label"], "Extreme Greed")
        elif score >= 55:
            self.assertEqual(result["label"], "Greed")
        elif score >= 45:
            self.assertEqual(result["label"], "Neutral")
        elif score >= 25:
            self.assertEqual(result["label"], "Fear")
        else:
            self.assertEqual(result["label"], "Extreme Fear")

    def test_components_include_expected_keys(self):
        self._seed_case()
        result = self.index.compute(self.db_path)
        self.assertEqual(
            set(result["components"].keys()),
            {"article_sentiment", "thesis_confidence", "high_risk_theses", "contradictions"},
        )

    def test_save_daily_score_and_history(self):
        self._seed_case()
        self.index.save_daily_score(self.db_path)
        history = self.index.get_history(self.db_path, days=30)
        self.assertGreaterEqual(len(history), 1)
        self.assertIn("recorded_at", history[0])

    def _seed_case(self, negative_articles=2, positive_articles=1, neutral_articles=1, avg_conf=0.60, high_risk=1, contradictions=1):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM article_enrichment")
        conn.execute("DELETE FROM ingested_articles")
        conn.execute("DELETE FROM agent_theses")
        conn.execute("DELETE FROM contradictions")

        article_id = 1
        for label, count, score in (("negative", negative_articles, -0.6), ("positive", positive_articles, 0.6), ("neutral", neutral_articles, 0.0)):
            for _ in range(count):
                conn.execute(
                    "INSERT INTO ingested_articles (id, headline, source_name, fetched_at, published_at) VALUES (?, ?, ?, ?, ?)",
                    (article_id, f"{label} article {article_id}", "Reuters", iso_now(-1), iso_now(-1)),
                )
                conn.execute(
                    """
                    INSERT INTO article_enrichment (
                        article_id, signal, sentiment_score, impact_score, confidence_score, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (article_id, label, score, 70, 0.8, iso_now(-1)),
                )
                article_id += 1

        for idx in range(max(1, high_risk + 1)):
            risk = "HIGH" if idx < high_risk else "LOW"
            conn.execute(
                """
                INSERT INTO agent_theses (
                    thesis_key, confidence, status, terminal_risk, last_updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (f"thesis {idx}", avg_conf, "active", risk, iso_now(-1)),
            )

        for idx in range(contradictions):
            conn.execute(
                """
                INSERT INTO contradictions (
                    thesis_key, explanation, severity, created_at, resolved
                ) VALUES (?, ?, ?, ?, 0)
                """,
                (f"thesis {idx}", "conflict", "MEDIUM", iso_now(-1)),
            )

        conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main()
