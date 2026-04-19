"""Canonical market quote normalization for GeoClaw.

This layer keeps dashboard comparisons source-aware: same provider, same source
symbol, and same 1-minute candle bucket before treating prices as comparable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


CANONICAL_INSTRUMENTS: Dict[str, Dict[str, Any]] = {
    "JP225": {
        "symbol": "JP225",
        "name": "Japan 225 CFD",
        "source": "TradingView",
        "source_symbol": "FOREXCOM-JP225",
        "provider_symbol": "FOREXCOM:JP225",
        "scanner_fallback_symbol": "TVC:NI225",
        "comparison_symbol": "TradingView FOREXCOM-JP225",
        "session": "Tokyo",
        "market_type": "CFD",
        "stale_after_seconds": 120,
        "price_basis": (
            "Canonical dashboard feed: TradingView FOREXCOM-JP225. "
            "Compare only same-source quote minutes."
        ),
    },
    "JP225_YAHOO": {
        "symbol": "JP225",
        "name": "Nikkei 225 index proxy",
        "source": "Yahoo Finance",
        "source_symbol": "^N225",
        "provider_symbol": "^N225",
        "comparison_symbol": "TradingView FOREXCOM-JP225",
        "session": "Tokyo",
        "market_type": "index proxy",
        "stale_after_seconds": 120,
        "price_basis": (
            "Fallback dashboard feed: Yahoo Finance ^N225 1-minute candle close. "
            "Do not compare this directly with TradingView FOREXCOM-JP225."
        ),
    }
}


def parse_utc_datetime(value: Any, default: Optional[datetime] = None) -> datetime:
    """Parse common timestamp shapes into a timezone-aware UTC datetime."""
    fallback = default or datetime.now(timezone.utc)
    try:
        if value is None or value == "":
            return fallback
        if hasattr(value, "to_pydatetime"):
            dt = value.to_pydatetime()
        elif isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return fallback


def minute_bucket(value: Any, now: Optional[datetime] = None) -> str:
    dt = parse_utc_datetime(value, default=now or datetime.now(timezone.utc))
    return dt.replace(second=0, microsecond=0).isoformat()


def normalize_candle_timestamp(value: Any, now: Optional[datetime] = None) -> Dict[str, str]:
    dt = parse_utc_datetime(value, default=now or datetime.now(timezone.utc))
    return {
        "quote_timestamp": dt.isoformat(),
        "quote_minute": dt.replace(second=0, microsecond=0).isoformat(),
    }


def normalize_quote(
    instrument: str,
    price: float,
    quote_timestamp: Any,
    *,
    previous_close: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    last: Optional[float] = None,
    stale_after_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return a normalized, source-aware quote payload for API and DB use."""
    meta = CANONICAL_INSTRUMENTS.get(instrument, {"symbol": instrument, "name": instrument})
    now_dt = now or datetime.now(timezone.utc)
    quote_dt = parse_utc_datetime(quote_timestamp, default=now_dt)
    quote_age_seconds = max(0, int((now_dt - quote_dt).total_seconds()))
    stale_after = int(stale_after_seconds or meta.get("stale_after_seconds") or 120)
    price_value = float(price)
    previous = float(previous_close) if previous_close else 0.0
    change = price_value - previous if previous else 0.0
    change_pct = (change / previous * 100) if previous else 0.0
    is_stale = quote_age_seconds > stale_after

    return {
        "symbol": meta.get("symbol", instrument),
        "name": meta.get("name", instrument),
        "source": meta.get("source", ""),
        "source_symbol": meta.get("source_symbol", instrument),
        "comparison_symbol": meta.get("comparison_symbol", ""),
        "session": meta.get("session", ""),
        "market_type": meta.get("market_type", ""),
        "price_basis": meta.get("price_basis", ""),
        "change_basis": "same_source_1m_candle_vs_previous_close" if previous else "same_source_1m_candle",
        "quote_timestamp": quote_dt.isoformat(),
        "quote_minute": quote_dt.replace(second=0, microsecond=0).isoformat(),
        "quote_age_seconds": quote_age_seconds,
        "stale_after_seconds": stale_after,
        "is_stale": is_stale,
        "freshness": "delayed" if is_stale else "live",
        "price": round(price_value, 4),
        "last": round(float(last if last is not None else price_value), 4),
        "bid": round(float(bid), 4) if bid is not None else None,
        "ask": round(float(ask), 4) if ask is not None else None,
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
        "direction": "up" if change > 0 else ("down" if change < 0 else "flat"),
    }


def same_source_minute_key(quote: Dict[str, Any]) -> tuple:
    return (
        quote.get("source"),
        quote.get("source_symbol"),
        quote.get("quote_minute"),
    )


def comparable_quotes(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Only compare quotes from the same source, source symbol, and minute."""
    return bool(left and right and same_source_minute_key(left) == same_source_minute_key(right))
