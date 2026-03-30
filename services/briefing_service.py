import json
from datetime import datetime, timezone
from typing import Dict, List

from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso
from services.llm_service import analyse_custom_text
from services.macro_calendar import MacroCalendar
from services.reasoning_service import list_reasoning_chains


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


def generate_daily_briefing(db=None, run_id: int = None) -> Dict:
    ensure_agent_tables()
    conn = db or get_conn()
    close_conn = db is None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT title, current_claim, confidence, watch_for_next
        FROM agent_theses
        WHERE COALESCE(status, '') != 'superseded'
        ORDER BY confidence DESC, evidence_count DESC, id DESC
        LIMIT 8
        """
    )
    theses = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT ia.headline, ae.reason
        FROM alert_events ae
        JOIN ingested_articles ia ON ia.id = ae.article_id
        WHERE COALESCE(ae.status, '') = 'CRITICAL_CONTRADICTION'
        ORDER BY ae.created_at DESC, ae.id DESC
        LIMIT 10
        """
    )
    contradictions = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT action_type, thesis_key, status
        FROM agent_actions
        WHERE COALESCE(executed_at, '') = ''
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    )
    actions = [dict(row) for row in cur.fetchall()]
    chains = list_reasoning_chains(limit=3)
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
    calendar_brief = MacroCalendar().generate_calendar_brief()

    fallback_text = _fallback_briefing(theses, contradictions, chains, actions, calendar_brief=calendar_brief)
    system_text = (
        "You are a senior intelligence analyst writing a daily briefing. "
        "Write a professional, concise intelligence brief covering: "
        "1. Top developing stories 2. Conflicting signals 3. Downstream risks "
        "4. Recommended actions 5. What to watch tomorrow. "
        "Use clear headers, be specific, maximum 400 words."
    )
    user_text = (
        f"Theses: {json.dumps(theses, ensure_ascii=False)}\n"
        f"Contradictions: {json.dumps(contradictions, ensure_ascii=False)}\n"
        f"Chains: {json.dumps(chains, ensure_ascii=False)}\n"
        f"Actions: {json.dumps(actions, ensure_ascii=False)}\n"
        f"Calibration: {json.dumps(calibration, ensure_ascii=False)}\n"
        f"MacroCalendar: {calendar_brief}"
    )
    text = analyse_custom_text(
        system_text,
        user_text,
        fallback_text=fallback_text,
        mode="daily_briefing",
        cache_key="daily_briefing::" + utc_now_iso()[:10],
    )["text"]
    text = _normalize_briefing_text(text)
    if "Macro Calendar" not in text:
        text = text.rstrip() + "\n\n" + calendar_brief

    briefing_columns = {row[1] for row in cur.execute("PRAGMA table_info(agent_briefings)").fetchall()}
    if "run_id" in briefing_columns:
        cur.execute(
            """
            INSERT INTO agent_briefings (
                briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count, run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (text, utc_now_iso(), len(theses), len(contradictions), len(chains), len(actions), int(run_id) if run_id else None),
        )
    else:
        cur.execute(
            """
            INSERT INTO agent_briefings (
                briefing_text, generated_at, thesis_count, contradiction_count, chain_count, action_count
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (text, utc_now_iso(), len(theses), len(contradictions), len(chains), len(actions)),
        )
    briefing_id = int(cur.lastrowid)
    conn.commit()
    cur.execute("SELECT * FROM agent_briefings WHERE id = ?", (briefing_id,))
    row = cur.fetchone()
    if close_conn:
        conn.close()
    return dict(row) if row else {}


def generate_briefing(db_path=None, run_id: int = None) -> str:
    briefing = generate_daily_briefing(run_id=run_id)
    return str((briefing or {}).get("briefing_text", "") or "")


def get_latest_briefing() -> Dict:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_briefings ORDER BY generated_at DESC, id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return generate_daily_briefing()


def list_briefings(limit: int = 7) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent_briefings ORDER BY generated_at DESC, id DESC LIMIT ?", (int(limit),))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows
