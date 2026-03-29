import json
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List

from config import (
    ACTION_CRITICAL_MIN_CONFIDENCE,
    ACTION_COOLDOWN_MINUTES,
    ACTION_PROPOSAL_MIN_CONFIDENCE,
    CLUSTER_COOLDOWN_MINUTES,
    DB_PATH,
    MAX_ACTION_PROPOSALS_PER_RUN,
    MAX_AUTONOMOUS_GOALS_PER_DAY,
    MAX_RESEARCH_RUNS_PER_DAY,
    MAX_THESIS_UPDATES_PER_RUN,
    THESIS_COOLDOWN_MINUTES,
)
from services.agent_service import run_agent_cycle
from services.agent_state_service import (
    bump_daily_counter,
    bump_real_agent_run,
    get_agent_state,
    get_daily_counter,
    is_cooldown_active,
    save_agent_state,
    set_cooldown,
)
from services.action_service import action_on_cooldown, evaluate_and_propose, list_actions
from services.briefing_service import generate_daily_briefing
from services.calibration_service import get_penalty_multiplier, record_prediction
from services.decision_service import create_decision, decision_metrics, list_decisions
from services.evaluation_service import evaluate_previous_items
from services.goal_service import ensure_agent_tables, generate_autonomous_goals, get_conn, list_goals, utc_now_iso
from services.llm_service import analyse_contradiction_meta, new_llm_run_state, summarize_llm_run_state
from services.memory_service import latest_memory_for_article, outcome_summary, write_memory
from services.presentation_service import get_terminal_payload_clean
from services.reasoning_pipeline import process_unreasoned_articles
from services.research_agent import research_thesis
from services.reflection_service import run_reflection
from services.db_helpers import get_conn as shared_get_conn
from services.task_service import close_expired_tasks, close_tasks, create_task, list_tasks
from services.thesis_service import get_thesis, list_theses, normalize_thesis_key, record_thesis_event, update_thesis_confidence, upsert_thesis
from services.thesis_lifecycle import decay_stale_theses, promote_demote_theses


def _journal(run_id: int, journal_type: str, summary: str, metrics: Dict):
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agent_journal (run_id, journal_type, summary, metrics_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(run_id) if run_id else None, str(journal_type or "run"), str(summary or ""), json.dumps(metrics or {}), utc_now_iso()),
    )
    conn.commit()
    conn.close()


def _normalize_targets(goals: List[Dict]) -> List[str]:
    seen = set()
    out = []
    for goal in goals:
        for target in goal.get("watch_targets", []) or []:
            clean = str(target or "").strip().lower()
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
    return out


def _normalize_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _thesis_key(card: Dict) -> str:
    text = (
        card.get("thesis")
        or card.get("why_it_matters")
        or card.get("headline")
        or ""
    )
    return _normalize_key(text)[:160]


def _identity_key(card: Dict) -> str:
    return _normalize_key(card.get("cluster_key") or _thesis_key(card))


def _thesis_status_for_decision(decision_type: str, contradiction_resolution: str = "") -> str:
    decision_type = str(decision_type or "").lower()
    contradiction_resolution = str(contradiction_resolution or "").lower()
    if contradiction_resolution == "contradiction" or decision_type == "downgrade":
        return "contradicted"
    if decision_type in ("alert", "upgrade"):
        return "active"
    if decision_type in ("queue", "follow_up"):
        return "tracking"
    if decision_type == "ignore":
        return "stale"
    return "active"


def _proposal_type_for_thesis(thesis: Dict) -> str:
    thesis = thesis or {}
    status = str(thesis.get("status", "") or "").lower()
    confidence = float(thesis.get("confidence", 0.0) or 0.0)
    evidence_count = int(thesis.get("evidence_count", 0) or 0)
    category = str(thesis.get("category", "other") or "other").lower()
    actionable_status = status in ("active", "confirmed", "tracking")
    if actionable_status and confidence >= float(ACTION_CRITICAL_MIN_CONFIDENCE or 0.85) and category in ("markets", "geopolitics", "energy"):
        return "email_summary"
    if actionable_status and confidence >= float(ACTION_PROPOSAL_MIN_CONFIDENCE or 0.55):
        return "alert"
    if status == "contradicted":
        return "slack_payload"
    return ""


def _contradiction_context(card: Dict) -> str:
    return " ".join(
        [
            str(card.get("headline", "") or ""),
            str(card.get("summary", "") or ""),
            str(card.get("why_it_matters", "") or ""),
        ]
    ).strip()


