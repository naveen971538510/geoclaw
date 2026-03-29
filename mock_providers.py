MOCK_MARKET = [
    {"symbol": "SPY", "label": "S&P 500 ETF", "price": 524.18, "change_pct": 0.43, "data_source": "mock_fallback", "market_mode": "mock"},
    {"symbol": "BTC-USD", "label": "Bitcoin", "price": 67420.00, "change_pct": -1.12, "data_source": "mock_fallback", "market_mode": "mock"},
    {"symbol": "GLD", "label": "Gold ETF", "price": 218.55, "change_pct": 0.21, "data_source": "mock_fallback", "market_mode": "mock"},
    {"symbol": "EURUSD", "label": "EURUSD", "price": 1.0832, "change_pct": -0.08, "data_source": "mock_fallback", "market_mode": "mock"},
    {"symbol": "TNX", "label": "10Y Treasury", "price": 4.51, "change_pct": 0.03, "data_source": "mock_fallback", "market_mode": "mock"},
]


def get_market_data(real_fetch_func):
    try:
        data = real_fetch_func()
        if data:
            return data
    except Exception:
        pass
    return [dict(item) for item in MOCK_MARKET]
