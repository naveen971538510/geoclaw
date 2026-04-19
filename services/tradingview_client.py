"""
Minimal TradingView feed wrapper used by the GeoClaw dashboard.

Design goals:
    * Keep a single blocking dependency — ``requests`` — so importing this
      module never fails in environments without optional libraries.
    * Never raise from ``fetch_quote`` / ``fetch_bars`` — return ``None`` or
      an empty list and let the caller decide how to surface the failure.
    * Fall back to yfinance whenever TradingView's public scanner endpoint
      is rate-limited or the user is offline. The dashboard's DB fallback
      sits one layer above us.

The TradingView "scanner" endpoint (``https://scanner.tradingview.com/symbol``)
is an unauthenticated JSON feed exposed by their public web widgets. It is
ideal for last-price / change / session-status lookups but does not serve
historical OHLC bars, which is why ``fetch_bars`` relies on yfinance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from services.price_normalizer import normalize_candle_timestamp, resolve_yahoo_symbol

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 GeoClaw/1.0"
)

_QUOTE_URL = "https://scanner.tradingview.com/symbol"
_QUOTE_FIELDS = (
    "lp,ch,chp,rtc,rch,rchp,"
    "prev_close_price,open_price,high_price,low_price,"
    "bid,ask,last_bar_update_time,update_mode,market,market_session"
)

_TV_INTERVAL_TO_YF = {
    "1": "1m",
    "1m": "1m",
    "5": "5m",
    "5m": "5m",
    "15": "15m",
    "15m": "15m",
    "30": "30m",
    "30m": "30m",
    "60": "60m",
    "1h": "60m",
    "240": "1h",
    "D": "1d",
    "1d": "1d",
    "W": "1wk",
    "M": "1mo",
}


class TradingViewClient:
    """Thin wrapper around TradingView's public scanner feed."""

    def __init__(
        self,
        timeout: float = 6.0,
        session: Optional[requests.Session] = None,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = float(timeout)
        self._session = session or requests.Session()
        if "User-Agent" not in self._session.headers:
            self._session.headers["User-Agent"] = user_agent
        self._session.headers.setdefault("Accept", "application/json, text/plain, */*")
        self._session.headers.setdefault("Referer", "https://www.tradingview.com/")

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def fetch_quote(self, provider_symbol: str) -> Optional[Dict[str, Any]]:
        """Return a single-symbol quote, trying TradingView then Yahoo."""
        sym = (provider_symbol or "").strip()
        if not sym:
            return None
        tv = self._tradingview_quote(sym)
        if tv is not None:
            return tv
        return self._yahoo_quote(sym)

    def _tradingview_quote(self, provider_symbol: str) -> Optional[Dict[str, Any]]:
        params = {"symbol": provider_symbol, "fields": _QUOTE_FIELDS}
        try:
            resp = self._session.get(_QUOTE_URL, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 — broad by design
            logger.debug("TradingView quote failed for %s: %s", provider_symbol, exc)
            return None
        if not isinstance(data, dict):
            return None
        price = data.get("lp")
        if price in (None, 0, 0.0):
            price = data.get("rtc")
        if price in (None, 0, 0.0):
            return None
        ts_raw = data.get("last_bar_update_time") or data.get("rt-update")
        if isinstance(ts_raw, (int, float)) and ts_raw > 0:
            quote_timestamp = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc).isoformat()
        else:
            quote_timestamp = datetime.now(timezone.utc).isoformat()
        return {
            "provider": "tradingview",
            "provider_symbol": provider_symbol,
            "price": float(price),
            "previous_close": _optional_float(data.get("prev_close_price")),
            "open": _optional_float(data.get("open_price")),
            "day_high": _optional_float(data.get("high_price")),
            "day_low": _optional_float(data.get("low_price")),
            "bid": _optional_float(data.get("bid")),
            "ask": _optional_float(data.get("ask")),
            "market_session": str(data.get("market_session") or data.get("market") or ""),
            "update_mode": str(data.get("update_mode") or ""),
            "quote_timestamp": quote_timestamp,
        }

    def _yahoo_quote(self, provider_symbol: str) -> Optional[Dict[str, Any]]:
        yahoo_symbol = resolve_yahoo_symbol(provider_symbol)
        if not yahoo_symbol:
            return None
        try:
            import yfinance as yf  # local import — keeps module light if yf missing
        except Exception:
            return None
        try:
            ticker = yf.Ticker(yahoo_symbol)
            info = ticker.fast_info
            price = _optional_float(getattr(info, "last_price", None))
            if price is None:
                price = _optional_float(getattr(info, "regular_market_price", None))
            if price is None or price <= 0:
                return None
            prev_close = _optional_float(getattr(info, "previous_close", None))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Yahoo quote failed for %s (%s): %s", provider_symbol, yahoo_symbol, exc)
            return None
        return {
            "provider": "yahoo",
            "provider_symbol": provider_symbol,
            "price": price,
            "previous_close": prev_close,
            "open": _optional_float(getattr(info, "open", None)),
            "day_high": _optional_float(getattr(info, "day_high", None)),
            "day_low": _optional_float(getattr(info, "day_low", None)),
            "bid": None,
            "ask": None,
            "market_session": "",
            "update_mode": "yahoo_delayed",
            "quote_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Bars
    # ------------------------------------------------------------------

    def fetch_bars(
        self,
        provider_symbol: str,
        interval: str = "1",
        count: int = 60,
    ) -> List[Dict[str, Any]]:
        """Return OHLC bars for ``provider_symbol`` using yfinance under the hood.

        TradingView's public scanner does not expose historical OHLC without
        auth; yfinance is close enough for the dashboard candlestick chart.
        """
        count = max(1, min(int(count or 60), 300))
        yahoo_symbol = resolve_yahoo_symbol(provider_symbol)
        if not yahoo_symbol:
            return []
        yf_interval = _TV_INTERVAL_TO_YF.get(str(interval or "1"), "1m")
        period = _period_for_interval(yf_interval, count)
        try:
            import yfinance as yf
        except Exception:
            return []
        try:
            hist = yf.Ticker(yahoo_symbol).history(period=period, interval=yf_interval)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Bars fetch failed for %s (%s %s): %s", yahoo_symbol, yf_interval, period, exc)
            return []
        if hist is None or hist.empty:
            return []
        bars: List[Dict[str, Any]] = []
        for ts, row in hist.tail(count).iterrows():
            try:
                ts_iso = ts.isoformat()
                q = normalize_candle_timestamp(ts_iso)
                bars.append(
                    {
                        "t": ts_iso,
                        "quote_timestamp": q["quote_timestamp"],
                        "quote_minute": q["quote_minute"],
                        "o": round(float(row["Open"]), 4),
                        "h": round(float(row["High"]), 4),
                        "l": round(float(row["Low"]), 4),
                        "c": round(float(row["Close"]), 4),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        return bars


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _period_for_interval(yf_interval: str, count: int) -> str:
    """Pick a yfinance ``period`` that covers ``count`` bars for an interval."""
    if yf_interval in {"1m"}:
        return "1d"
    if yf_interval in {"2m", "5m", "15m", "30m"}:
        return "5d" if count <= 240 else "1mo"
    if yf_interval in {"60m", "1h"}:
        return "1mo"
    if yf_interval in {"1d"}:
        return "1y"
    if yf_interval in {"1wk"}:
        return "5y"
    if yf_interval in {"1mo"}:
        return "10y"
    return "1mo"


__all__ = ["TradingViewClient"]
