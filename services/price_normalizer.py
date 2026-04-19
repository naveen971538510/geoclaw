"""
Canonical instrument registry + quote-normalisation helpers.

The dashboard API and live-price pipeline treat every tradable instrument
through this module so that downstream consumers (React SPA, SQLite cache,
neural schema, alerts) only have to understand one flat quote shape.

This module intentionally has no hard dependency on ``yfinance`` or
``requests``; it only massages data that other services fetch. The
TradingView client lives next door in ``tradingview_client.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

# ---------------------------------------------------------------------------
# Canonical instrument table.
#
# Each entry describes one instrument the dashboard knows how to render.
# Values:
#   name                Human-readable name for the hero card.
#   label               Short badge text.
#   asset_class         index | equity | metal | fx | crypto.
#   session             Primary trading session shown on the dashboard.
#   market_type         CFD | Index | Equity | Commodity.
#   source              Default live-source label shown in the UI.
#   source_symbol       The symbol string the UI badges next to the name.
#   comparison_symbol   Symbol referenced in chart-basis text.
#   provider_symbol     Exchange-qualified TradingView symbol.
#   yahoo_symbol        Best-effort yfinance symbol used for fallback / bars.
#   price_basis         Short slug used by the client to describe price basis.
#   change_basis        Short slug used by the client to describe change basis.
#
# Keys starting with ``_YAHOO`` are alternative entries used when the live
# TradingView feed is unavailable and the dashboard needs to fall back to a
# delayed Yahoo Finance quote without losing the metadata plumbing.
# ---------------------------------------------------------------------------

CANONICAL_INSTRUMENTS: Dict[str, Dict[str, Any]] = {
    "JP225": {
        "name": "Nikkei 225 proxy",
        "label": "JP225",
        "asset_class": "index",
        "session": "Tokyo",
        "market_type": "CFD",
        "source": "TradingView",
        "source_symbol": "FOREXCOM:JP225",
        "comparison_symbol": "FOREXCOM:JP225",
        "provider_symbol": "FOREXCOM:JP225",
        "yahoo_symbol": "^N225",
        "price_basis": "tradingview_forexcom_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "JP225_YAHOO": {
        "name": "Nikkei 225 (Yahoo delayed)",
        "label": "JP225",
        "asset_class": "index",
        "session": "Tokyo",
        "market_type": "Index",
        "source": "Yahoo Finance",
        "source_symbol": "^N225",
        "comparison_symbol": "^N225",
        "provider_symbol": "^N225",
        "yahoo_symbol": "^N225",
        "price_basis": "yahoo_delayed",
        "change_basis": "yahoo_candle_vs_previous_close",
    },
    "USA500": {
        "name": "S&P 500 (USA500 proxy)",
        "label": "USA500",
        "asset_class": "index",
        "session": "New York",
        "market_type": "CFD",
        "source": "TradingView",
        "source_symbol": "FOREXCOM:SPXUSD",
        "comparison_symbol": "FOREXCOM:SPXUSD",
        "provider_symbol": "FOREXCOM:SPXUSD",
        "yahoo_symbol": "^GSPC",
        "price_basis": "tradingview_forexcom_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "TSLA": {
        "name": "Tesla, Inc.",
        "label": "TSLA",
        "asset_class": "equity",
        "session": "New York",
        "market_type": "Equity",
        "source": "TradingView",
        "source_symbol": "NASDAQ:TSLA",
        "comparison_symbol": "NASDAQ:TSLA",
        "provider_symbol": "NASDAQ:TSLA",
        "yahoo_symbol": "TSLA",
        "price_basis": "tradingview_equity_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "NVDA": {
        "name": "NVIDIA Corporation",
        "label": "NVDA",
        "asset_class": "equity",
        "session": "New York",
        "market_type": "Equity",
        "source": "TradingView",
        "source_symbol": "NASDAQ:NVDA",
        "comparison_symbol": "NASDAQ:NVDA",
        "provider_symbol": "NASDAQ:NVDA",
        "yahoo_symbol": "NVDA",
        "price_basis": "tradingview_equity_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "META": {
        "name": "Meta Platforms, Inc.",
        "label": "META",
        "asset_class": "equity",
        "session": "New York",
        "market_type": "Equity",
        "source": "TradingView",
        "source_symbol": "NASDAQ:META",
        "comparison_symbol": "NASDAQ:META",
        "provider_symbol": "NASDAQ:META",
        "yahoo_symbol": "META",
        "price_basis": "tradingview_equity_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "AMZN": {
        "name": "Amazon.com, Inc.",
        "label": "AMZN",
        "asset_class": "equity",
        "session": "New York",
        "market_type": "Equity",
        "source": "TradingView",
        "source_symbol": "NASDAQ:AMZN",
        "comparison_symbol": "NASDAQ:AMZN",
        "provider_symbol": "NASDAQ:AMZN",
        "yahoo_symbol": "AMZN",
        "price_basis": "tradingview_equity_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "INTC": {
        "name": "Intel Corporation",
        "label": "INTC",
        "asset_class": "equity",
        "session": "New York",
        "market_type": "Equity",
        "source": "TradingView",
        "source_symbol": "NASDAQ:INTC",
        "comparison_symbol": "NASDAQ:INTC",
        "provider_symbol": "NASDAQ:INTC",
        "yahoo_symbol": "INTC",
        "price_basis": "tradingview_equity_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "MU": {
        "name": "Micron Technology, Inc.",
        "label": "MU",
        "asset_class": "equity",
        "session": "New York",
        "market_type": "Equity",
        "source": "TradingView",
        "source_symbol": "NASDAQ:MU",
        "comparison_symbol": "NASDAQ:MU",
        "provider_symbol": "NASDAQ:MU",
        "yahoo_symbol": "MU",
        "price_basis": "tradingview_equity_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "GOLD": {
        "name": "Gold spot (XAU/USD)",
        "label": "GOLD",
        "asset_class": "metal",
        "session": "24h",
        "market_type": "Commodity",
        "source": "TradingView",
        "source_symbol": "OANDA:XAUUSD",
        "comparison_symbol": "OANDA:XAUUSD",
        "provider_symbol": "OANDA:XAUUSD",
        "yahoo_symbol": "GC=F",
        "price_basis": "tradingview_spot_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
    "SILVER": {
        "name": "Silver spot (XAG/USD)",
        "label": "SILVER",
        "asset_class": "metal",
        "session": "24h",
        "market_type": "Commodity",
        "source": "TradingView",
        "source_symbol": "OANDA:XAGUSD",
        "comparison_symbol": "OANDA:XAGUSD",
        "provider_symbol": "OANDA:XAGUSD",
        "yahoo_symbol": "SI=F",
        "price_basis": "tradingview_spot_live",
        "change_basis": "same_source_1m_candle_vs_previous_close",
    },
}


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def parse_utc_datetime(value: Union[str, int, float, datetime, None]) -> datetime:
    """Coerce a loose timestamp into an aware UTC ``datetime``.

    Accepts:
        * ``datetime`` instances (naïve assumed to be UTC),
        * numeric epoch seconds / milliseconds,
        * ISO-8601 strings (with or without ``Z``).

    Falls back to ``datetime.now(UTC)`` rather than raising so pipeline
    code can keep flowing when an upstream source returns garbage.
    """
    if value is None or value == "":
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # treat very large values as milliseconds
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return datetime.now(timezone.utc)
    text = str(value).strip()
    if not text:
        return datetime.now(timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_candle_timestamp(value: Any) -> Dict[str, str]:
    """Return ``{quote_timestamp, quote_minute}`` aligned to the minute."""
    dt = parse_utc_datetime(value)
    minute = dt.replace(second=0, microsecond=0)
    return {
        "quote_timestamp": dt.isoformat(),
        "quote_minute": minute.isoformat(),
    }


# ---------------------------------------------------------------------------
# Quote shape
# ---------------------------------------------------------------------------


def _direction(change: float) -> str:
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def normalize_quote(
    symbol_key: str,
    price: Union[float, int, None],
    quote_timestamp: Any,
    *,
    previous_close: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    last: Optional[float] = None,
    stale_after_seconds: int = 120,
) -> Dict[str, Any]:
    """Build the canonical live-quote envelope consumed by the dashboard."""
    instrument = CANONICAL_INSTRUMENTS.get(symbol_key) or {}
    price_f = float(price or 0.0)
    prev_close = float(previous_close or 0.0)
    change = (price_f - prev_close) if prev_close else 0.0
    change_pct = (change / prev_close * 100.0) if prev_close else 0.0
    quote_ts = normalize_candle_timestamp(quote_timestamp)
    now = datetime.now(timezone.utc)
    age_seconds = max(0.0, (now - parse_utc_datetime(quote_ts["quote_timestamp"])).total_seconds())
    is_stale = age_seconds > float(stale_after_seconds)
    return {
        "symbol": symbol_key,
        "name": instrument.get("name", symbol_key),
        "source": instrument.get("source", "TradingView"),
        "source_symbol": instrument.get("source_symbol", symbol_key),
        "comparison_symbol": instrument.get("comparison_symbol", instrument.get("source_symbol", symbol_key)),
        "session": instrument.get("session", ""),
        "market_type": instrument.get("market_type", ""),
        "price_basis": instrument.get("price_basis", ""),
        "change_basis": instrument.get("change_basis", ""),
        "price": price_f,
        "last": float(last if last is not None else price_f),
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,
        "change": change,
        "change_pct": round(change_pct, 3),
        "quote_timestamp": quote_ts["quote_timestamp"],
        "quote_minute": quote_ts["quote_minute"],
        "quote_age_seconds": int(age_seconds),
        "stale_after_seconds": int(stale_after_seconds),
        "is_stale": bool(is_stale),
        "freshness": "delayed" if is_stale else "live",
        "direction": _direction(change),
    }


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def resolve_provider_symbol(symbol_key: str) -> str:
    """Return the TradingView ``EXCHANGE:TICKER`` for a canonical key."""
    entry = CANONICAL_INSTRUMENTS.get(symbol_key) or {}
    return str(entry.get("provider_symbol") or entry.get("source_symbol") or symbol_key)


def resolve_yahoo_symbol(symbol_key_or_provider: str) -> str:
    """Return the yfinance symbol for either a canonical key or provider sym."""
    key = str(symbol_key_or_provider or "")
    entry = CANONICAL_INSTRUMENTS.get(key)
    if entry:
        return str(entry.get("yahoo_symbol") or entry.get("source_symbol") or key)
    upper = key.upper()
    for meta in CANONICAL_INSTRUMENTS.values():
        if str(meta.get("provider_symbol") or "").upper() == upper:
            return str(meta.get("yahoo_symbol") or meta.get("source_symbol") or key)
    if ":" in key:
        return key.split(":", 1)[1]
    return key


__all__ = [
    "CANONICAL_INSTRUMENTS",
    "normalize_candle_timestamp",
    "normalize_quote",
    "parse_utc_datetime",
    "resolve_provider_symbol",
    "resolve_yahoo_symbol",
]
