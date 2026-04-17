import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from services.tradingview_client import TradingViewClient


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class TestTradingViewQuote(unittest.TestCase):
    def _client_with_response(self, payload, status_code=200):
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _FakeResponse(payload, status_code=status_code)
        return TradingViewClient(session=session), session

    def test_fetch_quote_parses_scanner_payload(self):
        payload = {
            "lp": 400.25,
            "prev_close_price": 395.10,
            "open_price": 396.00,
            "high_price": 402.50,
            "low_price": 393.20,
            "bid": 400.20,
            "ask": 400.30,
            "last_bar_update_time": 1_700_000_000,
            "update_mode": "delayed_streaming_900",
            "market_session": "america",
        }
        client, session = self._client_with_response(payload)

        quote = client.fetch_quote("NASDAQ:TSLA")

        self.assertIsNotNone(quote)
        assert quote is not None  # narrow for mypy/human readers
        self.assertEqual(quote["provider"], "tradingview")
        self.assertEqual(quote["provider_symbol"], "NASDAQ:TSLA")
        self.assertEqual(quote["price"], 400.25)
        self.assertEqual(quote["previous_close"], 395.10)
        self.assertEqual(quote["bid"], 400.20)
        self.assertEqual(quote["ask"], 400.30)
        expected_ts = datetime.fromtimestamp(1_700_000_000, tz=timezone.utc).isoformat()
        self.assertEqual(quote["quote_timestamp"], expected_ts)
        session.get.assert_called_once()
        called_url = session.get.call_args[0][0]
        self.assertIn("scanner.tradingview.com", called_url)

    def test_fetch_quote_empty_symbol_returns_none(self):
        client, session = self._client_with_response({})
        self.assertIsNone(client.fetch_quote(""))
        session.get.assert_not_called()

    def test_fetch_quote_prefers_rtc_when_lp_missing(self):
        payload = {"rtc": 42.5, "prev_close_price": 40}
        client, _ = self._client_with_response(payload)
        quote = client.fetch_quote("NASDAQ:FOO")
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote["price"], 42.5)

    def test_fetch_quote_falls_back_to_yahoo_when_tv_errors(self):
        session = MagicMock()
        session.headers = {}
        session.get.side_effect = RuntimeError("boom")
        client = TradingViewClient(session=session)

        fake_yf_module = MagicMock()
        fake_info = MagicMock()
        fake_info.last_price = 123.45
        fake_info.previous_close = 120.00
        fake_info.open = 121.00
        fake_info.day_high = 125.00
        fake_info.day_low = 119.00
        fake_yf_module.Ticker.return_value.fast_info = fake_info

        with patch.dict("sys.modules", {"yfinance": fake_yf_module}):
            quote = client.fetch_quote("NASDAQ:TSLA")

        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote["provider"], "yahoo")
        self.assertEqual(quote["price"], 123.45)
        self.assertEqual(quote["previous_close"], 120.00)

    def test_fetch_quote_returns_none_when_tv_and_yahoo_both_fail(self):
        session = MagicMock()
        session.headers = {}
        session.get.side_effect = RuntimeError("boom")
        client = TradingViewClient(session=session)

        fake_yf_module = MagicMock()
        fake_yf_module.Ticker.side_effect = RuntimeError("no yahoo")

        with patch.dict("sys.modules", {"yfinance": fake_yf_module}):
            self.assertIsNone(client.fetch_quote("NASDAQ:TSLA"))


class TestTradingViewBars(unittest.TestCase):
    def test_fetch_bars_uses_yfinance_history(self):
        session = MagicMock()
        session.headers = {}
        client = TradingViewClient(session=session)

        import pandas as pd
        index = pd.to_datetime([
            "2026-04-17T12:00:00Z",
            "2026-04-17T12:01:00Z",
        ])
        hist_df = pd.DataFrame(
            {
                "Open": [400.00, 400.50],
                "High": [400.80, 401.00],
                "Low": [399.90, 400.40],
                "Close": [400.50, 400.95],
            },
            index=index,
        )

        fake_yf_module = MagicMock()
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = hist_df
        fake_yf_module.Ticker.return_value = fake_ticker

        with patch.dict("sys.modules", {"yfinance": fake_yf_module}):
            bars = client.fetch_bars("NASDAQ:TSLA", interval="1", count=10)

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["o"], 400.00)
        self.assertEqual(bars[-1]["c"], 400.95)
        self.assertIn("quote_timestamp", bars[0])
        self.assertIn("quote_minute", bars[0])

    def test_fetch_bars_returns_empty_on_error(self):
        session = MagicMock()
        session.headers = {}
        client = TradingViewClient(session=session)

        fake_yf_module = MagicMock()
        fake_yf_module.Ticker.side_effect = RuntimeError("boom")

        with patch.dict("sys.modules", {"yfinance": fake_yf_module}):
            self.assertEqual(client.fetch_bars("NASDAQ:TSLA"), [])


if __name__ == "__main__":
    unittest.main()
