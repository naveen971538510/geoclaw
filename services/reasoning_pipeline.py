import json
from datetime import datetime, timezone
from typing import Dict

from config import DB_PATH
from services.db_helpers import get_conn
from services.logging_service import get_logger
from services.rule_engine import RuleEngine
from services.thesis_service import normalize_thesis_key

logger = get_logger("reasoning_pipeline")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_confidence(value: float) -> float:
    return max(0.05, min(0.95, float(value or 0.0)))


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
    stats = {"processed": 0, "theses_updated": 0, "chains_written": 0}

    for article in articles:
        item = dict(article)
        article_view = {
            "id": item.get("id"),
            "headline": item.get("headline") or "",
            "body": item.get("summary") or "",
            "summary": item.get("summary") or "",
            "source_name": item.get("source_name") or "unknown",
            "published_at": item.get("published_at") or item.get("fetched_at") or "",
            "url": item.get("url") or "",
        }
        thesis_key = normalize_thesis_key(engine.derive_thesis_key(article_view))
        delta, chain = engine.reason(article_view)
        timeframe = str((chain[0] or {}).get("timeframe") if chain else "")

        existing = cur.execute(
            """
            SELECT confidence, evidence_count
            FROM agent_theses
            WHERE thesis_key = ?
            LIMIT 1
            """,
            (thesis_key,),
        ).fetchone()

        now = _utc_now_iso()
        reason = str((chain[0] or {}).get("mechanism") if chain else "rule match")
        if existing:
            old_conf = float(existing["confidence"] or 0.5)
            evidence_count = int(existing["evidence_count"] or 0)
            evidence_weight = 1.0 / (1.0 + evidence_count * 0.1)
            new_conf = _clamp_confidence(old_conf + (float(delta or 0.0) * evidence_weight))
            cur.execute(
                """
                UPDATE agent_theses
                SET confidence = ?,
                    evidence_count = COALESCE(evidence_count, 0) + 1,
                    last_article_id = ?,
                    last_updated_at = ?,
                    last_update_reason = ?
                WHERE thesis_key = ?
                """,
                (new_conf, int(item["id"]), now, reason, thesis_key),
            )
            _record_event(cur, thesis_key, "strengthened" if new_conf >= old_conf else "weakened", reason, new_conf, evidence_count + 1)
            logger.info("Thesis %s: delta=%.3f new_conf=%.3f", thesis_key, float(delta or 0.0), new_conf)
        else:
            initial_conf = _clamp_confidence(0.50 + float(delta or 0.0))
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
                    category
                )
                VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, 0, '', ?, 'other')
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
                ),
            )
            _record_event(cur, thesis_key, "created", reason, initial_conf, 1)
            logger.info("Thesis %s: delta=%.3f new_conf=%.3f", thesis_key, float(delta or 0.0), initial_conf)
        stats["theses_updated"] += 1

        terminal_risk = engine.compute_terminal_risk(thesis_key, cur.execute(
            "SELECT confidence FROM agent_theses WHERE thesis_key = ? LIMIT 1",
            (thesis_key,),
        ).fetchone()["confidence"], timeframe=timeframe)
        watchlist_suggestion = engine.compute_watchlist_suggestion(thesis_key)
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
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(item["id"]),
                thesis_key,
                json.dumps(chain or [], ensure_ascii=False),
                terminal_risk,
                watchlist_suggestion,
                now,
            ),
        )
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
