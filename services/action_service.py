import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from config import (
    ACTION_COOLDOWN_MINUTES,
    ACTION_CRITICAL_MIN_CONFIDENCE,
    ACTION_PROPOSAL_MIN_CONFIDENCE,
    ALLOW_AUTO_APPROVED_ACTIONS,
)
from services.goal_service import ensure_agent_tables, get_conn, utc_now_iso
from services.thesis_service import get_thesis, get_thesis_detail, normalize_thesis_key, record_thesis_event


VALID_ACTION_TYPES = {"webhook", "email_summary", "slack_payload", "alert"}
ACTION_POLICY = {
    "no_action": {"max_confidence": 0.40, "max_evidence": 1},
    "draft_only": {"min_confidence": 0.40, "max_confidence": 0.60},
    "approval_required": {"min_confidence": 0.60, "max_confidence": 0.80},
    "auto_approved": {"min_confidence": 0.80, "min_evidence": 3},
}


def can_auto_approve(confidence, evidence_count) -> bool:
    try:
        confidence = float(confidence or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        evidence_count = int(evidence_count or 0)
    except (TypeError, ValueError):
        evidence_count = 0
    return confidence >= 0.80 and evidence_count >= 3


def _parse_iso(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fetch_action(action_id: int) -> Dict:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            id,
            action_type,
            payload_json,
            thesis_key,
            confidence,
            evidence_count,
            status,
            triggered_by,
            created_at,
            reviewed_at,
            executed_at,
            audit_note
        FROM agent_actions
        WHERE id = ?
        LIMIT 1
        """,
        (int(action_id),),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {}
    item = dict(row)
    try:
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
    except Exception:
        item["payload"] = {}
    thesis = get_thesis(item.get("thesis_key", ""))
    item["thesis_claim"] = (thesis or {}).get("current_claim", "")
    item["thesis_title"] = (thesis or {}).get("title", "") or item["thesis_claim"]
    return item


def list_actions(limit: int = 100) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            id,
            action_type,
            payload_json,
            thesis_key,
            confidence,
            evidence_count,
            status,
            triggered_by,
            created_at,
            reviewed_at,
            executed_at,
            audit_note
        FROM agent_actions
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = cur.fetchall()
    conn.close()
    return [_fetch_action(int(row["id"])) for row in rows]


def generate_email_summary(thesis_key) -> str:
    detail = get_thesis_detail(thesis_key)
    if not detail:
        return ""
    lines = [
        f"Title: {detail.get('title', '')}",
        f"Thesis: {detail.get('current_claim', '')}",
        f"Confidence: {float(detail.get('confidence', 0.5) or 0.5):.2f}",
        f"Status: {detail.get('status', 'active')}",
        f"Evidence count: {int(detail.get('evidence_count', 0) or 0)}",
        "",
        "Top linked articles:",
    ]
    linked = detail.get("linked_articles", []) or []
    if linked:
        for article in linked[:5]:
            lines.append(f"- {article.get('headline', '')}")
    else:
        lines.append("- No linked articles yet.")
    return "\n".join(lines).strip()


def generate_slack_payload(thesis_key) -> Dict:
    detail = get_thesis_detail(thesis_key)
    if not detail:
        return {"blocks": []}
    return {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{detail.get('title', '') or 'Thesis'}*\n{detail.get('current_claim', '')}\n*Confidence*: {float(detail.get('confidence', 0.5) or 0.5):.2f}\n*Status*: {detail.get('status', 'active')}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Evidence count: {int(detail.get('evidence_count', 0) or 0)} · Link note: preview only, not posted",
                    }
                ],
            },
        ]
    }


def _has_open_action(cur, thesis_key: str, action_type: str) -> int:
    cur.execute(
        """
        SELECT id
        FROM agent_actions
        WHERE LOWER(COALESCE(thesis_key, '')) = ?
          AND action_type = ?
          AND status IN ('draft', 'proposed', 'auto_approved', 'approved')
          AND COALESCE(executed_at, '') = ''
        ORDER BY id DESC
        LIMIT 1
        """,
        (normalize_thesis_key(thesis_key), str(action_type or "")),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else 0


def _policy_outcome(confidence, evidence_count):
    confidence = float(confidence or 0.0)
    evidence_count = int(evidence_count or 0)
    now = utc_now_iso()
    if confidence < 0.40:
        return "draft", f"blocked by policy: low confidence ({now})"
    if confidence < 0.60:
        return "draft", f"draft only: awaiting more evidence ({now})"
    if confidence < 0.80:
        return "proposed", f"approval required ({now})"
    if ALLOW_AUTO_APPROVED_ACTIONS and evidence_count >= 3 and can_auto_approve(confidence, evidence_count):
        return "auto_approved", f"auto-approved by policy ({now})"
    return "proposed", f"approval required ({now})"


def action_on_cooldown(thesis_key: str, action_type: str, minutes: int = None) -> Dict:
    ensure_agent_tables()
    clean_key = normalize_thesis_key(thesis_key)
    clean_type = str(action_type or "").strip()
    if not clean_key or not clean_type:
        return {"blocked": False, "remaining_seconds": 0, "action_id": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(minutes or ACTION_COOLDOWN_MINUTES)))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_at
        FROM agent_actions
        WHERE LOWER(COALESCE(thesis_key, '')) = ?
          AND action_type = ?
          AND created_at >= ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (clean_key, clean_type, cutoff.isoformat()),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"blocked": False, "remaining_seconds": 0, "action_id": 0}
    created_at = _parse_iso(row["created_at"])
    if created_at is None:
        return {"blocked": True, "remaining_seconds": 0, "action_id": int(row["id"] or 0)}
    remaining = max(0, int((created_at + timedelta(minutes=max(1, int(minutes or ACTION_COOLDOWN_MINUTES))) - datetime.now(timezone.utc)).total_seconds()))
    return {"blocked": remaining > 0, "remaining_seconds": remaining, "action_id": int(row["id"] or 0)}


def pending_action_count() -> int:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM agent_actions
        WHERE status IN ('draft', 'proposed', 'auto_approved', 'approved')
          AND COALESCE(executed_at, '') = ''
        """
    )
    row = cur.fetchone()
    conn.close()
    return int(row[0] or 0) if row else 0


def propose_action(action_type, payload, thesis_key, confidence, evidence_count, triggered_by) -> Dict:
    ensure_agent_tables()
    clean_type = str(action_type or "").strip()
    if clean_type not in VALID_ACTION_TYPES:
        raise ValueError("Unsupported action_type")
    clean_key = normalize_thesis_key(thesis_key)
    thesis = get_thesis(clean_key)
    detail = get_thesis_detail(clean_key)
    if not thesis or not detail:
        raise ValueError("Unknown thesis_key")

    confidence_value = float(confidence if confidence is not None else detail.get("confidence", 0.5))
    evidence_value = int(evidence_count if evidence_count is not None else detail.get("evidence_count", 0))

    if clean_type == "email_summary":
        preview_payload = {"body": generate_email_summary(clean_key)}
    elif clean_type == "slack_payload":
        preview_payload = generate_slack_payload(clean_key)
    elif clean_type == "alert":
        preview_payload = payload or {
            "title": detail.get("title", ""),
            "current_claim": detail.get("current_claim", ""),
            "confidence": confidence_value,
            "evidence_count": evidence_value,
        }
    else:
        preview_payload = payload or {
            "thesis_key": clean_key,
            "claim": detail.get("current_claim", ""),
            "confidence": confidence_value,
            "status": detail.get("status", "active"),
            "note": "Preview only. No webhook execution path is enabled.",
        }

    status, policy_note = _policy_outcome(confidence_value, evidence_value)
    audit_note = f"{policy_note}; triggered_by={str(triggered_by or 'system')}"
    now = utc_now_iso()

    conn = get_conn()
    cur = conn.cursor()
    existing_id = _has_open_action(cur, clean_key, clean_type)
    if existing_id:
        conn.close()
        return _fetch_action(existing_id)

    cur.execute(
        """
        INSERT INTO agent_actions (
            action_type, payload_json, thesis_key, confidence, evidence_count, status,
            triggered_by, created_at, reviewed_at, executed_at, audit_note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_type,
            json.dumps(preview_payload, ensure_ascii=False),
            clean_key,
            confidence_value,
            evidence_value,
            status,
            str(triggered_by or "system"),
            now,
            now if status == "auto_approved" else "",
            "",
            audit_note,
        ),
    )
    action_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    record_thesis_event(clean_key, "action_proposed", f"{clean_type} proposed ({status})", confidence_value, evidence_value)
    return _fetch_action(action_id)


def preview_action(action_id: int) -> Dict:
    action = _fetch_action(action_id)
    if not action:
        return {}
    if action.get("action_type") == "email_summary":
        return {"preview": {"body": generate_email_summary(action.get("thesis_key", ""))}}
    if action.get("action_type") == "slack_payload":
        return {"preview": generate_slack_payload(action.get("thesis_key", ""))}
    return {"preview": action.get("payload", {})}


def approve_action(action_id, approved_by) -> Dict:
    ensure_agent_tables()
    action = _fetch_action(action_id)
    if not action:
        return {}
    now = utc_now_iso()
    note = f"Approved by {str(approved_by or 'operator')} at {now}"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE agent_actions
        SET status = 'approved', reviewed_at = ?, audit_note = ?
        WHERE id = ?
        """,
        (now, note, int(action_id)),
    )
    conn.commit()
    conn.close()
    updated = _fetch_action(action_id)
    if updated:
        record_thesis_event(updated.get("thesis_key", ""), "action_approved", note, updated.get("confidence", 0.0), updated.get("evidence_count", 0))
    return updated


def reject_action(action_id, reason) -> Dict:
    ensure_agent_tables()
    action = _fetch_action(action_id)
    if not action:
        return {}
    now = utc_now_iso()
    note = f"Rejected at {now}: {str(reason or 'No reason provided')}"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE agent_actions
        SET status = 'rejected', reviewed_at = ?, audit_note = ?
        WHERE id = ?
        """,
        (now, note, int(action_id)),
    )
    conn.commit()
    conn.close()
    updated = _fetch_action(action_id)
    if updated:
        record_thesis_event(updated.get("thesis_key", ""), "action_rejected", note, updated.get("confidence", 0.0), updated.get("evidence_count", 0))
    return updated


def evaluate_and_propose(thesis, decisions, db=None) -> Dict:
    thesis = thesis or {}
    if not thesis:
        return {}
    detail = get_thesis_detail(thesis.get("thesis_key", ""))
    thesis_data = {**thesis, **detail}
    status = str(thesis_data.get("status", "") or "").lower()
    confidence = float(thesis_data.get("confidence", 0.0) or 0.0)
    evidence_count = int(thesis_data.get("evidence_count", 0) or 0)
    category = str(thesis_data.get("category", "other") or "other").lower()

    candidate = None
    payload = {}
    triggered_by = "auto_loop"
    actionable_status = status in ("active", "confirmed", "tracking")
    if actionable_status and confidence >= float(ACTION_CRITICAL_MIN_CONFIDENCE or 0.85) and category in ("markets", "geopolitics", "energy"):
        candidate = "email_summary"
        payload = thesis_data
        triggered_by = "auto_loop_critical"
    elif status == "contradicted":
        candidate = "slack_payload"
        payload = {
            "title": thesis_data.get("title", ""),
            "bear_case": thesis_data.get("bear_case", ""),
            "contradiction_count": int(thesis_data.get("contradiction_count", 0) or 0),
        }
        triggered_by = "auto_loop_contradiction"
    elif actionable_status and confidence >= float(ACTION_PROPOSAL_MIN_CONFIDENCE or 0.55):
        candidate = "alert"
        payload = {
            "title": thesis_data.get("title", ""),
            "current_claim": thesis_data.get("current_claim", ""),
            "confidence": confidence,
            "evidence_count": evidence_count,
        }
        triggered_by = "auto_loop"

    if not candidate:
        return {}

    cooldown = action_on_cooldown(thesis_data.get("thesis_key", ""), candidate)
    if cooldown.get("blocked"):
        return {
            "blocked": True,
            "blocked_reason": "cooldown",
            "remaining_seconds": int(cooldown.get("remaining_seconds", 0) or 0),
            "candidate_action_type": candidate,
            "thesis_key": thesis_data.get("thesis_key", ""),
        }

    return propose_action(
        candidate,
        payload,
        thesis_data.get("thesis_key", ""),
        confidence,
        evidence_count,
        triggered_by,
    )
    return {}
