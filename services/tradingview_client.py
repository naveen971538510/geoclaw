"""Small no-key TradingView quote client for dashboard market data."""

from __future__ import annotations

import json
import random
import string
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.logging_service import get_logger


logger = get_logger("tradingview_client")

TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/global/scan"
TRADINGVIEW_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
JP225_DISPLAY_SYMBOL = "FOREXCOM-JP225"
JP225_PRO_SYMBOL = "FOREXCOM:JP225"
JP225_SCANNER_SYMBOL = "TVC:NI225"

_COLUMNS = [
    "name",
    "description",
    "close",
    "change",
    "change_abs",
    "high",
    "low",
    "open",
    "update_mode",
    "type",
    "subtype",
    "exchange",
    "currency",
    "timezone",
    "update_time",
    "current_session",
]


class TradingViewClient:
    def fetch_quote(self, symbol: str = JP225_PRO_SYMBOL) -> Optional[Dict[str, Any]]:
        quote = self._fetch_quote_websocket(symbol)
        if quote:
            return quote
        return self._fetch_quote_scanner_fallback(JP225_SCANNER_SYMBOL)

    def fetch_bars(self, symbol: str = JP225_PRO_SYMBOL, interval: str = "1", count: int = 60) -> List[Dict[str, Any]]:
        """Fetch true TradingView OHLC bars for the chart sparkline."""
        try:
            from websockets.sync.client import connect
        except Exception as exc:
            logger.debug("TradingView websocket client unavailable: %s", exc)
            return []

        session = "cs_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))
        resolved_symbol = json.dumps({"symbol": symbol, "adjustment": "splits"}, separators=(",", ":"))

        try:
            with connect(
                TRADINGVIEW_WS_URL,
                origin="https://www.tradingview.com",
                user_agent_header="Mozilla/5.0",
                open_timeout=6,
                close_timeout=1,
            ) as ws:
                for message in (
                    self._frame("set_auth_token", ["unauthorized_user_token"]),
                    self._frame("chart_create_session", [session, ""]),
                    self._frame("resolve_symbol", [session, "symbol_1", f"={resolved_symbol}"]),
                    self._frame("create_series", [session, "s1", "s1", "symbol_1", str(interval), int(count)]),
                ):
                    ws.send(message)

                bars: List[Dict[str, Any]] = []
                for _ in range(12):
                    raw = ws.recv(timeout=5)
                    for payload in self._parse_frames(str(raw)):
                        if payload.get("m") == "timescale_update":
                            parts = payload.get("p") or []
                            series = ((parts[1] if len(parts) > 1 else {}) or {}).get("s1", {}).get("s", [])
                            parsed = self._parse_series_bars(series)
                            if parsed:
                                bars = parsed
                        elif payload.get("m") == "series_completed" and bars:
                            return bars[-int(count):]
                    if bars:
                        return bars[-int(count):]
        except Exception as exc:
            logger.warning("TradingView bar fetch failed for %s: %s", symbol, exc)
        return []

    def _fetch_quote_websocket(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            from websockets.sync.client import connect
        except Exception as exc:
            logger.debug("TradingView websocket client unavailable: %s", exc)
            return None

        session = "qs_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))
        fields = [
            "lp",
            "lp_time",
            "ch",
            "chp",
            "bid",
            "ask",
            "open_price",
            "high_price",
            "low_price",
            "prev_close_price",
            "description",
            "exchange",
            "type",
            "currency_code",
            "update_mode",
            "current_session",
        ]

        try:
            with connect(
                TRADINGVIEW_WS_URL,
                origin="https://www.tradingview.com",
                user_agent_header="Mozilla/5.0",
                open_timeout=6,
                close_timeout=1,
            ) as ws:
                for message in (
                    self._frame("set_auth_token", ["unauthorized_user_token"]),
                    self._frame("quote_create_session", [session]),
                    self._frame("quote_set_fields", [session] + fields),
                    self._frame("quote_add_symbols", [session, symbol]),
                    self._frame("quote_fast_symbols", [session, symbol]),
                ):
                    ws.send(message)

                values: Dict[str, Any] = {}
                for _ in range(10):
                    raw = ws.recv(timeout=4)
                    for payload in self._parse_frames(str(raw)):
                        if payload.get("m") != "qsd":
                            continue
                        parts = payload.get("p") or []
                        if len(parts) < 2:
                            continue
                        quote_data = parts[1] or {}
                        if quote_data.get("s") not in (None, "ok"):
                            continue
                        values.update(quote_data.get("v") or {})
                    if values.get("lp") is not None and (values.get("bid") is not None or values.get("ask") is not None):
                        break

            price = values.get("lp")
            if price is None:
                logger.warning("TradingView websocket returned no last price for %s", symbol)
                return None
            quote_dt = datetime.fromtimestamp(int(values.get("lp_time") or datetime.now(timezone.utc).timestamp()), timezone.utc)
            return {
                "source": "TradingView",
                "source_symbol": JP225_DISPLAY_SYMBOL,
                "scanner_symbol": symbol,
                "price": float(price),
                "previous_close": float(values.get("prev_close_price") or 0.0),
                "change": float(values.get("ch") or 0.0),
                "change_pct": float(values.get("chp") or 0.0),
                "open": float(values.get("open_price") or 0.0),
                "day_high": float(values.get("high_price") or price),
                "day_low": float(values.get("low_price") or price),
                "bid": float(values["bid"]) if values.get("bid") is not None else None,
                "ask": float(values["ask"]) if values.get("ask") is not None else None,
                "quote_timestamp": quote_dt.isoformat(),
                "quote_minute": quote_dt.replace(second=0, microsecond=0).isoformat(),
                "update_mode": values.get("update_mode") or "",
                "market_session": values.get("current_session") or "",
                "exchange": values.get("exchange") or "FOREXCOM",
                "currency": values.get("currency_code") or "JPY",
                "timezone": "",
                "description": values.get("description") or "Japan 225 CFD",
            }
        except Exception as exc:
            logger.warning("TradingView websocket quote failed for %s: %s", symbol, exc)
            return None

    def _fetch_quote_scanner_fallback(self, scanner_symbol: str) -> Optional[Dict[str, Any]]:
        payload = {
            "symbols": {"tickers": [scanner_symbol], "query": {"types": []}},
            "columns": _COLUMNS,
        }
        req = urllib.request.Request(
            TRADINGVIEW_SCAN_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "GeoClaw/1.0 (+local dashboard)",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("TradingView quote fetch failed for %s: %s", scanner_symbol, exc)
            return None

        rows = data.get("data") or []
        if not rows:
            logger.warning("TradingView returned no quote row for %s", scanner_symbol)
            return None

        row = rows[0]
        values = row.get("d") or []
        if len(values) < len(_COLUMNS):
            logger.warning("TradingView quote row was incomplete for %s", scanner_symbol)
            return None

        raw = dict(zip(_COLUMNS, values))
        price = raw.get("close")
        if price is None:
            return None

        update_time = raw.get("update_time")
        if update_time:
            quote_dt = datetime.fromtimestamp(int(update_time), timezone.utc)
        else:
            quote_dt = datetime.now(timezone.utc)

        change_abs = float(raw.get("change_abs") or 0.0)
        prev_close = float(price) - change_abs if change_abs else 0.0

        return {
            "source": "TradingView",
            "source_symbol": "TVC-NI225",
            "scanner_symbol": scanner_symbol,
            "price": float(price),
            "previous_close": prev_close,
            "change": change_abs,
            "change_pct": float(raw.get("change") or 0.0),
            "open": float(raw.get("open") or 0.0),
            "day_high": float(raw.get("high") or price),
            "day_low": float(raw.get("low") or price),
            "quote_timestamp": quote_dt.isoformat(),
            "quote_minute": quote_dt.replace(second=0, microsecond=0).isoformat(),
            "update_mode": raw.get("update_mode") or "",
            "market_session": raw.get("current_session") or "",
            "exchange": raw.get("exchange") or "",
            "currency": raw.get("currency") or "JPY",
            "timezone": raw.get("timezone") or "",
            "description": raw.get("description") or "Japan 225 CFD",
        }

    def _frame(self, method: str, params: list) -> str:
        payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
        return f"~m~{len(payload)}~m~{payload}"

    def _parse_frames(self, raw: str) -> list:
        frames = []
        cursor = 0
        while True:
            marker = raw.find("~m~", cursor)
            if marker < 0:
                break
            size_start = marker + 3
            size_end = raw.find("~m~", size_start)
            if size_end < 0:
                break
            try:
                size = int(raw[size_start:size_end])
            except ValueError:
                cursor = size_end + 3
                continue
            payload_start = size_end + 3
            payload = raw[payload_start:payload_start + size]
            cursor = payload_start + size
            try:
                frames.append(json.loads(payload))
            except Exception:
                continue
        return frames

    def _parse_series_bars(self, series: list) -> List[Dict[str, Any]]:
        bars: List[Dict[str, Any]] = []
        for item in series:
            values = (item or {}).get("v") or []
            if len(values) < 5:
                continue
            ts, open_, high, low, close = values[:5]
            quote_dt = datetime.fromtimestamp(int(float(ts)), timezone.utc)
            bars.append({
                "t": quote_dt.isoformat(),
                "quote_timestamp": quote_dt.isoformat(),
                "quote_minute": quote_dt.replace(second=0, microsecond=0).isoformat(),
                "o": round(float(open_), 2),
                "h": round(float(high), 2),
                "l": round(float(low), 2),
                "c": round(float(close), 2),
            })
        return bars
