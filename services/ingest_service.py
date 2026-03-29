import json
import re
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
    MAX_REASONING_CHAINS_PER_CLUSTER,
)
from intelligence import normalize_article, classify_article, dedupe_articles, rank_article, suppress_articles
from intelligence.classify import check_contradiction
from intelligence.quality import looks_low_quality, normalize_headline
from sources import RSSSource, GDELTSource, NewsAPISource, GuardianSource
from services.db_helpers import get_conn as shared_get_conn
from services.llm_service import analyse_article_meta, analyse_cluster_meta, new_llm_run_state, summarize_llm_run_state
from services.reasoning_service import build_reasoning_chain


DEFAULT_QUERY = '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency OR recession)'
LLM_RELEVANCE_THRESHOLD = 0.30
ENRICHMENT_COLUMN_DDL = {
    "why_it_matters": "TEXT",
    "confidence_score": "REAL DEFAULT 0.5",
    "urgency_level": "TEXT DEFAULT 'medium'",
    "impact_radius": "TEXT DEFAULT 'regional'",
    "contradicts_narrative": "INTEGER DEFAULT 0",
    "llm_category": "TEXT DEFAULT 'other'",
    "llm_importance": "TEXT DEFAULT 'medium'",
    "llm_mode": "TEXT DEFAULT ''",
    "llm_fallback_reason": "TEXT DEFAULT ''",
    "cluster_key": "TEXT DEFAULT ''",
    "cluster_size": "INTEGER DEFAULT 1",
}
INGESTED_ARTICLE_COLUMN_DDL = {
    "is_reasoned": "INTEGER DEFAULT 0",
}
CLUSTER_STOPWORDS = {
    "after", "amid", "analyst", "analysts", "bank", "banks", "from", "have", "into", "latest",
    "market", "markets", "more", "news", "over", "report", "reports", "reporting", "said", "says",
    "share", "shares", "still", "stocks", "than", "that", "their", "there", "these", "this",
    "those", "under", "update", "with", "would",
}
REASONING_CATEGORIES = {"markets", "geopolitics", "energy"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    return shared_get_conn(DB_PATH)


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


def _table_columns(cur, table_name: str):
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def _ensure_article_enrichment_columns(cur):
    existing = _table_columns(cur, "article_enrichment")
    for column_name, ddl in ENRICHMENT_COLUMN_DDL.items():
        if column_name not in existing:
            cur.execute(f"ALTER TABLE article_enrichment ADD COLUMN {column_name} {ddl}")


def _ensure_ingested_article_columns(cur):
    existing = _table_columns(cur, "ingested_articles")
    for column_name, ddl in INGESTED_ARTICLE_COLUMN_DDL.items():
        if column_name not in existing:
            cur.execute(f"ALTER TABLE ingested_articles ADD COLUMN {column_name} {ddl}")


def _should_analyse_with_llm(article: Dict, ranking: Dict) -> bool:
    combined_text = " ".join(
        [str(article.get("headline", "") or "").strip(), str(article.get("summary", "") or "").strip()]
    ).strip()
    if len(combined_text) < 40:
        return False
    if looks_low_quality(
        article.get("source_name", ""),
        article.get("url", ""),
        headline=article.get("headline", ""),
        summary=article.get("summary", ""),
    ):
        return False
    relevance_score = float(ranking.get("impact_score", 0)) / 100.0
    return relevance_score > LLM_RELEVANCE_THRESHOLD


def _cluster_terms(article: Dict, enrichment: Dict) -> List[str]:
    text = " ".join(
        [
            normalize_headline(article.get("headline", "")),
            str(article.get("summary", "") or "").lower(),
        ]
    )
    ordered = []
    for token in re.findall(r"[a-z0-9]{3,}", text):
        if token in CLUSTER_STOPWORDS:
            continue
        if token not in ordered:
            ordered.append(token)

    tags = []
    for item in (enrichment.get("asset_tags", []) or []) + (enrichment.get("watchlist_hits", []) or []) + (enrichment.get("macro_tags", []) or []):
        clean = re.sub(r"[^a-z0-9]+", "", str(item or "").strip().lower())
        if clean and clean not in tags:
            tags.append(clean)
    return (ordered[:4] + tags[:3])[:6]


def _cluster_key(article: Dict, enrichment: Dict) -> str:
    terms = _cluster_terms(article, enrichment)
    if terms:
        return "cluster:" + "|".join(terms)
    fallback = normalize_headline(article.get("headline", ""))[:48] or article.get("url", "") or "story"
    return "cluster:" + fallback


def _cluster_entries(entries: List[Dict]) -> List[Dict]:
    clusters = []
    for entry in entries:
        terms = set(_cluster_terms(entry["article"], entry["enrichment"]))
        key = _cluster_key(entry["article"], entry["enrichment"])
        entry["cluster_key"] = key
        matched = None
        best_score = 0.0
        for cluster in clusters:
            union = terms | cluster["terms"]
            if not union:
                continue
            overlap = len(terms & cluster["terms"]) / float(len(union))
            if overlap >= 0.45 and overlap > best_score:
                matched = cluster
                best_score = overlap
        if matched:
            matched["items"].append(entry)
            matched["terms"] = matched["terms"] | terms
        else:
            clusters.append({"cluster_key": key, "terms": terms, "items": [entry]})
    return [{"cluster_key": cluster["cluster_key"], "items": cluster["items"]} for cluster in clusters]


def _apply_llm_analysis(entry: Dict, analysis: Dict, mode: str, fallback_reason: str):
    enrichment = entry["enrichment"]
    ranking = entry["ranking"]

    enrichment["why_it_matters"] = str(analysis.get("why_it_matters", "") or "")
    enrichment["contradicts_narrative"] = bool(analysis.get("contradicts_narrative", False))
    enrichment["llm_category"] = str(analysis.get("category", "other") or "other")
    enrichment["llm_importance"] = str(analysis.get("importance", "medium") or "medium")
    enrichment["llm_mode"] = str(mode or "")
    enrichment["llm_fallback_reason"] = str(fallback_reason or "")
    enrichment["cluster_key"] = str(entry.get("cluster_key", "") or "")
    enrichment["cluster_size"] = int(entry.get("cluster_size", 1) or 1)

    cluster_thesis = str(analysis.get("thesis", "") or "").strip()
    if cluster_thesis and enrichment["cluster_size"] > 1:
        enrichment["thesis"] = cluster_thesis

    ranking["confidence_score"] = float(analysis.get("confidence", ranking.get("confidence_score", 0.5)) or 0.5)
    ranking["urgency_level"] = str(analysis.get("urgency", ranking.get("urgency_level", "medium")) or "medium")
    ranking["impact_radius"] = str(analysis.get("impact", ranking.get("impact_radius", "regional")) or "regional")

    importance = str(analysis.get("importance", "medium") or "medium")
    boost = {"low": 0, "medium": 2, "high": 6, "critical": 10}.get(importance, 0) if mode in ("article", "cluster") else 0
    current_impact = int(ranking.get("impact_score", 0) or 0)
    ranking["impact_score"] = min(100, max(current_impact, current_impact + boost))

    if enrichment["contradicts_narrative"]:
        tags = list(enrichment.get("alert_tags", []) or [])
        if "CONTRADICTION" not in tags:
            tags.append("CONTRADICTION")
        enrichment["alert_tags"] = tags


def _apply_skipped_llm_defaults(entry: Dict):
    _apply_llm_analysis(entry, {}, "skipped", "")


def _effective_category(article: Dict, enrichment: Dict) -> str:
    current = str(enrichment.get("llm_category", "other") or "other").strip().lower()
    if current in REASONING_CATEGORIES:
        return current
    asset_tags = {str(item or "").strip().upper() for item in (enrichment.get("asset_tags", []) or [])}
    macro_tags = {str(item or "").strip().upper() for item in (enrichment.get("macro_tags", []) or [])}
    alert_tags = {str(item or "").strip().upper() for item in (enrichment.get("alert_tags", []) or [])}
    text = " ".join([str(article.get("headline", "") or ""), str(article.get("summary", "") or "")]).lower()

    if macro_tags & {"GEOPOLITICS"} or alert_tags & {"WAR", "SANCTIONS", "TARIFF"} or any(token in text for token in ("iran", "israel", "hormuz", "missile", "strike", "sanctions", "tariff")):
        return "geopolitics"
    if asset_tags & {"OIL", "WTI", "BRENT", "ENERGY"} or any(token in text for token in ("oil", "crude", "opec", "brent", "wti", "natural gas", "lng")):
        return "energy"
    if asset_tags & {"RATES", "FOREX", "GOLD", "BTC", "SPY"} or any(token in text for token in ("yield", "rates", "treasury", "forex", "fx", "dollar", "stocks", "equities", "gold", "bitcoin", "market")):
        return "markets"
    return current


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
                language = ?, country = ?, fetched_at = ?, content_hash = ?, is_duplicate = ?, is_reasoned = 0
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
            language, country, fetched_at, content_hash, is_duplicate, is_reasoned
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
            confidence, why_it_matters, confidence_score, urgency_level, impact_radius,
            contradicts_narrative, llm_category, llm_importance, llm_mode, llm_fallback_reason,
            cluster_key, cluster_size, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            enrichment.get("why_it_matters", ""),
            float(ranking.get("confidence_score", 0.5)),
            ranking.get("urgency_level", "medium"),
            ranking.get("impact_radius", "regional"),
            int(bool(enrichment.get("contradicts_narrative", False))),
            enrichment.get("llm_category", "other"),
            enrichment.get("llm_importance", "medium"),
            enrichment.get("llm_mode", ""),
            enrichment.get("llm_fallback_reason", ""),
            enrichment.get("cluster_key", ""),
            int(enrichment.get("cluster_size", 1) or 1),
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


def run_ingestion_cycle(
    query: str = None,
    max_records_per_source: int = 20,
    watchlist: List[str] = None,
    enabled_sources: List[str] = None,
    reasoning_budget: int = 0,
) -> Dict:
    watchlist = watchlist or DEFAULT_WATCHLIST
    query = query or DEFAULT_QUERY

    conn = get_conn()
    cur = conn.cursor()
    _ensure_ingested_article_columns(cur)
    _ensure_article_enrichment_columns(cur)
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
    filtered_items, suppressed_items = suppress_articles(normalized)
    unique_items = dedupe_articles(filtered_items)

    alerts_created = 0
    reasoning_chains_built = 0
    reasoning_cap_blocks = 0
    reasoning_by_cluster = {}
    scored = []
    llm_metrics = {
        "eligible_articles": 0,
        "eligible_clusters": 0,
        "fallback_articles": 0,
        "fallback_reasons": {
            "missing_key": 0,
            "api_error": 0,
            "validation_error": 0,
            "budget_blocked": 0,
        },
    }
    llm_run_state = new_llm_run_state()

    try:
        prepared_entries = []
        for article in unique_items:
            enrichment = classify_article(article, watchlist=watchlist)
            if check_contradiction(
                " ".join([str(article.get("headline", "") or ""), str(article.get("summary", "") or "")]),
                conn,
            ):
                tags = list(enrichment.get("alert_tags", []) or [])
                if "CRITICAL_CONTRADICTION" not in tags:
                    tags.append("CRITICAL_CONTRADICTION")
                enrichment["alert_tags"] = tags
            ranking = rank_article(article, enrichment)
            prepared_entries.append(
                {
                    "article": article,
                    "enrichment": enrichment,
                    "ranking": ranking,
                    "eligible_for_llm": _should_analyse_with_llm(article, ranking),
                }
            )

        for cluster in _cluster_entries(prepared_entries):
            items = cluster["items"]
            cluster_size = len(items)
            for item in items:
                item["cluster_key"] = cluster["cluster_key"]
                item["cluster_size"] = cluster_size

            eligible_items = [item for item in items if item.get("eligible_for_llm")]
            if not eligible_items:
                for item in items:
                    _apply_skipped_llm_defaults(item)
                continue

            llm_metrics["eligible_articles"] += len(eligible_items)
            llm_metrics["eligible_clusters"] += 1

            if len(items) > 1:
                meta = analyse_cluster_meta([item["article"] for item in items], cluster_key=cluster["cluster_key"], run_state=llm_run_state)
            else:
                article = items[0]["article"]
                meta = analyse_article_meta(
                    article.get("headline", ""),
                    article.get("summary", ""),
                    article.get("source_name", ""),
                    cache_key=cluster["cluster_key"],
                    run_state=llm_run_state,
                )

            fallback_reason = str(meta.get("fallback_reason", "") or "")
            if meta.get("used_fallback"):
                llm_metrics["fallback_articles"] += len(eligible_items)
                if fallback_reason not in llm_metrics["fallback_reasons"]:
                    llm_metrics["fallback_reasons"][fallback_reason] = 0
                llm_metrics["fallback_reasons"][fallback_reason] += len(eligible_items)

            analysis = meta.get("analysis", {}) or {}
            applied_mode = "default" if meta.get("used_fallback") else str(meta.get("mode", "") or "")
            for item in items:
                _apply_llm_analysis(item, analysis, applied_mode, fallback_reason)

        for item in prepared_entries:
            article = item["article"]
            enrichment = item["enrichment"]
            ranking = item["ranking"]
            inferred_category = _effective_category(article, enrichment)
            if str(enrichment.get("llm_category", "other") or "other").lower() == "other" and inferred_category in REASONING_CATEGORIES:
                enrichment["llm_category"] = inferred_category
            article_id = _store_article(cur, article)
            _store_enrichment(cur, article_id, enrichment, ranking)
            alerts_created += _store_alert(cur, article_id, ranking, enrichment)
            if (
                int(reasoning_budget or 0) > reasoning_chains_built
                and float(int(ranking.get("impact_score", 0) or 0)) / 100.0 > 0.60
                and inferred_category in REASONING_CATEGORIES
            ):
                cluster_key = str(enrichment.get("cluster_key", "") or "")
                if cluster_key and int(reasoning_by_cluster.get(cluster_key, 0) or 0) >= int(MAX_REASONING_CHAINS_PER_CLUSTER):
                    reasoning_cap_blocks += 1
                else:
                    try:
                        build_reasoning_chain(
                            article.get("headline", ""),
                            inferred_category,
                            db=conn,
                            article_id=article_id,
                            thesis_key=enrichment.get("thesis", "") or article.get("headline", ""),
                            source_name=article.get("source_name", ""),
                        )
                        reasoning_chains_built += 1
                        if cluster_key:
                            reasoning_by_cluster[cluster_key] = int(reasoning_by_cluster.get(cluster_key, 0) or 0) + 1
                    except Exception:
                        pass

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
                "cluster_key": enrichment.get("cluster_key", ""),
                "llm_mode": enrichment.get("llm_mode", ""),
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
    llm_metrics.update(summarize_llm_run_state(llm_run_state))
    return {
        "status": "ok" if not errors else "partial",
        "items_fetched": len(raw_items),
        "items_kept": len(unique_items),
        "items_suppressed": len(suppressed_items),
        "alerts_created": alerts_created,
        "errors": errors,
        "suppressed_preview": suppressed_items[:5],
        "used_sources": used_sources,
        "llm_metrics": llm_metrics,
        "reasoning_cap_blocks": reasoning_cap_blocks,
        "reasoning_chains_built": reasoning_chains_built,
        "top": scored[:10],
    }
