import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso
from services.llm_service import analyse_custom_json
from services.thesis_service import record_thesis_event, update_thesis_confidence, upsert_thesis
from sources import RSSSource, GDELTSource


def _fallback_queries(current_claim: str, category: str) -> List[str]:
    base = " ".join(str(current_claim or "").split()[:6]).strip() or str(category or "market news")
    return [
        f"{base} confirmation",
        f"{base} contradiction",
        f"{base} latest update",
    ]


def _search_local_articles(query: str, hours: int = 48) -> List[Dict]:
    ensure_agent_tables()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_conn()
    cur = conn.cursor()
    like = "%" + str(query or "").strip().lower() + "%"
    cur.execute(
        """
        SELECT ia.headline, ia.summary, ia.url, ia.source_name, ia.published_at
        FROM ingested_articles ia
        WHERE COALESCE(ia.fetched_at, ia.published_at, '') >= ?
          AND (
            LOWER(COALESCE(ia.headline, '')) LIKE ?
            OR LOWER(COALESCE(ia.summary, '')) LIKE ?
          )
        ORDER BY COALESCE(ia.published_at, ia.fetched_at, '') DESC, ia.id DESC
        LIMIT 8
        """,
        (cutoff, like, like),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _search_live_articles(query: str) -> List[Dict]:
    results = []
    for source in (RSSSource(), GDELTSource()):
        try:
            items = source.fetch(query=query, max_records=4)
        except Exception:
            continue
        for item in items or []:
            results.append(
                {
                    "headline": str(getattr(item, "headline", "") or getattr(item, "title", "") or ""),
                    "summary": str(getattr(item, "summary", "") or ""),
                    "url": str(getattr(item, "url", "") or ""),
                    "source_name": str(getattr(item, "source_name", "") or getattr(item, "source", "") or "live"),
                    "published_at": str(getattr(item, "published_at", "") or ""),
                }
            )
    return [row for row in results if row.get("headline")]


def _evaluate_article(current_claim: str, article: Dict) -> Dict:
    headline = str(article.get("headline", "") or "")
    summary = str(article.get("summary", "") or "")
    text = f"{headline} {summary}".lower()
    fallback_verdict = "unrelated"
    if any(word in text for word in ("contradict", "falls", "drop", "recession", "downgrade", "sell")):
        fallback_verdict = "contradict"
    elif any(word in text for word in ("rise", "rally", "upgrade", "buy", "support", "growth")):
        fallback_verdict = "support"
    fallback = {"verdict": fallback_verdict, "reason": ""}
    system_text = "Does this article support, contradict, or is it unrelated to this thesis? Return JSON only: { verdict: support or contradict or unrelated, reason: one sentence }"
    user_text = f"Thesis: {current_claim}\nArticle: {headline} {summary}"

    def _valid(payload):
        return isinstance(payload, dict) and str(payload.get("verdict", "")).strip().lower() in {"support", "contradict", "unrelated"}

    def _clean(payload):
        return {
            "verdict": str(payload.get("verdict") or fallback_verdict).strip().lower(),
            "reason": str(payload.get("reason") or "").strip(),
        }

    return analyse_custom_json(
        system_text,
        user_text,
        fallback=fallback,
        mode="research_verdict",
        cache_key=f"research_verdict::{headline[:80]}::{current_claim[:80]}",
        validator=_valid,
        cleaner=_clean,
    )["analysis"]


def _journal_research(thesis_key: str, summary: str, metrics: Dict):
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_journal (run_id, journal_type, summary, metrics_json, created_at)
        VALUES (NULL, 'research_agent', ?, ?, ?)
        """,
        (summary, json.dumps(metrics or {}), utc_now_iso()),
    )
    conn.commit()
    conn.close()


def research_thesis(thesis_key, current_claim, category):
    clean_key = str(thesis_key or "").strip()
    system_text = (
        "You are a research analyst. Given this thesis claim, generate 3 specific "
        "search queries that would find confirming or contradicting evidence. "
        "Return JSON only: { queries: [string, string, string] }"
    )
    user_text = f"Thesis: {current_claim}\nCategory: {category}"
    fallback = {"queries": _fallback_queries(current_claim, category)}

    def _valid(payload):
        return isinstance(payload, dict) and isinstance(payload.get("queries"), list) and len(payload.get("queries")) >= 3

    def _clean(payload):
        queries = [str(item or "").strip() for item in (payload.get("queries") or []) if str(item or "").strip()]
        while len(queries) < 3:
            queries.extend(_fallback_queries(current_claim, category))
        return {"queries": queries[:3]}

    query_meta = analyse_custom_json(
        system_text,
        user_text,
        fallback=fallback,
        mode="research_queries",
        cache_key=f"research_queries::{clean_key or current_claim[:80]}",
        validator=_valid,
        cleaner=_clean,
    )
    queries = query_meta["analysis"]["queries"]

    found_articles = []
    for query in queries:
        local_rows = _search_local_articles(query)
        if len(local_rows) < 2:
            local_rows.extend(_search_live_articles(query))
        for row in local_rows:
            if row.get("url") and row["url"] not in {item.get("url") for item in found_articles}:
                found_articles.append(row)

    support_rows = []
    contradict_rows = []
    verdicts = []
    for article in found_articles[:12]:
        verdict = _evaluate_article(current_claim, article)
        verdicts.append({"headline": article.get("headline", ""), **verdict})
        if verdict.get("verdict") == "support":
            support_rows.append(article)
        elif verdict.get("verdict") == "contradict":
            contradict_rows.append({"article": article, "reason": verdict.get("reason", "")})

    if len(support_rows) >= 2:
        for row in support_rows[:3]:
            update_thesis_confidence(clean_key, row.get("source_name", "unknown"), 0.72)
        upsert_thesis(
            clean_key,
            current_claim=current_claim,
            status="active",
            evidence_delta=len(support_rows[:3]),
            source_name=support_rows[0].get("source_name", "unknown") if support_rows else "unknown",
            category=category,
            last_update_reason="Research agent found supporting evidence.",
        )

    if contradict_rows:
        reason = contradict_rows[0].get("reason", "") or contradict_rows[0]["article"].get("headline", "")
        record_thesis_event(clean_key, "contradicted", reason, 0.0, len(contradict_rows))
        upsert_thesis(
            clean_key,
            current_claim=current_claim,
            status="contradicted",
            contradiction_delta=len(contradict_rows),
            evidence_delta=0,
            source_name=contradict_rows[0]["article"].get("source_name", "unknown"),
            category=category,
            last_update_reason=reason,
        )

    metrics = {
        "research_agent_runs": 1,
        "queries": queries,
        "articles_found": len(found_articles),
        "support": len(support_rows),
        "contradict": len(contradict_rows),
    }
    _journal_research(clean_key, f"Research agent reviewed {clean_key or 'thesis'}", metrics)
    return {
        "status": "ok",
        "queries": queries,
        "articles_found": len(found_articles),
        "support_count": len(support_rows),
        "contradict_count": len(contradict_rows),
        "verdicts": verdicts,
        "metrics": metrics,
    }