def _relevance_score(card: Dict, target_hits: List[str]) -> float:
    impact = float(int(card.get("impact_score", 0) or 0)) / 100.0
    confidence = float(int(card.get("confidence", 0) or 0)) / 100.0
    llm_confidence = float(card.get("confidence_score", 0.5) or 0.5)
    watch_hits = len(card.get("watchlist_hits", []) or [])
    alert_tags = len(card.get("alert_tags", []) or [])
    trusted = 0.06 if str(card.get("trust_label", "") or "") == "trusted" else 0.0
    target_bonus = min(0.18, len(target_hits) * 0.08)
    signal_bonus = 0.04 if str(card.get("signal", "Neutral") or "Neutral") != "Neutral" else 0.0
    importance_bonus = {
        "low": 0.0,
        "medium": 0.03,
        "high": 0.08,
        "critical": 0.12,
    }.get(str(card.get("llm_importance", "medium") or "medium").lower(), 0.03)
    cluster_bonus = 0.04 if int(card.get("cluster_size", 1) or 1) > 1 else 0.0
    base = min(
        1.0,
        max(impact, confidence, llm_confidence, impact * 0.55 + confidence * 0.20 + llm_confidence * 0.25)
        + watch_hits * 0.06
        + alert_tags * 0.05
        + target_bonus
        + trusted
        + signal_bonus
        + importance_bonus
        + cluster_bonus,
    )
    penalty = get_penalty_multiplier(card.get("source", "unknown"), card.get("llm_category", "other"))
    return max(0.0, min(1.0, base * float(penalty or 1.0)))


def _store_loop_alert(article_id: int, card: Dict, decision: Dict) -> int:
    if not article_id or str(decision.get("decision_type", "") or "") != "alert":
        return 0

    conn = get_conn()
    cur = conn.cursor()
    reason_parts = (
        card.get("alert_tags", [])
        or card.get("watchlist_hits", [])
        or card.get("asset_tags", [])
        or ["agent_loop"]
    )
    reason = ", ".join([str(x) for x in reason_parts[:5]]) or "agent_loop"
    now = utc_now_iso()
    cur.execute("SELECT id FROM alert_events WHERE article_id = ? ORDER BY id DESC LIMIT 1", (int(article_id),))
    row = cur.fetchone()
    if row:
        cur.execute(
            """
            UPDATE alert_events
            SET priority = ?, reason = ?, created_at = ?, status = COALESCE(status, 'open')
            WHERE id = ?
            """,
            ("high", reason, now, int(row["id"])),
        )
        touched = 1
    else:
        cur.execute(
            """
            INSERT INTO alert_events (article_id, priority, reason, created_at, is_read, status)
            VALUES (?, ?, ?, ?, 0, 'open')
            """,
            (int(article_id), "high", reason, now),
        )
        touched = 1
    conn.commit()
    conn.close()
    return touched


