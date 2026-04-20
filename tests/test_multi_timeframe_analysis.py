import unittest
from datetime import datetime, timedelta, timezone

from services.multi_timeframe_analysis import analyse_timeframe, build_multi_timeframe_analysis


def _trend_candles(start: float = 38000.0, step: float = 18.0, count: int = 80):
    candles = []
    price = start
    now = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
    for index in range(count):
        open_price = price
        close_price = price + step
        candles.append(
            {
                "t": (now + timedelta(minutes=index)).isoformat(),
                "quote_timestamp": (now + timedelta(minutes=index)).isoformat(),
                "quote_minute": (now + timedelta(minutes=index)).replace(second=0, microsecond=0).isoformat(),
                "o": round(open_price, 2),
                "h": round(close_price + 6.0, 2),
                "l": round(open_price - 4.0, 2),
                "c": round(close_price, 2),
            }
        )
        price = close_price
    return candles


def _news_items():
    now = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
    return [
        {
            "headline": "Nikkei rally extends as risk on mood lifts Japan stocks",
            "source": "Test Wire",
            "url": "https://example.com/1",
            "ts": now.isoformat(),
            "reason": "Broad risk on session and softer yen.",
        },
        {
            "headline": "Oil falls after ceasefire hopes support Asia equities",
            "source": "Test Wire",
            "url": "https://example.com/2",
            "ts": (now - timedelta(hours=1)).isoformat(),
            "reason": "Oil eases and exporters catch a bid.",
        },
        {
            "headline": "BOJ holds policy steady while semiconductor demand improves",
            "source": "Test Wire",
            "url": "https://example.com/3",
            "ts": (now - timedelta(hours=2)).isoformat(),
            "reason": "Chip demand and steady policy remain supportive.",
        },
    ]


class TestMultiTimeframeAnalysis(unittest.TestCase):
    def test_analyse_timeframe_detects_bullish_trend(self):
        frame = analyse_timeframe("1h", _trend_candles())
        self.assertTrue(frame["ready"])
        self.assertEqual(frame["bias"], "bullish")
        self.assertGreater(frame["score"], 0)
        self.assertGreater(frame["confidence"], 50)

    def test_build_analysis_waits_for_all_required_inputs(self):
        payload = build_multi_timeframe_analysis(
            price=39450.0,
            quote_timestamp="2026-04-15T10:00:00+00:00",
            timeframes={"5m": _trend_candles(), "1h": _trend_candles(), "12h": []},
            news_items=_news_items(),
            message="stay conservative",
        )
        self.assertFalse(payload["ready"])
        self.assertIn("12h", payload["missing_requirements"])
        self.assertEqual(payload["confluence"]["bias"], "WAITING")
        self.assertEqual(payload["forecast"]["direction"], "WAIT")
        self.assertEqual(payload["regime"]["label"], "Low Conviction")
        self.assertEqual(payload["forecast"]["time_horizon"], "Wait for full confluence")
        self.assertEqual(payload["quant_strategy"]["state"], "WAIT")
        self.assertEqual(payload["quant_strategy"]["name"], "Stand Aside")

    def test_build_analysis_returns_bullish_confluence(self):
        payload = build_multi_timeframe_analysis(
            price=39450.0,
            quote_timestamp="2026-04-15T10:00:00+00:00",
            timeframes={
                "5m": _trend_candles(step=10.0),
                "1h": _trend_candles(step=15.0),
                "12h": _trend_candles(step=20.0),
            },
            news_items=_news_items(),
            message="scalp it but focus on news",
        )
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["confluence"]["bias"], "BULLISH")
        self.assertGreater(payload["confluence"]["confidence"], 50)
        self.assertEqual(payload["forecast"]["direction"], "LONG")
        self.assertIsNotNone(payload["forecast"]["entry_low"])
        self.assertIsNotNone(payload["forecast"]["entry_high"])
        self.assertIsNotNone(payload["forecast"]["target_level"])
        self.assertIsNotNone(payload["forecast"]["invalidation_level"])
        self.assertEqual(payload["forecast"]["time_horizon"], "1-4 hours")
        self.assertTrue(payload["regime"]["label"])
        self.assertEqual(payload["message_profile"]["mode"], "execution")
        self.assertEqual(payload["message_profile"]["emphasis"], "news")
        self.assertEqual(payload["quant_strategy"]["side"], "LONG")
        self.assertIn(payload["quant_strategy"]["state"], {"ACTIVE", "REDUCED"})
        self.assertTrue(payload["quant_strategy"]["rules"])
        self.assertGreater(payload["quant_strategy"]["edge_score"], 40)


if __name__ == "__main__":
    unittest.main()
