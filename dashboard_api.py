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

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
try:
    import config  # noqa: F401
except Exception:
    pass

from intelligence.db import ensure_intelligence_schema, get_database_url, query_all
from intelligence.groq_briefing import build_signals_context, generate_dashboard_briefing

app = FastAPI(title="GeoClaw Dashboard API", version="1.0.0")

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


@app.get("/api/signals")
def api_signals():
    try:
        _require_db()
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = query_all(
            """
            SELECT id, signal_name, value, direction, confidence, explanation_plain_english, ts
            FROM geoclaw_signals
            WHERE ts >= %s
            ORDER BY confidence DESC, ts DESC
            LIMIT 500;
            """,
            (since,),
        )
        for r in rows:
            if r.get("ts"):
                r["ts"] = r["ts"].isoformat()
        return JSONResponse({"status": "ok", "signals": rows})
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
            if d == "BULLISH":
                delta = 1.0
            elif d == "BEARISH":
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


@app.get("/health")
def health():
    return {"status": "ok", "service": "geoclaw-dashboard"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("dashboard_api:app", host="0.0.0.0", port=8001, reload=False)
