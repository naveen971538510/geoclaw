"""
Persistent thesis tracker fed only by agent_brain current-run snapshots.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import DB_PATH
from services.db_helpers import get_conn


TABLE_NAME = "autonomous_theses"
THESIS_KEY = "market-signal-regime"
ACTIVE_STATUSES = ("open", "watching", "confirmed")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _signal_confidence(signal: Dict[str, Any]) -> float:
    try:
        return float(signal.get("confidence") or 0.0)
    except Exception:
        return 0.0


def _signal_ts(signal: Dict[str, Any]) -> float:
    parsed = _parse_dt(signal.get("ts"))
    return parsed.timestamp() if parsed else 0.0


def _dedupe_signals(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    for signal in signals:
        name = str(signal.get("signal_name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        current = by_name.get(key)
        if current is None or _signal_ts(signal) >= _signal_ts(current):
            by_name[key] = signal
    return sorted(
        by_name.values(),
        key=lambda item: (_signal_confidence(item), _signal_ts(item)),
        reverse=True,
    )


def _signal_totals(signals: List[Dict[str, Any]]) -> tuple[float, float]:
    buy_total = sum(
        _signal_confidence(signal)
        for signal in signals
        if str(signal.get("direction") or "").upper() == "BUY"
    )
    sell_total = sum(
        _signal_confidence(signal)
        for signal in signals
        if str(signal.get("direction") or "").upper() == "SELL"
    )
    return buy_total, sell_total


def _latest_price_timestamp(prices: List[Dict[str, Any]]) -> str:
    latest_ts = 0.0
    for price in prices:
        parsed = _parse_dt(price.get("ts"))
        if parsed:
            latest_ts = max(latest_ts, parsed.timestamp())
    if not latest_ts:
        return ""
    return datetime.fromtimestamp(latest_ts, tz=timezone.utc).isoformat()


def _direction_from_bias(bias: str, buy_total: float, sell_total: float) -> str:
    clean = str(bias or "").upper()
    if clean == "BULLISH":
        return "bullish"
    if clean == "BEARISH":
        return "bearish"
    total = buy_total + sell_total
    if total > 0 and buy_total / total > 0.6:
        return "bullish"
    if total > 0 and sell_total / total > 0.6:
        return "bearish"
    return "neutral"


def _confidence_from_evidence(direction: str, buy_total: float, sell_total: float, signal_count: int) -> float:
    if signal_count <= 0:
        return 0.25
    total = max(0.0, buy_total + sell_total)
    if total <= 0:
        return 0.30
    dominant = max(buy_total, sell_total)
    dominance = dominant / total
    average_strength = min(1.0, dominant / max(1.0, signal_count * 100.0))
    confidence = 0.30 + (0.40 * dominance) + (0.20 * average_strength)
    if direction == "neutral":
        confidence = min(confidence, 0.55)
    return max(0.05, min(0.85, confidence))


def _ensure_schema(cur) -> None:
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            thesis_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_updated_at TEXT NOT NULL,
            last_change_reason TEXT NOT NULL
        )
        """
    )
    cur.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_status_updated
        ON {TABLE_NAME}(status, last_updated_at)
        """
    )
    cur.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_status_confidence
        ON {TABLE_NAME}(status, confidence DESC, last_updated_at)
        """
    )


def _build_evidence(run_state: Dict[str, Any]) -> Dict[str, Any]:
    signals = _dedupe_signals(run_state.get("signals_snapshot", []) or [])
    price_result = run_state.get("price_data", {}) or {}
    macro_result = run_state.get("macro_metrics", {}) or {}
    bias_result = run_state.get("market_bias", {}) or {}
    signal_freshness = run_state.get("signal_freshness") or {}
    macro_freshness = macro_result.get("freshness", {}) or {}
    prices = price_result.get("prices", []) or []
    buy_total, sell_total = _signal_totals(signals)
    bias = str(bias_result.get("bias") or "").upper() or "NEUTRAL"
    direction = _direction_from_bias(bias, buy_total, sell_total)
    confidence = _confidence_from_evidence(direction, buy_total, sell_total, len(signals))
    top_signals = [
        {
            "signal_name": str(signal.get("signal_name") or ""),
            "direction": str(signal.get("direction") or "").upper(),
            "confidence": round(_signal_confidence(signal), 2),
            "ts": str(signal.get("ts") or ""),
        }
        for signal in signals[:8]
    ]
    return {
        "run_id": str(run_state.get("run_id") or ""),
        "run_started_at": str(run_state.get("started_at") or ""),
        "bias": bias,
        "direction": direction,
        "confidence": round(confidence, 4),
        "signal_count": len(signals),
        "buy_confidence_total": round(buy_total, 2),
        "sell_confidence_total": round(sell_total, 2),
        "signal_freshness_status": str(signal_freshness.get("status") or "unknown"),
        "latest_signal_time": str(signal_freshness.get("latest_signal_time") or ""),
        "price_timestamp": _latest_price_timestamp(prices),
        "price_count": int(price_result.get("count") or len(prices) or 0),
        "price_refresh_status": str((price_result.get("refresh") or {}).get("status") or "unknown"),
        "macro_freshness_status": str(macro_freshness.get("status") or "unknown"),
        "macro_missing_metrics": [str(item) for item in (macro_freshness.get("missing_metrics") or [])],
        "macro_stale_metrics": [
            str(item.get("metric") or item)
            for item in (macro_freshness.get("stale_metrics") or [])
        ],
        "top_signals": top_signals,
    }


