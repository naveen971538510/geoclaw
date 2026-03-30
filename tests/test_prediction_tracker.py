import sqlite3
import unittest
from unittest.mock import patch

from services.prediction_tracker import PredictionTracker
from tests.support import iso_now, make_temp_db, remove_db, seed_sample_data


class TestPredictionTracker(unittest.TestCase):
    def setUp(self):
        self.db_path = make_temp_db()
        seed_sample_data(self.db_path)
        self.tracker = PredictionTracker(self.db_path)

    def tearDown(self):
        remove_db(self.db_path)

    def test_record_high_confidence_creates_prediction(self):
        with patch("services.price_feed.PriceFeed") as mock_feed:
            mock_feed.return_value.get_price.return_value = {"price": 101.5}
            pred_id = self.tracker.record_prediction("Iran tensions affect Strait of Hormuz oil flow", 0.82, run_id=7)
        self.assertGreater(pred_id, 0)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT symbol, predicted_direction FROM thesis_predictions WHERE id=?", (pred_id,)).fetchone()
        conn.close()
        self.assertEqual(row[0], "CL=F")
        self.assertEqual(row[1], "risk_up")

    def test_low_confidence_no_prediction(self):
        pred_id = self.tracker.record_prediction("Iran tensions affect Strait of Hormuz oil flow", 0.40)
        self.assertEqual(pred_id, 0)

    def test_unknown_keyword_no_prediction(self):
        pred_id = self.tracker.record_prediction("Local politics remain stable", 0.88)
        self.assertEqual(pred_id, 0)

    def test_check_pending_returns_dict(self):
        self._insert_pending_prediction("risk_up", 100.0, "CL=F")
        with patch("services.price_feed.PriceFeed") as mock_feed:
            mock_feed.return_value._available = True
            mock_feed.return_value.get_price.return_value = {"price": 102.0, "change_pct": 2.0}
            result = self.tracker.check_pending_predictions()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["checked"], 1)

    def test_accuracy_report_structure(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO thesis_predictions (
                thesis_key, predicted_direction, symbol, outcome, checked_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("Iran tensions affect Strait of Hormuz oil flow", "risk_up", "CL=F", "verified", iso_now(-1)),
        )
        conn.commit()
        conn.close()
        report = self.tracker.get_accuracy_report()
        self.assertIn("accuracy_pct", report)
        self.assertIn("recent", report)

    def test_outcome_enum_valid(self):
        self._insert_pending_prediction("risk_up", 100.0, "CL=F")
        with patch("services.price_feed.PriceFeed") as mock_feed:
            mock_feed.return_value._available = True
            mock_feed.return_value.get_price.return_value = {"price": 102.5, "change_pct": 2.5}
            self.tracker.check_pending_predictions()
        conn = sqlite3.connect(self.db_path)
        outcome = conn.execute("SELECT outcome FROM thesis_predictions ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.close()
        self.assertIn(outcome, {"verified", "refuted", "neutral", "pending"})

    def test_risk_down_prediction_can_verify_negative_move(self):
        self._insert_pending_prediction("risk_down", 100.0, "^VIX", thesis_key="Ceasefire trend improves")
        with patch("services.price_feed.PriceFeed") as mock_feed:
            mock_feed.return_value._available = True
            mock_feed.return_value.get_price.return_value = {"price": 98.0, "change_pct": -2.0}
            result = self.tracker.check_pending_predictions()
        self.assertEqual(result["verified"], 1)

    def _insert_pending_prediction(self, direction, baseline, symbol, thesis_key="Iran tensions affect Strait of Hormuz oil flow"):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO thesis_predictions (
                thesis_key, predicted_direction, predicted_asset, symbol,
                price_at_prediction, confidence_at_prediction, predicted_at,
                run_id, check_after_hours, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (thesis_key, direction, "test_asset", symbol, baseline, 0.8, iso_now(-48), 1, 24, "pending"),
        )
        conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main()
