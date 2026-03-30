import csv
import io
import json
import sqlite3
import unittest

from services.exporter import Exporter
from tests.support import iso_now, make_temp_db, remove_db, seed_sample_data


class TestExporter(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        seed_sample_data(self.db_path)
        self.exporter = Exporter(self.db_path)

    def tearDown(self):
        remove_db(self.db_path)

    def test_export_theses_csv_valid_csv(self):
        content = self.exporter.export_theses_csv()
        rows = list(csv.DictReader(io.StringIO(content)))
        self.assertGreaterEqual(len(rows), 1)
        self.assertIn("confidence_pct", rows[0])

    def test_export_articles_csv_has_header(self):
        content = self.exporter.export_articles_csv(days=7)
        self.assertIn("headline,source,published_at,sentiment_label,relevance_score,fetched_at", content.splitlines()[0])

    def test_export_briefing_txt_returns_string(self):
        content = self.exporter.export_briefing_txt()
        self.assertIsInstance(content, str)
        self.assertIn("GeoClaw briefing", content)

    def test_export_full_json_valid_json(self):
        payload = json.loads(self.exporter.export_full_json())
        self.assertIn("theses", payload)
        self.assertIn("actions", payload)

    def test_export_empty_db_graceful(self):
        empty_db = make_temp_db()
        exporter = Exporter(empty_db)
        self.assertEqual(exporter.export_briefing_txt(), "No briefing available.")
        payload = json.loads(exporter.export_full_json())
        self.assertEqual(payload["theses"], [])
        remove_db(empty_db)

    def test_export_predictions_csv_has_outcome_rows(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO thesis_predictions (
                thesis_key, predicted_direction, symbol, outcome, predicted_at, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Iran tensions affect Strait of Hormuz oil flow", "risk_up", "CL=F", "verified", iso_now(-2), iso_now(-1)),
        )
        conn.commit()
        conn.close()
        content = self.exporter.export_predictions_csv()
        self.assertIn("thesis_key,predicted_direction,symbol", content.splitlines()[0])
        self.assertIn("Iran tensions affect Strait of Hormuz oil flow", content)


if __name__ == "__main__":
    unittest.main()
