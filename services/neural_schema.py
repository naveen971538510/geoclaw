import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import DB_PATH


SCHEMA_VERSION = "neural_schema_v1"


class NeuralSchema:
    """
    Lightweight intelligence graph over GeoClaw's existing facts.

    This is not a new model. It is a structured scoring layer that connects
    theses, articles, sources, predictions, contradictions, and actions before
    ranking what deserves attention next.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DB_PATH)

    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def ensure_schema(self) -> None:
        conn = self._db()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS neural_schema_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER DEFAULT 0,
                    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    schema_version TEXT DEFAULT 'neural_schema_v1',
                    top_signal TEXT DEFAULT '',
                    confidence_score REAL DEFAULT 0.0,
                    node_count INTEGER DEFAULT 0,
                    edge_count INTEGER DEFAULT 0,
                    schema_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_neural_schema_generated
                ON neural_schema_snapshots(generated_at DESC)
                """
            )
            conn.commit()
        finally:
            conn.close()

    def build(self, limit: int = 12, persist: bool = False, run_id: int = 0, compact: bool = False) -> Dict:
        conn = self._db()
        try:
            schema = self._build_from_conn(conn, limit=max(1, min(int(limit or 12), 50)))
        finally:
            conn.close()

        if persist:
            self.save_snapshot(schema, run_id=run_id)
        return self._compact(schema) if compact else schema

    def latest_or_build(self, compact: bool = True) -> Dict:
        self.ensure_schema()
        conn = self._db()
        try:
            row = conn.execute(
                """
                SELECT schema_json
                FROM neural_schema_snapshots
                ORDER BY generated_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                try:
                    schema = json.loads(row["schema_json"] or "{}")
                    if schema:
                        return self._compact(schema) if compact else schema
                except Exception:
                    pass
        finally:
            conn.close()
        return self.build(compact=compact)

    def save_snapshot(self, schema: Dict, run_id: int = 0) -> int:
        self.ensure_schema()
        top_signal = ""
        confidence_score = 0.0
        if schema.get("ranked_signals"):
            top = schema["ranked_signals"][0]
            top_signal = str(top.get("label") or top.get("thesis_key") or "")[:240]
            confidence_score = float(top.get("schema_score", 0.0) or 0.0) / 100.0
        conn = self._db()
        try:
            cur = conn.execute(
                """
                INSERT INTO neural_schema_snapshots (
                    run_id, generated_at, schema_version, top_signal,
                    confidence_score, node_count, edge_count, schema_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id or 0),
                    datetime.now(timezone.utc).isoformat(),
                    SCHEMA_VERSION,
                    top_signal,
                    confidence_score,
                    int(schema.get("node_count", 0) or 0),
                    int(schema.get("edge_count", 0) or 0),
                    json.dumps(schema, default=str),
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()

    def _build_from_conn(self, conn, limit: int) -> Dict:
        if not self._table_exists(conn, "agent_theses"):
            return self._empty("agent_theses table is not available yet")

        thesis_cols = self._columns(conn, "agent_theses")
        category_expr = "category" if "category" in thesis_cols else "'' AS category"
        updated_expr = "last_updated_at" if "last_updated_at" in thesis_cols else "created_at"
        thesis_rows = conn.execute(
            f"""
            SELECT thesis_key, current_claim, title, confidence, status,
                   terminal_risk, evidence_count, confidence_velocity,
                   last_update_reason, watchlist_suggestion, {category_expr}, timeframe
            FROM agent_theses
            WHERE COALESCE(status, '') NOT IN ('superseded', 'expired')
            ORDER BY confidence DESC, evidence_count DESC, {updated_expr} DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        nodes: List[Dict] = []
        edges: List[Dict] = []
        ranked: List[Dict] = []
        gaps: List[Dict] = []
        seen_nodes = set()

        for row in thesis_rows:
            thesis = dict(row)
            thesis_key = str(thesis.get("thesis_key") or "").strip()
            if not thesis_key:
                continue

            articles = self._linked_articles(conn, thesis_key)
            predictions = self._prediction_stats(conn, thesis_key)
            contradiction_count = self._active_contradiction_count(conn, thesis_key)
            action_count = self._open_action_count(conn, thesis_key)
            source_reliability = self._avg_source_reliability(conn, [a.get("source") for a in articles])
            score = self._schema_score(thesis, source_reliability, predictions, contradiction_count, action_count)
            direction = self._direction(float(thesis.get("confidence_velocity", 0.0) or 0.0))
            label = str(thesis.get("title") or thesis.get("current_claim") or thesis_key).strip()[:140]
            thesis_node_id = "thesis:" + self._stable_id(thesis_key)

            if thesis_node_id not in seen_nodes:
                seen_nodes.add(thesis_node_id)
                nodes.append(
                    {
                        "id": thesis_node_id,
                        "type": "thesis",
                        "label": label,
                        "thesis_key": thesis_key,
                        "schema_score": round(score, 1),
                        "confidence": round(float(thesis.get("confidence", 0.0) or 0.0), 3),
                        "confidence_velocity": round(float(thesis.get("confidence_velocity", 0.0) or 0.0), 3),
                        "status": thesis.get("status") or "",
                        "terminal_risk": thesis.get("terminal_risk") or "",
                    }
                )

            for article in articles[:3]:
                article_id = "article:" + self._stable_id(str(article.get("headline") or article.get("url") or ""))
                source = str(article.get("source") or "unknown")
                source_id = "source:" + self._stable_id(source.lower())
                if article_id not in seen_nodes:
                    seen_nodes.add(article_id)
                    nodes.append({"id": article_id, "type": "article", "label": str(article.get("headline") or "")[:140], "source": source})
                if source_id not in seen_nodes:
                    seen_nodes.add(source_id)
                    nodes.append({"id": source_id, "type": "source", "label": source, "reliability": round(self._source_reliability(conn, source), 3)})
                edges.append({"from": article_id, "to": thesis_node_id, "type": "supports", "weight": 0.72})
                edges.append({"from": source_id, "to": article_id, "type": "published", "weight": round(self._source_reliability(conn, source), 3)})

            if contradiction_count > 0:
                contradiction_id = "contradiction:" + self._stable_id(thesis_key)
                nodes.append({"id": contradiction_id, "type": "contradiction", "label": f"{contradiction_count} active contradiction(s)"})
                edges.append({"from": contradiction_id, "to": thesis_node_id, "type": "weakens", "weight": min(1.0, 0.2 * contradiction_count)})

            if predictions.get("total", 0) > 0:
                prediction_id = "prediction:" + self._stable_id(thesis_key)
                nodes.append({"id": prediction_id, "type": "prediction", "label": self._prediction_label(predictions), "stats": predictions})
                edge_type = "verifies" if predictions.get("verified", 0) >= predictions.get("refuted", 0) else "refutes"
                edges.append({"from": prediction_id, "to": thesis_node_id, "type": edge_type, "weight": min(1.0, 0.25 + predictions.get("total", 0) * 0.1)})

            why_now = str(thesis.get("last_update_reason") or thesis.get("watchlist_suggestion") or thesis.get("current_claim") or "").strip()
            ranked.append(
                {
                    "thesis_key": thesis_key,
                    "label": label,
                    "schema_score": round(score, 1),
                    "confidence_pct": round(float(thesis.get("confidence", 0.0) or 0.0) * 100),
                    "direction": direction,
                    "terminal_risk": thesis.get("terminal_risk") or "",
                    "evidence_count": int(thesis.get("evidence_count", 0) or 0),
                    "source_reliability": round(source_reliability, 3),
                    "prediction_stats": predictions,
                    "contradiction_count": contradiction_count,
                    "open_action_count": action_count,
                    "why_now": why_now[:180],
                    "next_best_action": self._next_action(thesis, predictions, contradiction_count),
                }
            )

            gaps.extend(self._gaps_for(thesis, articles, predictions, contradiction_count))

        ranked.sort(key=lambda item: float(item.get("schema_score", 0.0) or 0.0), reverse=True)
        gaps = sorted(gaps, key=lambda item: int(item.get("priority", 9) or 9))[:8]
        summary = "No neural schema signals yet."
        if ranked:
            top = ranked[0]
            summary = (
                f"Top intelligence node: {top['label']} "
                f"({top['schema_score']}/100 schema score, {top['confidence_pct']}% thesis confidence). "
                f"Next: {top['next_best_action']}"
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "ranked_signals": ranked[:8],
            "gaps": gaps,
            "nodes": nodes[:120],
            "edges": edges[:180],
            "node_count": len(nodes),
            "edge_count": len(edges),
            "metrics": {
                "theses_scored": len(ranked),
                "gaps_found": len(gaps),
                "top_score": float(ranked[0]["schema_score"]) if ranked else 0.0,
            },
        }

    def _compact(self, schema: Dict) -> Dict:
        return {
            "schema_version": schema.get("schema_version", SCHEMA_VERSION),
            "generated_at": schema.get("generated_at", ""),
            "summary": schema.get("summary", ""),
            "ranked_signals": list(schema.get("ranked_signals", []) or [])[:5],
            "gaps": list(schema.get("gaps", []) or [])[:5],
            "node_count": int(schema.get("node_count", 0) or 0),
            "edge_count": int(schema.get("edge_count", 0) or 0),
            "metrics": schema.get("metrics", {}) or {},
        }

    def _empty(self, reason: str) -> Dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": f"Neural schema unavailable: {reason}.",
            "ranked_signals": [],
            "gaps": [{"priority": 5, "type": "schema_unavailable", "message": reason}],
            "nodes": [],
            "edges": [],
            "node_count": 0,
            "edge_count": 0,
            "metrics": {"theses_scored": 0, "gaps_found": 1, "top_score": 0.0},
        }

    def _linked_articles(self, conn, thesis_key: str) -> List[Dict]:
        if not self._table_exists(conn, "ingested_articles"):
            return []
        article_cols = self._columns(conn, "ingested_articles")
        source_expr = self._source_expr(article_cols)
        rows = []
        if self._table_exists(conn, "reasoning_chains") and "article_id" in self._columns(conn, "reasoning_chains"):
            try:
                rows = conn.execute(
                    f"""
                    SELECT ia.headline, ia.url, {source_expr} AS source,
                           COALESCE(ia.published_at, ia.fetched_at, '') AS ts
                    FROM reasoning_chains rc
                    JOIN ingested_articles ia ON ia.id = rc.article_id
                    WHERE rc.thesis_key = ?
                    ORDER BY COALESCE(rc.created_at, '') DESC, rc.id DESC
                    LIMIT 5
                    """,
                    (thesis_key,),
                ).fetchall()
            except Exception:
                rows = []
        if not rows:
            terms = [word for word in thesis_key.lower().split() if len(word) >= 5][:4]
            if not terms:
                return []
            text_cols = [col for col in ("headline", "summary", "body") if col in article_cols]
            if not text_cols:
                return []
            clauses = []
            params = []
            for term in terms:
                for col in text_cols:
                    clauses.append(f"LOWER(COALESCE({col}, '')) LIKE ?")
                    params.append(f"%{term}%")
            rows = conn.execute(
                f"""
                SELECT headline, url, {source_expr} AS source,
                       COALESCE(published_at, fetched_at, '') AS ts
                FROM ingested_articles
                WHERE {' OR '.join(clauses)}
                ORDER BY COALESCE(published_at, fetched_at, '') DESC, id DESC
                LIMIT 5
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def _prediction_stats(self, conn, thesis_key: str) -> Dict:
        if not self._table_exists(conn, "thesis_predictions"):
            return {"verified": 0, "refuted": 0, "neutral": 0, "pending": 0, "total": 0}
        rows = conn.execute(
            """
            SELECT COALESCE(outcome, 'pending') AS outcome, COUNT(*) AS cnt
            FROM thesis_predictions
            WHERE thesis_key = ?
            GROUP BY COALESCE(outcome, 'pending')
            """,
            (thesis_key,),
        ).fetchall()
        stats = {"verified": 0, "refuted": 0, "neutral": 0, "pending": 0}
        for row in rows:
            key = str(row["outcome"] or "pending").lower()
            stats[key if key in stats else "pending"] += int(row["cnt"] or 0)
        stats["total"] = sum(stats.values())
        return stats

    def _active_contradiction_count(self, conn, thesis_key: str) -> int:
        if not self._table_exists(conn, "contradictions"):
            return 0
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM contradictions
            WHERE thesis_key = ? AND COALESCE(resolved, 0)=0
            """,
            (thesis_key,),
        ).fetchone()
        return int((row["cnt"] if row else 0) or 0)

    def _open_action_count(self, conn, thesis_key: str) -> int:
        if not self._table_exists(conn, "agent_actions"):
            return 0
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM agent_actions
            WHERE thesis_key = ?
              AND COALESCE(status, '') IN ('pending', 'draft', 'proposed', 'approved', 'auto_approved')
            """,
            (thesis_key,),
        ).fetchone()
        return int((row["cnt"] if row else 0) or 0)

    def _avg_source_reliability(self, conn, sources: List[Any]) -> float:
        scores = [self._source_reliability(conn, str(source or "")) for source in sources if str(source or "").strip()]
        return sum(scores) / len(scores) if scores else 0.65

    def _source_reliability(self, conn, source: str) -> float:
        if not source or not self._table_exists(conn, "source_reliability"):
            return 0.65
        row = conn.execute(
            """
            SELECT reliability_score
            FROM source_reliability
            WHERE source_name LIKE ?
            ORDER BY reliability_score DESC
            LIMIT 1
            """,
            (f"%{source.strip()}%",),
        ).fetchone()
        return float((row["reliability_score"] if row else 0.65) or 0.65)

    def _schema_score(self, thesis: Dict, source_reliability: float, predictions: Dict, contradiction_count: int, action_count: int) -> float:
        confidence = float(thesis.get("confidence", 0.0) or 0.0)
        velocity = max(-0.2, min(0.2, float(thesis.get("confidence_velocity", 0.0) or 0.0)))
        evidence = int(thesis.get("evidence_count", 0) or 0)
        terminal_risk = str(thesis.get("terminal_risk") or "").upper()
        risk_bonus = 8.0 if "HIGH" in terminal_risk else (4.0 if "MEDIUM" in terminal_risk else 0.0)
        prediction_bonus = predictions.get("verified", 0) * 8.0 - predictions.get("refuted", 0) * 12.0 + predictions.get("pending", 0) * 1.0
        score = (
            confidence * 62.0
            + velocity * 150.0
            + min(evidence, 6) * 3.0
            + (source_reliability - 0.65) * 24.0
            + prediction_bonus
            + action_count * 1.5
            + risk_bonus
            - contradiction_count * 10.0
        )
        return max(0.0, min(100.0, score))

    def _gaps_for(self, thesis: Dict, articles: List[Dict], predictions: Dict, contradiction_count: int) -> List[Dict]:
        thesis_key = str(thesis.get("thesis_key") or "")
        label = str(thesis.get("title") or thesis.get("current_claim") or thesis_key)[:120]
        gaps = []
        if int(thesis.get("evidence_count", 0) or 0) < 3 and float(thesis.get("confidence", 0.0) or 0.0) >= 0.6:
            gaps.append({"priority": 1, "type": "low_evidence_high_confidence", "thesis_key": thesis_key, "message": f"Needs more evidence: {label}"})
        if contradiction_count > 0:
            gaps.append({"priority": 1, "type": "active_contradiction", "thesis_key": thesis_key, "message": f"Resolve contradiction before acting: {label}"})
        if predictions.get("pending", 0) > 0 and predictions.get("verified", 0) == 0:
            gaps.append({"priority": 2, "type": "truth_check_pending", "thesis_key": thesis_key, "message": f"Awaiting prediction truth check: {label}"})
        if not articles:
            gaps.append({"priority": 3, "type": "no_linked_articles", "thesis_key": thesis_key, "message": f"No linked recent articles for: {label}"})
        return gaps

    def _next_action(self, thesis: Dict, predictions: Dict, contradiction_count: int) -> str:
        if contradiction_count > 0:
            return "Resolve contradiction before promoting this signal."
        if predictions.get("refuted", 0) > predictions.get("verified", 0):
            return "Lower confidence or require fresh evidence."
        if int(thesis.get("evidence_count", 0) or 0) < 3:
            return "Research one more independent source."
        if predictions.get("pending", 0) > 0:
            return "Wait for the prediction check window."
        return "Keep on dashboard; monitor for velocity change."

    def _prediction_label(self, stats: Dict) -> str:
        return f"{stats.get('verified', 0)} verified / {stats.get('refuted', 0)} refuted / {stats.get('pending', 0)} pending"

    def _direction(self, velocity: float) -> str:
        if velocity > 0.02:
            return "strengthening"
        if velocity < -0.02:
            return "weakening"
        return "stable"

    def _source_expr(self, columns: List[str]) -> str:
        candidates = [col for col in ("source_name", "source") if col in columns]
        if not candidates:
            return "'unknown'"
        expr = ", ".join(candidates + ["'unknown'"])
        return f"COALESCE({expr})"

    def _table_exists(self, conn, table_name: str) -> bool:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)

    def _columns(self, conn, table_name: str) -> List[str]:
        try:
            return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
        except Exception:
            return []

    def _stable_id(self, value: str) -> str:
        return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:12]
