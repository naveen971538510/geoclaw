import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List

from config import (
    DB_PATH,
    ENABLE_GDELT,
    ENABLE_GUARDIAN,
    ENABLE_NEWSAPI,
    ENABLE_RSS,
    DEFAULT_WATCHLIST,
    ALERT_MIN_IMPACT_SCORE,
    ALERT_MIN_ALERT_TAGS,
    ALERT_MIN_WATCHLIST_HITS,
)
from intelligence import normalize_article, classify_article, dedupe_articles, rank_article
from sources import RSSSource, GDELTSource, NewsAPISource, GuardianSource


DEFAULT_QUERY = '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency OR recession)'


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _json(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _compact_error(msg: str) -> str:
    s = str(msg or "")
    s = re.sub(r'(api[Kk]ey=)[^&\s]+', r'\1***', s)
    s = re.sub(r'(apikey=)[^&\s]+', r'\1***', s)
    s = re.sub(r'(api-key=)[^&\s]+', r'\1***', s)
    s = re.sub(r'(key=)[^&\s]+', r'\1***', s)
    s = re.sub(r'https?://([^/?\s]+)[^\s]*', r'https://\1/...', s)
    return s


def _start_run(cur, run_type: str) -> int:
    cur.execute(
        """
        INSERT INTO agent_runs (run_type, started_at, status, items_fetched, items_kept, alerts_created)
        VALUES (?, ?, ?, 0, 0, 0)
        """,
        (run_type, utc_now_iso(), "running"),
    )
    return int(cur.lastrowid)


def _finish_run(cur, run_id: int, status: str, items_fetched: int, items_kept: int, alerts_created: int, error_text: str = ""):
    cur.execute(
        """
        UPDATE agent_runs
        SET finished_at = ?, status = ?, items_fetched = ?, items_kept = ?, alerts_created = ?, error_text = ?
        WHERE id = ?
        """,
        (utc_now_iso(), status, items_fetched, items_kept, alerts_created, error_text, run_id),
    )


def _store_article(cur, article: Dict) -> int:
    cur.execute("SELECT id FROM ingested_articles WHERE url = ?", (article["url"],))
    row = cur.fetchone()
    if row:
        article_id = int(row["id"])
        cur.execute(
            """
            UPDATE ingested_articles
            SET source_name = ?, external_id = ?, headline = ?, summary = ?, published_at = ?,
                language = ?, country = ?, fetched_at = ?, content_hash = ?, is_duplicate = ?
            WHERE id = ?
            """,
            (
                article["source_name"],
                article.get("external_id", ""),
                article["headline"],
                article.get("summary", ""),
                article.get("published_at", ""),
                article.get("language", ""),
                article.get("country", ""),
                article.get("fetched_at", ""),
                article.get("content_hash", ""),
                int(article.get("is_duplicate", 0)),
                article_id,
            ),
        )
        return article_id

    cur.execute(
        """
        INSERT INTO ingested_articles (
            source_name, external_id, headline, summary, url, published_at,
            language, country, fetched_at, content_hash, is_duplicate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article["source_name"],
            article.get("external_id", ""),
            article["headline"],
            article.get("summary", ""),
            article["url"],
            article.get("published_at", ""),
            article.get("language", ""),
            article.get("country", ""),
            article.get("fetched_at", ""),
            article.get("content_hash", ""),
            int(article.get("is_duplicate", 0)),
        ),
    )
    return int(cur.lastrowid)


def _store_enrichment(cur, article_id: int, enrichment: Dict, ranking: Dict):
    cur.execute("DELETE FROM article_enrichment WHERE article_id = ?", (article_id,))
    cur.execute(
        """
        INSERT INTO article_enrichment (
            article_id, signal, sentiment_score, impact_score, asset_tags, macro_tags,
            watchlist_hits, alert_tags, thesis, bull_case, bear_case, what_to_watch,
            confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article_id,
            enrichment.get("signal", "Neutral"),
            float(enrichment.get("sentiment_score", 0.0)),
            int(ranking.get("impact_score", 0)),
            _json(enrichment.get("asset_tags", [])),
            _json(enrichment.get("macro_tags", [])),
            _json(enrichment.get("watchlist_hits", [])),
            _json(enrichment.get("alert_tags", [])),
            enrichment.get("thesis", ""),
            enrichment.get("bull_case", ""),
            enrichment.get("bear_case", ""),
            enrichment.get("what_to_watch", ""),
            int(ranking.get("confidence", 0)),
            utc_now_iso(),
        ),
    )


def _store_alert(cur, article_id: int, ranking: Dict, enrichment: Dict) -> int:
    impact_score = int(ranking.get("impact_score", 0))
    priority = ranking.get("priority", "watch")
    alert_tags = enrichment.get("alert_tags", []) or []
    watch_hits = enrichment.get("watchlist_hits", []) or []

    should_alert = (
        priority in ("high", "urgent")
        or (
            impact_score >= ALERT_MIN_IMPACT_SCORE
            and (
                len(alert_tags) >= ALERT_MIN_ALERT_TAGS
                or len(watch_hits) >= ALERT_MIN_WATCHLIST_HITS
            )
        )
    )

    if not should_alert:
        return 0

    cur.execute("SELECT id FROM alert_events WHERE article_id = ? LIMIT 1", (article_id,))
    if cur.fetchone():
        return 0

    reason_parts = alert_tags or watch_hits or ["impact"]
    reason = ", ".join(reason_parts[:5])

    cur.execute(
        """
        INSERT INTO alert_events (article_id, priority, reason, created_at, is_read)
        VALUES (?, ?, ?, ?, 0)
        """,
        (article_id, priority, reason, utc_now_iso()),
    )
    return 1


def _get_sources(enabled_sources=None):
    enabled_sources = set(enabled_sources or [])
    out = []

    def allowed(name: str) -> bool:
        return not enabled_sources or name in enabled_sources

    if ENABLE_RSS and allowed("rss"):
        out.append(("rss", RSSSource()))
    if ENABLE_GDELT and allowed("gdelt"):
        out.append(("gdelt", GDELTSource()))
    if ENABLE_NEWSAPI and allowed("newsapi"):
        out.append(("newsapi", NewsAPISource()))
    if ENABLE_GUARDIAN and allowed("guardian"):
        out.append(("guardian", GuardianSource()))
    return out


def run_ingestion_cycle(query: str = None, max_records_per_source: int = 20, watchlist: List[str] = None, enabled_sources: List[str] = None) -> Dict:
    watchlist = watchlist or DEFAULT_WATCHLIST
    query = query or DEFAULT_QUERY

    conn = get_conn()
    cur = conn.cursor()
    run_id = _start_run(cur, "ingestion_cycle")
    conn.commit()

    raw_items = []
    errors = []
    used_sources = []

    for name, source in _get_sources(enabled_sources=enabled_sources):
        try:
            items = source.fetch(query=query, max_records=max_records_per_source)
            raw_items.extend(items)
            used_sources.append(name)
        except Exception as exc:
            errors.append(f"{name}: {_compact_error(str(exc))}")

    normalized = [normalize_article(x) for x in raw_items if getattr(x, "headline", "") and getattr(x, "url", "")]
    unique_items = dedupe_articles(normalized)

    alerts_created = 0
    scored = []

    try:
        for article in unique_items:
            enrichment = classify_article(article, watchlist=watchlist)
            ranking = rank_article(article, enrichment)

            article_id = _store_article(cur, article)
            _store_enrichment(cur, article_id, enrichment, ranking)
            alerts_created += _store_alert(cur, article_id, ranking, enrichment)

            scored.append({
                "article_id": article_id,
                "headline": article["headline"],
                "source_name": article["source_name"],
                "url": article["url"],
                "published_at": article.get("published_at", ""),
                "signal": enrichment["signal"],
                "impact_score": ranking["impact_score"],
                "priority": ranking["priority"],
                "alert_tags": enrichment["alert_tags"],
                "asset_tags": enrichment["asset_tags"],
                "watchlist_hits": enrichment["watchlist_hits"],
            })

        _finish_run(
            cur,
            run_id=run_id,
            status="ok" if not errors else "partial",
            items_fetched=len(raw_items),
            items_kept=len(unique_items),
            alerts_created=alerts_created,
            error_text=" | ".join(errors),
        )
        conn.commit()
    except Exception as exc:
        _finish_run(
            cur,
            run_id=run_id,
            status="failed",
            items_fetched=len(raw_items),
            items_kept=0,
            alerts_created=alerts_created,
            error_text=_compact_error(str(exc)),
        )
        conn.commit()
        conn.close()
        raise

    conn.close()

    scored.sort(key=lambda x: x["impact_score"], reverse=True)
    return {
        "status": "ok" if not errors else "partial",
        "items_fetched": len(raw_items),
        "items_kept": len(unique_items),
        "alerts_created": alerts_created,
        "errors": errors,
        "used_sources": used_sources,
        "top": scored[:10],
    }