def _summary_from_evidence(evidence: Dict[str, Any]) -> str:
    signal_count = int(evidence.get("signal_count") or 0)
    buy_total = float(evidence.get("buy_confidence_total") or 0.0)
    sell_total = float(evidence.get("sell_confidence_total") or 0.0)
    direction = str(evidence.get("direction") or "neutral")
    if signal_count <= 0:
        return "No fresh directional signal set was available in the current run."
    if direction == "neutral":
        prefix = "Current-run signal totals are mixed"
    else:
        prefix = f"Current-run signal totals lean {direction}"
    macro_status = str(evidence.get("macro_freshness_status") or "unknown")
    signal_status = str(evidence.get("signal_freshness_status") or "unknown")
    return (
        f"{prefix}: BUY {buy_total:.1f} vs SELL {sell_total:.1f} "
        f"across {signal_count} deduplicated signals. "
        f"Macro freshness {macro_status}; signal freshness {signal_status}."
    )


def _status_for_new_thesis(evidence: Dict[str, Any]) -> str:
    if int(evidence.get("signal_count") or 0) <= 0:
        return "watching"
    if str(evidence.get("direction") or "neutral") == "neutral":
        return "watching"
    return "open"


def _evidence_signature(evidence: Dict[str, Any]) -> str:
    material = {
        "direction": evidence.get("direction"),
        "confidence": round(float(evidence.get("confidence") or 0.0), 2),
        "signal_count": evidence.get("signal_count"),
        "buy_confidence_total": evidence.get("buy_confidence_total"),
        "sell_confidence_total": evidence.get("sell_confidence_total"),
        "signal_freshness_status": evidence.get("signal_freshness_status"),
        "price_timestamp": evidence.get("price_timestamp"),
        "price_refresh_status": evidence.get("price_refresh_status"),
        "macro_freshness_status": evidence.get("macro_freshness_status"),
        "macro_missing_metrics": evidence.get("macro_missing_metrics"),
        "macro_stale_metrics": evidence.get("macro_stale_metrics"),
        "top_signals": [
            {
                "signal_name": item.get("signal_name"),
                "direction": item.get("direction"),
                "confidence": item.get("confidence"),
            }
            for item in (evidence.get("top_signals") or [])
        ],
    }
    return json.dumps(material, sort_keys=True)


