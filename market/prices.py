import sqlite3
from datetime import datetime, timezone
from typing import Dict, List
import requests

from config import ALPHAVANTAGE_KEY, DB_PATH, TRACKED_SYMBOLS
from mock_providers import MOCK_MARKET, get_market_data
from services.provider_state_service import (
    mark_provider_invalid,
    mark_provider_limited,
    mark_provider_temp_issue,
    provider_ready,
    record_provider_success,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _save_snapshot(cur, symbol: str, label: str, price, change_abs, change_pct, asof: str):
    cur.execute(
        """
        INSERT INTO market_snapshots (symbol, label, price, change_abs, change_pct, asof)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (symbol, label, price, change_abs, change_pct, asof),
    )


def _latest_snapshot_map(cur) -> Dict[str, Dict]:
    cur.execute(
        """
        SELECT ms1.*
        FROM market_snapshots ms1
        JOIN (
            SELECT symbol, MAX(id) AS max_id
            FROM market_snapshots
            GROUP BY symbol
        ) ms2
        ON ms1.symbol = ms2.symbol AND ms1.id = ms2.max_id
        """
    )
    return {
        row["symbol"]: {
            "symbol": row["symbol"],
            "label": row["label"],
            "price": row["price"],
            "change_abs": row["change_abs"],
            "change_pct": row["change_pct"],
            "asof": row["asof"],
            "data_source": "database_cache",
            "market_mode": "cached",
        }
        for row in cur.fetchall()
    }

def get_mock_market_snapshots() -> List[Dict]:
    out = []
    for item in MOCK_MARKET:
        row = dict(item)
        row["change_abs"] = row.get("change_abs")
        row["asof"] = row.get("asof") or utc_now_iso()
        out.append(row)
    return out


def _api_error(data: dict) -> str:
    if not isinstance(data, dict):
        return "alphavantage bad payload"
    if data.get("Error Message"):
        return "alphavantage invalid symbol or key"
    if data.get("Note"):
        note = str(data.get("Note", "")).lower()
        if "frequency" in note or "call volume" in note or "rate limit" in note:
            return "alphavantage rate limited"
        return "alphavantage note response"
    if data.get("Information"):
        info = str(data.get("Information", "")).lower()
        if "api key" in info or "premium" in info:
            return "alphavantage unauthorized or unsupported"
        return "alphavantage information response"
    return ""


def _fetch_equity_quote(symbol: str, timeout: int = 20) -> Dict:
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
        "apikey": ALPHAVANTAGE_KEY,
    }
    res = requests.get("https://www.alphavantage.co/query", params=params, timeout=timeout, headers={"User-Agent": "GeoClaw/2.0"})
    res.raise_for_status()
    data = res.json()

    err = _api_error(data)
    if err:
        raise RuntimeError(err)

    q = data.get("Global Quote", {}) or {}
    price = q.get("05. price")
    change = q.get("09. change")
    change_pct = q.get("10. change percent", "")
    if price in (None, "", "0", "0.0"):
        raise RuntimeError("alphavantage empty quote")
    return {
        "price": float(price),
        "change_abs": float(change) if change not in (None, "") else None,
        "change_pct": float(str(change_pct).replace("%", "").strip()) if change_pct not in (None, "") else None,
    }


def _fetch_fx_quote(pair: str, timeout: int = 20) -> Dict:
    if len(pair) != 6:
        raise RuntimeError("alphavantage bad fx pair")
    from_symbol = pair[:3]
    to_symbol = pair[3:]
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_symbol,
        "to_currency": to_symbol,
        "apikey": ALPHAVANTAGE_KEY,
    }
    res = requests.get("https://www.alphavantage.co/query", params=params, timeout=timeout, headers={"User-Agent": "GeoClaw/2.0"})
    res.raise_for_status()
    data = res.json()

    err = _api_error(data)
    if err:
        raise RuntimeError(err)

    q = data.get("Realtime Currency Exchange Rate", {}) or {}
    rate = q.get("5. Exchange Rate")
    if rate in (None, "", "0", "0.0"):
        raise RuntimeError("alphavantage empty fx quote")
    return {
        "price": float(rate),
        "change_abs": None,
        "change_pct": None,
    }


def fetch_and_store_market_snapshots(timeout: int = 20) -> Dict:
    conn = get_conn()
    cur = conn.cursor()
    cached = _latest_snapshot_map(cur)

    if not ALPHAVANTAGE_KEY:
        conn.close()
        return {
            "status": "cached" if cached else "skipped",
            "saved": 0,
            "fallback_used": bool(cached),
            "fallback_count": len(cached),
            "errors": ["ALPHAVANTAGE_KEY not set"],
        }

    if not provider_ready("alphavantage", bool(ALPHAVANTAGE_KEY)):
        conn.close()
        return {
            "status": "cached" if cached else "skipped",
            "saved": 0,
            "fallback_used": bool(cached),
            "fallback_count": len(cached),
            "errors": ["alphavantage temporarily disabled"],
        }

    saved = 0
    errors = []
    fallback_symbols = []
    asof = utc_now_iso()

    for item in TRACKED_SYMBOLS:
        try:
            if item["kind"] == "equity":
                q = _fetch_equity_quote(item["symbol"], timeout=timeout)
            else:
                q = _fetch_fx_quote(item["symbol"], timeout=timeout)

            _save_snapshot(
                cur,
                symbol=item["symbol"],
                label=item["label"],
                price=q.get("price"),
                change_abs=q.get("change_abs"),
                change_pct=q.get("change_pct"),
                asof=asof,
            )
            record_provider_success("alphavantage")
            saved += 1
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            if "unauthorized" in low or "invalid" in low or "unsupported" in low:
                mark_provider_invalid("alphavantage", msg)
            elif "rate limited" in low:
                mark_provider_limited("alphavantage", msg, retry_after_seconds=900)
            else:
                mark_provider_temp_issue("alphavantage", msg, retry_after_seconds=300)
            errors.append(f'{item["symbol"]}: {msg}')
            if item["symbol"] in cached and cached[item["symbol"]].get("price") is not None:
                fallback_symbols.append(item["symbol"])

    conn.commit()
    conn.close()

    fallback_used = bool(fallback_symbols) or (saved == 0 and bool(cached))
    return {
        "status": "ok" if saved and not errors else ("partial" if saved or fallback_used else "skipped"),
        "saved": saved,
        "fallback_used": fallback_used,
        "fallback_count": len(fallback_symbols) if fallback_symbols else (len(cached) if saved == 0 else 0),
        "fallback_symbols": fallback_symbols[:10],
        "errors": errors[:10],
    }


def _load_latest_market_snapshots() -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ms1.*
        FROM market_snapshots ms1
        JOIN (
            SELECT symbol, MAX(id) AS max_id
            FROM market_snapshots
            GROUP BY symbol
        ) ms2
        ON ms1.symbol = ms2.symbol AND ms1.id = ms2.max_id
        ORDER BY ms1.symbol
        """
    )
    rows = cur.fetchall()
    conn.close()

    out = []
    for row in rows:
        out.append(
            {
                "symbol": row["symbol"],
                "label": row["label"],
                "price": row["price"],
                "change_abs": row["change_abs"],
                "change_pct": row["change_pct"],
                "asof": row["asof"],
                "data_source": "database_cache",
                "market_mode": "cached",
            }
        )
    return out


def get_latest_market_snapshots() -> List[Dict]:
    return get_market_data(_load_latest_market_snapshots)
