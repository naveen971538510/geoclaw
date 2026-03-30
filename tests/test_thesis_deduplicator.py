import sqlite3
import unittest

from services.thesis_deduplicator import ThesisDeduplicator
from tests.support import iso_now, make_temp_db, remove_db


class TestThesisDeduplicator(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        conn = sqlite3.connect(self.db_path)
        rows = [
            ("Iran oil supply risk rises after missile threats", 0.80, 5, "active"),
            ("Missile threats raise Iran oil supply risk", 0.62, 2, "active"),
            ("Eurozone retail sales improve modestly", 0.41, 1, "active"),
        ]
        for thesis_key, confidence, evidence_count, status in rows:
            conn.execute(
                """
                INSERT INTO agent_theses (
                    thesis_key, confidence, evidence_count, status, last_updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (thesis_key, confidence, evidence_count, status, iso_now(-1)),
            )
        conn.execute(
            "INSERT INTO reasoning_chains (thesis_key, article_id, chain_text) VALUES (?, ?, ?)",
            ("Missile threats raise Iran oil supply risk", 1, "duplicate chain"),
        )
        conn.commit()
        conn.close()
        self.dedup = ThesisDeduplicator()

    def tearDown(self):
        remove_db(self.db_path)

    def test_identical_texts_high_similarity(self):
        docs = ["Iran oil supply risk rises", "Iran oil supply risk rises"]
        left = self.dedup._tfidf_vector(docs[0], docs)
        right = self.dedup._tfidf_vector(docs[1], docs)
        self.assertGreaterEqual(self.dedup._cosine_similarity(left, right), 0.99)

    def test_different_texts_low_similarity(self):
        docs = ["Iran oil supply risk rises", "Eurozone retail sales improve"]
        left = self.dedup._tfidf_vector(docs[0], docs)
        right = self.dedup._tfidf_vector(docs[1], docs)
        self.assertLess(self.dedup._cosine_similarity(left, right), 0.5)

    def test_find_duplicates_returns_list(self):
        pairs = self.dedup.find_duplicates(self.db_path)
        self.assertIsInstance(pairs, list)
        self.assertGreaterEqual(len(pairs), 1)

    def test_merge_dry_run_no_db_change(self):
        result = self.dedup.merge_duplicates(self.db_path, dry_run=True)
        conn = sqlite3.connect(self.db_path)
        status = conn.execute(
            "SELECT status FROM agent_theses WHERE thesis_key=?",
            ("Missile threats raise Iran oil supply risk",),
        ).fetchone()[0]
        conn.close()
        self.assertTrue(result["dry_run"])
        self.assertEqual(status, "active")

    def test_merge_supersedes_weaker_thesis(self):
        result = self.dedup.merge_duplicates(self.db_path, dry_run=False)
        conn = sqlite3.connect(self.db_path)
        status = conn.execute(
            "SELECT status FROM agent_theses WHERE thesis_key=?",
            ("Missile threats raise Iran oil supply risk",),
        ).fetchone()[0]
        conn.close()
        self.assertGreaterEqual(result["merged"], 1)
        self.assertEqual(status, "superseded")

    def test_merge_updates_reasoning_chain_target(self):
        self.dedup.merge_duplicates(self.db_path, dry_run=False)
        conn = sqlite3.connect(self.db_path)
        thesis_key = conn.execute("SELECT thesis_key FROM reasoning_chains LIMIT 1").fetchone()[0]
        conn.close()
        self.assertEqual(thesis_key, "Iran oil supply risk rises after missile threats")


if __name__ == "__main__":
    unittest.main()
