import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict


ASSET_DIRECTION_MAP = {
    "war": ("risk_up", "^VIX"),
    "missile": ("risk_up", "^VIX"),
    "sanction": ("risk_up", "DX-Y.NYB"),
    "iran": ("risk_up", "CL=F"),
    "oil": ("risk_up", "CL=F"),
    "opec": ("risk_up", "CL=F"),
    "ceasefire": ("risk_down", "^VIX"),
    "rate hike": ("risk_up", "^TNX"),
    "rate cut": ("risk_down", "^TNX"),
    "recession": ("risk_up", "^VIX"),
    "inflation": ("risk_up", "^TNX"),
    "gold": ("risk_up", "GC=F"),
    "default": ("risk_up", "^VIX"),
    "china": ("risk_up", "USDCNH=X"),
    "dollar": ("risk_up", "DX-Y.NYB"),
    "fed": ("risk_up", "^TNX"),
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class PredictionTracker:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def record_prediction(self, thesis_key: str, confidence: float, run_id: int = 0) -> int:
        confidence_value = float(confidence or 0.0)
        if confidence_value < 0.65:
            return 0

        clean_key = str(thesis_key or "").strip()
        key_lower = clean_key.lower()
        direction = None
        symbol = None
        asset_name = None
        for keyword, mapping in ASSET_DIRECTION_MAP.items():
            if keyword in key_lower:
                asset_name = keyword
                direction, symbol = mapping
                break

        if not direction or not symbol:
            return 0

        current_price = None
        try:
            from services.price_feed import PriceFeed

            price_data = PriceFeed().get_price(symbol)
            current_price = (price_data or {}).get("price")
        except Exception:
            current_price = None

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.execute(
            """
            INSERT INTO thesis_predictions (
                thesis_key, predicted_direction, predicted_asset, symbol,
                price_at_prediction, confidence_at_prediction, predicted_at,
                run_id, check_after_hours
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 24)
            """,
            (
                clean_key,
                direction,
                asset_name,
                symbol,
                current_price,
                confidence_value,
                _utc_now_iso(),
                int(run_id or 0),
            ),
        )
        pred_id = int(cursor.lastrowid or 0)
        conn.commit()
        conn.close()
        return pred_id

    def check_pending_predictions(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        rows = conn.execute(
            """
            SELECT *
            FROM thesis_predictions
            WHERE COALESCE(outcome, 'pending') = 'pending'
            ORDER BY predicted_at ASC, id ASC
            LIMIT 50
            """
        ).fetchall()

        due_predictions = []
        now_dt = datetime.now(timezone.utc)
        for row in rows:
            item = dict(row)
            predicted_at = _parse_iso(item.get("predicted_at", ""))
            if predicted_at is None:
                continue
            due_after = predicted_at + timedelta(hours=int(item.get("check_after_hours", 24) or 24))
            if due_after <= now_dt:
                due_predictions.append(item)

        results = {"checked": 0, "verified": 0, "refuted": 0, "neutral": 0}

        try:
            from services.price_feed import PriceFeed

            pf = PriceFeed()
            price_available = bool(pf._available)
        except Exception:
            pf = None
            price_available = False

        now = _utc_now_iso()
        verification_threshold = 0.8

        for item in due_predictions:
            outcome = "neutral"
            current_price = None
            change_pct = 0.0
            note = ""

            if price_available and pf and item.get("symbol"):
                try:
                    price_data = pf.get_price(item["symbol"])
                    current_price = (price_data or {}).get("price")
                    change_pct = float((price_data or {}).get("change_pct", 0.0) or 0.0)
                except Exception:
                    current_price = None

            if current_price is not None and item.get("price_at_prediction"):
                try:
                    baseline = float(item["price_at_prediction"] or 0.0)
                    if baseline > 0:
                        change_pct = ((float(current_price) - baseline) / baseline) * 100.0
                except Exception:
                    pass

            if abs(change_pct) < verification_threshold:
                outcome = "neutral"
                note = f"Price moved only {change_pct:.2f}% — insufficient signal"
                results["neutral"] += 1
            elif item.get("predicted_direction") == "risk_up" and change_pct > 0:
                outcome = "verified"
                note = f"{item.get('symbol', '')} moved +{change_pct:.2f}% as predicted"
                results["verified"] += 1
            elif item.get("predicted_direction") == "risk_down" and change_pct < 0:
                outcome = "verified"
                note = f"{item.get('symbol', '')} moved {change_pct:.2f}% as predicted"
                results["verified"] += 1
            else:
                outcome = "refuted"
                note = f"{item.get('symbol', '')} moved {change_pct:.2f}% — opposite of prediction"
                results["refuted"] += 1

            conn.execute(
                """
                UPDATE thesis_predictions
                SET outcome = ?, outcome_note = ?, checked_at = ?,
                    price_at_check = ?, actual_change_pct = ?
                WHERE id = ?
                """,
                (outcome, note, now, current_price, change_pct, int(item["id"])),
            )
            results["checked"] += 1

        conn.commit()
        conn.close()
        return results

    def get_accuracy_report(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT outcome, COUNT(*) AS cnt
            FROM thesis_predictions
            WHERE COALESCE(outcome, 'pending') != 'pending'
            GROUP BY outcome
            """
        ).fetchall()
        recent = conn.execute(
            """
            SELECT thesis_key, predicted_direction, symbol, price_at_prediction,
                   price_at_check, actual_change_pct, outcome, outcome_note, checked_at
            FROM thesis_predictions
            WHERE COALESCE(outcome, 'pending') != 'pending'
            ORDER BY checked_at DESC, id DESC
            LIMIT 10
            """
        ).fetchall()
        conn.close()

        counts = {str(row["outcome"] or ""): int(row["cnt"] or 0) for row in rows}
        verified = counts.get("verified", 0)
        refuted = counts.get("refuted", 0)
        neutral = counts.get("neutral", 0)
        total = verified + refuted + neutral
        accuracy = verified / max(verified + refuted, 1) * 100.0
        return {
            "verified": verified,
            "refuted": refuted,
            "neutral": neutral,
            "total": total,
            "accuracy_pct": round(accuracy, 1),
            "recent": [dict(row) for row in recent],
        }
