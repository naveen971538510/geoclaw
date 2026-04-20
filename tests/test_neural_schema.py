import sqlite3
import unittest

from services.neural_schema import NeuralSchema
from tests.support import make_temp_db, remove_db, seed_sample_data


class TestNeuralSchema(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        seed_sample_data(self.db_path)

    def tearDown(self):
        remove_db(self.db_path)

    def test_build_returns_ranked_grounded_signals(self):
        schema = NeuralSchema(self.db_path).build()
        self.assertEqual(schema["schema_version"], "neural_schema_v1")
        self.assertGreaterEqual(schema["node_count"], 1)
        self.assertGreaterEqual(schema["edge_count"], 1)
        self.assertTrue(schema["ranked_signals"])
        top = schema["ranked_signals"][0]
        self.assertIn("schema_score", top)
        self.assertGreaterEqual(top["schema_score"], 0)
        self.assertLessEqual(top["schema_score"], 100)
        self.assertIn("next_best_action", top)

    def test_persist_snapshot_writes_row(self):
        schema = NeuralSchema(self.db_path).build(persist=True, run_id=123)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT run_id, top_signal, schema_json FROM neural_schema_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 123)
        self.assertTrue(row[1])
        self.assertIn(schema["schema_version"], row[2])

    def test_compact_latest_or_build_is_dashboard_safe(self):
        compact = NeuralSchema(self.db_path).latest_or_build(compact=True)
        self.assertIn("summary", compact)
        self.assertIn("ranked_signals", compact)
        self.assertIn("gaps", compact)
        self.assertNotIn("nodes", compact)
        self.assertNotIn("edges", compact)


if __name__ == "__main__":
    unittest.main()
