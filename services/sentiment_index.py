import json
import sqlite3
from datetime import datetime, timedelta, timezone


def _parse_ts(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _table_columns(conn, table_name: str):
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    except Exception:
        return set()


class SentimentIndex:
    def _article_sentiment_inputs(self, conn) -> dict:
        positive = 0
        negative = 0
        neutral = 0
        enrichment_cols = _table_columns(conn, "article_enrichment")
        article_cols = _table_columns(conn, "ingested_articles")
        if not enrichment_cols:
            return {"positive": 0, "negative": 0, "neutral": 0}

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        has_join = "article_id" in enrichment_cols and "id" in article_cols
        rows = []
        if has_join:
            rows = conn.execute(
                """
                SELECT ae.signal, ae.sentiment_score, ae.created_at, ia.fetched_at
                FROM article_enrichment ae
                LEFT JOIN ingested_articles ia ON ia.id = ae.article_id
                ORDER BY COALESCE(ia.fetched_at, ae.created_at) DESC
                LIMIT 500
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT signal, sentiment_score, created_at, NULL AS fetched_at
                FROM article_enrichment
                ORDER BY created_at DESC
                LIMIT 500
                """
            ).fetchall()

        for row in rows:
            timestamp = _parse_ts(row["fetched_at"] or row["created_at"])
            if timestamp is not None and timestamp < cutoff:
                continue
            signal = str(row["signal"] or "").strip().lower()
            score = float(row["sentiment_score"] or 0.0)
            if signal in {"positive", "bullish"} or score >= 0.2:
                positive += 1
            elif signal in {"negative", "bearish"} or score <= -0.2:
                negative += 1
            else:
                neutral += 1

        return {"positive": positive, "negative": negative, "neutral": neutral}

    def compute(self, db_path: str) -> dict:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        article_inputs = self._article_sentiment_inputs(conn)
        pos = int(article_inputs.get("positive", 0) or 0)
        neg = int(article_inputs.get("negative", 0) or 0)
        neu = int(article_inputs.get("neutral", 0) or 0)
        total_articles = pos + neg + neu or 1
        sentiment_score = ((pos - neg) / total_articles + 1) / 2 * 100

        thesis_row = conn.execute(
            """
            SELECT AVG(confidence) AS avg_conf
            FROM agent_theses
            WHERE COALESCE(status, '') != 'superseded'
            """
        ).fetchone()
        avg_conf = float((thesis_row["avg_conf"] if thesis_row else 0.5) or 0.5)
        thesis_score = (1 - avg_conf) * 100

        high_risk_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM agent_theses
            WHERE terminal_risk='HIGH' AND COALESCE(status, '') != 'superseded'
            """
        ).fetchone()
        high_risk = int((high_risk_row["cnt"] if high_risk_row else 0) or 0)
        high_risk_score = max(0, 100 - high_risk * 15)

        contradictions_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM contradictions WHERE resolved=0"
        ).fetchone()
        contradictions = int((contradictions_row["cnt"] if contradictions_row else 0) or 0)
        contradiction_score = max(0, 100 - contradictions * 10)
        conn.close()

        composite = (
            0.40 * sentiment_score
            + 0.30 * thesis_score
            + 0.20 * high_risk_score
            + 0.10 * contradiction_score
        )
        composite = max(0, min(100, composite))

        if composite >= 75:
            label, color = "Extreme Greed", "#3fb950"
        elif composite >= 55:
            label, color = "Greed", "#7ee787"
        elif composite >= 45:
            label, color = "Neutral", "#d29922"
        elif composite >= 25:
            label, color = "Fear", "#f0883e"
        else:
            label, color = "Extreme Fear", "#f85149"

        return {
            "score": round(composite, 1),
            "label": label,
            "color": color,
            "components": {
                "article_sentiment": round(sentiment_score, 1),
                "thesis_confidence": round(thesis_score, 1),
                "high_risk_theses": round(high_risk_score, 1),
                "contradictions": round(contradiction_score, 1),
            },
            "inputs": {
                "positive_articles": pos,
                "negative_articles": neg,
                "neutral_articles": neu,
                "avg_thesis_conf": round(avg_conf, 3),
                "high_risk_count": high_risk,
                "contradiction_count": contradictions,
            },
        }

    def save_daily_score(self, db_path: str):
        score_data = self.compute(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            INSERT INTO sentiment_index_log
              (score, label, components, recorded_at)
            VALUES (?,?,?,?)
            """,
            (
                float(score_data["score"] or 0.0),
                str(score_data["label"] or ""),
                json.dumps(score_data.get("components", {})),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        return score_data

    def get_history(self, db_path: str, days: int = 30) -> list:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, int(days or 30)))
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT score, label, recorded_at
            FROM sentiment_index_log
            ORDER BY recorded_at ASC
            """
        ).fetchall()
        conn.close()
        history = []
        for row in rows:
            recorded_at = _parse_ts(row["recorded_at"])
            if recorded_at is not None and recorded_at < cutoff:
                continue
            history.append(
                {
                    "score": float(row["score"] or 0.0),
                    "label": str(row["label"] or ""),
                    "recorded_at": str(row["recorded_at"] or ""),
                }
            )
        return history
