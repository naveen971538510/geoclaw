from typing import Dict, Iterable, List


VALID_TIMEFRAMES = {"immediate", "days", "weeks", "months"}
VALID_TERMINAL_RISKS = {"HIGH", "MEDIUM", "LOW"}
VALID_APPROVAL_STATES = {"pending", "draft", "approved", "rejected", "auto_approved", "proposed"}


def sanitize_model_name(value: str, default: str = "gpt-5.4-mini") -> str:
    clean = str(value or "").strip().strip('"').strip("'")
    if not clean:
        return default
    allowed = []
    for char in clean:
        if char.isalnum() or char in {"-", "_", ".", ":"}:
            allowed.append(char)
            continue
        break
    sanitized = "".join(allowed).strip()
    return sanitized or default


def clamp(value, minimum: float, maximum: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(fallback)
    return max(float(minimum), min(float(maximum), number))


def clamp_confidence(value, fallback: float = 0.5) -> float:
    return clamp(value, 0.0, 1.0, fallback)


def clamp_delta(value, fallback: float = 0.05) -> float:
    return clamp(value, -0.20, 0.25, fallback)


def _string_list(values, limit: int = 5) -> List[str]:
    out = []
    seen = set()
    for raw in values or []:
        text = str(raw or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        out.append(text)
        seen.add(key)
        if len(out) >= int(limit):
            break
    return out


def default_thesis_bundle(headline: str = "", existing_thesis: Dict = None) -> Dict:
    base_title = str((existing_thesis or {}).get("thesis_key") or headline or "Developing market thesis").strip()
    return {
        "thesis_key": base_title[:180] or "Developing market thesis",
        "confidence_delta": 0.05,
        "timeframe": "days",
        "terminal_risk": "LOW",
        "market_implication": "Monitor the directly exposed assets for confirmation.",
        "watchlist_suggestion": "",
        "reasoning": "The event can shift expectations, but it still needs confirming follow-through.",
        "confidence_basis": "Confidence would fall if corroborating headlines fail to appear.",
        "why_now": "This story is active because fresh evidence has entered the system.",
    }


def validate_thesis_bundle(payload: Dict) -> bool:
    required = {
        "thesis_key",
        "confidence_delta",
        "timeframe",
        "terminal_risk",
        "market_implication",
        "watchlist_suggestion",
        "reasoning",
        "confidence_basis",
    }
    if not isinstance(payload, dict) or any(key not in payload for key in required):
        return False
    if not isinstance(payload.get("thesis_key"), str) or not str(payload.get("thesis_key") or "").strip():
        return False
    if str(payload.get("timeframe") or "").strip().lower() not in VALID_TIMEFRAMES:
        return False
    if str(payload.get("terminal_risk") or "").strip().upper() not in VALID_TERMINAL_RISKS:
        return False
    for key in ("market_implication", "watchlist_suggestion", "reasoning", "confidence_basis"):
        if not isinstance(payload.get(key), str):
            return False
    try:
        float(payload.get("confidence_delta"))
    except (TypeError, ValueError):
        return False
    return True


def clean_thesis_bundle(payload: Dict, headline: str = "", existing_thesis: Dict = None) -> Dict:
    fallback = default_thesis_bundle(headline=headline, existing_thesis=existing_thesis)
    return {
        "thesis_key": str(payload.get("thesis_key") or fallback["thesis_key"]).strip()[:180] or fallback["thesis_key"],
        "confidence_delta": clamp_delta(payload.get("confidence_delta"), fallback=fallback["confidence_delta"]),
        "timeframe": str(payload.get("timeframe") or fallback["timeframe"]).strip().lower() if str(payload.get("timeframe") or "").strip().lower() in VALID_TIMEFRAMES else fallback["timeframe"],
        "terminal_risk": str(payload.get("terminal_risk") or fallback["terminal_risk"]).strip().upper() if str(payload.get("terminal_risk") or "").strip().upper() in VALID_TERMINAL_RISKS else fallback["terminal_risk"],
        "market_implication": str(payload.get("market_implication") or fallback["market_implication"]).strip(),
        "watchlist_suggestion": str(payload.get("watchlist_suggestion") or fallback["watchlist_suggestion"]).strip(),
        "reasoning": str(payload.get("reasoning") or fallback["reasoning"]).strip(),
        "confidence_basis": str(payload.get("confidence_basis") or fallback["confidence_basis"]).strip(),
        "why_now": str(payload.get("why_now") or payload.get("reasoning") or fallback["why_now"]).strip(),
    }


def default_query_answer_bundle(result: Dict) -> Dict:
    result = result or {}
    answer = str(result.get("direct_answer") or result.get("answer") or "No answer available.").strip()
    return {
        "direct_answer": answer,
        "supporting_points": _string_list(result.get("supporting_points") or result.get("grounding_points") or [], limit=4),
        "follow_up": _string_list(result.get("follow_up") or [], limit=3),
        "caveat": str(result.get("caveat") or "").strip(),
    }


def validate_query_answer_bundle(payload: Dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("direct_answer"), str) or not str(payload.get("direct_answer") or "").strip():
        return False
    if not isinstance(payload.get("supporting_points"), list):
        return False
    points = [item for item in payload.get("supporting_points") if isinstance(item, str) and item.strip()]
    if len(points) < 2:
        return False
    follow_up = payload.get("follow_up", [])
    if follow_up is not None and not isinstance(follow_up, list):
        return False
    return True


def clean_query_answer_bundle(payload: Dict, fallback: Dict) -> Dict:
    fallback = default_query_answer_bundle(fallback)
    direct_answer = str(payload.get("direct_answer") or fallback["direct_answer"]).strip() or fallback["direct_answer"]
    supporting_points = _string_list(payload.get("supporting_points") or fallback["supporting_points"], limit=4) or fallback["supporting_points"]
    follow_up = _string_list(payload.get("follow_up") or fallback["follow_up"], limit=3) or fallback["follow_up"]
    caveat = str(payload.get("caveat") or fallback.get("caveat", "")).strip()
    return {
        "direct_answer": direct_answer,
        "supporting_points": supporting_points,
        "follow_up": follow_up,
        "caveat": caveat,
    }


def format_query_answer_text(bundle: Dict, confidence: float, sources: Iterable[str]) -> str:
    bundle = default_query_answer_bundle(bundle)
    lines = [bundle["direct_answer"]]
    if bundle["supporting_points"]:
        lines.append("")
        lines.extend([f"- {point}" for point in bundle["supporting_points"][:4]])
    lines.append("")
    lines.append(f"Confidence: {round(clamp_confidence(confidence) * 100)}%")
    source_list = [str(item or "").strip() for item in (sources or []) if str(item or "").strip()]
    if source_list:
        lines.append("Sources: " + ", ".join(source_list))
    if bundle.get("caveat"):
        lines.append("Note: " + bundle["caveat"])
    return "\n".join(lines).strip()


def default_debate_argument(persona: str = "bull", argument: str = "", key_point: str = "") -> Dict:
    fallback_argument = argument or (
        "The evidence set is still incomplete, so this thesis should be treated as provisional."
        if persona == "bear"
        else "The evidence set is building, so the thesis deserves close monitoring."
    )
    fallback_key_point = key_point or ("Evidence remains incomplete" if persona == "bear" else "Risk is still live")
    return {
        "argument": str(fallback_argument).strip(),
        "key_point": str(fallback_key_point).strip(),
    }


def validate_debate_argument(payload: Dict) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("argument"), str)
        and isinstance(payload.get("key_point"), str)
        and bool(str(payload.get("argument") or "").strip())
        and bool(str(payload.get("key_point") or "").strip())
    )


