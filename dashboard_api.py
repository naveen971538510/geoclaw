"""
GeoClaw dashboard API — FastAPI on port 8001.
"""

from __future__ import annotations

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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    import config  # noqa: F401
except Exception:
    pass

from intelligence.db import ensure_intelligence_schema, get_database_url, query_all
from intelligence.groq_briefing import build_signals_context, generate_dashboard_briefing
from intelligence.scenario_engine import generate_scenarios
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

PRICE_PANEL_META = {
    "BTCUSD": {"label": "BTC", "name": "Bitcoin"},
    "SPX": {"label": "SPX", "name": "S&P 500"},
    "XAUUSD": {"label": "XAUUSD", "name": "Gold"},
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
def api_prices(symbols: str = "BTCUSD,SPX,XAUUSD", points: int = 18):
    try:
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


@app.get("/api/news")
def api_news():
    try:
        _require_db()
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = query_all(
            """
            SELECT id, headline, source, url, sentiment, confidence, reason, ts
            FROM news_signals
            WHERE ts >= %s
            ORDER BY confidence DESC, ts DESC
            LIMIT 20;
            """,
            (since,),
        )
        for r in rows:
            if r.get("ts"):
                r["ts"] = r["ts"].isoformat()
        return JSONResponse({"status": "ok", "news": rows})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/scenarios")
def api_scenarios():
    try:
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


# ─────────────────────────────────────────────
# Agentic Intelligence Endpoints
# ─────────────────────────────────────────────


@app.get("/api/agent/reactive/status")
def reactive_agent_status():
    """Live status of the reactive investigation agent."""
    try:
        from services.reactive_agent import get_reactive_agent
        agent = get_reactive_agent()
        return JSONResponse({"status": "ok", **agent.get_status()})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "running": False})


@app.get("/api/agent/llm/status")
def llm_router_status():
    """Status of the multi-provider LLM router (Groq/OpenAI/Gemini)."""
    try:
        from services.llm_router import get_llm_router
        router = get_llm_router()
        return JSONResponse({"status": "ok", **router.status()})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)})


@app.get("/api/events/live")
def live_events(since: float = 0, limit: int = 50):
    """Live event feed from the EventBus — polls for new events since a timestamp."""
    try:
        from services.event_bus import get_bus
        bus = get_bus()
        if since > 0:
            events = bus.get_recent(since)
        else:
            events = bus.get_history(limit=limit)
        return JSONResponse({
            "status": "ok",
            "events": events[-limit:],
            "count": len(events),
            "server_time": datetime.now(timezone.utc).timestamp(),
        })
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "events": []})


@app.get("/api/theses/confidence")
def theses_confidence_chart():
    """Thesis confidence data formatted for visualization."""
    try:
        rows = query_all(
            """
            SELECT thesis_key, current_claim, confidence, status,
                   evidence_count, confidence_velocity, category,
                   last_update_reason, created_at, last_updated_at
            FROM agent_theses
            WHERE COALESCE(status, '') NOT IN ('superseded', 'stale')
            ORDER BY confidence DESC, evidence_count DESC
            LIMIT 20;
            """
        )
        theses = []
        for row in rows:
            r = dict(row)
            conf = float(r.get("confidence") or 0)
            velocity = float(r.get("confidence_velocity") or 0)
            theses.append({
                "thesis_key": r.get("thesis_key", ""),
                "claim": (r.get("current_claim") or r.get("thesis_key") or "")[:120],
                "confidence_pct": round(conf * 100) if conf <= 1 else round(conf),
                "velocity": round(velocity, 3),
                "trend": "rising" if velocity > 0.02 else ("falling" if velocity < -0.02 else "stable"),
                "status": r.get("status", "active"),
                "evidence_count": int(r.get("evidence_count") or 0),
                "category": r.get("category", ""),
                "last_reason": (r.get("last_update_reason") or "")[:100],
            })
        return JSONResponse({"status": "ok", "theses": theses, "count": len(theses)})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "theses": []})


