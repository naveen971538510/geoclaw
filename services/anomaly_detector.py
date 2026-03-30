import sqlite3


class AnomalyDetector:
    THRESHOLDS = {
        "confidence_spike": 0.18,
        "article_surge": 50,
        "high_risk_cluster": 3,
        "contradiction_storm": 3,
    }

    def detect_all(self, db_path: str, run_id: int = None) -> list:
        anomalies = []
        anomalies.extend(self._detect_confidence_spikes(db_path))
        anomalies.extend(self._detect_high_risk_cluster(db_path))
        anomalies.extend(self._detect_contradiction_storm(db_path))
        anomalies.extend(self._detect_article_surge(db_path))
        return anomalies

    def _detect_confidence_spikes(self, db_path: str) -> list:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT thesis_key, confidence_velocity
            FROM agent_theses
            WHERE ABS(COALESCE(confidence_velocity, 0)) >= ?
              AND COALESCE(status, '') != 'superseded'
            """,
            (self.THRESHOLDS["confidence_spike"],),
        ).fetchall()
        conn.close()
        anomalies = []
        for row in rows:
            velocity = float(row["confidence_velocity"] or 0.0)
            anomalies.append(
                {
                    "type": "confidence_spike",
                    "severity": "HIGH" if abs(velocity) > 0.25 else "MEDIUM",
                    "description": f"Confidence {'surge' if velocity > 0 else 'collapse'} detected: {str(row['thesis_key'] or '')[:80]}",
                    "data": {"thesis_key": row["thesis_key"], "velocity": velocity},
                }
            )
        return anomalies

    def _detect_high_risk_cluster(self, db_path: str) -> list:
        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_theses
            WHERE terminal_risk='HIGH'
              AND COALESCE(status, '') != 'superseded'
              AND COALESCE(confidence, 0) >= 0.65
            """
        ).fetchone()[0]
        conn.close()
        if count >= self.THRESHOLDS["high_risk_cluster"]:
            return [
                {
                    "type": "high_risk_cluster",
                    "severity": "HIGH",
                    "description": f"{count} simultaneous HIGH-risk theses — elevated systemic risk",
                    "data": {"count": count},
                }
            ]
        return []

    def _detect_contradiction_storm(self, db_path: str) -> list:
        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM contradictions
            WHERE created_at >= datetime('now','-1 hours') AND COALESCE(resolved, 0) = 0
            """
        ).fetchone()[0]
        conn.close()
        if count >= self.THRESHOLDS["contradiction_storm"]:
            return [
                {
                    "type": "contradiction_storm",
                    "severity": "MEDIUM",
                    "description": f"{count} new contradictions in the last hour — agent beliefs under stress",
                    "data": {"count": count},
                }
            ]
        return []

    def _detect_article_surge(self, db_path: str) -> list:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT source_name, COUNT(*) AS cnt
            FROM ingested_articles
            WHERE fetched_at >= datetime('now','-2 hours')
            GROUP BY source_name
            HAVING cnt >= 10
            """
        ).fetchall()
        conn.close()
        anomalies = []
        for row in rows:
            anomalies.append(
                {
                    "type": "article_surge",
                    "severity": "LOW",
                    "description": f"High article volume from {row['source_name']}: {row['cnt']} in 2h",
                    "data": {"source": row["source_name"], "count": row["cnt"]},
                }
            )
        return anomalies
