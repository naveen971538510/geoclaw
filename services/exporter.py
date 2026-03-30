import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone


def _parse_iso(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _sentiment_label(signal: str, score: float) -> str:
    label = str(signal or "").strip().lower()
    if label in {"positive", "bullish"} or float(score or 0.0) >= 0.2:
        return "positive"
    if label in {"negative", "bearish"} or float(score or 0.0) <= -0.2:
        return "negative"
    return "neutral"


class Exporter:
    def __init__(self, db_path):
        self.db_path = db_path

    def export_theses_csv(self) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT thesis_key, confidence, status, timeframe, terminal_risk,
                   evidence_count, watchlist_suggestion, last_update_reason,
                   created_at, last_updated_at
            FROM agent_theses
            WHERE COALESCE(status, '') != 'superseded'
            ORDER BY confidence DESC
            """
        ).fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "thesis_key",
                "confidence_pct",
                "status",
                "timeframe",
                "terminal_risk",
                "evidence_count",
                "watchlist_suggestion",
                "last_update_reason",
                "created_at",
                "updated_at",
            ],
        )
        writer.writeheader()
        for row in rows:
            item = dict(row)
            writer.writerow(
                {
                    "thesis_key": item.get("thesis_key", ""),
                    "confidence_pct": round(float(item.get("confidence", 0.0) or 0.0) * 100, 1),
                    "status": item.get("status", ""),
                    "timeframe": item.get("timeframe", ""),
                    "terminal_risk": item.get("terminal_risk", ""),
                    "evidence_count": item.get("evidence_count", 0),
                    "watchlist_suggestion": item.get("watchlist_suggestion", ""),
                    "last_update_reason": item.get("last_update_reason", ""),
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("last_updated_at", ""),
                }
            )
        return output.getvalue()

    def export_articles_csv(self, days: int = 7) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 7)))
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ia.headline, ia.source_name, ia.published_at, ia.fetched_at,
                   ae.signal, ae.sentiment_score, ae.impact_score, ae.confidence_score
            FROM ingested_articles ia
            LEFT JOIN article_enrichment ae ON ae.article_id = ia.id
            ORDER BY ia.published_at DESC, ia.fetched_at DESC
            LIMIT 1000
            """
        ).fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "headline",
                "source",
                "published_at",
                "sentiment_label",
                "relevance_score",
                "fetched_at",
            ],
        )
        writer.writeheader()
        for row in rows:
            fetched_at = _parse_iso(row["fetched_at"]) or _parse_iso(row["published_at"])
            if fetched_at is not None and fetched_at < cutoff:
                continue
            writer.writerow(
                {
                    "headline": row["headline"] or "",
                    "source": row["source_name"] or "",
                    "published_at": row["published_at"] or "",
                    "sentiment_label": _sentiment_label(row["signal"], float(row["sentiment_score"] or 0.0)),
                    "relevance_score": round(
                        max(
                            float(int(row["impact_score"] or 0)) / 100.0,
                            float(row["confidence_score"] or 0.0),
                        ),
                        3,
                    ),
                    "fetched_at": row["fetched_at"] or "",
                }
            )
        return output.getvalue()

    def export_briefing_txt(self, briefing_id: int = None) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if briefing_id:
            row = conn.execute(
                "SELECT * FROM agent_briefings WHERE id=?",
                (int(briefing_id),),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM agent_briefings ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        conn.close()
        if not row:
            return "No briefing available."
        return str(dict(row).get("briefing_text", "") or "")

    def export_full_json(self) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        theses = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM agent_theses
                WHERE COALESCE(status, '') != 'superseded'
                ORDER BY confidence DESC
                LIMIT 50
                """
            ).fetchall()
        ]
        actions = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM agent_actions
                WHERE COALESCE(status, '') IN ('draft', 'proposed', 'approved', 'auto_approved')
                ORDER BY created_at DESC
                LIMIT 20
                """
            ).fetchall()
        ]
        briefing_row = conn.execute(
            "SELECT * FROM agent_briefings ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return json.dumps(
            {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "theses": theses,
                "actions": actions,
                "briefing": dict(briefing_row) if briefing_row else {},
            },
            indent=2,
            default=str,
        )

    def export_predictions_csv(self) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT thesis_key, predicted_direction, symbol,
                   price_at_prediction, price_at_check, actual_change_pct,
                   outcome, predicted_at, checked_at
            FROM thesis_predictions
            WHERE outcome != 'pending'
            ORDER BY predicted_at DESC
            """
        ).fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "thesis_key",
                "predicted_direction",
                "symbol",
                "price_at_prediction",
                "price_at_check",
                "actual_change_pct",
                "outcome",
                "predicted_at",
                "checked_at",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
        return output.getvalue()
