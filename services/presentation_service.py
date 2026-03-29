from typing import Dict, List

from intelligence.quality import looks_low_quality, normalize_headline, source_domain, trust_score, ts
from market import get_latest_market_snapshots
from market.prices import get_mock_market_snapshots
from services.operator_state_service import get_operator_state
from services.provider_state_service import get_provider_state
from services.terminal_service import get_terminal_payload as _raw_get_terminal_payload

def _ts(value: str) -> float:
    return ts(value)


def _source_name(card: Dict) -> str:
    return str(card.get("source", "") or "")


def _source_text(card: Dict) -> str:
    return (_source_name(card) + " " + source_domain(card.get("url", ""))).lower()


def _trust_score(card: Dict) -> int:
    return trust_score(_source_name(card), card.get("url", ""))


def _looks_low_quality(card: Dict) -> bool:
    return looks_low_quality(
        _source_name(card),
        card.get("url", ""),
        headline=card.get("headline", ""),
        summary=" ".join(card.get("alert_tags", []) or []),
    )


def _normalized_headline(text: str) -> str:
    return normalize_headline(text)[:140]


def _card_quality(card: Dict) -> int:
    impact = int(card.get("impact_score", 0) or 0)
    trust = _trust_score(card)
    alerts = len(card.get("alert_tags", []) or [])
    watch = len(card.get("watchlist_hits", []) or [])
    recency = 3 if _ts(card.get("published_at", "")) > 0 else 0
    penalty = 10 if _looks_low_quality(card) else 0
    return impact + trust * 4 + alerts * 10 + watch * 4 + recency - penalty


def _trust_label(card: Dict) -> str:
    trust = _trust_score(card)
    if trust >= 4:
        return "trusted"
    if trust >= 2:
        return "mixed"
    return "unverified"


def _quality_note(card: Dict) -> str:
    if _looks_low_quality(card):
        return "Lower-quality source. Hide it with the junk filter if needed."
    if _trust_score(card) >= 4:
        return "High-trust source."
    return "Use context and corroboration before acting."


def _dedupe_cards(cards: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for card in cards:
        key = (
            _normalized_headline(card.get("headline", "")),
            tuple(sorted(card.get("asset_tags", []) or [])),
        )
        if not key[0]:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(card)
    return out


def _clean_cards(cards: List[Dict], limit: int) -> List[Dict]:
    enriched = []
    for card in cards:
        c = dict(card)
        c["_trust_score"] = _trust_score(c)
        c["_quality"] = _card_quality(c)
        enriched.append(c)

    enriched.sort(
        key=lambda c: (
            c["_quality"],
            c["_trust_score"],
            _ts(c.get("published_at", "")),
        ),
        reverse=True,
    )

    strong = []
    weak = []
    for c in enriched:
        if _looks_low_quality(c) and int(c.get("impact_score", 0) or 0) < 35 and not (c.get("alert_tags") or []):
            weak.append(c)
        else:
            strong.append(c)

    ordered = _dedupe_cards(strong) + _dedupe_cards(weak)
    cleaned = ordered[:limit]

    for c in cleaned:
        c["trust_label"] = _trust_label(c)
        c["quality_note"] = _quality_note(c)
        c["is_low_quality"] = _looks_low_quality(c)
        tags = [str(tag or "").upper() for tag in (c.get("alert_tags") or [])]
        c["has_contradiction"] = "CONTRADICTION" in tags
        c["stale_signal"] = bool(c["has_contradiction"]) and int(c.get("impact_score", 0) or 0) < 45
        c.pop("_trust_score", None)
        c.pop("_quality", None)
    return cleaned


def _build_source_distribution(cards: List[Dict]) -> List[Dict]:
    counts = {}
    for c in cards:
        source = _source_name(c) or "Unknown"
        counts[source] = counts.get(source, 0) + 1
    out = [{"source": k, "count": v} for k, v in counts.items()]
    out.sort(key=lambda x: (-x["count"], x["source"]))
    return out[:12]


def _build_asset_heat(cards: List[Dict]) -> List[Dict]:
    counts = {}
    for c in cards:
        for tag in c.get("asset_tags", []) or []:
            counts[tag] = counts.get(tag, 0) + 1
    out = [{"asset": k, "count": v} for k, v in counts.items()]
    out.sort(key=lambda x: (-x["count"], x["asset"]))
    return out


def _filter_market_snapshot(items: List[Dict]) -> List[Dict]:
    good = []
    for item in (items or []):
        if item and item.get("price") is not None:
            current = dict(item)
            current["market_mode"] = current.get("market_mode") or "live"
            current["data_source"] = current.get("data_source") or "provider_live"
            good.append(current)
    if good:
        return good
    fallback = [dict(x) for x in get_latest_market_snapshots() if x and x.get("price") is not None]
    if fallback:
        for item in fallback:
            item["market_mode"] = item.get("market_mode") or "cached"
            item["data_source"] = item.get("data_source") or "database_cache"
        return fallback
    return get_mock_market_snapshots()


def _provider_badges() -> List[Dict]:
    state = get_provider_state()
    providers = state.get("providers", {}) or {}
    out = []

    for provider in ["rss", "gdelt", "newsapi", "guardian", "alphavantage"]:
        entry = providers.get(provider, {})
        status = str(entry.get("status", "") or "")
        reason = str(entry.get("reason", "") or "")

        if provider == "rss":
            out.append({"name": "rss", "status": "ok", "label": "RSS"})
            continue
        if provider == "gdelt" and status == "limited":
            out.append({"name": "gdelt", "status": "limited", "label": "GDELT", "reason": reason})
            continue
        if status == "invalid":
            out.append({"name": provider, "status": "invalid", "label": provider.upper(), "reason": reason})
        elif status == "limited":
            out.append({"name": provider, "status": "limited", "label": provider.upper(), "reason": reason})
        elif status == "ok" or provider == "gdelt":
            out.append({"name": provider, "status": "ok", "label": provider.upper()})
        else:
            out.append({"name": provider, "status": "unknown", "label": provider.upper()})
    return out


def get_terminal_payload_clean(limit: int = 100) -> Dict:
    payload = _raw_get_terminal_payload(limit=max(200, limit))
    cards = payload.get("cards", []) or []
    top_alerts = payload.get("top_alerts", []) or []

    cleaned_cards = _clean_cards(cards, limit=limit)
    cleaned_alerts = _clean_cards(top_alerts, limit=10)

    bullish = sum(1 for c in cleaned_cards if c.get("signal") == "Bullish")
    bearish = sum(1 for c in cleaned_cards if c.get("signal") == "Bearish")
    neutral = sum(1 for c in cleaned_cards if c.get("signal") == "Neutral")
    alerts = sum(1 for c in cleaned_cards if (c.get("alert_tags") or []))
    watch = sum(1 for c in cleaned_cards if (c.get("watchlist_hits") or []))

    payload["cards"] = cleaned_cards
    payload["top_alerts"] = cleaned_alerts
    payload["source_distribution"] = _build_source_distribution(cleaned_cards)
    payload["asset_heat"] = _build_asset_heat(cleaned_cards)
    payload["market_snapshot"] = _filter_market_snapshot(payload.get("market_snapshot", []))
    payload["provider_badges"] = _provider_badges()
    payload["operator_state"] = get_operator_state()
    payload["stats"] = {
        "articles": len(cleaned_cards),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "alerts": alerts,
        "watchlist_hits": watch,
    }
    return payload
