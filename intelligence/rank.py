from datetime import datetime, timezone
from typing import Dict

from .quality import looks_low_quality, parse_dt, trust_score


def _recency_bonus(published_at: str) -> int:
    dt = parse_dt(published_at)
    if dt is None:
        return 0
    mins = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 60))
    if mins <= 60:
        return 8
    if mins <= 240:
        return 6
    if mins <= 1440:
        return 4
    if mins <= 4320:
        return 2
    return 0


def rank_article(article: Dict, enrichment: Dict) -> Dict:
    alert_count = len(enrichment.get("alert_tags", []) or [])
    watch_count = len(enrichment.get("watchlist_hits", []) or [])
    macro_count = len(enrichment.get("macro_tags", []) or [])
    asset_tags = enrichment.get("asset_tags", []) or []
    asset_count = len(asset_tags)

    score = 0
    score += alert_count * 16
    score += watch_count * 8
    score += macro_count * 6

    if "OIL" in asset_tags:
        score += 5
    if "GOLD" in asset_tags:
        score += 4
    if "FOREX" in asset_tags:
        score += 5
    if "RATES" in asset_tags:
        score += 6
    if "STOCKS" in asset_tags:
        score += 4

    signal = enrichment.get("signal", "Neutral")
    if signal in ("Bullish", "Bearish"):
        score += 5
    else:
        score += 1

    source_weight = trust_score(article.get("source_name", ""), article.get("url", ""))
    score += source_weight
    score += _recency_bonus(article.get("published_at", ""))

    if alert_count >= 2:
        score += 8
    if watch_count >= 2:
        score += 6
    if macro_count >= 2:
        score += 5
    if asset_count >= 2:
        score += 4
    if source_weight >= 7 and (alert_count or watch_count):
        score += 6

    if looks_low_quality(
        article.get("source_name", ""),
        article.get("url", ""),
        headline=article.get("headline", ""),
        summary=article.get("summary", ""),
    ):
        score -= 20

    impact_score = max(0, min(100, int(score)))

    if impact_score >= 78 or (impact_score >= 70 and alert_count >= 2 and source_weight >= 6):
        priority = "urgent"
    elif impact_score >= 52 or (watch_count >= 2 and impact_score >= 45):
        priority = "high"
    elif impact_score >= 28:
        priority = "watch"
    else:
        priority = "noise"

    confidence = min(
        100,
        30
        + alert_count * 10
        + watch_count * 6
        + macro_count * 4
        + source_weight * 5
        + (8 if priority in ("high", "urgent") else 0),
    )

    return {
        "impact_score": impact_score,
        "priority": priority,
        "confidence": confidence,
    }
