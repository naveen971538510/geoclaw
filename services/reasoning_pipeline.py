import json
from datetime import datetime, timezone
from typing import Dict

from config import DB_PATH
from services.db_helpers import get_conn
from services.event_bus import publish
from services.feed_manager import get_source_weight
from services.llm_analyst import LLMAnalyst
from services.logging_service import get_logger
from services.prediction_tracker import PredictionTracker
from services.rule_engine import RuleEngine
from services.thesis_service import normalize_thesis_key

logger = get_logger("reasoning_pipeline")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_confidence(value: float) -> float:
    return max(0.05, min(0.95, float(value or 0.0)))


def _recency_weight(published_at_str):
    try:
        published = datetime.fromisoformat(str(published_at_str or "").replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - published).total_seconds() / 3600.0
        if age < 1:
            return 1.0
        if age < 6:
            return 0.8
        if age < 24:
            return 0.6
        if age < 72:
            return 0.4
        return 0.2
    except Exception:
        return 0.5


def _record_event(cur, thesis_key: str, event_type: str, note: str, confidence: float, evidence_count: int):
    cur.execute(
        """
        INSERT INTO thesis_events (
            thesis_key, event_type, note, confidence_at_event, evidence_count_at_event, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            normalize_thesis_key(thesis_key),
            str(event_type or "").strip(),
            str(note or "").strip(),
            float(confidence or 0.0),
            int(evidence_count or 0),
            _utc_now_iso(),
        ),
    )


def process_unreasoned_articles(db_path=None, max_articles: int = 50) -> Dict:
    conn = get_conn(db_path or DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(ingested_articles)")
    article_columns = {row["name"] if isinstance(row, dict) else row[1] for row in cur.fetchall()}
    if "is_reasoned" not in article_columns:
        cur.execute("ALTER TABLE ingested_articles ADD COLUMN is_reasoned INTEGER DEFAULT 0")
        conn.commit()

    articles = cur.execute(
        """
        SELECT
            ia.id,
            ia.headline,
            ia.summary,
            ia.source_name,
            ia.url,
            ia.published_at,
            ia.fetched_at,
            ae.thesis,
            ae.cluster_key
        FROM ingested_articles ia
        LEFT JOIN article_enrichment ae
          ON ae.article_id = ia.id
        WHERE COALESCE(ia.is_reasoned, 0) = 0
        ORDER BY COALESCE(ia.published_at, ia.fetched_at, '') DESC, ia.id DESC
        LIMIT ?
        """,
        (int(max_articles),),
    ).fetchall()

    engine = RuleEngine()
    llm = LLMAnalyst(db_path or DB_PATH)
    llm.reset_run_counter()
    stats = {"processed": 0, "theses_updated": 0, "chains_written": 0, "llm_used": 0, "rule_engine_used": 0}

    for article in articles:
        item = dict(article)
        article_view = {
            "id": item.get("id"),
            "headline": item.get("headline") or "",
            "body": item.get("summary") or "",
            "summary": item.get("summary") or "",
            "source": item.get("source_name") or "unknown",
            "source_name": item.get("source_name") or "unknown",
            "published_at": item.get("published_at") or item.get("fetched_at") or "",
            "url": item.get("url") or "",
        }

        existing = cur.execute(
            """
            SELECT confidence, evidence_count, confidence_velocity
            FROM agent_theses
            WHERE thesis_key = ?
            LIMIT 1
            """,
            (normalize_thesis_key(engine.derive_thesis_key(article_view)),),
        ).fetchone()
        existing_row = dict(existing) if existing else None

        llm_result = llm.analyse_article(
            article_view["headline"],
            article_view.get("body", "") or "",
            existing_thesis=existing_row,
        )
        if llm_result:
            thesis_key = normalize_thesis_key(llm_result.get("thesis_key") or engine.derive_thesis_key(article_view))
            delta = float(llm_result.get("confidence_delta", 0.0) or 0.0)
            timeframe = str(llm_result.get("timeframe", "days") or "days")
            terminal_risk = str(llm_result.get("terminal_risk", "LOW") or "LOW")
            watchlist_suggestion = str(llm_result.get("watchlist_suggestion", "") or "")
            chain = [
                {
                    "hop": 1,
                    "from": "news event",
                    "to": "market positioning",
                    "mechanism": str(llm_result.get("reasoning", "") or "LLM analysis"),
                    "confidence": round(min(0.95, 0.5 + abs(delta)), 2),
                    "timeframe": timeframe,
                },
                {
                    "hop": 2,
                    "from": "market positioning",
                    "to": str(llm_result.get("market_implication", "watch") or "watch"),
                    "mechanism": str(llm_result.get("confidence_basis", "") or "Await confirming evidence"),
                    "confidence": round(min(0.95, 0.5 + abs(delta) * 0.8), 2),
                    "timeframe": timeframe,
                },
            ]
            reasoning_source = "llm"
            reason = str(llm_result.get("reasoning", "") or "llm analysis")
            stats["llm_used"] += 1
        else:
            thesis_key = normalize_thesis_key(engine.derive_thesis_key(article_view))
            delta, chain = engine.reason(article_view)
            timeframe = str((chain[0] or {}).get("timeframe") if chain else "days")
            terminal_risk = engine.compute_terminal_risk(thesis_key, 0.5 + float(delta or 0.0), timeframe=timeframe)
            watchlist_suggestion = engine.compute_watchlist_suggestion(thesis_key)
            reasoning_source = "rule_engine"
            reason = str((chain[0] or {}).get("mechanism") if chain else "rule match")
            stats["rule_engine_used"] += 1

        now = _utc_now_iso()
        try:
            from services.source_learner import SourceLearner

            source_weight = SourceLearner(db_path or DB_PATH).get_weight(item.get("source_name", ""))
        except Exception:
            source_weight = get_source_weight(item.get("source_name", ""))
        recency_weight = _recency_weight(item.get("published_at") or item.get("fetched_at") or "")
        if existing_row:
            old_conf = float(existing_row.get("confidence", 0.5) or 0.5)
            evidence_count = int(existing_row.get("evidence_count", 0) or 0)
            evidence_weight = 1.0 / (1.0 + evidence_count * 0.1)
            adjusted_delta = float(delta or 0.0) * float(source_weight or 0.65) * float(recency_weight or 0.5) * evidence_weight
            new_conf = _clamp_confidence(old_conf + adjusted_delta)
            emitted_confidence = new_conf
            old_velocity = float(existing_row.get("confidence_velocity", 0.0) or 0.0)
            new_velocity = 0.3 * (new_conf - old_conf) + 0.7 * old_velocity
            cur.execute(
                """
                UPDATE agent_theses
                SET confidence = ?,
                    evidence_count = COALESCE(evidence_count, 0) + 1,
                    last_article_id = ?,
                    last_updated_at = ?,
                    last_update_reason = ?,
                    terminal_risk = ?,
                    watchlist_suggestion = ?,
                    timeframe = ?,
                    confidence_velocity = ?
                WHERE thesis_key = ?
                """,
                (
                    new_conf,
                    int(item["id"]),
                    now,
                    reason,
                    terminal_risk,
                    watchlist_suggestion,
                    timeframe,
                    new_velocity,
                    thesis_key,
                ),
            )
            _record_event(cur, thesis_key, "strengthened" if new_conf >= old_conf else "weakened", reason, new_conf, evidence_count + 1)
            logger.info("Thesis %s: delta=%.3f adjusted=%.3f new_conf=%.3f", thesis_key, float(delta or 0.0), adjusted_delta, new_conf)
        else:
            old_conf = 0.5
            evidence_weight = 1.0
            adjusted_delta = float(delta or 0.0) * float(source_weight or 0.65) * float(recency_weight or 0.5) * evidence_weight
            initial_conf = _clamp_confidence(old_conf + adjusted_delta)
            emitted_confidence = initial_conf
            new_velocity = 0.3 * (initial_conf - old_conf)
            cur.execute(
                """
                INSERT INTO agent_theses (
                    thesis_key,
                    title,
                    current_claim,
                    confidence,
                    status,
                    last_updated_at,
                    evidence_count,
                    created_at,
                    last_article_id,
                    contradiction_count,
                    notes,
                    last_update_reason,
                    category,
                    terminal_risk,
                    watchlist_suggestion,
                    timeframe,
                    confidence_velocity
                )
                VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, 0, '', ?, 'other', ?, ?, ?, ?)
                """,
                (
                    thesis_key,
                    str(item.get("headline") or "")[:120],
                    str(item.get("thesis") or thesis_key or item.get("headline") or ""),
                    initial_conf,
                    now,
                    now,
                    int(item["id"]),
                    reason,
                    terminal_risk,
                    watchlist_suggestion,
                    timeframe,
                    new_velocity,
                ),
            )
            _record_event(cur, thesis_key, "created", reason, initial_conf, 1)
            logger.info("Thesis %s: delta=%.3f adjusted=%.3f new_conf=%.3f", thesis_key, float(delta or 0.0), adjusted_delta, initial_conf)
        stats["theses_updated"] += 1

        cur.execute(
            """
            UPDATE article_enrichment
            SET thesis = ?,
                what_to_watch = CASE
                    WHEN COALESCE(TRIM(what_to_watch), '') = '' THEN ?
                    ELSE what_to_watch
                END
            WHERE article_id = ?
            """,
            (thesis_key, watchlist_suggestion, int(item["id"])),
        )
        cur.execute(
            """
            INSERT INTO reasoning_chains (
                article_id,
                thesis_key,
                chain_json,
                terminal_risk,
                watchlist_suggestion,
                created_at,
                reasoning_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(item["id"]),
                thesis_key,
                json.dumps(chain or [], ensure_ascii=False),
                terminal_risk,
                watchlist_suggestion,
                now,
                reasoning_source,
            ),
        )
        current_confidence_row = cur.execute(
            "SELECT confidence FROM agent_theses WHERE thesis_key = ? LIMIT 1",
            (thesis_key,),
        ).fetchone()
        current_confidence = float((current_confidence_row["confidence"] if current_confidence_row else 0.5) or 0.5)
        cur.execute(
            """
            INSERT INTO thesis_confidence_log (thesis_key, confidence, run_id, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (thesis_key, current_confidence, 0, now),
        )
        publish(
            "thesis_updated",
            {
                "thesis_key": thesis_key[:80],
                "old_confidence": round(float(old_conf or 0.0), 3),
                "new_confidence": round(float(emitted_confidence or current_confidence or 0.0), 3),
                "delta": round(float((emitted_confidence or current_confidence or 0.0) - float(old_conf or 0.0)), 3),
            },
        )
        try:
            pred_id = PredictionTracker(db_path or DB_PATH).record_prediction(thesis_key, float(emitted_confidence or current_confidence or 0.0), run_id=0)
            if pred_id:
                stats["predictions_recorded"] = int(stats.get("predictions_recorded", 0) or 0) + 1
        except Exception as exc:
            logger.warning("Prediction recording failed: %s", exc)
        stats["chains_written"] += 1

        cur.execute(
            """
            UPDATE ingested_articles
            SET is_reasoned = 1
            WHERE id = ?
            """,
            (int(item["id"]),),
        )
        stats["processed"] += 1

    conn.commit()
    conn.close()
    return stats
