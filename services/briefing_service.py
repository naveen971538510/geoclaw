import json
from datetime import datetime, timezone
from typing import Dict, List

from config import DB_PATH
from services.ai_contracts import clean_briefing_bundle, render_briefing_bundle, validate_briefing_bundle
from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso
from services.llm_service import analyse_custom_json
from services.macro_calendar import MacroCalendar
from services.reasoning_service import list_reasoning_chains


def _normalize_audience(audience: str) -> str:
    clean = str(audience or "trader").strip().lower()
    return clean if clean in {"trader", "executive", "raw_json", "autonomy_report"} else "trader"


def _table_exists(cur, table_name: str) -> bool:
    row = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (str(table_name or "").strip(),),
    ).fetchone()
    return bool(row)


def _conn_db_path(conn) -> str:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for row in rows:
            if len(row) >= 3 and row[2]:
                return str(row[2])
    except Exception:
        pass
    return str(DB_PATH)


def _fallback_briefing(theses, contradictions, chains, actions, calendar_brief: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    lines = [f"## GeoClaw Intelligence Brief — {ts}", ""]
    lines.append("### Top developing stories")
    if theses:
        for thesis in theses[:5]:
            conf = round(float(thesis.get("confidence", 0.0) or 0.0) * 100)
            lines.append(f"- {thesis.get('title') or thesis.get('current_claim')} ({conf}% confidence, {thesis.get('status', 'active')})")
    else:
        lines.append("- The thesis layer has not formed any strong active narrative yet.")
    lines.append("")
    lines.append("### Conflicting signals")
    if contradictions:
        for contradiction in contradictions[:5]:
            lines.append(f"- {contradiction.get('headline', '')}: {contradiction.get('reason', '')}")
    else:
        lines.append("- No major contradiction alerts were recorded in the last 24 hours.")
    lines.append("")
    lines.append("### Downstream risks")
    if chains:
        for chain in chains[:3]:
            lines.append(f"- {chain.get('terminal_risk', '') or 'Watch the downstream risk path.'}")
    else:
        lines.append("- Downstream risks are still developing. Watch the highest-confidence theses for spillover.")
    lines.append("")
    lines.append("### Recommended actions")
    if actions:
        for action in actions[:5]:
            lines.append(f"- {action.get('action_type', '')}: {action.get('thesis_key', '') or action.get('status', '')}")
    else:
        lines.append("- No new operator action proposals are pending right now.")
    lines.append("")
    lines.append("### Macro Calendar")
    if calendar_brief:
        lines.extend([line for line in str(calendar_brief or "").splitlines() if line and not line.startswith("### ")])
    else:
        lines.append("- No major macro events in the next 7 days.")
    lines.append("")
    lines.append("### What to watch tomorrow")
    if theses:
        for thesis in theses[:3]:
            lines.append(f"- {thesis.get('watch_for_next', '') or 'Watch the next confirming headline.'}")
    else:
        lines.append("- Watch for fresh corroboration or contradiction in the next cycle.")
    return "\n".join(lines).strip()


def _normalize_briefing_text(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return clean
    if "Confidence" not in clean and "theses" not in clean.lower():
        clean += "\n\nConfidence: Review the top thesis confidence levels in this briefing."
    return clean


def _fallback_briefing_bundle(context: Dict) -> Dict:
    theses = context["theses"]
    contradictions = context["contradictions"]
    actions = context["actions"]
    chains = context["chains"]
    calendar_events = context["calendar_events"]
    regime = context["regime"]
    sentiment = context["sentiment"]
    prediction_accuracy = context.get("prediction_accuracy", {}) or {}

    sections = [
        {
            "title": "Top developing stories",
            "points": [
                (
                    f"{(thesis.get('title') or thesis.get('current_claim') or thesis.get('thesis_key') or 'Developing thesis')[:140]} "
                    f"({round(float(thesis.get('confidence', 0.0) or 0.0) * 100)}% confidence, {thesis.get('status', 'active')}). "
                    f"{str(thesis.get('last_update_reason', '') or '').strip()[:120]}"
                ).strip()
                for thesis in theses[:3]
            ]
            or ["The thesis layer has not formed a dominant high-confidence story yet."],
        },
        {
            "title": "Conflicting signals",
            "points": [
                f"{item.get('headline', '')[:90]} — {str(item.get('reason', '') or '').strip()[:120]}"
                for item in contradictions[:3]
            ]
            or ["No major contradiction alerts were recorded in the latest cycle."],
        },
        {
            "title": "Downstream risks",
            "points": [
                str(chain.get("terminal_risk") or "Watch the downstream risk path for confirmation.")
                for chain in chains[:3]
            ]
            or [f"Regime remains {regime.get('regime', 'UNKNOWN')}: {regime.get('description', '')}".strip()],
        },
        {
            "title": "Recommended actions",
            "points": [
                f"{item.get('action_type', '')}: {str(item.get('thesis_key', '') or item.get('audit_note', '') or '').strip()[:140]}"
                for item in actions[:4]
            ]
            or ["No new operator action proposals are pending right now."],
        },
        {
            "title": "Macro calendar",
            "points": [
                f"{event.get('estimated_date', '')} — {event.get('name', '')} [{event.get('impact', '')}]"
                for event in calendar_events[:4]
            ]
            or ["No major macro events are lined up in the next week."],
        },
    ]
    watch_items = []
    for thesis in theses[:4]:
        suggestion = str(thesis.get("watchlist_suggestion") or thesis.get("watch_for_next") or "").strip()
        if suggestion:
            watch_items.append(suggestion)
    for row in (context.get("source_reliability") or [])[:2]:
        watch_items.append(
            f"Source watch: {row.get('source_name', '')} reliability {round(float(row.get('reliability_score', 0.0) or 0.0) * 100)}%"
        )
    closing = (
        f"Regime: {regime.get('regime', 'UNKNOWN')} | Sentiment: {sentiment.get('label', 'Unavailable')} "
        f"({sentiment.get('score', 0)}). Prediction hit rate: {prediction_accuracy.get('accuracy_pct', 0)}%."
    )
    return {
        "headline": "GeoClaw Intelligence Brief",
        "sections": sections,
        "watch_items": watch_items[:4],
        "closing": closing,
    }


def _collect_briefing_context(conn) -> Dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT title, current_claim, thesis_key, confidence, status, terminal_risk,
               watch_for_next, watchlist_suggestion, evidence_count, last_update_reason
        FROM agent_theses
        WHERE COALESCE(status, '') != 'superseded'
        ORDER BY confidence DESC, evidence_count DESC, id DESC
        LIMIT 8
        """
    )
    theses = [dict(row) for row in cur.fetchall()]

    contradictions = []
    if _table_exists(cur, "contradictions"):
        cur.execute(
            """
            SELECT thesis_key AS headline, explanation AS reason, severity, created_at
            FROM contradictions
            WHERE COALESCE(resolved, 0) = 0
            ORDER BY created_at DESC, id DESC
            LIMIT 10
            """
        )
        contradictions = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT action_type, thesis_key, status, audit_note, payload_json
        FROM agent_actions
        WHERE COALESCE(status, '') IN ('pending', 'proposed', 'approved', 'auto_approved')
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    )
    actions = [dict(row) for row in cur.fetchall()]

    chains = list_reasoning_chains(limit=3)

    calibration = []
    if _table_exists(cur, "agent_calibration"):
        cur.execute(
            """
            SELECT source_name, category, count
            FROM agent_calibration
            WHERE row_type = 'reflection' AND over_confident = 1
            ORDER BY created_at DESC, id DESC
            LIMIT 5
            """
        )
        calibration = [dict(row) for row in cur.fetchall()]

    calendar = MacroCalendar()
    calendar_events = calendar.get_upcoming(7)
    calendar_brief = calendar.generate_calendar_brief()

    try:
        from services.pattern_detector import PatternDetector

        regime = PatternDetector().compute_market_regime(theses)
    except Exception:
        regime = {"regime": "UNKNOWN", "description": "Regime unavailable.", "risk_level": "UNKNOWN"}

    try:
        from services.sentiment_index import SentimentIndex

        sentiment = SentimentIndex().compute(_conn_db_path(conn))
    except Exception:
        sentiment = {"score": 0, "label": "Unavailable"}

    try:
        from services.prediction_tracker import PredictionTracker

        prediction_accuracy = PredictionTracker(_conn_db_path(conn)).get_accuracy_report()
    except Exception:
        prediction_accuracy = {"accuracy_pct": 0, "verified": 0, "refuted": 0, "neutral": 0}

    source_reliability = []
    if _table_exists(cur, "source_reliability"):
        cur.execute(
            """
            SELECT source_name, reliability_score, total_predictions
            FROM source_reliability
            ORDER BY reliability_score DESC, total_predictions DESC
            LIMIT 5
            """
        )
        source_reliability = [dict(row) for row in cur.fetchall()]

    return {
        "theses": theses,
        "contradictions": contradictions,
        "actions": actions,
        "chains": chains,
        "calibration": calibration,
        "calendar_events": calendar_events,
        "calendar_brief": calendar_brief,
        "regime": regime,
        "sentiment": sentiment,
        "prediction_accuracy": prediction_accuracy,
        "source_reliability": source_reliability,
    }


def _executive_briefing_text(context: Dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    theses = context["theses"]
    actions = context["actions"]
    regime = context["regime"]
    calendar_events = context["calendar_events"]
    top = theses[0] if theses else {}
    top_conf = round(float(top.get("confidence", 0.0) or 0.0) * 100)
    top_key = top.get("thesis_key") or top.get("current_claim") or "no dominant thesis yet"
    top_evidence = int(top.get("evidence_count", 0) or 0)
    top_reason = str(top.get("last_update_reason", "") or "").strip()
    pressing = next((item for item in theses if "HIGH" in str(item.get("terminal_risk", "")).upper()), top)
    pressing_text = pressing.get("thesis_key") or pressing.get("current_claim") or "no critical thesis identified"
    watch_items = []
    for thesis in theses[:3]:
        suggestion = str(thesis.get("watchlist_suggestion", "") or thesis.get("watch_for_next", "") or "").strip()
        if suggestion:
            watch_items.append(suggestion)
    watch_text = ", ".join(watch_items[:3]) if watch_items else "top thesis watchlists remain the key monitoring set"
    next_event = calendar_events[0] if calendar_events else {}
    next_event_text = (
        f"{next_event.get('name', 'No major event')} in {next_event.get('days_away', 0)} days"
        if next_event
        else "No major macro event is currently scheduled in the next week"
    )
    dominant_narrative = regime.get("regime", "mixed")
    return (
        f"GeoClaw Intelligence — Executive Summary\n{ts}\n\n"
        f"Markets are navigating {dominant_narrative} conditions. "
        f"The highest-conviction thesis is: {top_key} at {top_conf}% confidence, supported by {top_evidence} corroborating articles. "
        f"{top_reason or 'The evidence base is still consolidating across current narratives.'}\n\n"
        f"The most pressing risk is {pressing_text}. "
        f"{len(actions)} potential market actions have been proposed for review.\n\n"
        f"Key assets to monitor: {watch_text}.\n\n"
        f"Regime Assessment: {regime.get('regime', 'UNKNOWN')}. {regime.get('description', '')}\n\n"
        f"Macro Calendar: {next_event_text}."
    ).strip()


def _raw_json_payload(context: Dict, run_id: int = None) -> Dict:
    actions = []
    for action in context["actions"]:
        reason = str(action.get("audit_note", "") or "")
        if not reason:
            reason = str(action.get("payload_json", "") or "")
        actions.append(
            {
                "action_type": action.get("action_type", ""),
                "reason": reason,
                "status": action.get("status", ""),
            }
        )
    return {
        "generated_at": utc_now_iso(),
        "run_id": int(run_id or 0),
        "theses": [
            {
                "thesis_key": thesis.get("thesis_key", ""),
                "confidence": thesis.get("confidence", 0.0),
                "status": thesis.get("status", ""),
                "terminal_risk": thesis.get("terminal_risk", ""),
            }
            for thesis in context["theses"][:8]
        ],
        "actions": actions[:10],
        "regime": {
            "regime": context["regime"].get("regime", ""),
            "risk_level": context["regime"].get("risk_level", ""),
        },
        "sentiment_index": {
            "score": context["sentiment"].get("score", 0),
            "label": context["sentiment"].get("label", ""),
        },
        "calendar": [
            {
                "name": event.get("name", ""),
                "estimated_date": event.get("estimated_date", ""),
                "impact": event.get("impact", ""),
            }
            for event in context["calendar_events"][:8]
        ],
        "export_format": "raw_json",
    }


def generate_daily_briefing(db=None, run_id: int = None, audience: str = "trader", store: bool = True) -> Dict:
    ensure_agent_tables()
    audience = _normalize_audience(audience)
    conn = db or get_conn()
    close_conn = db is None
    context = _collect_briefing_context(conn)
    theses = context["theses"]
    contradictions = context["contradictions"]
    actions = context["actions"]
    chains = context["chains"]
    calendar_brief = context["calendar_brief"]

    if audience == "executive":
        text = _executive_briefing_text(context)
    elif audience == "raw_json":
        raw_payload = _raw_json_payload(context, run_id=run_id)
        text = json.dumps(raw_payload, indent=2)
    else:
        fallback_text = _fallback_briefing(theses, contradictions, chains, actions, calendar_brief=calendar_brief)
        fallback_bundle = _fallback_briefing_bundle(context)
        system_text = (
            "You are a senior intelligence analyst writing a trader briefing. "
            "Return strict JSON only with keys: headline, sections, watch_items, closing. "
            "Each section must have title and points. "
            "Use only the supplied facts. Do not add new numbers, dates, or entities. "
            "Keep points concise and decision-focused."
        )
        user_text = (
            f"Theses: {json.dumps(theses, ensure_ascii=False)}\n"
            f"Contradictions: {json.dumps(contradictions, ensure_ascii=False)}\n"
            f"Chains: {json.dumps(chains, ensure_ascii=False)}\n"
            f"Actions: {json.dumps(actions, ensure_ascii=False)}\n"
            f"Calibration: {json.dumps(context['calibration'], ensure_ascii=False)}\n"
            f"MacroCalendar: {json.dumps(context['calendar_events'], ensure_ascii=False)}\n"
            f"Regime: {json.dumps(context['regime'], ensure_ascii=False)}\n"
            f"Sentiment: {json.dumps(context['sentiment'], ensure_ascii=False)}\n"
            f"PredictionAccuracy: {json.dumps(context['prediction_accuracy'], ensure_ascii=False)}\n"
            f"SourceReliability: {json.dumps(context['source_reliability'], ensure_ascii=False)}\n"
            f"FallbackBriefing: {fallback_text}"
        )
        bundle = analyse_custom_json(
            system_text,
            user_text,
            fallback=fallback_bundle,
            mode="daily_briefing",
            cache_key="daily_briefing::" + audience + "::" + utc_now_iso()[:13],
            validator=validate_briefing_bundle,
            cleaner=lambda payload: clean_briefing_bundle(payload, fallback_bundle),
            lane="polish",
            task_type="briefing_sections",
            max_output_tokens=520,
        )["analysis"]
        text = render_briefing_bundle(clean_briefing_bundle(bundle, fallback_bundle))
        text = _normalize_briefing_text(text)
        if "Macro Calendar" not in text:
            text = text.rstrip() + "\n\n" + calendar_brief

    item = {
        "id": 0,
        "briefing_text": text,
        "generated_at": utc_now_iso(),
        "thesis_count": len(theses),
        "contradiction_count": len(contradictions),
        "chain_count": len(chains),
        "action_count": len(actions),
        "run_id": int(run_id or 0),
        "audience": audience,
    }

    if store and audience == "trader":
        cur = conn.cursor()
        briefing_columns = {row[1] for row in cur.execute("PRAGMA table_info(agent_briefings)").fetchall()}
        if "run_id" in briefing_columns and "format" in briefing_columns:
            cur.execute(
                """
                INSERT INTO agent_briefings (
                    briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count, run_id, format
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["briefing_text"],
                    item["generated_at"],
                    item["thesis_count"],
                    item["contradiction_count"],
                    item["chain_count"],
                    item["action_count"],
                    int(run_id) if run_id else None,
                    "trader",
                ),
            )
        elif "run_id" in briefing_columns:
            cur.execute(
                """
                INSERT INTO agent_briefings (
                    briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count, run_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["briefing_text"],
                    item["generated_at"],
                    item["thesis_count"],
                    item["contradiction_count"],
                    item["chain_count"],
                    item["action_count"],
                    int(run_id) if run_id else None,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO agent_briefings (
                    briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["briefing_text"],
                    item["generated_at"],
                    item["thesis_count"],
                    item["contradiction_count"],
                    item["chain_count"],
                    item["action_count"],
                ),
            )
        briefing_id = int(cur.lastrowid)
        conn.commit()
        cur.execute("SELECT * FROM agent_briefings WHERE id = ?", (briefing_id,))
        row = cur.fetchone()
        item = dict(row) if row else item

    if close_conn:
        conn.close()
    return item


def generate_briefing(db_path=None, run_id: int = None, audience: str = "trader") -> str:
    briefing = generate_daily_briefing(run_id=run_id, audience=audience, store=False)
    return str((briefing or {}).get("briefing_text", "") or "")


def get_latest_briefing(audience: str = "trader") -> Dict:
    ensure_agent_tables()
    audience = _normalize_audience(audience)
    if audience == "autonomy_report":
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM agent_briefings
            WHERE COALESCE(format, 'trader') = 'autonomy_report'
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {"briefing_text": "No autonomy report has been written yet.", "generated_at": utc_now_iso(), "format": "autonomy_report"}
    if audience != "trader":
        return generate_daily_briefing(audience=audience, store=False)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_briefings ORDER BY generated_at DESC, id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return generate_daily_briefing(audience="trader", store=True)


def list_briefings(limit: int = 7) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_briefings ORDER BY generated_at DESC, id DESC LIMIT ?", (int(limit),))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows
