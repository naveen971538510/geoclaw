"""
JP225 Prediction & Research Engine
====================================
Makes specific price predictions, tracks accuracy, and does deep research.

Capabilities:
  1. predict()      — generate 1h/4h/24h price direction + target + confidence
  2. research()     — deep multi-source research on any JP225 topic
  3. check_open()   — resolve open predictions against current price
  4. accuracy()     — win rate, calibration, streak stats
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from config import DB_PATH
from services.logging_service import get_logger
from services.llm_router import chat, extract_message_content

logger = get_logger("jp225_predictor")

# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jp225_predictions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                horizon_label TEXT    NOT NULL,
                horizon_hours REAL    NOT NULL,
                direction     TEXT    NOT NULL,
                price_target  REAL,
                price_at_pred REAL    NOT NULL,
                confidence    INTEGER NOT NULL,
                reasoning     TEXT,
                key_driver    TEXT,
                risk_factor   TEXT,
                factors_json  TEXT,
                created_at    TEXT    NOT NULL,
                resolve_after TEXT    NOT NULL,
                resolved_at   TEXT,
                price_at_res  REAL,
                outcome       TEXT,
                pnl_pct       REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jp225_research (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                query       TEXT    NOT NULL,
                answer      TEXT    NOT NULL,
                sources     TEXT,
                confidence  INTEGER,
                created_at  TEXT    NOT NULL
            )
        """)


# ─────────────────────────────────────────────
# Prediction engine
# ─────────────────────────────────────────────

_PREDICTION_PROMPT = """You are a quantitative analyst making a specific price prediction for the Nikkei 225 CFD (^N225 / FOREXCOM:JP225).

Current price: {price}
Today's change: {change_pct:+.2f}%

Factor signals (each scored -100 bearish → +100 bullish for JP225):
{factors}

Composite neural score: {composite:+.1f}

Recent headlines (last 2 hours):
{headlines}

Make a SPECIFIC prediction for each time horizon. Be precise — give exact price targets.

Return a JSON object:
{{
  "predictions": [
    {{
      "horizon_label": "1h",
      "horizon_hours": 1,
      "direction": "LONG" | "SHORT" | "FLAT",
      "price_target": <exact number>,
      "confidence": <50-95>,
      "reasoning": "<2 sentences — specific to current factors>",
      "key_driver": "<the single factor driving this prediction>",
      "risk_factor": "<what would invalidate this prediction>"
    }},
    {{
      "horizon_label": "4h",
      "horizon_hours": 4,
      "direction": "LONG" | "SHORT" | "FLAT",
      "price_target": <exact number>,
      "confidence": <50-90>,
      "reasoning": "<2 sentences>",
      "key_driver": "<factor>",
      "risk_factor": "<invalidation>"
    }},
    {{
      "horizon_label": "24h",
      "horizon_hours": 24,
      "direction": "LONG" | "SHORT" | "FLAT",
      "price_target": <exact number>,
      "confidence": <45-80>,
      "reasoning": "<2 sentences>",
      "key_driver": "<factor>",
      "risk_factor": "<invalidation>"
    }}
  ],
  "overall_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "session_note": "<one line about current Tokyo/Asia session context>"
}}

Only respond with the JSON. No markdown."""

_RESEARCH_PROMPT = """You are a senior research analyst specialising in Japan equities and the Nikkei 225 CFD.

Research question: {query}

Current JP225 context:
- Price: {price}
- Today's change: {change_pct:+.2f}%
- Neural schema bias: {bias} ({confidence}%)

Recent news and factor signals:
{context}

Provide a deep, specific research answer. Include:
1. Direct answer to the question
2. Key evidence (cite specific factors/news)
3. What to watch for next (specific catalysts or levels)
4. A confidence-weighted conclusion

Return JSON:
{{
  "answer": "<3-5 paragraphs of genuine research>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "catalysts_to_watch": ["<catalyst>", "<catalyst>"],
  "conclusion": "<one definitive sentence>",
  "confidence": <0-100>,
  "sources": ["<source/factor used>"]
}}

Only respond with JSON. No markdown."""


def _call_llm(prompt: str, max_tokens: int = 800) -> Dict[str, Any]:
    """Call the shared router and return message content plus provider metadata."""
    try:
        resp = chat(
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
            temperature=0.15,
            max_tokens=max_tokens,
        )
        return {
            "content": extract_message_content(resp).strip(),
            "provider": str(resp.get("_provider") or "router"),
            "error": "",
        }
    except Exception as e:
        logger.warning("Prediction/research LLM call failed via router: %s", e)
        return {"content": None, "provider": None, "error": str(e)}


def _parse_json(raw: str) -> Optional[Dict]:
    if not raw:
        return None
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = "\n".join(text.split("\n")[:-1])
        return json.loads(text)
    except Exception:
        return None


def predict(
    price: float,
    change_pct: float,
    factors: List[Dict],
    composite: float,
    headlines: List[str],
    bias: str = "NEUTRAL",
    neural_confidence: int = 50,
) -> Dict[str, Any]:
    """Generate 1h/4h/24h predictions and store them."""
    _ensure_tables()

    factor_lines = "\n".join(
        f"  {f['label']:20s} {f.get('score',0):+4d}  ({f.get('direction','?')})  change: {f.get('change_pct') or 0:+.2f}%"
        for f in factors
    )
    headline_text = "\n".join(f"  - {h}" for h in headlines[:10]) or "  (none)"

    prompt = _PREDICTION_PROMPT.format(
        price=price,
        change_pct=change_pct,
        factors=factor_lines,
        composite=composite,
        headlines=headline_text,
    )

    llm_result = _call_llm(prompt, max_tokens=900)
    raw = llm_result.get("content")
    parsed = _parse_json(raw) if raw else None
    llm_provider = str(llm_result.get("provider") or "")
    llm_fallback_reason = str(llm_result.get("error") or "")

    now = datetime.now(timezone.utc)
    predictions = []

    if parsed and parsed.get("predictions"):
        for p in parsed["predictions"]:
            horizon_h = float(p.get("horizon_hours", 1))
            resolve_after = (now + timedelta(hours=horizon_h)).isoformat()
            row = {
                "horizon_label": str(p.get("horizon_label", "1h")),
                "horizon_hours": horizon_h,
                "direction": str(p.get("direction", "FLAT")).upper(),
                "price_target": float(p.get("price_target") or price),
                "price_at_pred": price,
                "confidence": int(p.get("confidence") or 50),
                "reasoning": str(p.get("reasoning") or ""),
                "key_driver": str(p.get("key_driver") or ""),
                "risk_factor": str(p.get("risk_factor") or ""),
                "factors_json": json.dumps(factors),
                "created_at": now.isoformat(),
                "resolve_after": resolve_after,
            }
            with _conn() as conn:
                conn.execute("""
                    INSERT INTO jp225_predictions
                    (horizon_label,horizon_hours,direction,price_target,price_at_pred,
                     confidence,reasoning,key_driver,risk_factor,factors_json,
                     created_at,resolve_after)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    row["horizon_label"], row["horizon_hours"], row["direction"],
                    row["price_target"], row["price_at_pred"], row["confidence"],
                    row["reasoning"], row["key_driver"], row["risk_factor"],
                    row["factors_json"], row["created_at"], row["resolve_after"],
                ))
                row["id"] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            predictions.append(row)
    else:
        # Fallback quant-only prediction
        direction = "LONG" if composite > 15 else ("SHORT" if composite < -15 else "FLAT")
        move_1h = price * (composite / 10000)
        row = {
            "horizon_label": "1h", "horizon_hours": 1,
            "direction": direction,
            "price_target": round(price + move_1h, 0),
            "price_at_pred": price,
            "confidence": min(75, max(50, int(abs(composite)))),
            "reasoning": f"Quant fallback. Composite score: {composite:+.1f}. LLM unavailable.",
            "key_driver": max(factors, key=lambda f: abs(f.get("score",0)), default={}).get("label","—"),
            "risk_factor": "LLM synthesis unavailable",
            "factors_json": json.dumps(factors),
            "created_at": now.isoformat(),
            "resolve_after": (now + timedelta(hours=1)).isoformat(),
        }
        predictions.append(row)

    return {
        "predictions": predictions,
        "overall_bias": parsed.get("overall_bias", bias) if parsed else bias,
        "session_note": parsed.get("session_note", "") if parsed else "",
        "llm_provider": llm_provider or "quant_only",
        "llm_fallback_reason": "" if parsed else (llm_fallback_reason or "shared_llm_router_failed"),
        "generated_at": now.isoformat(),
    }


# ─────────────────────────────────────────────
# Research engine
# ─────────────────────────────────────────────

def research(
    query: str,
    price: float,
    change_pct: float,
    bias: str,
    confidence: int,
    factors: List[Dict],
    headlines: List[str],
) -> Dict[str, Any]:
    """Deep research on any JP225-related question."""
    _ensure_tables()

    factor_context = "\n".join(
        f"  {f['label']}: {f.get('direction','?')} (score {f.get('score',0):+d}, change {f.get('change_pct') or 0:+.2f}%)"
        for f in factors
    )
    news_context = "\n".join(f"  - {h}" for h in headlines[:8]) or "  (none)"
    context = f"FACTORS:\n{factor_context}\n\nNEWS:\n{news_context}"

    prompt = _RESEARCH_PROMPT.format(
        query=query,
        price=price,
        change_pct=change_pct,
        bias=bias,
        confidence=confidence,
        context=context,
    )

    llm_result = _call_llm(prompt, max_tokens=1000)
    raw = llm_result.get("content")
    parsed = _parse_json(raw) if raw else None
    llm_provider = str(llm_result.get("provider") or "")
    llm_fallback_reason = str(llm_result.get("error") or "")
    now = datetime.now(timezone.utc).isoformat()

    if parsed:
        result = {
            "query": query,
            "answer": str(parsed.get("answer", "")),
            "key_points": list(parsed.get("key_points", [])),
            "catalysts_to_watch": list(parsed.get("catalysts_to_watch", [])),
            "conclusion": str(parsed.get("conclusion", "")),
            "confidence": int(parsed.get("confidence", 50)),
            "sources": list(parsed.get("sources", [])),
            "llm_provider": llm_provider or "router",
            "llm_fallback_reason": "",
            "created_at": now,
        }
    else:
        result = {
            "query": query,
            "answer": "Research engine unavailable — the shared LLM router could not complete the request.",
            "key_points": [],
            "catalysts_to_watch": [],
            "conclusion": "Unable to research at this time.",
            "confidence": 0,
            "sources": [],
            "llm_provider": "quant_only",
            "llm_fallback_reason": llm_fallback_reason or "shared_llm_router_failed",
            "created_at": now,
        }

    # Store
    try:
        with _conn() as conn:
            conn.execute("""
                INSERT INTO jp225_research (query, answer, sources, confidence, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (query, result["answer"],
                  json.dumps(result["sources"]), result["confidence"], now))
    except Exception:
        pass

    return result


# ─────────────────────────────────────────────
# Prediction resolution + accuracy
# ─────────────────────────────────────────────

def check_and_resolve(current_price: float) -> int:
    """Resolve any open predictions whose resolve_after has passed. Returns count resolved."""
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    resolved = 0
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT id, direction, price_at_pred, price_target
                FROM jp225_predictions
                WHERE resolved_at IS NULL AND resolve_after <= ?
            """, (now,)).fetchall()

            for row in rows:
                pred_price = float(row["price_at_pred"] or 0)
                direction = str(row["direction"] or "FLAT").upper()
                actual_chg = ((current_price - pred_price) / pred_price * 100) if pred_price else 0

                if direction == "LONG":
                    outcome = "WIN" if actual_chg > 0.1 else ("LOSS" if actual_chg < -0.1 else "FLAT")
                elif direction == "SHORT":
                    outcome = "WIN" if actual_chg < -0.1 else ("LOSS" if actual_chg > 0.1 else "FLAT")
                else:
                    outcome = "FLAT"

                pnl = actual_chg if direction == "LONG" else (-actual_chg if direction == "SHORT" else 0)

                conn.execute("""
                    UPDATE jp225_predictions
                    SET resolved_at=?, price_at_res=?, outcome=?, pnl_pct=?
                    WHERE id=?
                """, (now, current_price, outcome, round(pnl, 3), row["id"]))
                resolved += 1

    except Exception as exc:
        logger.error("Resolution error: %s", exc)
    return resolved


def get_accuracy() -> Dict[str, Any]:
    """Return prediction win rate, calibration, streak."""
    _ensure_tables()
    try:
        with _conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM jp225_predictions WHERE resolved_at IS NOT NULL"
            ).fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM jp225_predictions WHERE outcome='WIN'"
            ).fetchone()[0]
            recent = conn.execute("""
                SELECT horizon_label, direction, price_at_pred, price_target,
                       price_at_res, outcome, pnl_pct, confidence, reasoning,
                       key_driver, created_at, resolved_at
                FROM jp225_predictions
                WHERE resolved_at IS NOT NULL
                ORDER BY resolved_at DESC LIMIT 10
            """).fetchall()
            open_preds = conn.execute("""
                SELECT id, horizon_label, direction, price_target,
                       price_at_pred, confidence, reasoning, key_driver,
                       created_at, resolve_after
                FROM jp225_predictions
                WHERE resolved_at IS NULL
                ORDER BY created_at DESC LIMIT 6
            """).fetchall()

        return {
            "total_predictions": total,
            "wins": wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "recent_resolved": [dict(r) for r in recent],
            "open_predictions": [dict(r) for r in open_preds],
        }
    except Exception as exc:
        return {"error": str(exc), "total_predictions": 0, "wins": 0, "win_rate": 0,
                "recent_resolved": [], "open_predictions": []}


def get_open_predictions() -> List[Dict]:
    _ensure_tables()
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT id, horizon_label, direction, price_target, price_at_pred,
                       confidence, reasoning, key_driver, risk_factor,
                       created_at, resolve_after
                FROM jp225_predictions
                WHERE resolved_at IS NULL
                ORDER BY created_at DESC LIMIT 9
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
