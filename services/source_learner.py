import sqlite3
from datetime import datetime, timezone
from typing import Dict, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceLearner:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def update_from_predictions(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        rows = conn.execute(
            """
            SELECT tp.thesis_key, tp.outcome, ia.source_name AS source
            FROM thesis_predictions tp
            JOIN reasoning_chains rc ON rc.thesis_key = tp.thesis_key
            JOIN ingested_articles ia ON ia.id = rc.article_id
            WHERE tp.outcome IN ('verified', 'refuted')
              AND COALESCE(tp.checked_at, '') >= datetime('now', '-7 days')
            GROUP BY tp.thesis_key, tp.outcome, ia.source_name
            """
        ).fetchall()

        source_stats = {}
        for row in rows:
            source = str(row["source"] or "unknown")
            source_stats.setdefault(source, {"verified": 0, "refuted": 0})
            source_stats[source][str(row["outcome"] or "refuted")] += 1

        updated = 0
        now = _utc_now_iso()
        for source, stats in source_stats.items():
            verified = int(stats.get("verified", 0) or 0)
            refuted = int(stats.get("refuted", 0) or 0)
            total = verified + refuted
            if total <= 0:
                continue

            accuracy = verified / total
            existing = conn.execute(
                """
                SELECT reliability_score, total_predictions
                FROM source_reliability
                WHERE source_name = ?
                LIMIT 1
                """,
                (source,),
            ).fetchone()

            if existing:
                old_score = float(existing["reliability_score"] or 0.65)
                new_score = max(0.40, min(0.98, 0.30 * accuracy + 0.70 * old_score))
                conn.execute(
                    """
                    UPDATE source_reliability
                    SET reliability_score = ?,
                        total_predictions = total_predictions + ?,
                        verified_predictions = verified_predictions + ?,
                        refuted_predictions = refuted_predictions + ?,
                        last_updated = ?
                    WHERE source_name = ?
                    """,
                    (new_score, total, verified, refuted, now, source),
                )
            else:
                score = max(0.40, min(0.98, accuracy))
                conn.execute(
                    """
                    INSERT INTO source_reliability (
                        source_name, total_predictions, verified_predictions,
                        refuted_predictions, reliability_score, last_updated
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (source, total, verified, refuted, score, now),
                )
            updated += 1

        conn.commit()
        conn.close()
        return {"sources_updated": updated, "source_stats": source_stats}

    def get_weight(self, source_name: str) -> float:
        clean = str(source_name or "").strip()
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                """
                SELECT reliability_score
                FROM source_reliability
                WHERE LOWER(source_name) LIKE ?
                ORDER BY reliability_score DESC
                LIMIT 1
                """,
                (f"%{clean.lower()}%",),
            ).fetchone()
            conn.close()
            return float(row[0] or 0.65) if row else 0.65
        except Exception:
            return 0.65

    def get_leaderboard(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT source_name, reliability_score, total_predictions,
                   verified_predictions, refuted_predictions
            FROM source_reliability
            ORDER BY reliability_score DESC, total_predictions DESC, source_name ASC
            """
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
