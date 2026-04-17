import unittest
from datetime import datetime, timedelta, timezone

from services.price_normalizer import (
    CANONICAL_INSTRUMENTS,
    normalize_candle_timestamp,
    normalize_quote,
    parse_utc_datetime,
    resolve_provider_symbol,
    resolve_yahoo_symbol,
)


class TestCanonicalInstruments(unittest.TestCase):
    def test_ten_dashboard_assets_are_present(self):
        required = {
            "JP225", "USA500", "TSLA", "NVDA", "META", "AMZN",
            "INTC", "MU", "GOLD", "SILVER",
        }
        self.assertTrue(required.issubset(set(CANONICAL_INSTRUMENTS)))

    def test_every_entry_has_provider_and_yahoo_symbols(self):
        for symbol, entry in CANONICAL_INSTRUMENTS.items():
            self.assertTrue(entry.get("provider_symbol"), f"{symbol} missing provider_symbol")
            self.assertTrue(entry.get("yahoo_symbol"), f"{symbol} missing yahoo_symbol")
            self.assertIn(entry.get("asset_class"), {"equity", "index", "metal", "fx"})

    def test_resolve_helpers(self):
        self.assertEqual(resolve_provider_symbol("TSLA"), "NASDAQ:TSLA")
        self.assertEqual(resolve_yahoo_symbol("TSLA"), "TSLA")
        self.assertEqual(resolve_yahoo_symbol("NASDAQ:TSLA"), "TSLA")
        self.assertEqual(resolve_yahoo_symbol("UNKNOWN:FOO"), "FOO")


class TestParseUtcDatetime(unittest.TestCase):
    def test_iso_with_z_suffix(self):
        dt = parse_utc_datetime("2026-04-17T12:34:00Z")
        self.assertEqual(dt, datetime(2026, 4, 17, 12, 34, tzinfo=timezone.utc))

    def test_epoch_seconds(self):
        dt = parse_utc_datetime(1_700_000_000)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.year, 2023)

    def test_epoch_millis(self):
        dt = parse_utc_datetime(1_700_000_000_000)
        self.assertEqual(dt.year, 2023)

    def test_datetime_passthrough_preserves_tz(self):
        raw = datetime(2026, 1, 2, 3, 4, 5)
        dt = parse_utc_datetime(raw)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_garbage_falls_back_to_now(self):
        dt = parse_utc_datetime("not-a-date")
        self.assertEqual(dt.tzinfo, timezone.utc)


class TestNormalizeCandleTimestamp(unittest.TestCase):
    def test_floors_to_minute(self):
        out = normalize_candle_timestamp("2026-04-17T12:34:56+00:00")
        self.assertEqual(out["quote_timestamp"], "2026-04-17T12:34:56+00:00")
        self.assertEqual(out["quote_minute"], "2026-04-17T12:34:00+00:00")


class TestNormalizeQuote(unittest.TestCase):
    def _fresh_timestamp(self):
        return datetime.now(timezone.utc).isoformat()

    def test_shape_includes_all_required_fields(self):
        quote = normalize_quote(
            "TSLA",
            250.5,
            self._fresh_timestamp(),
            previous_close=240.0,
        )
        required = {
            "symbol", "name", "source", "source_symbol", "price", "last",
            "change", "change_pct", "direction", "quote_timestamp",
            "quote_minute", "quote_age_seconds", "is_stale", "freshness",
            "session", "market_type",
        }
        self.assertTrue(required.issubset(quote.keys()))
        self.assertEqual(quote["source_symbol"], "NASDAQ:TSLA")

    def test_change_math(self):
        quote = normalize_quote(
            "NVDA",
            110.0,
            self._fresh_timestamp(),
            previous_close=100.0,
        )
        self.assertAlmostEqual(quote["change"], 10.0)
        self.assertAlmostEqual(quote["change_pct"], 10.0)
        self.assertEqual(quote["direction"], "up")

    def test_stale_quote_flagged(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        quote = normalize_quote(
            "GOLD",
            2300.0,
            old,
            previous_close=2290.0,
            stale_after_seconds=60,
        )
        self.assertTrue(quote["is_stale"])
        self.assertEqual(quote["freshness"], "delayed")

    def test_fresh_quote_is_live(self):
        quote = normalize_quote(
            "META",
            500.0,
            self._fresh_timestamp(),
            previous_close=480.0,
            stale_after_seconds=120,
        )
        self.assertFalse(quote["is_stale"])
        self.assertEqual(quote["freshness"], "live")

    def test_negative_change_direction(self):
        quote = normalize_quote(
            "AMZN",
            180.0,
            self._fresh_timestamp(),
            previous_close=200.0,
        )
        self.assertEqual(quote["direction"], "down")
        self.assertLess(quote["change_pct"], 0)

    def test_no_previous_close_yields_flat(self):
        quote = normalize_quote(
            "INTC",
            30.0,
            self._fresh_timestamp(),
            previous_close=None,
        )
        self.assertEqual(quote["direction"], "flat")
        self.assertEqual(quote["change"], 0.0)
        self.assertEqual(quote["change_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