def clean_debate_argument(payload: Dict, fallback: Dict) -> Dict:
    fallback = default_debate_argument(argument=fallback.get("argument", ""), key_point=fallback.get("key_point", ""))
    return {
        "argument": str(payload.get("argument") or fallback["argument"]).strip() or fallback["argument"],
        "key_point": str(payload.get("key_point") or fallback["key_point"]).strip() or fallback["key_point"],
    }


def default_briefing_bundle(fallback_text: str = "", watch_items: List[str] = None) -> Dict:
    return {
        "headline": "GeoClaw Intelligence Brief",
        "sections": [
            {"title": "Top developing stories", "points": _string_list([fallback_text], limit=1) or ["The thesis layer is still consolidating."]},
        ],
        "watch_items": _string_list(watch_items or [], limit=4),
        "closing": "Stay focused on the highest-confidence thesis, the latest contradictions, and the next confirming headline.",
    }


def validate_briefing_bundle(payload: Dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("headline"), str) or not str(payload.get("headline") or "").strip():
        return False
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        return False
    for section in sections[:6]:
        if not isinstance(section, dict):
            return False
        if not isinstance(section.get("title"), str) or not str(section.get("title") or "").strip():
            return False
        if not isinstance(section.get("points"), list):
            return False
        if not _string_list(section.get("points") or [], limit=4):
            return False
    return True


