"""
GeoClaw dashboard API — FastAPI on port 8001.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    import config  # noqa: F401
except Exception:
    pass

from intelligence.db import ensure_intelligence_schema, get_database_url, query_all
from intelligence.groq_briefing import build_signals_context, generate_dashboard_briefing
from intelligence.scenario_engine import generate_scenarios
from services.price_normalizer import CANONICAL_INSTRUMENTS, normalize_candle_timestamp, normalize_quote, parse_utc_datetime
from services.tradingview_client import TradingViewClient
from services.signal_taxonomy import SIGNAL_SECTION_ORDER, enrich_signal_row, group_signals

app = FastAPI(title="GeoClaw Dashboard API", version="1.0.0")
SPA_DIST_DIR = ROOT / "static" / "dashboard-app"

_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
]
_prod = (os.environ.get("GEOCLAW_PRODUCTION_ORIGIN") or "").strip()
if _prod:
    _origins.append(_prod)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/dashboard-app", StaticFiles(directory=str(SPA_DIST_DIR), check_dir=False), name="dashboard_app")

# ---------------------------------------------------------------------------
# Auth middleware — mirrors _mutation_guard in main.py.
# Protected paths: /api/* and /bias.  Public: /health, SPA pages, static assets.
# Set GEOCLAW_LOCAL_TOKEN to enable remote access (Authorization: Bearer <token>).
# Without the env var the API is restricted to localhost only.
# ---------------------------------------------------------------------------

# Cached once at module load — changing the token requires a server restart.
_API_TOKEN = str(os.environ.get("GEOCLAW_LOCAL_TOKEN") or "").strip()


def _unauth_response(request: Request) -> JSONResponse:
    """401 with CORS headers preserved so dev consoles show the real error."""
    origin = str(request.headers.get("origin") or "")
    headers: dict[str, str] = {}
    if origin in _origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        {
            "status": "error",
            "error": (
                "Unauthorized — set GEOCLAW_LOCAL_TOKEN and pass it as "
                "Authorization: Bearer <token> (or ?token=<token>)"
            ),
        },
        status_code=401,
        headers=headers,
    )


def _is_protected_path(path: str) -> bool:
    return path.startswith("/api/") or path == "/bias"


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if request.method != "OPTIONS" and _is_protected_path(request.url.path):
        client_host = str((request.client.host if request.client else "") or "")
        is_local = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
        if _API_TOKEN:
            auth_header = str(request.headers.get("authorization") or "")
            provided = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
            if not provided:
                provided = str(request.query_params.get("token") or "").strip()
            if not is_local and not hmac.compare_digest(provided, _API_TOKEN):
                return _unauth_response(request)
        elif not is_local:
            return _unauth_response(request)
    return await call_next(request)

PRICE_PANEL_META = {
    "JP225": {"label": "JP225", "name": CANONICAL_INSTRUMENTS["JP225"]["name"]},
}

STRIPE_TIERS = {
    "basic": {"name": "Basic", "unit_amount": 2900},
    "pro": {"name": "Pro", "unit_amount": 9900},
    "institutional": {"name": "Institutional", "unit_amount": 49900},
}


class CheckoutRequest(BaseModel):
    tier: str


def _serialize_datetime_fields(row: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    item = dict(row or {})
    for key in keys:
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def _signal_score(direction: str, confidence: float) -> float:
    clean = str(direction or "").upper()
    if clean == "BUY":
        return float(confidence)
    if clean == "SELL":
        return -float(confidence)
    return 0.0


def _signal_bias_payload(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    weights = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
    net = 0.0
    total = 0.0
    for row in rows:
        direction = str(row.get("direction") or "HOLD").upper()
        confidence = float(row.get("confidence") or 0.0)
        if direction not in weights:
            direction = "HOLD"
        weights[direction] += confidence
        net += _signal_score(direction, confidence)
        total += confidence
    weighted_confidence = (abs(net) / total * 100.0) if total else 0.0
    if total and net >= total * 0.15:
        label = "BULLISH"
    elif total and net <= -total * 0.15:
        label = "BEARISH"
    else:
        label = "NEUTRAL"
    return {
        "label": label,
        "weighted_confidence": round(weighted_confidence, 1),
        "buy_weight": round(weights["BUY"], 1),
        "sell_weight": round(weights["SELL"], 1),
        "hold_weight": round(weights["HOLD"], 1),
    }


def _latest_signal_cycle_rows() -> List[Dict[str, Any]]:
    rows = query_all(
        """
        SELECT DISTINCT ON (signal_name)
            id, signal_name, value, direction, confidence, explanation_plain_english, ts
        FROM geoclaw_signals
        ORDER BY signal_name, ts DESC;
        """
    )
    active_rows = [
        enrich_signal_row(_serialize_datetime_fields(row, "ts"))
        for row in rows
        if str(row.get("signal_name") or "").strip() != "Composite macro regime"
    ]
    active_rows.sort(key=lambda item: (float(item.get("confidence") or 0.0), str(item.get("ts") or "")), reverse=True)
    return active_rows


def _origin_base(request: Request) -> str:
    configured = str(os.environ.get("GEOCLAW_PRODUCTION_ORIGIN") or "").strip().rstrip("/")
    origin = str(request.headers.get("origin") or "").strip().rstrip("/")
    if origin:
        return origin
    if configured:
        return configured
    base = str(request.base_url).rstrip("/")
    return base[:-1] if base.endswith("/") else base


def _spa_index_response():
    index_path = SPA_DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        """
        <html>
          <body style="background:#050b12;color:#d8e2f0;font-family:ui-sans-serif,system-ui,sans-serif;padding:40px;">
            <h1>GeoClaw dashboard bundle not built yet.</h1>
            <p>Run <code>npm install</code> and <code>npm run build</code> inside <code>ui/dashboard</code> to generate the SPA bundle.</p>
          </body>
        </html>
        """,
        status_code=503,
    )


def _require_db():
    if not get_database_url():
        raise RuntimeError("DATABASE_URL is not configured")


def _local_sqlite_mode() -> bool:
    return str(os.environ.get("GEOCLAW_DB_BACKEND") or "").strip().lower() in {"sqlite", "sqlite3", "local"}


def _local_query(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    from services.db_helpers import query

    return [dict(row) for row in query(sql, params)]


def _local_latest_signals(limit: int = 30) -> List[Dict[str, Any]]:
    rows = _local_query(
        """
        SELECT id, thesis_key, title, confidence, status, terminal_risk,
               last_update_reason, created_at, last_updated_at
        FROM agent_theses
        WHERE COALESCE(status, '') NOT IN ('superseded', 'expired')
        ORDER BY confidence DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 30), 200)),),
    )
    signals: List[Dict[str, Any]] = []
    for row in rows:
        thesis = str(row.get("thesis_key") or "")
        title = str(row.get("title") or "").strip() or thesis[:160] or "GeoClaw thesis"
        confidence = max(0.0, min(float(row.get("confidence") or 0.0) * 100.0, 100.0))
        reason = str(row.get("last_update_reason") or "").strip()
        risk = str(row.get("terminal_risk") or "").strip().upper()
        if not reason:
            reason = risk or "Thesis-derived signal."
        status = str(row.get("status") or "").strip().lower()
        # Map thesis fields to a direction
        if status == "confirmed" or confidence >= 75:
            direction = "BUY"
        elif "high" in risk or "extreme" in risk or status == "bearish":
            direction = "SELL"
        elif confidence >= 50:
            direction = "BUY"
        else:
            direction = "HOLD"
        signals.append(
            enrich_signal_row(
                {
                    "id": row.get("id"),
                    "signal_name": title,
                    "value": round(confidence, 1),
                    "direction": direction,
                    "confidence": round(confidence, 1),
                    "explanation_plain_english": reason,
                    "ts": row.get("last_updated_at") or row.get("created_at") or "",
                }
            )
        )
    return signals