def _decision_for_card(card: Dict, watch_targets: List[str], thesis_state: Dict = None, contradiction_resolution: str = "") -> Dict:
    impact = int(card.get("impact_score", 0) or 0)
    confidence = int(card.get("confidence", 0) or 0)
    watch_hits = card.get("watchlist_hits", []) or []
    alert_tags = card.get("alert_tags", []) or []
    trust = str(card.get("trust_label", "unverified") or "unverified")
    low_quality = bool(card.get("is_low_quality"))
    targets = set(watch_targets or [])
    target_hits = [t for t in targets if t in " ".join((card.get("asset_tags", []) or []) + (watch_hits or []) + (alert_tags or [])).lower()]
    prior = thesis_state or latest_memory_for_article(int(card.get("article_id", 0) or 0), memory_type="thesis")
    contradiction = "CONTRADICTION" in [str(tag or "").upper() for tag in alert_tags]
    relevance = _relevance_score(card, target_hits)
    llm_importance = str(card.get("llm_importance", "medium") or "medium").lower()
    cluster_size = int(card.get("cluster_size", 1) or 1)
    contradiction_resolution = str(contradiction_resolution or "").lower()

    if low_quality and relevance < 0.30 and not alert_tags:
        return {"decision_type": "ignore", "reason": "Low-quality syndicated signal with weak impact.", "priority_score": max(5, impact // 2), "state": "closed"}

    if prior:
        prior_conf = int(float(prior.get("confidence", 0) or 0) * 100) if float(prior.get("confidence", 0) or 0) <= 1 else int(prior.get("confidence", 0) or 0)
        prior_thesis = str(prior.get("current_claim", "") or prior.get("thesis", "") or "")
        if contradiction_resolution == "contradiction":
            return {"decision_type": "downgrade", "reason": "Fresh reporting includes contradiction markers against the prior thesis.", "priority_score": max(20, impact + 5), "state": "open"}
        if contradiction_resolution == "update":
            return {"decision_type": "upgrade", "reason": "The flagged contradiction looks like a meaningful thesis update, not a reversal.", "priority_score": impact + 12, "state": "open"}
        if contradiction_resolution == "nuance":
            return {"decision_type": "follow_up", "reason": "The flagged contradiction looks like nuance that still needs follow-up.", "priority_score": impact + 4, "state": "open"}
        if relevance >= 0.70 and (watch_hits or alert_tags or target_hits or llm_importance in ("high", "critical")):
            return {"decision_type": "alert", "reason": "Strong follow-up now clears the operator alert threshold.", "priority_score": impact + 18, "state": "open"}
        if impact >= prior_conf + 10 or (relevance >= 0.58 and confidence >= max(45, prior_conf - 10)):
            return {"decision_type": "upgrade", "reason": "Follow-up evidence is strengthening the prior thesis.", "priority_score": impact + 10, "state": "open"}
        if relevance >= 0.30 and (watch_hits or alert_tags or target_hits or trust == "trusted"):
            return {"decision_type": "queue", "reason": "Relevant follow-up cleared the operator queue threshold.", "priority_score": impact + 8, "state": "open"}
        if relevance >= 0.30:
            return {"decision_type": "follow_up", "reason": "Relevant follow-up is worth keeping in view.", "priority_score": max(18, impact), "state": "open"}
        if impact + 20 < prior_conf and relevance < 0.20:
            return {"decision_type": "downgrade", "reason": "Follow-up evidence is weaker than the prior thesis.", "priority_score": max(15, impact), "state": "open"}
        if prior_thesis and card.get("signal") and card.get("signal") != "Neutral":
            return {"decision_type": "follow_up", "reason": "This looks like a live follow-up to an existing thesis.", "priority_score": impact, "state": "open"}

    if contradiction_resolution == "contradiction":
        return {"decision_type": "downgrade", "reason": "Contradiction check confirmed that the thesis is now weaker.", "priority_score": impact + 6, "state": "open"}
    if contradiction_resolution == "update":
        return {"decision_type": "follow_up", "reason": "Contradiction check suggests the story is an update rather than a reversal.", "priority_score": impact + 5, "state": "open"}
    if contradiction and (impact >= 45 or watch_hits or target_hits):
        return {"decision_type": "follow_up", "reason": "Contradiction marker detected. Re-check before promoting this thesis.", "priority_score": impact + 6, "state": "open"}
    if relevance >= 0.68 and (watch_hits or alert_tags or target_hits or trust == "trusted" or llm_importance in ("high", "critical") or cluster_size > 1):
        return {"decision_type": "alert", "reason": "High-impact story aligned with watch targets or alert tags.", "priority_score": impact + 20, "state": "open"}
    if relevance >= 0.30 and (watch_hits or target_hits or alert_tags or trust == "trusted"):
        return {"decision_type": "queue", "reason": "Important watchlist-aligned story worth operator attention.", "priority_score": impact + 10, "state": "open"}
    if impact >= 40 and trust == "trusted":
        return {"decision_type": "queue", "reason": "Trusted source with enough impact to keep in the queue.", "priority_score": impact, "state": "open"}
    if alert_tags:
        return {"decision_type": "follow_up", "reason": "Alert tags present, but the story needs more confirmation.", "priority_score": impact, "state": "open"}
    return {"decision_type": "ignore", "reason": "Low-priority signal for the current goal set.", "priority_score": max(1, impact // 2), "state": "closed"}


def _create_task_for_decision(card: Dict, decision: Dict):
    article_id = int(card.get("article_id", 0) or 0) or None
    asset_text = ", ".join(card.get("asset_tags", []) or []) or (", ".join(card.get("watchlist_hits", []) or []) or "market")
    decision_type = str(decision.get("decision_type", "") or "")
    impact = int(card.get("impact_score", 0) or 0)
    confidence_raw = int(card.get("confidence", 0) or 0)
    confidence_score = max(0.05, min(1.0, confidence_raw / 100.0))
    alert_tags = [str(tag or "").upper() for tag in (card.get("alert_tags", []) or [])]
    urgency_level = "urgent" if decision_type == "alert" or impact >= 75 else ("high" if impact >= 60 or "CONTRADICTION" in alert_tags else "medium")
    asset_count = len(card.get("asset_tags", []) or [])
    macro_count = len(card.get("macro_tags", []) or [])
    impact_radius = "cross_asset" if asset_count >= 2 or macro_count >= 2 else ("global" if "GEOPOLITICS" in (card.get("macro_tags", []) or []) else "regional")
    ttl = "next run" if decision_type in ("follow_up", "downgrade") else ("today" if decision_type == "queue" else "")
    common_kwargs = {
        "related_article_id": article_id,
        "source_count": max(1, len(card.get("alert_tags", []) or []) + len(card.get("watchlist_hits", []) or []) or 1),
        "confidence_score": confidence_score,
        "urgency_level": urgency_level,
        "impact_radius": impact_radius,
        "ttl": ttl,
        "identity_key": _identity_key(card),
        "thesis_key": normalize_thesis_key(_thesis_key(card)),
    }
    if decision_type == "alert":
        return create_task("follow_up", f"Follow up {asset_text}", f"Re-check this high-impact story: {card.get('headline', '')}", due_hint="next run", **common_kwargs)
    if decision_type == "queue":
        return create_task("monitor", f"Monitor {asset_text}", f"Keep this queued item in the operator stack: {card.get('headline', '')}", due_hint="today", **common_kwargs)
    if decision_type == "follow_up":
        return create_task("follow_up", f"Re-check {asset_text}", f"Verify whether the thesis is strengthening or weakening: {card.get('headline', '')}", due_hint="next run", **common_kwargs)
    if decision_type == "downgrade":
        return create_task("review", f"Review weakening thesis on {asset_text}", f"Confidence dropped or contradiction appeared: {card.get('headline', '')}", due_hint="today", **common_kwargs)
    if decision_type == "upgrade":
        return create_task("escalate", f"Escalate stronger thesis on {asset_text}", f"Confidence improved: {card.get('headline', '')}", due_hint="now", **common_kwargs)
    return create_task("suppress", f"Suppress duplicate story cluster for {asset_text}", f"Ignore low-value repetition: {card.get('headline', '')}", due_hint="background", **common_kwargs)


def _apply_task_closure_rules(card: Dict, decision_shape: Dict):
    identity_key = _identity_key(card)
    decision_type = str(decision_shape.get("decision_type", "") or "")
    contradiction_resolution = str(card.get("contradiction_resolution", "") or "").lower()
    has_contradiction = contradiction_resolution == "contradiction" or (
        contradiction_resolution not in ("update", "nuance", "unrelated")
        and (bool(card.get("contradicts_narrative")) or bool(card.get("has_contradiction")))
    )

    contradicted = 0
    superseded = 0
    completed = 0

    if identity_key and (has_contradiction or decision_type == "downgrade"):
        contradicted = close_tasks(
            identity_key=identity_key,
            new_status="contradicted",
            reason="A fresh contradiction weakened the current thesis.",
        )
    elif identity_key and contradiction_resolution == "update":
        completed = close_tasks(
            identity_key=identity_key,
            new_status="completed",
            reason="Contradiction check resolved into an update to the thesis.",
        )
    elif identity_key and decision_type == "alert":
        superseded = close_tasks(
            identity_key=identity_key,
            new_status="superseded",
            reason="A stronger alert replaced the prior queue or follow-up task.",
        )
    elif identity_key and decision_type == "upgrade":
        completed = close_tasks(
            identity_key=identity_key,
            new_status="completed",
            reason="The follow-up thesis strengthened enough to complete the earlier task.",
        )
    elif identity_key and decision_type == "ignore":
        completed = close_tasks(
            identity_key=identity_key,
            new_status="completed",
            reason="Repeated low-value cluster was handled and does not need more tasking.",
        )

    return {
        "stale": 0,
        "contradicted": contradicted,
        "superseded": superseded,
        "completed": completed,
    }


def run_real_agent_loop(max_records_per_source: int = 8) -> Dict:
    started = time.time()
    ensure_agent_tables()
    state = bump_real_agent_run()
    real_agent_runs = int(state.get("real_agent_runs", 0) or 0)
    starting_action_ids = {int(item.get("id", 0) or 0) for item in list_actions(limit=500)}
    seen_action_ids = set(starting_action_ids)
    ingestion = run_agent_cycle(max_records_per_source=max_records_per_source)
    reasoning_pipeline_stats = process_unreasoned_articles(DB_PATH, max_articles=50)
    payload = get_terminal_payload_clean(limit=80)
    goals = list_goals(active_only=True)
    watch_targets = _normalize_targets(goals)
    cards = payload.get("cards", []) or []
    decisions = []
    tasks = []
    surfaced_alerts = 0
    closure_totals = {"stale": int(close_expired_tasks() or 0), "contradicted": 0, "superseded": 0, "completed": 0}
    seen_identities = set()
    contradiction_llm_state = new_llm_run_state(per_run_cap=2)
    thesis_upserts = 0
    thesis_confidence_updates = 0
    thesis_keys_touched = set()
    proposal_candidates = {}
    action_proposals_created = 0
    research_agent_runs = 0
    autonomous_goals_created = 0
    reflection_metrics = {}
    briefing_created = 0
    decay_stats = {"decayed": 0, "superseded": 0}
    promote_demote_stats = {"promoted_to_confirmed": 0, "demoted_to_weakened": 0}
    cooldown_blocked_actions = 0
    goal_cap_blocks = 0
    research_cap_blocks = 0
    thesis_cap_blocks = 0
    action_cap_blocks = 0
    cluster_cooldown_blocks = 0
    reasoning_cap_blocks = int(ingestion.get("reasoning_cap_blocks", 0) or 0)

    for card in cards[:20]:
        identity_key = _identity_key(card)
        if identity_key and identity_key in seen_identities:
            continue
        if identity_key and is_cooldown_active("cluster_review", identity_key):
            cluster_cooldown_blocks += 1
            continue
        if identity_key:
            seen_identities.add(identity_key)
        thesis_key = normalize_thesis_key(_thesis_key(card))
        thesis_state = get_thesis(thesis_key) if thesis_key else None
        contradiction_meta = None
        contradiction_resolution = ""
        if (card.get("has_contradiction") or card.get("contradicts_narrative")) and thesis_state and thesis_state.get("current_claim"):
            contradiction_meta = analyse_contradiction_meta(
                _contradiction_context(card),
                thesis_state.get("current_claim", ""),
                cluster_key=identity_key,
                run_state=contradiction_llm_state,
            )
            contradiction_resolution = str(((contradiction_meta or {}).get("analysis", {}) or {}).get("resolution", "") or "")
            card["contradiction_resolution"] = contradiction_resolution
            card["contradiction_note"] = str(((contradiction_meta or {}).get("analysis", {}) or {}).get("note", "") or "")

        decision_shape = _decision_for_card(card, watch_targets, thesis_state=thesis_state, contradiction_resolution=contradiction_resolution)
        decision = create_decision(
            article_id=int(card.get("article_id", 0) or 0) or None,
            decision_type=decision_shape["decision_type"],
            reason=decision_shape["reason"],
            confidence=int(card.get("confidence", 0) or 0),
            priority_score=int(decision_shape["priority_score"]),
            state=decision_shape["state"],
            cluster_key=str(card.get("cluster_key", "") or ""),
            thesis_key=thesis_key,
        )
        decisions.append(decision)
        if decision_shape["decision_type"] in ("alert", "follow_up"):
            record_prediction(
                thesis_key or _thesis_key(card),
                float(card.get("confidence_score", 0.5) or 0.5),
                str(card.get("signal", "Neutral") or "Neutral"),
                card.get("source", "unknown"),
                card.get("llm_category", "other"),
            )
        surfaced_alerts += _store_loop_alert(int(card.get("article_id", 0) or 0) or None, card, decision_shape)
        closures = _apply_task_closure_rules(card, decision_shape)
        for key, value in closures.items():
            closure_totals[key] += int(value or 0)
        thesis_status = _thesis_status_for_decision(decision_shape["decision_type"], contradiction_resolution=contradiction_resolution)
        thesis_record = thesis_state or {"thesis_key": thesis_key}
        did_upsert_thesis = False
        can_update_thesis = bool(thesis_key) and not is_cooldown_active("thesis_update", thesis_key)
        if thesis_key and not can_update_thesis:
            thesis_cap_blocks += 1
        if thesis_key and can_update_thesis and (thesis_upserts + thesis_confidence_updates) >= int(MAX_THESIS_UPDATES_PER_RUN):
            can_update_thesis = False
            thesis_cap_blocks += 1
        if can_update_thesis:
            thesis_record = upsert_thesis(
                thesis_key=thesis_key or card.get("headline", ""),
                current_claim=str(card.get("thesis", "") or card.get("why_it_matters", "") or card.get("headline", "")),
                confidence=max(0.3, min(1.0, float(card.get("confidence_score", 0.5) or 0.5))),
                status=thesis_status,
                evidence_delta=max(1, int(card.get("cluster_size", 1) or 1)),
                last_article_id=int(card.get("article_id", 0) or 0) or None,
                last_decision_id=int(decision.get("id", 0) or 0) or None,
                notes=decision_shape["reason"] + ((" | " + card.get("contradiction_note", "")) if card.get("contradiction_note") else ""),
                contradiction_delta=1 if contradiction_resolution == "contradiction" or decision_shape["decision_type"] == "downgrade" else 0,
                source_name=card.get("source", "unknown"),
                category=card.get("llm_category", "other"),
                related_headlines=[card.get("headline", "")],
            )
            did_upsert_thesis = bool(thesis_record)
            if thesis_key:
                set_cooldown("thesis_update", thesis_key, THESIS_COOLDOWN_MINUTES)
        if did_upsert_thesis:
            thesis_upserts += 1
        if thesis_record:
            if thesis_record.get("thesis_key"):
                thesis_keys_touched.add(str(thesis_record.get("thesis_key")))
                candidate = dict(thesis_record)
                candidate["status"] = thesis_status
                if str(candidate.get("status", "") or "").lower() in ("active", "confirmed", "tracking", "contradicted"):
                    proposal_candidates[str(thesis_record.get("thesis_key"))] = candidate
        write_memory(
            article_id=int(card.get("article_id", 0) or 0) or None,
            memory_type="thesis",
            thesis=str(card.get("thesis", "") or card.get("summary", "") or card.get("headline", "")),
            confidence=int(card.get("confidence", 0) or 0),
            status=thesis_status,
            notes=decision_shape["reason"],
            linked_decision_id=int(decision.get("id", 0) or 0) or None,
            thesis_key=thesis_record.get("thesis_key", thesis_key),
        )
        if decision_shape["decision_type"] in ("queue", "alert", "follow_up", "upgrade"):
            write_memory(
                article_id=int(card.get("article_id", 0) or 0) or None,
                memory_type="top_story",
                thesis=str(card.get("headline", "") or ""),
                confidence=int(card.get("confidence", 0) or 0),
                status="tracked",
                notes=decision_shape["reason"],
                linked_decision_id=int(decision.get("id", 0) or 0) or None,
                thesis_key=thesis_record.get("thesis_key", thesis_key),
            )
        if decision_shape["decision_type"] in ("queue", "alert", "follow_up", "downgrade", "upgrade", "ignore"):
            tasks.append(_create_task_for_decision(card, decision_shape))

        if card.get("alert_tags"):
            write_memory(
                article_id=int(card.get("article_id", 0) or 0) or None,
                memory_type="alert_reason",
                thesis=", ".join(card.get("alert_tags", []) or []),
                confidence=int(card.get("confidence", 0) or 0),
                status="remembered",
                notes=decision_shape["reason"],
                linked_decision_id=int(decision.get("id", 0) or 0) or None,
                thesis_key=thesis_record.get("thesis_key", thesis_key),
            )
        if identity_key:
            set_cooldown("cluster_review", identity_key, CLUSTER_COOLDOWN_MINUTES)

    for thesis in list_theses(limit=40, statuses=["active", "confirmed", "tracking"]):
        thesis_key = str(thesis.get("thesis_key", "") or "")
        if not thesis_key or thesis_key in proposal_candidates:
            continue
        if float(thesis.get("confidence", 0.0) or 0.0) < float(ACTION_PROPOSAL_MIN_CONFIDENCE or 0.55):
            continue
        proposal_candidates[thesis_key] = thesis

    for thesis in sorted(
        proposal_candidates.values(),
        key=lambda item: float((item or {}).get("confidence", 0.0) or 0.0),
        reverse=True,
    ):
        if action_proposals_created >= int(MAX_ACTION_PROPOSALS_PER_RUN):
            action_cap_blocks += 1
            continue
        action_type = _proposal_type_for_thesis(thesis)
        if action_type:
            cooldown = action_on_cooldown(thesis.get("thesis_key", ""), action_type, minutes=ACTION_COOLDOWN_MINUTES)
            if cooldown.get("blocked"):
                cooldown_blocked_actions += 1
                continue
        proposed = evaluate_and_propose(thesis, decisions, db=None)
        if proposed and proposed.get("blocked"):
            if str(proposed.get("blocked_reason", "") or "") == "cooldown":
                cooldown_blocked_actions += 1
            continue
        if proposed and int(proposed.get("id", 0) or 0) not in seen_action_ids:
            seen_action_ids.add(int(proposed.get("id", 0) or 0))
            action_proposals_created += 1

    evaluations = evaluate_previous_items(cards, max_items=16)
    current_cards_by_article = {int(card.get("article_id", 0) or 0): card for card in cards if int(card.get("article_id", 0) or 0)}

    for decision in decisions:
        if str(decision.get("decision_type", "") or "") != "upgrade":
            continue
        article_id = int(decision.get("article_id", 0) or 0)
        card = current_cards_by_article.get(article_id, {})
        if not card:
            continue
        thesis_key = normalize_thesis_key(decision.get("thesis_key", "") or _thesis_key(card))
        if thesis_key and not is_cooldown_active("thesis_update", thesis_key) and (thesis_upserts + thesis_confidence_updates) < int(MAX_THESIS_UPDATES_PER_RUN):
            updated = update_thesis_confidence(
                thesis_key,
                card.get("source", "unknown"),
                float(card.get("confidence_score", 0.5) or 0.5),
            )
            if updated:
                thesis_confidence_updates += 1
                thesis_keys_touched.add(str(updated.get("thesis_key", thesis_key)))
                set_cooldown("thesis_update", thesis_key, THESIS_COOLDOWN_MINUTES)
        elif thesis_key:
            thesis_cap_blocks += 1

    for evaluation in evaluations:
        outcome_status = str(evaluation.get("status", "") or "")
        thesis_key = normalize_thesis_key(evaluation.get("thesis_key", "") or evaluation.get("thesis", ""))
        if outcome_status in ("contradicted", "stale"):
            record_thesis_event(
                thesis_key,
                outcome_status,
                evaluation.get("notes", "") or f"Evaluation marked thesis as {outcome_status}.",
                float(evaluation.get("confidence", 0.0) or 0.0) / (100.0 if float(evaluation.get("confidence", 0.0) or 0.0) > 1 else 1.0),
                0,
            )
        if outcome_status != "confirmed":
            continue
        article_id = int(evaluation.get("article_id", 0) or 0)
        card = current_cards_by_article.get(article_id, {})
        if thesis_key and card and not is_cooldown_active("thesis_update", thesis_key) and (thesis_upserts + thesis_confidence_updates) < int(MAX_THESIS_UPDATES_PER_RUN):
            updated = update_thesis_confidence(
                thesis_key,
                card.get("source", "unknown"),
                float(card.get("confidence_score", 0.5) or 0.5),
            )
            if updated:
                thesis_confidence_updates += 1
                thesis_keys_touched.add(str(updated.get("thesis_key", thesis_key)))
                set_cooldown("thesis_update", thesis_key, THESIS_COOLDOWN_MINUTES)
        elif thesis_key and card:
            thesis_cap_blocks += 1

    active_theses = list_theses(limit=50, statuses=["active", "tracking", "contradicted"])
    research_runs_today = get_daily_counter("research_agent_runs")
    for thesis in active_theses:
        if (
            str(thesis.get("status", "") or "").lower() == "active"
            and int(thesis.get("evidence_count", 0) or 0) < 3
            and float(thesis.get("confidence", 0.0) or 0.0) < 0.65
        ):
            thesis_key = str(thesis.get("thesis_key", "") or "")
            if research_runs_today + research_agent_runs >= int(MAX_RESEARCH_RUNS_PER_DAY):
                research_cap_blocks += 1
                continue
            if thesis_key and is_cooldown_active("thesis_research", thesis_key):
                research_cap_blocks += 1
                continue
            research_result = research_thesis(
                thesis_key,
                thesis.get("current_claim", ""),
                thesis.get("category", "other"),
            )
            research_agent_runs += int((research_result.get("metrics", {}) or {}).get("research_agent_runs", 0) or 0)
            if thesis_key:
                set_cooldown("thesis_research", thesis_key, THESIS_COOLDOWN_MINUTES)
            if research_agent_runs:
                bump_daily_counter("research_agent_runs", int((research_result.get("metrics", {}) or {}).get("research_agent_runs", 0) or 1))

    for thesis in list_theses(limit=50, statuses=["contradicted"]):
        if action_proposals_created >= int(MAX_ACTION_PROPOSALS_PER_RUN):
            action_cap_blocks += 1
            continue
        action_type = _proposal_type_for_thesis(thesis)
        if action_type:
            cooldown = action_on_cooldown(thesis.get("thesis_key", ""), action_type, minutes=ACTION_COOLDOWN_MINUTES)
            if cooldown.get("blocked"):
                cooldown_blocked_actions += 1
                continue
        proposed = evaluate_and_propose(thesis, decisions, db=None)
        if proposed and proposed.get("blocked"):
            if str(proposed.get("blocked_reason", "") or "") == "cooldown":
                cooldown_blocked_actions += 1
            continue
        if proposed and int(proposed.get("id", 0) or 0) not in seen_action_ids:
            seen_action_ids.add(int(proposed.get("id", 0) or 0))
            action_proposals_created += 1

    if real_agent_runs % 5 == 0:
        reflection_metrics = run_reflection()
    if real_agent_runs % 3 == 0:
        goals_remaining = max(0, int(MAX_AUTONOMOUS_GOALS_PER_DAY) - int(get_daily_counter("autonomous_goals_created") or 0))
        if goals_remaining <= 0:
            goal_cap_blocks += 1
        else:
            created_goals = generate_autonomous_goals(limit_new=min(3, goals_remaining))
            autonomous_goals_created = len(created_goals)
            if autonomous_goals_created:
                bump_daily_counter("autonomous_goals_created", autonomous_goals_created)

    live_state = get_agent_state()
    last_briefing = str(live_state.get("briefing_last_run", "") or "")
    should_brief = True
    if last_briefing:
        parsed = None
        try:
            parsed = datetime.fromisoformat(last_briefing.replace("Z", "+00:00"))
        except Exception:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            should_brief = (datetime.now(timezone.utc) - parsed).total_seconds() >= 23 * 3600
    if should_brief:
        briefing = generate_daily_briefing(run_id=_latest_agent_run_id())
        if briefing:
            live_state["briefing_last_run"] = str(briefing.get("generated_at", "") or utc_now_iso())
            save_agent_state(live_state)
            briefing_created = 1

    decay_stats = decay_stale_theses(DB_PATH)
    promote_demote_stats = promote_demote_theses(DB_PATH)

    current_action_ids = {int(item.get("id", 0) or 0) for item in list_actions(limit=500)}
    action_proposals_created = max(action_proposals_created, len(current_action_ids - starting_action_ids))

    decision_counts = Counter(str(d.get("decision_type", "") or "") for d in decisions)
    duration_seconds = round(max(0.0, time.time() - started), 3)
    metrics = {
        "items_fetched": int(ingestion.get("items_fetched", 0) or 0),
        "items_kept": int(ingestion.get("items_kept", 0) or 0),
        "alerts_created": max(int(ingestion.get("alerts_created", 0) or 0), int(surfaced_alerts or 0), int(decision_counts.get("alert", 0) or 0)),
        "decision_counts": dict(decision_counts),
        "evaluations": Counter(str(x.get("status", "") or "") for x in evaluations),
        "watchlist_hits": int((payload.get("stats", {}) or {}).get("watchlist_hits", 0) or 0),
        "llm_metrics": ingestion.get("llm_metrics", {}) or {},
        "contradiction_llm_metrics": summarize_llm_run_state(contradiction_llm_state),
        "task_closures": closure_totals,
        "cluster_identities_seen": len(seen_identities),
        "action_proposals_created": action_proposals_created,
        "research_agent_runs": research_agent_runs,
        "autonomous_goals_created": autonomous_goals_created,
        "reasoning_chains_built": int(ingestion.get("reasoning_chains_built", 0) or 0) + int(reasoning_pipeline_stats.get("chains_written", 0) or 0),
        "reasoning_pipeline": reasoning_pipeline_stats,
        "reasoning_cap_blocks": reasoning_cap_blocks,
        "thesis_lifecycle": {**decay_stats, **promote_demote_stats},
        "reflection_summary": reflection_metrics,
        "briefing_created": briefing_created,
        "cooldown_blocked_actions": cooldown_blocked_actions,
        "goal_cap_blocks": goal_cap_blocks,
        "research_cap_blocks": research_cap_blocks,
        "thesis_cap_blocks": thesis_cap_blocks,
        "action_cap_blocks": action_cap_blocks,
        "cluster_cooldown_blocks": cluster_cooldown_blocks,
        "duration_seconds": duration_seconds,
        "db_touched_counts": {
            "decisions": len(decisions),
            "tasks": len(tasks),
            "theses": thesis_upserts + thesis_confidence_updates,
            "journal": 1,
        },
        "thesis_updates": {
            "upserts": thesis_upserts,
            "confidence_updates": thesis_confidence_updates,
            "touched": sorted([key for key in thesis_keys_touched if key])[:20],
        },
    }
    latest_run_id = _latest_agent_run_id()
    _journal(latest_run_id, "agent_loop", "Observed latest payload, wrote decisions/tasks, and re-checked prior thesis items.", metrics)

    return {
        "status": "ok",
        "run_id": latest_run_id,
        "ingestion": ingestion,
        "reasoning_pipeline": reasoning_pipeline_stats,
        "goals": goals,
        "watch_targets": watch_targets,
        "decisions_created": len(decisions),
        "tasks_created": len(tasks),
        "alerts_created": metrics["alerts_created"],
        "evaluations_created": len(evaluations),
        "decision_counts": dict(decision_counts),
        "outcomes": outcome_summary(limit=300),
        "top_decisions": decisions[:10],
        "top_tasks": tasks[:10],
        "journal_summary": metrics,
    }


def _latest_agent_run_id() -> int:
    conn = shared_get_conn(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM agent_runs ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return int(row["id"] or 0) if row else 0


def list_journal(limit: int = 50) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, run_id, journal_type, summary, metrics_json, created_at
        FROM agent_journal
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = cur.fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        except Exception:
            item["metrics"] = {}
        out.append(item)
    return out


def queue_snapshot(limit: int = 20) -> List[Dict]:
    rows = list_decisions(limit=limit, open_only=False)
    return [row for row in rows if str(row.get("decision_type", "") or "") in ("queue", "alert", "follow_up", "upgrade", "downgrade")][:limit]


def metrics_snapshot(limit: int = 20) -> Dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, run_type, started_at, finished_at, status, items_fetched, items_kept, alerts_created
        FROM agent_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    runs = [dict(row) for row in cur.fetchall()]
    conn.close()
    journal = list_journal(limit=limit)
    decision_mix = decision_metrics(limit=200)
    watchlist_points = []
    for item in journal:
        metrics = item.get("metrics", {}) or {}
        watchlist_points.append(
            {
                "created_at": item.get("created_at", ""),
                "watchlist_hits": int(metrics.get("watchlist_hits", 0) or 0),
                "decision_counts": metrics.get("decision_counts", {}),
            }
        )
    return {
        "runs": runs,
        "decision_mix": decision_mix,
        "watchlist_points": watchlist_points,
        "outcomes": outcome_summary(limit=300),
    }
