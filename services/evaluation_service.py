from typing import Dict, List

from services.calibration_service import record_outcome
from services.memory_service import list_memory, write_memory
from services.thesis_service import upsert_thesis


def evaluate_previous_items(current_cards: List[Dict], max_items: int = 20) -> List[Dict]:
    prior = list_memory(limit=120, statuses=["active", "confirmed", "weakened"])
    current_by_article = {int(card.get("article_id") or 0): card for card in current_cards if card.get("article_id")}
    outcomes: List[Dict] = []

    for item in prior[:max_items]:
        article_id = int(item.get("article_id") or 0)
        current = current_by_article.get(article_id)
        thesis = str(item.get("thesis", "") or "")
        prior_conf = int(item.get("confidence", 0) or 0)
        if current:
            signal = str(current.get("signal", "Neutral") or "Neutral")
            alert_tags = [str(tag or "").upper() for tag in (current.get("alert_tags", []) or [])]
            if bool(current.get("contradicts_narrative")) or "CONTRADICTION" in alert_tags:
                outcome = "contradicted"
                note = "Current follow-up includes contradiction markers against the stored thesis."
            elif signal == "Neutral" and prior_conf >= 60:
                outcome = "weakened"
                note = "Current signal softened to neutral."
            elif signal == "Bearish" and "bull" in thesis.lower():
                outcome = "contradicted"
                note = "Follow-up signal now points in the opposite direction."
            elif signal == "Bullish" and "bear" in thesis.lower():
                outcome = "contradicted"
                note = "Follow-up signal now points in the opposite direction."
            elif int(current.get("impact_score", 0) or 0) + 10 < prior_conf:
                outcome = "weakened"
                note = "Fresh follow-up arrived, but with lower conviction."
            else:
                outcome = "confirmed"
                note = "Fresh follow-up continues to support the earlier thesis."
        else:
            outcome = "stale"
            note = "No fresh confirmation appeared in the latest observed set."

        outcomes.append(
            write_memory(
                article_id=article_id or None,
                memory_type="evaluation",
                thesis=thesis,
                confidence=max(30, prior_conf - 5 if outcome in ("weakened", "stale") else prior_conf),
                status=outcome,
                notes=note,
                thesis_key=item.get("thesis_key", ""),
            )
        )
        upsert_thesis(
            thesis_key=item.get("thesis_key", "") or thesis,
            current_claim=thesis,
            confidence=max(0.3, min(1.0, float(prior_conf) / 100.0)),
            status=outcome,
            evidence_delta=0 if outcome == "stale" else 1,
            last_article_id=article_id or None,
            notes=note,
            contradiction_delta=1 if outcome == "contradicted" else 0,
        )
        record_outcome(item.get("thesis_key", "") or thesis, outcome)
    return outcomes
