"""
Hyperliquid Client
==================
Fetches real-time market data from Hyperliquid DEX (api.hyperliquid.xyz).
No API key required. Public endpoints only.

Supported instruments (xyz: namespace):
  xyz:CL  — WTI Crude Oil perpetual

API: POST https://api.hyperliquid.xyz/info
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("geoclaw.hyperliquid")

BASE_URL = "https://api.hyperliquid.xyz/info"
TIMEOUT  = 10

# Instrument metadata
HL_INSTRUMENTS = {
    "xyz:CL": {"label": "WTI Crude Oil", "unit": "barrel", "category": "energy"},
}


def _post(payload: dict) -> Any:
    r = requests.post(BASE_URL, json=payload, timeout=TIMEOUT,
                      headers={"Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


# ─── Candles ──────────────────────────────────────────────────────────────────

def get_candles(coin: str = "xyz:CL", interval: str = "1h",
                lookback_hours: int = 168) -> List[Dict]:
    """
    Fetch OHLCV candles.
    interval: 1m, 5m, 15m, 1h, 4h, 1d
    Returns list of {t, o, h, l, c, v, n, ts_iso}
    """
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - lookback_hours * 3_600_000

    raw = _post({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval,
                "startTime": start_ms, "endTime": now_ms},
    })

    if not isinstance(raw, list):
        return []

    return [
        {
            "t":      c["t"],
            "ts_iso": datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc).isoformat(),
            "date":   datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            "o":  float(c["o"]),
            "h":  float(c["h"]),
            "l":  float(c["l"]),
            "c":  float(c["c"]),
            "v":  float(c["v"]),
            "n":  int(c["n"]),   # number of trades
        }
        for c in raw
    ]


# ─── Live price ───────────────────────────────────────────────────────────────

def get_price(coin: str = "xyz:CL") -> Optional[float]:
    """Latest mid price from orderbook."""
    try:
        book = _post({"type": "l2Book", "coin": coin})
        levels = book.get("levels", [])
        if len(levels) >= 2 and levels[0] and levels[1]:
            bid = float(levels[0][0]["px"])
            ask = float(levels[1][0]["px"])
            return round((bid + ask) / 2, 4)
        # Fallback: latest candle close
        candles = get_candles(coin, interval="1m", lookback_hours=1)
        if candles:
            return float(candles[-1]["c"])
    except Exception as exc:
        logger.warning("get_price failed for %s: %s", coin, exc)
    return None


# ─── Orderbook ────────────────────────────────────────────────────────────────

def get_orderbook(coin: str = "xyz:CL", depth: int = 10) -> Dict:
    """
    Returns top N bids and asks.
    {bids: [{px, sz, n}...], asks: [...], spread, mid, coin}
    """
    book = _post({"type": "l2Book", "coin": coin})
    levels = book.get("levels", [[], []])
    bids = [{"px": float(l["px"]), "sz": float(l["sz"]), "n": l["n"]}
            for l in levels[0][:depth]]
    asks = [{"px": float(l["px"]), "sz": float(l["sz"]), "n": l["n"]}
            for l in (levels[1][:depth] if len(levels) > 1 else [])]

    mid    = round((bids[0]["px"] + asks[0]["px"]) / 2, 4) if bids and asks else None
    spread = round(asks[0]["px"] - bids[0]["px"], 4) if bids and asks else None
    spread_pct = round(spread / mid * 100, 4) if mid and spread else None

    return {
        "coin": coin,
        "bids": bids,
        "asks": asks,
        "mid":  mid,
        "spread": spread,
        "spread_pct": spread_pct,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Market summary ───────────────────────────────────────────────────────────

def get_market_summary(coin: str = "xyz:CL") -> Dict:
    """Combined snapshot: price, 24h stats, orderbook top."""
    try:
        candles_1d = get_candles(coin, interval="1h", lookback_hours=24)
        candles_7d = get_candles(coin, interval="1d", lookback_hours=168)
        book       = get_orderbook(coin, depth=5)

        price     = book.get("mid") or (float(candles_1d[-1]["c"]) if candles_1d else None)
        open_24h  = float(candles_1d[0]["o"])  if candles_1d else None
        high_24h  = max(c["h"] for c in candles_1d) if candles_1d else None
        low_24h   = min(c["l"] for c in candles_1d) if candles_1d else None
        vol_24h   = sum(c["v"] for c in candles_1d) if candles_1d else None
        trades_24h = sum(c["n"] for c in candles_1d) if candles_1d else None

        change_24h     = round(price - open_24h, 4) if price and open_24h else None
        change_pct_24h = round((price - open_24h) / open_24h * 100, 3) if price and open_24h else None

        return {
            "coin":     coin,
            "label":    HL_INSTRUMENTS.get(coin, {}).get("label", coin),
            "price":    price,
            "open_24h": open_24h,
            "high_24h": round(high_24h, 4) if high_24h else None,
            "low_24h":  round(low_24h,  4) if low_24h  else None,
            "change_24h":     change_24h,
            "change_pct_24h": change_pct_24h,
            "volume_24h":     round(vol_24h, 2) if vol_24h else None,
            "trades_24h":     trades_24h,
            "bid":  book["bids"][0]["px"] if book["bids"] else None,
            "ask":  book["asks"][0]["px"] if book["asks"] else None,
            "spread":     book["spread"],
            "spread_pct": book["spread_pct"],
            "candles_1h": candles_1d[-48:],   # last 48h at 1h
            "candles_1d": candles_7d,          # 7d daily
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        logger.warning("market_summary failed for %s: %s", coin, exc)
        return {"coin": coin, "error": str(exc)}
