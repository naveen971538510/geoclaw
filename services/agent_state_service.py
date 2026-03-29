import json
from datetime import datetime, timedelta, timezone
from typing import Dict

from config import AGENT_AUTONOMOUS_GOAL_INTERVAL_RUNS, AGENT_BRIEFING_INTERVAL_HOURS, AGENT_REFLECTION_INTERVAL_RUNS, AGENT_STATE_FILE


def _default_state() -> Dict:
    return {
        "real_agent_runs": 0,
        "briefing_last_run": "",
        "reflection_interval_runs": int(AGENT_REFLECTION_INTERVAL_RUNS),
        "autonomous_goal_interval_runs": int(AGENT_AUTONOMOUS_GOAL_INTERVAL_RUNS),
        "briefing_interval_hours": int(AGENT_BRIEFING_INTERVAL_HOURS),
        "daily_counters": {},
        "cooldowns": {},
    }


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _prune_daily_counters(state: Dict) -> Dict:
    counters = state.get("daily_counters", {}) or {}
    keep = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    for day_key, values in counters.items():
        try:
            parsed = datetime.strptime(str(day_key), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if parsed >= cutoff:
            keep[str(day_key)] = values if isinstance(values, dict) else {}
    state["daily_counters"] = keep
    return state


def get_agent_state() -> Dict:
    state = _default_state()
    try:
        if AGENT_STATE_FILE.exists():
            raw = json.loads(AGENT_STATE_FILE.read_text())
            if isinstance(raw, dict):
                state.update(raw)
    except Exception:
        pass
    return state


def save_agent_state(state: Dict) -> Dict:
    merged = _default_state()
    merged.update(state or {})
    _prune_daily_counters(merged)
    AGENT_STATE_FILE.write_text(json.dumps(merged, indent=2, sort_keys=True))
    return merged


def bump_real_agent_run() -> Dict:
    state = get_agent_state()
    state["real_agent_runs"] = int(state.get("real_agent_runs", 0) or 0) + 1
    return save_agent_state(state)


def get_daily_counter(name: str, day_key: str = None) -> int:
    state = get_agent_state()
    counters = state.get("daily_counters", {}) or {}
    day = str(day_key or _today_key())
    day_values = counters.get(day, {}) or {}
    return int(day_values.get(str(name or ""), 0) or 0)


def bump_daily_counter(name: str, amount: int = 1) -> Dict:
    state = get_agent_state()
    day = _today_key()
    counters = state.setdefault("daily_counters", {})
    day_values = counters.setdefault(day, {})
    key = str(name or "")
    day_values[key] = int(day_values.get(key, 0) or 0) + int(amount or 0)
    return save_agent_state(state)


def cooldown_remaining(bucket: str, key: str) -> int:
    state = get_agent_state()
    cooldowns = state.get("cooldowns", {}) or {}
    bucket_map = cooldowns.get(str(bucket or ""), {}) or {}
    until_ts = int(bucket_map.get(str(key or ""), 0) or 0)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return max(0, until_ts - now_ts)


def is_cooldown_active(bucket: str, key: str) -> bool:
    return cooldown_remaining(bucket, key) > 0


def set_cooldown(bucket: str, key: str, minutes: int) -> Dict:
    state = get_agent_state()
    cooldowns = state.setdefault("cooldowns", {})
    bucket_name = str(bucket or "")
    bucket_map = cooldowns.setdefault(bucket_name, {})
    now_ts = int(datetime.now(timezone.utc).timestamp())
    bucket_map[str(key or "")] = now_ts + max(60, int(minutes or 0) * 60)
    return save_agent_state(state)