def _load_evidence(raw: Any) -> Dict[str, Any]:
    try:
        data = json.loads(str(raw or "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _updated_lifecycle(row: Dict[str, Any], evidence: Dict[str, Any]) -> tuple[str, float, str, bool]:
    old_evidence = _load_evidence(row.get("evidence_json"))
    old_direction = str(row.get("direction") or "neutral")
    new_direction = str(evidence.get("direction") or "neutral")
    old_confidence = float(row.get("confidence") or 0.0)
    new_confidence = float(evidence.get("confidence") or 0.0)
    signal_status = str(evidence.get("signal_freshness_status") or "unknown")
    macro_status = str(evidence.get("macro_freshness_status") or "unknown")
    signal_count = int(evidence.get("signal_count") or 0)
    evidence_changed = _evidence_signature(old_evidence) != _evidence_signature(evidence)

    if signal_count <= 0 or signal_status not in {"ok", "fresh"}:
        adjusted = max(0.05, min(new_confidence, old_confidence - 0.10))
        status = "invalidated" if adjusted < 0.20 else "watching"
        return status, adjusted, f"Evidence weakened: signal freshness is {signal_status}.", True

    if macro_status in {"degraded", "unavailable"}:
        adjusted = max(0.10, min(new_confidence, old_confidence - 0.05))
        return "watching", adjusted, f"Evidence weakened: macro freshness is {macro_status}.", True

    if old_direction != new_direction and old_direction != "neutral":
        adjusted = min(new_confidence, max(0.25, old_confidence * 0.85))
        return "watching", adjusted, f"Evidence direction changed from {old_direction} to {new_direction}.", True

    if new_confidence >= old_confidence + 0.03:
        status = "confirmed" if new_confidence >= 0.72 and new_direction != "neutral" else "open"
        return status, new_confidence, "Evidence strengthened on current-run signal totals.", True

    if new_confidence <= old_confidence - 0.03:
        status = "watching" if new_confidence >= 0.20 else "invalidated"
        return status, new_confidence, "Evidence weakened on current-run signal totals.", True

    if evidence_changed:
        status = str(row.get("status") or "watching")
        if status not in {"open", "watching", "confirmed", "invalidated", "resolved"}:
            status = "watching"
        return status, new_confidence, "Evidence refreshed without a material directional change.", True

    return str(row.get("status") or "watching"), old_confidence, str(row.get("last_change_reason") or ""), False


def _public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "thesis_key": str(row.get("thesis_key") or ""),
        "title": str(row.get("title") or ""),
        "summary": str(row.get("summary") or ""),
        "status": str(row.get("status") or ""),
        "direction": str(row.get("direction") or ""),
        "confidence": round(float(row.get("confidence") or 0.0), 2),
        "first_seen_at": str(row.get("first_seen_at") or ""),
        "last_updated_at": str(row.get("last_updated_at") or ""),
        "last_change_reason": str(row.get("last_change_reason") or ""),
    }


def update_theses_from_run_state(run_state: Dict[str, Any], db_path: Any = None) -> Dict[str, Any]:
    evidence = _build_evidence(run_state)
    now = _utc_now_iso()
    title = "Current-run market signal regime"
    summary = _summary_from_evidence(evidence)
    changed_theses: List[Dict[str, Any]] = []
    current_thesis = {
        "thesis_key": THESIS_KEY,
        "title": title,
        "summary": summary,
        "status": _status_for_new_thesis(evidence),
        "direction": str(evidence.get("direction") or "neutral"),
        "confidence": round(float(evidence.get("confidence") or 0.0), 2),
        "last_change_reason": "Current-run evidence inspected.",
    }

    conn = get_conn(db_path or DB_PATH)
    cur = conn.cursor()
    _ensure_schema(cur)
    row = cur.execute(
        f"SELECT * FROM {TABLE_NAME} WHERE thesis_key = ? LIMIT 1",
        (THESIS_KEY,),
    ).fetchone()

    if row is None:
        status = _status_for_new_thesis(evidence)
        reason = "Created from current-run signal, price, and macro snapshot."
        cur.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                thesis_key, title, summary, status, direction, confidence,
                evidence_json, first_seen_at, last_updated_at, last_change_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                THESIS_KEY,
                title,
                summary,
                status,
                str(evidence.get("direction") or "neutral"),
                float(evidence.get("confidence") or 0.0),
                json.dumps(evidence, sort_keys=True),
                now,
                now,
                reason,
            ),
        )
        changed_theses.append(
            {
                "thesis_key": THESIS_KEY,
                "title": title,
                "summary": summary,
                "status": status,
                "direction": str(evidence.get("direction") or "neutral"),
                "confidence": round(float(evidence.get("confidence") or 0.0), 2),
                "last_change_reason": reason,
            }
        )
        current_thesis.update(changed_theses[-1])
    else:
        current = dict(row)
        status, confidence, reason, changed = _updated_lifecycle(current, evidence)
        current_thesis.update(
            {
                "status": status,
                "confidence": round(confidence, 2),
                "last_change_reason": reason or "No material evidence change in the current run.",
            }
        )
        if changed:
            cur.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET title = ?,
                    summary = ?,
                    status = ?,
                    direction = ?,
                    confidence = ?,
                    evidence_json = ?,
                    last_updated_at = ?,
                    last_change_reason = ?
                WHERE thesis_key = ?
                """,
                (
                    title,
                    summary,
                    status,
                    str(evidence.get("direction") or "neutral"),
                    confidence,
                    json.dumps(evidence, sort_keys=True),
                    now,
                    reason,
                    THESIS_KEY,
                ),
            )
            changed_theses.append(
                {
                    "thesis_key": THESIS_KEY,
                    "title": title,
                    "summary": summary,
                    "status": status,
                    "direction": str(evidence.get("direction") or "neutral"),
                    "confidence": round(confidence, 2),
                    "last_change_reason": reason,
                }
            )
            current_thesis.update(changed_theses[-1])

    active_count = cur.execute(
        f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE status IN (?, ?, ?)",
        ACTIVE_STATUSES,
    ).fetchone()[0]
    rows = cur.execute(
        f"""
        SELECT *
        FROM {TABLE_NAME}
        WHERE status IN (?, ?, ?)
        ORDER BY confidence DESC, last_updated_at DESC
        LIMIT 5
        """,
        ACTIVE_STATUSES,
    ).fetchall()
    top_theses = [_public_row(dict(item)) for item in rows]
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "table": TABLE_NAME,
        "storage_path": str(db_path or DB_PATH),
        "active_thesis_count": int(active_count or 0),
        "changed_thesis_count": len(changed_theses),
        "changed_theses": changed_theses,
        "current_run_theses": [current_thesis],
        "top_theses": top_theses,
    }
