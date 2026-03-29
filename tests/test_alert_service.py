import os
import sqlite3
import tempfile
import unittest


class TestAlertService(unittest.TestCase):
    def setUp(self):
        self.fd, self.db = tempfile.mkstemp(suffix=".db")
        conn = sqlite3.connect(self.db)
        conn.execute(
            """
            CREATE TABLE alert_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              alert_type TEXT,
              title TEXT,
              body TEXT,
              created_at TEXT,
              resolved INTEGER DEFAULT 0,
              resolution_note TEXT DEFAULT '',
              resolved_at TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ingested_articles (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              headline TEXT,
              fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
              published_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO ingested_articles (headline) VALUES ('test headline')")
        conn.commit()
        conn.close()

    def tearDown(self):
        os.close(self.fd)
        os.unlink(self.db)

    def test_cooldown_ok_when_no_history(self):
        from services.alert_service import AlertService

        service = AlertService(self.db)
        self.assertTrue(service._cooldown_ok("test_alert", 2))

    def test_log_alert_writes_to_db(self):
        from services.alert_service import AlertService

        service = AlertService(self.db)
        service._log_alert("test", "Test Title", "Test body")
        conn = sqlite3.connect(self.db)
        count = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_cooldown_blocks_repeat(self):
        from services.alert_service import AlertService

        service = AlertService(self.db)
        service._log_alert("same_alert", "T", "B")
        self.assertFalse(service._cooldown_ok("same_alert", 24))

    def test_evaluate_theses_returns_count(self):
        from services.alert_service import AlertService

        service = AlertService(self.db)
        theses = [
            {
                "confidence": 0.95,
                "thesis_key": "test thesis",
                "status": "confirmed",
                "terminal_risk": "HIGH",
                "id": 1,
            }
        ]
        count = service.evaluate_theses(theses)
        self.assertIsInstance(count, int)


if __name__ == "__main__":
    unittest.main()
