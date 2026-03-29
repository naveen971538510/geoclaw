from datetime import datetime, timezone
from typing import Dict


TRUST_WEIGHTS = {
    "reuters": 8,
    "bloomberg": 8,
    "financial times": 8,
    "ft": 8,
    "wsj": 8,
    "bbc": 7,
    "the guardian": 6,
    "guardian": 6,
    "cnbc": 6,
    "marketwatch": 6,
    "yahoo": 5,
    "investing.com": 5,
    "associated press": 7,
    "ap": 6,
    "le monde": 5,
}


def _parse_dt(value: str):
    s = str(value or "").strip()
    if not s:
        return None
    for candidate in (s, s.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def _source_weight(source_name: str) -> int:
    low = str(source_name or "").lower()
    for key, weight in TRUST_WEIGHTS.items():
        if key in low:
            return weight
    return 1


def _recency_bonus(published_at: str) -> int:
    dt = _parse_dt(published_at)
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
    score = 0

    score += len(enrichment.get("alert_tags", [])) * 16
    score += len(enrichment.get("watchlist_hits", [])) * 8

    asset_tags = enrichment.get("asset_tags", []) or []
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

    source_weight = _source_weight(article.get("source_name", ""))
    score += source_weight
    score += _recency_bonus(article.get("published_at", ""))

    impact_score = max(0, min(100, int(score)))

    if impact_score >= 80:
        priority = "urgent"
    elif impact_score >= 60:
        priority = "high"
    elif impact_score >= 30:
        priority = "watch"
    else:
        priority = "noise"

    confidence = min(100, 35 + len(enrichment.get("alert_tags", [])) * 10 + source_weight * 5)

    return {
        "impact_score": impact_score,
        "priority": priority,
        "confidence": confidence,
    }