def _local_dashboard_overview_payload() -> Dict[str, Any]:
    active_rows = _local_latest_signals(limit=30)
    grouped = group_signals(active_rows)
    last_updated = max((str(item.get("ts") or "") for item in active_rows), default="")
    return {
        "status": "ok",
        "market_bias": _signal_bias_payload(active_rows),
        "group_order": list(SIGNAL_SECTION_ORDER),
        "signals": active_rows,
        "grouped_signals": grouped,
        "last_updated": last_updated,
        "mode": "local_sqlite",
    }


def _local_prices_payload(symbols: str = "JP225", points: int = 18) -> Dict[str, Any]:
    aliases = {"JP225": "^N225"}
    requested = [item.strip().upper() for item in str(symbols or "").split(",") if item.strip()]
    ordered = [symbol for symbol in requested if symbol in PRICE_PANEL_META] or list(PRICE_PANEL_META.keys())
    local_symbols = [aliases.get(symbol, symbol) for symbol in ordered]
    rows = _local_query(
        """
        SELECT symbol, price, captured_at, source, source_symbol,
               quote_timestamp, quote_minute, bid, ask, last
        FROM price_snapshots
        WHERE symbol IN ({})
        ORDER BY symbol ASC, captured_at DESC
        """.format(",".join("?" for _ in local_symbols)),
        tuple(local_symbols),
    ) if local_symbols else []
    max_points = max(2, min(int(points or 18), 60))
    history_by_symbol: Dict[str, List[Dict[str, Any]]] = {symbol: [] for symbol in ordered}
    reverse_aliases = {value: key for key, value in aliases.items()}
    for row in rows:
        local_symbol = str(row.get("symbol") or "").upper()
        dashboard_symbol = reverse_aliases.get(local_symbol, local_symbol)
        if dashboard_symbol in history_by_symbol and len(history_by_symbol[dashboard_symbol]) < max_points:
            history_by_symbol[dashboard_symbol].append({
                "price": row.get("price"),
                "ts": row.get("captured_at"),
                "source": row.get("source"),
                "source_symbol": row.get("source_symbol"),
                "quote_timestamp": row.get("quote_timestamp"),
                "quote_minute": row.get("quote_minute"),
                "bid": row.get("bid"),
                "ask": row.get("ask"),
                "last": row.get("last"),
            })
    payload = []
    for ticker in ordered:
        history = list(reversed(history_by_symbol.get(ticker, [])))
        latest = history[-1] if history else {}
        if latest:
            latest_source = latest.get("source")
            latest_source_symbol = latest.get("source_symbol")
            if latest_source or latest_source_symbol:
                filtered_history = [
                    item for item in history
                    if item.get("source") == latest_source
                    and item.get("source_symbol") == latest_source_symbol
                ]
                if filtered_history:
                    history = filtered_history
                    latest = history[-1]
            latest_quote_ts = latest.get("quote_timestamp")
            if latest_quote_ts:
                latest_quote_dt = parse_utc_datetime(latest_quote_ts)
                recent_same_minute_family = [
                    item for item in history
                    if item.get("quote_timestamp")
                    and abs((latest_quote_dt - parse_utc_datetime(item.get("quote_timestamp"))).total_seconds()) <= 7200
                ]
                if recent_same_minute_family:
                    history = recent_same_minute_family
                    latest = history[-1]
        previous = history[-2] if len(history) > 1 else latest
        direction_label = "flat"
        if latest.get("price") is not None and previous.get("price") is not None:
            if float(latest["price"]) > float(previous["price"]):
                direction_label = "up"
            elif float(latest["price"]) < float(previous["price"]):
                direction_label = "down"
        payload.append(
            {
                "symbol": ticker,
                "label": PRICE_PANEL_META[ticker]["label"],
                "name": PRICE_PANEL_META[ticker]["name"],
                "price": latest.get("price"),
                "last_fetch_time": latest.get("ts", ""),
                "source": latest.get("source") or _JP225_LIVE_SOURCE["source"],
                "source_symbol": latest.get("source_symbol") or aliases.get(ticker, ticker),
                "quote_timestamp": latest.get("quote_timestamp") or latest.get("ts", ""),
                "quote_minute": latest.get("quote_minute") or "",
                "bid": latest.get("bid"),
                "ask": latest.get("ask"),
                "last": latest.get("last") if latest.get("last") is not None else latest.get("price"),
                "direction": direction_label,
                "sparkline": [float(item.get("price") or 0.0) for item in history if item.get("price") is not None],
            }
        )
    return {"status": "ok", "prices": payload, "captured_at": datetime.now(timezone.utc).isoformat(), "mode": "local_sqlite"}


def _local_news_payload(limit: int = 20) -> Dict[str, Any]:
    rows = _local_query(
        """
        SELECT id, headline, source_name, url, summary, fetched_at, published_at
        FROM ingested_articles
        ORDER BY COALESCE(fetched_at, published_at, '') DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 20), 100)),),
    )
    news = []
    for row in rows:
        news.append(
            {
                "id": row.get("id"),
                "headline": row.get("headline"),
                "source": row.get("source_name"),
                "url": row.get("url"),
                "sentiment": "unknown",
                "confidence": 0,
                "reason": str(row.get("summary") or "")[:240],
                "ts": row.get("fetched_at") or row.get("published_at") or "",
            }
        )
    return {"status": "ok", "news": news, "mode": "local_sqlite"}


def _local_briefing_payload() -> Dict[str, Any]:
    rows = _local_query(
        """
        SELECT briefing_text, generated_at
        FROM agent_briefings
        ORDER BY generated_at DESC
        LIMIT 1
        """
    )
    if rows:
        text = str(rows[0].get("briefing_text") or "")
        generated_at = str(rows[0].get("generated_at") or datetime.now(timezone.utc).isoformat())
    else:
        top = _local_latest_signals(limit=5)
        bullets = [f"- {item['signal_name']} ({item['confidence']}% confidence)" for item in top]
        text = "GeoClaw local briefing fallback.\n\n" + ("\n".join(bullets) if bullets else "No active thesis signals available.")
        generated_at = datetime.now(timezone.utc).isoformat()
    return {"status": "ok", "briefing": text, "generated_at": generated_at, "mode": "local_sqlite"}


@app.on_event("startup")
def _startup():
    try:
        _require_db()
        ensure_intelligence_schema()
    except Exception:
        pass


@app.get("/", response_class=RedirectResponse)
def spa_home():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def spa_dashboard():
    return _spa_index_response()


@app.get("/prices", response_class=HTMLResponse)
def spa_prices():
    return _spa_index_response()


@app.get("/signals", response_class=HTMLResponse)
def spa_signals():
    return _spa_index_response()


@app.get("/bias")
def api_bias():
    try:
        if _local_sqlite_mode():
            active_rows = _local_latest_signals(limit=30)
            last_updated = max((str(item.get("ts") or "") for item in active_rows), default="")
            return JSONResponse(
                {
                    "status": "ok",
                    "cycle": {"signal_count": len(active_rows), "last_updated": last_updated},
                    **_signal_bias_payload(active_rows),
                    "mode": "local_sqlite",
                }
            )
        _require_db()
        active_rows = _latest_signal_cycle_rows()
        last_updated = max((str(item.get("ts") or "") for item in active_rows), default="")
        payload = _signal_bias_payload(active_rows)
        return JSONResponse(
            {
                "status": "ok",
                "cycle": {
                    "signal_count": len(active_rows),
                    "last_updated": last_updated,
                },
                **payload,
            }
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/subscribe", response_class=HTMLResponse)
def spa_subscribe():
    return _spa_index_response()


@app.get("/api/signals")
def api_signals(hours: Optional[int] = 24, limit: int = 500, direction: str = ""):
    try:
        if _local_sqlite_mode():
            signals = _local_latest_signals(limit=max(1, min(int(limit or 500), 5000)))
            clean_direction = str(direction or "").strip().upper()
            if clean_direction in {"BUY", "SELL", "HOLD"}:
                signals = [item for item in signals if str(item.get("direction") or "").upper() == clean_direction]
            return JSONResponse({"status": "ok", "signals": signals, "mode": "local_sqlite"})
        _require_db()
        where_parts = []
        params: List[Any] = []
        if hours and int(hours) > 0:
            where_parts.append("ts >= %s")
            params.append(datetime.now(timezone.utc) - timedelta(hours=int(hours)))
        clean_direction = str(direction or "").strip().upper()
        if clean_direction in {"BUY", "SELL", "HOLD"}:
            where_parts.append("direction = %s")
            params.append(clean_direction)
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        rows = query_all(
            f"""
            SELECT id, signal_name, value, direction, confidence, explanation_plain_english, ts
            FROM geoclaw_signals
            {where_clause}
            ORDER BY ts DESC, confidence DESC
            LIMIT %s;
            """,
            tuple(params + [max(1, min(int(limit or 500), 5000))]),
        )
        signals = [enrich_signal_row(_serialize_datetime_fields(row, "ts")) for row in rows]
        return JSONResponse({"status": "ok", "signals": signals})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/dashboard/overview")
def api_dashboard_overview():
    try:
        if _local_sqlite_mode():
            return JSONResponse(_local_dashboard_overview_payload())
        _require_db()
        active_rows = _latest_signal_cycle_rows()
        grouped = group_signals(active_rows)
        last_updated = max((str(item.get("ts") or "") for item in active_rows), default="")
        return JSONResponse(
            {
                "status": "ok",
                "market_bias": _signal_bias_payload(active_rows),
                "group_order": list(SIGNAL_SECTION_ORDER),
                "signals": active_rows,
                "grouped_signals": grouped,
                "last_updated": last_updated,
            }
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/prices")
def api_prices(symbols: str = "JP225", points: int = 18):
    try:
        if _local_sqlite_mode():
            return JSONResponse(_local_prices_payload(symbols=symbols, points=points))
        _require_db()
        requested = [item.strip().upper() for item in str(symbols or "").split(",") if item.strip()]
        ordered = [symbol for symbol in requested if symbol in PRICE_PANEL_META] or list(PRICE_PANEL_META.keys())
        rows = query_all(
            """
            SELECT ticker, price, ts
            FROM (
                SELECT ticker, price, ts,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY ts DESC) AS rn
                FROM price_data
                WHERE ticker = ANY(%s)
            ) ranked
            WHERE rn <= %s
            ORDER BY ticker ASC, ts ASC;
            """,
            (ordered, max(2, min(int(points or 18), 60))),
        )
        history_by_ticker: Dict[str, List[Dict[str, Any]]] = {ticker: [] for ticker in ordered}
        for row in rows:
            ticker = str(row.get("ticker") or "").upper()
            if ticker not in history_by_ticker:
                continue
            history_by_ticker[ticker].append(_serialize_datetime_fields(row, "ts"))

        payload = []
        for ticker in ordered:
            history = history_by_ticker.get(ticker, [])
            latest = history[-1] if history else {}
            previous = history[-2] if len(history) > 1 else latest
            latest_price = latest.get("price")
            previous_price = previous.get("price")
            direction_label = "flat"
            if latest_price is not None and previous_price is not None:
                if float(latest_price) > float(previous_price):
                    direction_label = "up"
                elif float(latest_price) < float(previous_price):
                    direction_label = "down"
            payload.append(
                {
                    "symbol": ticker,
                    "label": PRICE_PANEL_META[ticker]["label"],
                    "name": PRICE_PANEL_META[ticker]["name"],
                    "price": latest_price,
                    "last_fetch_time": latest.get("ts", ""),
                    "direction": direction_label,
                    "sparkline": [float(item.get("price") or 0.0) for item in history if item.get("price") is not None],
                }
            )
        return JSONResponse({"status": "ok", "prices": payload, "captured_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/macro")
def api_macro():
    try:
        if _local_sqlite_mode():
            return JSONResponse({"status": "ok", "macro": [], "mode": "local_sqlite"})
        _require_db()
        rows = query_all(
            """
            SELECT DISTINCT ON (metric_name)
                metric_name, observed_at, value, previous_value, pct_change
            FROM macro_signals
            ORDER BY metric_name, observed_at DESC;
            """
        )
        for r in rows:
            if r.get("observed_at"):
                r["observed_at"] = r["observed_at"].isoformat()
        return JSONResponse({"status": "ok", "macro": rows})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/charts")
def api_charts():
    try:
        if _local_sqlite_mode():
            return JSONResponse({"status": "ok", "charts": [], "mode": "local_sqlite"})
        _require_db()
        since = datetime.now(timezone.utc) - timedelta(days=7)
        rows = query_all(
            """
            SELECT id, ticker, pattern_name, direction, confidence, detected_at, bar_index
            FROM chart_signals
            WHERE detected_at >= %s
            ORDER BY detected_at DESC
            LIMIT 200;
            """,
            (since,),
        )
        for r in rows:
            if r.get("detected_at"):
                r["detected_at"] = r["detected_at"].isoformat()
        return JSONResponse({"status": "ok", "charts": rows})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


_news_cache: Dict[str, Any] = {"news": [], "ts": 0}

@app.get("/api/news")
def api_news():
    """Fetch live Japan/market news from RSS + web search. Cached 2 min."""
    import time as _time
    now = _time.time()
    if now - _news_cache["ts"] < 120 and _news_cache["news"]:
        return JSONResponse({"status": "ok", "news": _news_cache["news"]})
    news: List[Dict[str, Any]] = []
    seen: set = set()
    # 1. Live RSS
    try:
        from sources.rss_client import RSSSource
        rss = RSSSource()
        for a in rss.fetch()[:15]:
            h = str(getattr(a, "headline", "") or "").strip()
            if not h or h in seen:
                continue
            seen.add(h)
            news.append({
                "id": len(news) + 1,
                "headline": h,
                "source": str(getattr(a, "source", "") or "RSS"),
                "url": str(getattr(a, "url", "") or ""),
                "sentiment": "unknown",
                "confidence": 0,
                "reason": str(getattr(a, "summary", "") or "")[:200],
                "ts": str(getattr(a, "published_at", "") or datetime.now(timezone.utc).isoformat()),
            })
    except Exception:
        pass
    # 2. DuckDuckGo search for Nikkei 225 news
    try:
        import re
        from html import unescape
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": "Nikkei 225 Japan stock market today"},
            headers={"User-Agent": "GeoClaw/1.0"},
            timeout=8,
        )
        blocks = re.findall(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</span>',
            resp.text, re.DOTALL
        )
        for url_raw, title_raw, snippet_raw in blocks[:8]:
            title = re.sub(r'<[^>]+>', '', unescape(title_raw)).strip()
            if not title or title in seen:
                continue
            seen.add(title)
            url_match = re.search(r'uddg=([^&]+)', url_raw)
            url = requests.utils.unquote(url_match.group(1)) if url_match else url_raw
            news.append({
                "id": len(news) + 1,
                "headline": title[:200],
                "source": "Web Search",
                "url": url[:500],
                "sentiment": "unknown",
                "confidence": 0,
                "reason": re.sub(r'<[^>]+>', '', unescape(snippet_raw)).strip()[:200],
                "ts": datetime.now(timezone.utc).isoformat(),
            })
    except Exception:
        pass
    # 3. Fallback to DB if nothing came back
    if not news:
        try:
            db_result = _local_news_payload(limit=15)
            news = db_result.get("news", [])
        except Exception:
            pass
    _news_cache["news"] = news[:20]
    _news_cache["ts"] = now
    return JSONResponse({"status": "ok", "news": news[:20]})


@app.get("/api/scenarios")
def api_scenarios():
    try:
        if _local_sqlite_mode():
            return JSONResponse(
                {
                    "status": "ok",
                    "scenarios": "Local SQLite mode: scenario data is unavailable until the Postgres intelligence tables are configured.",
                    "mode": "local_sqlite",
                }
            )
        _require_db()
        macro_rows = query_all(
            """
            SELECT DISTINCT ON (metric_name)
                metric_name, observed_at, value, previous_value, pct_change
            FROM macro_signals
            ORDER BY metric_name, observed_at DESC;
            """
        )
        for r in macro_rows:
            if r.get("observed_at"):
                r["observed_at"] = r["observed_at"].isoformat()
        text = generate_scenarios(macro_rows)
        return JSONResponse({"status": "ok", "scenarios": text})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/briefing")
def api_briefing():
    try:
        if _local_sqlite_mode():
            return JSONResponse(_local_briefing_payload())
        _require_db()
        since = datetime.now(timezone.utc) - timedelta(hours=48)
        signals = query_all(
            """
            SELECT signal_name, direction, confidence, explanation_plain_english, ts
            FROM geoclaw_signals WHERE ts >= %s ORDER BY confidence DESC LIMIT 30;
            """,
            (since,),
        )
        macro = query_all(
            """
            SELECT DISTINCT ON (metric_name)
                metric_name, value, previous_value, pct_change, observed_at
            FROM macro_signals
            ORDER BY metric_name, observed_at DESC;
            """
        )
        charts = query_all(
            """
            SELECT ticker, pattern_name, direction, confidence, detected_at
            FROM chart_signals
            ORDER BY detected_at DESC LIMIT 20;
            """
        )
        ctx = build_signals_context(signals, macro, charts)
        text = generate_dashboard_briefing(ctx)
        return JSONResponse(
            {
                "status": "ok",
                "briefing": text,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


def _macro_bias_score() -> float:
    """Rough -1 bearish to +1 bullish from last 24h signals."""
    try:
        if _local_sqlite_mode():
            return 0.0
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = query_all(
            "SELECT direction, confidence FROM geoclaw_signals WHERE ts >= %s;",
            (since,),
        )
        if not rows:
            return 0.0
        s = 0.0
        w = 0.0
        for r in rows:
            d = str(r.get("direction") or "").upper()
            c = float(r.get("confidence") or 50)
            delta = 0.0
            if d in {"BUY", "BULLISH"}:
                delta = 1.0
            elif d in {"SELL", "BEARISH"}:
                delta = -1.0
            s += delta * (c / 100.0)
            w += c / 100.0
        return s / w if w else 0.0
    except Exception:
        return 0.0


@app.post("/api/portfolio")
async def api_portfolio(request: Request):
    try:
        if not _local_sqlite_mode():
            _require_db()
        raw = await request.body()
        payload = json.loads(raw.decode("utf-8") or "[]")
        tickers: List[str] = []
        if isinstance(payload, list):
            tickers = [str(t).strip().upper() for t in payload if str(t).strip()]
        elif isinstance(payload, dict):
            t = payload.get("tickers") or payload.get("symbols")
            if isinstance(t, list):
                tickers = [str(x).strip().upper() for x in t if str(x).strip()]
        if not tickers:
            return JSONResponse({"status": "error", "error": "Provide JSON array of tickers or {\"tickers\":[]}"}, status_code=400)

        import yfinance as yf

        bias = _macro_bias_score()
        results = []
        for sym in tickers[:40]:
            try:
                hist = yf.Ticker(sym).history(period="3mo", interval="1d", auto_adjust=True)
                if hist is None or len(hist) < 5:
                    results.append(
                        {
                            "ticker": sym,
                            "risk_score": 50,
                            "recommendation": "Insufficient price history — treat as medium unknown risk.",
                            "volatility_30d_pct": None,
                        }
                    )
                    continue
                rets = hist["Close"].pct_change().dropna().tail(30).tolist()
                vol = statistics.pstdev(rets) * (252 ** 0.5) * 100 if rets else 15.0
                base = min(100, max(0, vol * 4))
                macro_adj = (1.0 - bias) * 8
                risk = min(100, max(5, base + macro_adj))
                if risk > 70:
                    rec = "Elevated risk — reduce size or hedge; macro headwinds may amplify drawdowns."
                elif risk > 45:
                    rec = "Moderate risk — balanced sizing; watch macro signals and stops."
                else:
                    rec = "Lower near-term volatility vs peers — still monitor macro shocks."
                results.append(
                    {
                        "ticker": sym,
                        "risk_score": round(risk, 1),
                        "recommendation": rec,
                        "volatility_30d_pct": round(vol, 2),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "ticker": sym,
                        "risk_score": 55,
                        "recommendation": f"Could not analyze: {exc}",
                        "volatility_30d_pct": None,
                    }
                )
        return JSONResponse({"status": "ok", "macro_bias_hint": bias, "holdings": results})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.post("/api/checkout/create-session")
def api_checkout_create_session(payload: CheckoutRequest, request: Request):
    try:
        secret_key = str(os.environ.get("STRIPE_SECRET_KEY") or "").strip()
        if not secret_key:
            return JSONResponse({"status": "error", "error": "STRIPE_SECRET_KEY is not configured"}, status_code=503)

        tier_key = str(payload.tier or "").strip().lower()
        tier = STRIPE_TIERS.get(tier_key)
        if not tier:
            return JSONResponse({"status": "error", "error": "Unknown subscription tier"}, status_code=400)

        base_url = _origin_base(request)
        success_url = f"{base_url}/subscribe?status=success&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{base_url}/subscribe?status=cancelled"
        form_payload = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "gbp",
            "line_items[0][price_data][unit_amount]": str(int(tier["unit_amount"])),
            "line_items[0][price_data][recurring][interval]": "month",
            "line_items[0][price_data][product_data][name]": f"GeoClaw {tier['name']}",
            "line_items[0][price_data][product_data][description]": f"GeoClaw {tier['name']} monthly subscription",
        }
        response = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            headers={"Authorization": f"Bearer {secret_key}"},
            data=form_payload,
            timeout=30,
        )
        try:
            body = response.json()
        except Exception:
            body = {"error": {"message": response.text[:300]}}
        if response.status_code >= 400:
            message = str(((body.get("error") or {}).get("message")) or "Stripe Checkout session creation failed")
            return JSONResponse({"status": "error", "error": message}, status_code=response.status_code)
        return JSONResponse(
            {
                "status": "ok",
                "session_id": body.get("id", ""),
                "checkout_url": body.get("url", ""),
                "tier": tier_key,
            }
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/stream")
async def api_stream(request: Request):
    """
    Server-Sent Events feed — pushes signal and price updates every 5 s.
    Replaces 30-second setInterval polling; client reconnects automatically on drop.
    Connect with: const es = new EventSource('/api/stream')
    Auth: same rules as the rest of /api/* (GEOCLAW_LOCAL_TOKEN or localhost).
    """
    _SSE_INTERVAL = 30  # seconds between checks
    _last_hash = ""

    async def _event_generator():
        nonlocal _last_hash
        while True:
            if await request.is_disconnected():
                break
            try:
                if _local_sqlite_mode():
                    overview = _local_dashboard_overview_payload()
                    price_payload = _local_prices_payload()
                    data = {
                        "signals": [
                            {
                                "name": str(r.get("signal_name") or ""),
                                "direction": str(r.get("direction") or ""),
                                "confidence": float(r.get("confidence") or 0.0),
                                "ts": str(r.get("ts") or ""),
                            }
                            for r in overview.get("signals", [])
                        ],
                        "prices": [
                            {
                                "ticker": str(r.get("symbol") or ""),
                                "price": float(r.get("price") or 0.0),
                                "ts": str(r.get("last_fetch_time") or ""),
                            }
                            for r in price_payload.get("prices", [])
                        ],
                        "mode": "local_sqlite",
                    }
                    data_hash = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()
                    if data_hash == _last_hash:
                        yield ": keepalive\n\n"
                    else:
                        _last_hash = data_hash
                        data["ts"] = datetime.now(timezone.utc).isoformat()
                        yield f"data: {json.dumps(data)}\n\n"
                    await asyncio.sleep(_SSE_INTERVAL)
                    continue
                _require_db()
                signal_rows = query_all(
                    """
                    SELECT DISTINCT ON (signal_name)
                        signal_name, direction, confidence, ts
                    FROM geoclaw_signals
                    WHERE ts >= %s
                    ORDER BY signal_name, ts DESC;
                    """,
                    (datetime.now(timezone.utc) - timedelta(hours=24),),
                )
                price_rows = query_all(
                    """
                    SELECT DISTINCT ON (ticker) ticker, price, ts
                    FROM price_data
                    ORDER BY ticker, ts DESC;
                    """
                )
                # Build data payload (without volatile wallclock ts) for hashing
                data = {
                    "signals": [
                        {
                            "name": str(r.get("signal_name") or ""),
                            "direction": str(r.get("direction") or ""),
                            "confidence": float(r.get("confidence") or 0.0),
                            "ts": r["ts"].isoformat() if r.get("ts") else "",
                        }
                        for r in signal_rows
                    ],
                    "prices": [
                        {
                            "ticker": str(r.get("ticker") or ""),
                            "price": float(r.get("price") or 0.0),
                            "ts": r["ts"].isoformat() if r.get("ts") else "",
                        }
                        for r in price_rows
                    ],
                }
                data_hash = hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()
                if data_hash == _last_hash:
                    # Data unchanged — send a lightweight keepalive comment instead
                    yield ": keepalive\n\n"
                else:
                    _last_hash = data_hash
                    data["ts"] = datetime.now(timezone.utc).isoformat()
                    yield f"data: {json.dumps(data)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(_SSE_INTERVAL)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# Live JP225 price endpoint
# ---------------------------------------------------------------------------

_jp225_candles_cache: Dict[str, Any] = {}
_jp225_context_cache: Dict[str, Any] = {"articles": [], "ts": 0}
_JP225_INTERVALS = {
    "1": {"label": "1m", "seconds": 60, "count": 60},
    "1m": {"label": "1m", "seconds": 60, "count": 60},
    "30": {"label": "30m", "seconds": 1800, "count": 60},
    "30m": {"label": "30m", "seconds": 1800, "count": 60},
    "60": {"label": "1h", "seconds": 3600, "count": 60},
    "1h": {"label": "1h", "seconds": 3600, "count": 60},
}
_CANONICAL_LIVE_SOURCE = (os.environ.get("GEOCLAW_CANONICAL_LIVE_SOURCE", "tradingview") or "tradingview").strip().lower()
if _CANONICAL_LIVE_SOURCE not in {"tradingview", "tv", "forexcom", "yahoo", "yahoo_finance"}:
    _CANONICAL_LIVE_SOURCE = "tradingview"
_JP225_LIVE_SOURCE = dict(CANONICAL_INSTRUMENTS["JP225"])
_JP225_LIVE_SOURCE["stale_after_seconds"] = int(os.environ.get("GEOCLAW_LIVE_STALE_AFTER_SECONDS", "120") or 120)
_tradingview_client = TradingViewClient()


def _normalise_jp225_interval(interval: str = "1") -> Dict[str, Any]:
    clean = str(interval or "1").strip().lower()
    meta = dict(_JP225_INTERVALS.get(clean) or _JP225_INTERVALS["1"])
    meta["tv_interval"] = "1" if meta["label"] == "1m" else ("30" if meta["label"] == "30m" else "60")
    return meta


def _interval_quote_bucket(value: Any, interval_meta: Dict[str, Any]) -> Dict[str, str]:
    dt = parse_utc_datetime(value)
    seconds = int(interval_meta.get("seconds") or 60)
    if seconds >= 3600:
        bucket = dt.replace(minute=0, second=0, microsecond=0)
    elif seconds >= 1800:
        bucket = dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)
    else:
        bucket = dt.replace(second=0, microsecond=0)
    return {"quote_timestamp": dt.isoformat(), "quote_minute": bucket.isoformat()}


def _append_jp225_candle(quote: Dict[str, Any], interval_meta: Dict[str, Any], *, open_price: float = 0.0, day_high: float = 0.0, day_low: float = 0.0) -> List[Dict[str, Any]]:
    """Keep a tiny same-source candle cache from live scanner quotes."""
    cache_key = str(interval_meta["tv_interval"])
    cache = _jp225_candles_cache.setdefault(cache_key, {"candles": [], "source": ""})
    price = float(quote["price"])
    bucket = _interval_quote_bucket(quote["quote_timestamp"], interval_meta)
    candle = {
        "t": bucket["quote_minute"],
        "quote_timestamp": quote["quote_timestamp"],
        "quote_minute": bucket["quote_minute"],
        "o": round(float(open_price or price), 2),
        "h": round(float(day_high or price), 2),
        "l": round(float(day_low or price), 2),
        "c": round(price, 2),
    }
    candles = cache.get("candles") or []
    if candles and candles[-1].get("quote_minute") == candle["quote_minute"]:
        prev = candles[-1]
        candle["o"] = prev.get("o", candle["o"])
        candle["h"] = max(float(prev.get("h", candle["h"])), candle["h"])
        candle["l"] = min(float(prev.get("l", candle["l"])), candle["l"])
        candles[-1] = candle
    else:
        candles.append(candle)
    cache["candles"] = candles[-60:]
    cache["ts"] = datetime.now(timezone.utc).timestamp()
    cache["quote_timestamp"] = quote["quote_timestamp"]
    cache["source"] = quote["source"]
    return cache["candles"]


def _merge_quote_into_jp225_bars(candles: List[Dict[str, Any]], quote: Dict[str, Any], interval_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Align the latest quote with real TradingView bars without inventing history."""
    if not candles:
        return _append_jp225_candle(quote, interval_meta)

    candles = [dict(c) for c in candles[-60:]]
    price = float(quote["price"])
    bucket = _interval_quote_bucket(quote["quote_timestamp"], interval_meta)
    quote_minute = bucket["quote_minute"]
    if candles[-1].get("quote_minute") == quote_minute:
        last = candles[-1]
        last["quote_timestamp"] = quote["quote_timestamp"]
        last["h"] = round(max(float(last.get("h", price)), price), 2)
        last["l"] = round(min(float(last.get("l", price)), price), 2)
        last["c"] = round(price, 2)
    else:
        candles.append({
            "t": quote_minute,
            "quote_timestamp": quote["quote_timestamp"],
            "quote_minute": quote_minute,
            "o": round(price, 2),
            "h": round(price, 2),
            "l": round(price, 2),
            "c": round(price, 2),
        })

    cache_key = str(interval_meta["tv_interval"])
    cache = _jp225_candles_cache.setdefault(cache_key, {"candles": [], "source": ""})
    cache["candles"] = candles[-60:]
    cache["ts"] = datetime.now(timezone.utc).timestamp()
    cache["quote_timestamp"] = quote["quote_timestamp"]
    cache["source"] = quote["source"]
    return cache["candles"]


def _get_jp225_bars_cached(symbol: str, interval_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    now_ts = datetime.now(timezone.utc).timestamp()
    cache_key = str(interval_meta["tv_interval"])
    cache = _jp225_candles_cache.setdefault(cache_key, {"candles": [], "source": ""})
    cached = cache.get("candles") or []
    # Fetch full OHLC history at most once per minute. The latest in-progress
    # candle is still refreshed every 2s by merging in the live quote.
    if cached and cache.get("source") == "TradingView" and now_ts - float(cache.get("bars_ts") or 0) < 55:
        return list(cached[-int(interval_meta["count"]):])
    bars = _tradingview_client.fetch_bars(symbol, interval=str(interval_meta["tv_interval"]), count=int(interval_meta["count"]))
    if bars:
        cache["candles"] = bars[-int(interval_meta["count"]):]
        cache["bars_ts"] = now_ts
        cache["quote_timestamp"] = bars[-1].get("quote_timestamp", "")
        cache["source"] = "TradingView"
    return bars[-int(interval_meta["count"]):] if bars else list(cached[-int(interval_meta["count"]):])


def _fetch_jp225_context_articles() -> List[Dict[str, Any]]:
    import urllib.parse
    import xml.etree.ElementTree as ET

    now_ts = datetime.now(timezone.utc).timestamp()
    if _jp225_context_cache.get("articles") and now_ts - float(_jp225_context_cache.get("ts") or 0) < 600:
        return list(_jp225_context_cache["articles"])

    query = "Nikkei 225 today rise oil US Iran talks Japan stocks"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    articles: List[Dict[str, Any]] = []
    try:
        response = requests.get(url, headers={"User-Agent": "GeoClaw/1.0"}, timeout=8)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for item in root.findall("./channel/item")[:8]:
            title = str(item.findtext("title") or "").strip()
            if not title:
                continue
            articles.append({
                "title": title,
                "source": str(item.findtext("source") or "Google News").strip(),
                "url": str(item.findtext("link") or "").strip(),
                "published_at": str(item.findtext("pubDate") or "").strip(),
            })
    except Exception:
        articles = []

    _jp225_context_cache["articles"] = articles
    _jp225_context_cache["ts"] = now_ts
    return articles


def _build_jp225_market_context(quote: Dict[str, Any]) -> Dict[str, Any]:
    articles = _fetch_jp225_context_articles()
    text = " ".join(str(a.get("title") or "") for a in articles).lower()
    change_pct = float(quote.get("change_pct") or 0.0)

    drivers: List[Dict[str, Any]] = []
    if any(term in text for term in ("iran", "oil", "ceasefire", "talks")):
        drivers.append({
            "label": "Geopolitical relief / oil easing",
            "impact": "bullish",
            "score": 85,
            "why": "Today’s news flow links Asia equity strength with hopes for renewed US-Iran talks and lower oil risk, which is supportive for import-heavy Japan.",
        })
    if any(term in text for term in ("global shares", "stocks rise", "asian markets", "kospi", "gain")):
        drivers.append({
            "label": "Regional risk-on tape",
            "impact": "bullish",
            "score": 72,
            "why": "Asia/global equity headlines are broadly positive, so JP225 is moving with the wider risk-on session rather than as an isolated signal.",
        })
    if any(term in text for term in ("yen", "dollar", "exporter", "usd")):
        drivers.append({
            "label": "JPY/exporter sensitivity",
            "impact": "watch",
            "score": 55,
            "why": "JPY direction matters for Japanese exporters; a softer yen usually helps index sentiment, while a sharp yen rally can pressure it.",
        })
    if not drivers:
        drivers.append({
            "label": "Confirmed price momentum",
            "impact": "bullish" if change_pct > 0 else ("bearish" if change_pct < 0 else "neutral"),
            "score": min(90, max(20, int(abs(change_pct) * 35))),
            "why": "The same-source FOREXCOM quote is positive, but the news driver feed did not return a clean single explanation yet.",
        })

    sensitivity = [
        {
            "factor": "US-Iran / oil shock risk",
            "score": 85 if any(term in text for term in ("iran", "oil", "talks")) else 55,
            "current_signal": "supportive" if any(term in text for term in ("talks", "oil falls", "oil prices ease")) else "watch",
            "why": "Lower oil/geopolitical risk tends to support Japan; renewed escalation would be bearish.",
        },
        {
            "factor": "JPY strength",
            "score": -70,
            "current_signal": "risk if yen rallies",
            "why": "A stronger yen usually pressures exporter-heavy Japanese equities.",
        },
        {
            "factor": "Global risk appetite",
            "score": 65 if any(term in text for term in ("global shares", "stocks rise", "asian markets", "gain")) else 45,
            "current_signal": "supportive",
            "why": "JP225 often follows broad Asia/US equity risk sentiment.",
        },
        {
            "factor": "US rates / BOJ hawkishness",
            "score": -45,
            "current_signal": "macro watch",
            "why": "Rising discount rates or hawkish policy can pressure valuation-sensitive equities.",
        },
        {
            "factor": "Tech / semiconductor cycle",
            "score": 50,
            "current_signal": "watch",
            "why": "Japanese index leadership is sensitive to global tech and chip appetite.",
        },
    ]

    summary = (
        f"JP225 is positive on the same-source FOREXCOM feed (+{change_pct:.2f}%). "
        "The cleanest news-backed explanation is risk-on Asia sentiment tied to US-Iran talk hopes and easier oil-risk pressure."
        if change_pct >= 0
        else f"JP225 is negative on the same-source FOREXCOM feed ({change_pct:.2f}%). Watch whether news drivers confirm the move."
    )

    return {
        "title": "Why JP225 is moving today",
        "summary": summary,
        "drivers": drivers[:3],
        "articles": articles[:5],
        "sensitivity": sensitivity,
        "disclaimer": "Market context only — not investment advice. Use confirmed source, timeframe, risk, and your own plan before trading.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _jp225_json_payload(quote: Dict[str, Any], candles: List[Dict[str, Any]], *, interval_meta: Dict[str, Any], open_price: float, day_high: float, day_low: float, prev_close: float, is_proxy: bool, source_status: str = "", fallback_reason: str = "") -> JSONResponse:
    payload = {
        "symbol": "JP225",
        "name": quote["name"],
        "source": quote["source"],
        "source_symbol": quote["source_symbol"],
        "comparison_symbol": quote["comparison_symbol"],
        "session": quote["session"],
        "market_type": quote["market_type"],
        "price_basis": quote["price_basis"],
        "change_basis": quote["change_basis"],
        "quote_timestamp": quote["quote_timestamp"],
        "quote_minute": quote["quote_minute"],
        "quote_age_seconds": quote["quote_age_seconds"],
        "stale_after_seconds": quote["stale_after_seconds"],
        "is_stale": quote["is_stale"],
        "freshness": quote["freshness"],
        "is_proxy": is_proxy,
        "source_status": source_status,
        "fallback_reason": fallback_reason,
        "price": round(quote["price"], 2),
        "last": round(quote["last"], 2),
        "bid": quote["bid"],
        "ask": quote["ask"],
        "change": round(quote["change"], 2),
        "change_pct": quote["change_pct"],
        "prev_close": round(prev_close, 2),
        "open": round(open_price, 2),
        "day_high": round(day_high, 2),
        "day_low": round(day_low, 2),
        "direction": quote["direction"],
        "candles": candles[-60:],
        "chart_basis": {
            "source": quote["source"],
            "source_symbol": quote["source_symbol"],
            "interval": interval_meta["label"],
            "bars": len(candles[-60:]),
            "note": f"Same-source TradingView FOREXCOM {interval_meta['label']} OHLC bars.",
        },
        "market_context": _build_jp225_market_context(quote),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(payload)

@app.get("/api/live/jp225")
def api_live_jp225(interval: str = "1"):
    """Fetch the canonical dashboard JP225 feed on a selected candle basis."""
    try:
        import time
        interval_meta = _normalise_jp225_interval(interval)

        if _CANONICAL_LIVE_SOURCE in {"tradingview", "tv", "forexcom"}:
            tv_quote = _tradingview_client.fetch_quote(str(_JP225_LIVE_SOURCE.get("provider_symbol") or "TVC:NI225"))
            if tv_quote:
                quote = normalize_quote(
                    "JP225",
                    tv_quote["price"],
                    tv_quote["quote_timestamp"],
                    previous_close=tv_quote.get("previous_close"),
                    bid=tv_quote.get("bid"),
                    ask=tv_quote.get("ask"),
                    last=tv_quote.get("price"),
                    stale_after_seconds=int(_JP225_LIVE_SOURCE["stale_after_seconds"]),
                )
                candles = _get_jp225_bars_cached(str(_JP225_LIVE_SOURCE.get("provider_symbol") or "FOREXCOM:JP225"), interval_meta)
                candles = _merge_quote_into_jp225_bars(candles, quote, interval_meta)
                return _jp225_json_payload(
                    quote,
                    candles,
                    interval_meta=interval_meta,
                    open_price=float(tv_quote.get("open") or 0.0),
                    day_high=float(tv_quote.get("day_high") or quote["price"]),
                    day_low=float(tv_quote.get("day_low") or quote["price"]),
                    prev_close=float(tv_quote.get("previous_close") or 0.0),
                    is_proxy=False,
                    source_status=str(tv_quote.get("market_session") or tv_quote.get("update_mode") or ""),
                )

        import yfinance as yf
        yahoo_source = dict(CANONICAL_INSTRUMENTS["JP225_YAHOO"])
        ticker = yf.Ticker(yahoo_source["source_symbol"])
        info = ticker.fast_info
        prev_close = float(info.previous_close or 0)
        open_price = float(info.open or 0)
        # Fallback candles use their own cache key because the TradingView path
        # stores one cache bucket per selected dashboard interval.
        now_ts = time.time()
        fallback_key = f"yahoo:{interval_meta['tv_interval']}"
        fallback_cache = _jp225_candles_cache.setdefault(fallback_key, {"candles": [], "ts": 0, "quote_timestamp": ""})
        yf_interval = "1m" if interval_meta["label"] == "1m" else ("30m" if interval_meta["label"] == "30m" else "60m")
        if now_ts - float(fallback_cache.get("ts") or 0) > 30:
            hist = ticker.history(period="1d", interval=yf_interval)
            candles = []
            for ts, row in hist.iterrows():
                quote_time = normalize_candle_timestamp(ts)
                candles.append({
                    "t": ts.isoformat(),
                    "quote_timestamp": quote_time["quote_timestamp"],
                    "quote_minute": quote_time["quote_minute"],
                    "o": round(float(row["Open"]), 2),
                    "h": round(float(row["High"]), 2),
                    "l": round(float(row["Low"]), 2),
                    "c": round(float(row["Close"]), 2),
                })
            fallback_cache["candles"] = candles
            fallback_cache["ts"] = now_ts
            fallback_cache["quote_timestamp"] = candles[-1].get("quote_timestamp", "") if candles else ""
        candles = fallback_cache.get("candles") or []
        latest_candle = candles[-1] if candles else {}
        price = float(latest_candle.get("c") or info.last_price or 0)
        quote = normalize_quote(
            "JP225_YAHOO",
            price,
            latest_candle.get("quote_timestamp"),
            previous_close=prev_close,
            stale_after_seconds=int(_JP225_LIVE_SOURCE["stale_after_seconds"]),
        )
        day_high = max((c["h"] for c in candles), default=price)
        day_low = min((c["l"] for c in candles), default=price)
        return _jp225_json_payload(
            quote,
            candles,
            interval_meta=interval_meta,
            open_price=open_price,
            day_high=day_high,
            day_low=day_low,
            prev_close=prev_close,
            is_proxy=True,
            fallback_reason="TradingView quote unavailable" if _CANONICAL_LIVE_SOURCE in {"tradingview", "tv", "forexcom"} else "",
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Agentic intelligence endpoints
# ---------------------------------------------------------------------------

@app.post("/api/agent/run")
async def api_agent_run():
    """Trigger a background agent run."""
    try:
        import threading
        from agent_brain import run_agent_loop
        def _run():
            try:
                run_agent_loop()
            except Exception:
                pass
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return JSONResponse({"status": "ok", "message": "Agent run started"})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/agent/reactive/status")
def api_reactive_status():
    try:
        from services.reactive_agent import get_reactive_agent
        agent = get_reactive_agent()
        return JSONResponse({"status": "ok", **agent.status()})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/agent/llm/status")
def api_llm_status():
    try:
        from services.llm_router import get_status
        return JSONResponse({"status": "ok", **get_status()})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/events/live")
def api_events_live(since: float = 0):
    try:
        from services.event_bus import get_bus
        bus = get_bus()
        events = bus.get_recent(since_timestamp=float(since or 0))
        return JSONResponse({"status": "ok", "events": events, "count": len(events)})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/theses/confidence")
def api_theses_confidence():
    try:
        from services.db_helpers import query
        rows = query(
            """
            SELECT thesis_key, confidence, terminal_risk, status, watchlist_suggestion
            FROM agent_theses
            WHERE COALESCE(status, '') NOT IN ('superseded', 'expired')
            ORDER BY confidence DESC
            LIMIT 20
            """
        )
        theses = []
        for r in rows:
            d = dict(r)
            theses.append({
                "thesis_key": str(d.get("thesis_key", ""))[:200],
                "confidence": float(d.get("confidence") or 0),
                "terminal_risk": str(d.get("terminal_risk", "")),
                "status": str(d.get("status", "")),
                "watchlist": str(d.get("watchlist_suggestion", "")),
            })
        return JSONResponse({"status": "ok", "theses": theses, "count": len(theses)})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/predictions/board")
def api_predictions_board():
    try:
        from services.db_helpers import query
        rows = query(
            """
            SELECT thesis_key, predicted_direction, symbol, price_at_prediction,
                   price_at_check, actual_change_pct, outcome, outcome_note, checked_at
            FROM thesis_predictions
            ORDER BY id DESC
            LIMIT 50
            """
        )
        predictions = [dict(r) for r in rows]
        # Compute accuracy stats
        closed = [p for p in predictions if str(p.get("outcome") or "") in {"verified", "refuted"}]
        verified = sum(1 for p in closed if p.get("outcome") == "verified")
        refuted = sum(1 for p in closed if p.get("outcome") == "refuted")
        pending = sum(1 for p in predictions if str(p.get("outcome") or "") not in {"verified", "refuted", "neutral"})
        accuracy = (verified / max(verified + refuted, 1)) * 100
        return JSONResponse({
            "status": "ok",
            "predictions": predictions,
            "stats": {
                "verified": verified,
                "refuted": refuted,
                "pending": pending,
                "accuracy_pct": round(accuracy, 1),
                "total_closed": verified + refuted,
            },
        })
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/agent/investigations")
def api_investigations():
    try:
        from services.reactive_agent import get_reactive_agent
        agent = get_reactive_agent()
        investigations = agent.get_investigations(limit=30)
        return JSONResponse({"status": "ok", "investigations": investigations, "count": len(investigations)})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok", "service": "geoclaw-dashboard"}


if __name__ == "__main__":
    import uvicorn

    _port = int(os.environ.get("PORT", "8001"))
    uvicorn.run("dashboard_api:app", host="0.0.0.0", port=_port, reload=False)
