import unittest
from datetime import datetime, timedelta, timezone

from services.price_normalizer import comparable_quotes, minute_bucket, normalize_quote


class TestPriceNormalizer(unittest.TestCase):
    def test_normalize_quote_adds_source_and_minute_bucket(self):
        now = datetime(2026, 4, 14, 6, 0, tzinfo=timezone.utc)
        quote = normalize_quote("JP225", 57779.12, "2026-04-14T05:59:42+00:00", previous_close=57610, now=now)
        self.assertEqual(quote["source"], "TradingView")
        self.assertEqual(quote["source_symbol"], "FOREXCOM-JP225")
        self.assertEqual(quote["comparison_symbol"], "TradingView FOREXCOM-JP225")
        self.assertEqual(quote["quote_minute"], "2026-04-14T05:59:00+00:00")
        self.assertEqual(quote["freshness"], "live")
        self.assertGreater(quote["change_pct"], 0)

    def test_yahoo_fallback_is_labeled_separately(self):
        now = datetime(2026, 4, 14, 6, 0, tzinfo=timezone.utc)
        quote = normalize_quote("JP225_YAHOO", 57779.12, "2026-04-14T05:59:42+00:00", now=now)
        self.assertEqual(quote["source"], "Yahoo Finance")
        self.assertEqual(quote["source_symbol"], "^N225")
        self.assertEqual(quote["symbol"], "JP225")

    def test_stale_quote_marked_delayed(self):
        now = datetime(2026, 4, 14, 6, 0, tzinfo=timezone.utc)
        old = now - timedelta(minutes=5)
        quote = normalize_quote("JP225", 57779, old, previous_close=57610, stale_after_seconds=120, now=now)
        self.assertTrue(quote["is_stale"])
        self.assertEqual(quote["freshness"], "delayed")

    def test_comparable_only_same_source_symbol_and_minute(self):
        now = datetime(2026, 4, 14, 6, 0, tzinfo=timezone.utc)
        a = normalize_quote("JP225", 57779, "2026-04-14T05:59:15+00:00", now=now)
        b = normalize_quote("JP225", 57780, "2026-04-14T05:59:59+00:00", now=now)
        c = normalize_quote("JP225", 57781, "2026-04-14T06:00:00+00:00", now=now)
        self.assertTrue(comparable_quotes(a, b))
        self.assertFalse(comparable_quotes(a, c))

    def test_minute_bucket_handles_naive_time(self):
        self.assertEqual(
            minute_bucket("2026-04-14T05:59:42"),
            "2026-04-14T05:59:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
