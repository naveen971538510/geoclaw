import json
from typing import Dict, List

from services.goal_service import ensure_agent_tables, get_conn, list_goals, utc_now_iso
from services.llm_service import analyse_custom_json
from services.operator_state_service import get_operator_state
from services.action_service import propose_action
from services.thesis_service import get_thesis, normalize_thesis_key, upsert_thesis


def _fallback_reasoning(headline: str, category: str) -> Dict:
    suggestion = "oil" if category == "energy" else "sanctions" if category == "geopolitics" else "fed"
    return {
        "chain": [
            {
                "hop": 1,
                "from": headline,
                "to": "market positioning",
                "mechanism": "The event can change how investors price near-term risk and liquidity.",
                "confidence": 0.55,
                "timeframe": "immediate",
            },
            {
                "hop": 2,
                "from": "market positioning",
                "to": suggestion,
                "mechanism": "Positioning changes can spill into the most directly exposed asset or policy theme.",
                "confidence": 0.5,
                "timeframe": "days",
            },
        ],
        "terminal_risk": f"Watch for follow-through around {suggestion}.",
        "watchlist_suggestion": suggestion,
    }


def build_reasoning_chain(headline, category, db=None, article_id=None, thesis_key="", source_name=""):
    ensure_agent_tables()
    headline = str(headline or "").strip()
    category = str(category or "other").strip().lower() or "other"
    fallback = _fallback_reasoning(headline, category)
    system_text = (
        "You are a financial and geopolitical analyst. Given this event, trace the chain of implications step by step. "
        "Each step must follow logically from the previous. Maximum 5 hops. "
        "Return JSON only: { chain: [{ hop, from, to, mechanism, confidence, timeframe }], terminal_risk, watchlist_suggestion }"
    )
    user_text = f"Headline: {headline}\nCategory: {category}"

    def _valid(payload):
        return isinstance(payload, dict) and isinstance(payload.get("chain"), list) and "terminal_risk" in payload and "watchlist_suggestion" in payload

    def _clean(payload):
        chain = []
        for raw in (payload.get("chain") or [])[:5]:
            chain.append(
                {
                    "hop": int(raw.get("hop", len(chain) + 1) or len(chain) + 1),
                    "from": str(raw.get("from") or "").strip(),
                    "to": str(raw.get("to") or "").strip(),
                    "mechanism": str(raw.get("mechanism") or "").strip(),
                    "confidence": float(raw.get("confidence") or 0.5),
                    "timeframe": str(raw.get("timeframe") or "days").strip(),
                }
            )
        return {
            "chain": chain or fallback["chain"],
            "terminal_risk": str(payload.get("terminal_risk") or fallback["terminal_risk"]).strip(),
            "watchlist_suggestion": str(payload.get("watchlist_suggestion") or fallback["watchlist_suggestion"]).strip().lower(),
        }

    result = analyse_custom_json(
        system_text,
        user_text,
        fallback=fallback,
        mode="reasoning_chain",
        cache_key=f"reasoning::{normalize_thesis_key(headline)}::{category}",
        validator=_valid,
        cleaner=_clean,
        lane="reason",
        task_type="reasoning_chain",
    )["analysis"]

    conn = db or get_conn()
    close_conn = db is None
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO reasoning_chains (article_id, thesis_key, chain_json, terminal_risk, watchlist_suggestion, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(article_id) if article_id else None,
            normalize_thesis_key(thesis_key),
            json.dumps(result.get("chain", []), ensure_ascii=False),
            str(result.get("terminal_risk", "") or ""),
            str(result.get("watchlist_suggestion", "") or ""),
            utc_now_iso(),
        ),
    )
    reasoning_id = int(cur.lastrowid)
    conn.commit()
    if close_conn:
        conn.close()

    watchlist = set((get_operator_state() or {}).get("watchlist", []) or [])
    if not watchlist:
        for goal in list_goals(active_only=True):
            for target in (goal.get("watch_targets", []) or []):
                clean = str(target or "").strip().lower()
                if clean:
                    watchlist.add(clean)
    suggestion = str(result.get("watchlist_suggestion", "") or "").lower()
    clean_key = normalize_thesis_key(thesis_key or headline)
    existing_thesis = get_thesis(clean_key)
    if not existing_thesis and clean_key:
        existing_thesis = upsert_thesis(
            clean_key,
            current_claim=headline,
            confidence=0.5,
            status="tracking",
            evidence_delta=1,
            category=category,
            source_name=source_name or "unknown",
        )
    if suggestion and suggestion in watchlist and existing_thesis:
        propose_action(
            "alert",
            {
                "headline": headline,
                "chain": result.get("chain", []),
                "terminal_risk": result.get("terminal_risk", ""),
            },
            clean_key,
            float(existing_thesis.get("confidence", 0.5) or 0.5),
            int(existing_thesis.get("evidence_count", 0) or 0),
            "reasoning_chain",
        )

    return {"id": reasoning_id, **result}


def list_reasoning_chains(limit: int = 20) -> List[Dict]:
    ensure_agent_tables()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, article_id, thesis_key, chain_json, terminal_risk, watchlist_suggestion, created_at
        FROM reasoning_chains
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        try:
            item["chain"] = json.loads(item.pop("chain_json") or "[]")
        except Exception:
            item["chain"] = []
        rows.append(item)
    conn.close()
    return rows