@app.get("/api/predictions/board")
def predictions_board():
    """Prediction scoreboard — tracked predictions with outcomes."""
    try:
        rows = query_all(
            """
            SELECT thesis_key, predicted_direction, predicted_asset, symbol,
                   price_at_prediction, confidence_at_prediction, predicted_at,
                   outcome, outcome_note, actual_change_pct, price_at_check, checked_at
            FROM thesis_predictions
            ORDER BY predicted_at DESC
            LIMIT 30;
            """
        )
        predictions = []
        stats = {"total": 0, "verified": 0, "refuted": 0, "pending": 0}
        for row in rows:
            r = dict(row)
            outcome = str(r.get("outcome") or "pending").lower()
            stats["total"] += 1
            if outcome == "verified":
                stats["verified"] += 1
            elif outcome == "refuted":
                stats["refuted"] += 1
            else:
                stats["pending"] += 1
            predictions.append({
                "thesis_key": r.get("thesis_key", ""),
                "direction": r.get("predicted_direction", ""),
                "asset": r.get("predicted_asset") or r.get("symbol", ""),
                "entry_price": r.get("price_at_prediction"),
                "confidence": round(float(r.get("confidence_at_prediction") or 0) * 100),
                "predicted_at": str(r.get("predicted_at") or ""),
                "outcome": outcome,
                "actual_change_pct": r.get("actual_change_pct"),
                "exit_price": r.get("price_at_check"),
                "checked_at": str(r.get("checked_at") or ""),
                "note": (r.get("outcome_note") or "")[:100],
            })
        accuracy = round(stats["verified"] / max(stats["verified"] + stats["refuted"], 1) * 100)
        return JSONResponse({
            "status": "ok",
            "predictions": predictions,
            "stats": {**stats, "accuracy_pct": accuracy},
        })
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "predictions": [], "stats": {}})


@app.get("/api/agent/investigations")
def agent_investigations():
    """Recent reactive agent investigation results from the journal."""
    try:
        rows = query_all(
            """
            SELECT run_id, journal_type, summary, metrics_json, created_at
            FROM agent_journal
            WHERE journal_type IN ('research_agent', 'reactive_investigation')
            ORDER BY created_at DESC, id DESC
            LIMIT 20;
            """
        )
        investigations = []
        for row in rows:
            r = dict(row)
            metrics = {}
            try:
                metrics = json.loads(r.get("metrics_json") or "{}")
            except Exception:
                pass
            investigations.append({
                "type": r.get("journal_type", ""),
                "summary": r.get("summary", ""),
                "created_at": str(r.get("created_at") or ""),
                "articles_found": metrics.get("articles_found", 0),
                "support": metrics.get("support", 0),
                "contradict": metrics.get("contradict", 0),
                "queries": metrics.get("queries", []),
            })
        return JSONResponse({"status": "ok", "investigations": investigations, "count": len(investigations)})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "investigations": []})


@app.get("/api/portfolio/signals")
def portfolio_signals():
    """Pending position signals generated from high-confidence theses."""
    try:
        from services.portfolio_service import PortfolioService
        portfolio = PortfolioService(str(DB_PATH))
        signals = portfolio.get_pending_signals()
        total_alloc = round(sum(float(s.get("alloc_pct", 0)) for s in signals), 2)
        return JSONResponse({
            "status": "ok",
            "signals": signals,
            "count": len(signals),
            "total_alloc_pct": total_alloc,
        })
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc), "signals": []})


@app.post("/api/portfolio/signals/{signal_id}/action")
def portfolio_signal_action(signal_id: int, body: dict):
    """
    Mark a signal as actioned (approved/rejected).
    Body: {"action": "approved" | "rejected"}
    """
    try:
        import sqlite3
        action = str(body.get("action", "approved")).lower()
        if action not in ("approved", "rejected"):
            return JSONResponse({"status": "error", "error": "action must be 'approved' or 'rejected'"}, status_code=400)
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE portfolio_signals SET status=?, actioned_at=? WHERE id=?",
            (action, datetime.utcnow().isoformat(), signal_id)
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "ok", "signal_id": signal_id, "action": action})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok", "service": "geoclaw-dashboard"}


if __name__ == "__main__":
    import uvicorn

    _port = int(os.environ.get("PORT", "8001"))
    uvicorn.run("dashboard_api:app", host="0.0.0.0", port=_port, reload=False)