def clean_briefing_bundle(payload: Dict, fallback: Dict) -> Dict:
    fallback = default_briefing_bundle(
        fallback_text=" ".join(
            [
                str(point)
                for section in (fallback.get("sections") or [])
                for point in (section.get("points") or [])
            ]
        ),
        watch_items=fallback.get("watch_items") or [],
    )
    sections = []
    for raw in (payload.get("sections") or fallback.get("sections") or [])[:6]:
        title = str(raw.get("title") or "").strip()
        points = _string_list(raw.get("points") or [], limit=4)
        if title and points:
            sections.append({"title": title, "points": points})
    if not sections:
        sections = fallback["sections"]
    return {
        "headline": str(payload.get("headline") or fallback["headline"]).strip() or fallback["headline"],
        "sections": sections,
        "watch_items": _string_list(payload.get("watch_items") or fallback.get("watch_items") or [], limit=4),
        "closing": str(payload.get("closing") or fallback["closing"]).strip() or fallback["closing"],
    }


def render_briefing_bundle(bundle: Dict) -> str:
    clean = clean_briefing_bundle(bundle, bundle)
    lines = [f"## {clean['headline']}", ""]
    for section in clean["sections"]:
        lines.append(f"### {section['title']}")
        lines.extend([f"- {point}" for point in section["points"]])
        lines.append("")
    if clean["watch_items"]:
        lines.append("### What to watch")
        lines.extend([f"- {item}" for item in clean["watch_items"]])
        lines.append("")
    lines.append(clean["closing"])
    return "\n".join(lines).strip()


def normalize_action_reasoning(action: Dict) -> Dict:
    action = action or {}
    status = str(action.get("status") or "pending").strip().lower()
    raw_approval_state = str(action.get("approval_state") or "").strip().lower()
    approval_state = raw_approval_state if raw_approval_state in VALID_APPROVAL_STATES else (status if status in VALID_APPROVAL_STATES else "pending")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    metadata = action.get("metadata") if isinstance(action.get("metadata"), dict) else {}
    raw_reason = (
        str(action.get("reason") or "").strip()
        or str(metadata.get("reason") or "").strip()
        or str(action.get("audit_note") or "").strip()
        or str(payload.get("reason") or "").strip()
        or str(payload.get("terminal_risk") or "").strip()
        or str(action.get("thesis_claim") or action.get("thesis_key") or "").strip()
    )
    why_now = (
        str(payload.get("watchlist_suggestion") or "").strip()
        or str(payload.get("headline") or "").strip()
        or str(action.get("thesis_title") or "").strip()
    )
    return {
        "reason": raw_reason[:240],
        "why_now": why_now[:140],
        "approval_state": approval_state,
    }
