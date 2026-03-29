import json
import time
from typing import Dict, List

from config import DB_PATH, DEFAULT_WATCHLIST, OPERATOR_STATE_FILE
from services.db_helpers import get_conn


def _default_state() -> Dict:
    return {
        "watchlist": list(DEFAULT_WATCHLIST),
        "read_alerts": {},
        "starred_articles": {},
        "updated_at": 0,
    }


def _clean_list(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items or []:
        clean = str(item or "").strip().lower()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _clean_map(value: Dict) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for key, enabled in (value or {}).items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        out[clean_key] = bool(enabled)
    return out


def _read_state() -> Dict:
    state = _default_state()
    try:
        if OPERATOR_STATE_FILE.exists():
            raw = json.loads(OPERATOR_STATE_FILE.read_text())
            if isinstance(raw, dict):
                state["watchlist"] = _clean_list(raw.get("watchlist", state["watchlist"]))
                state["read_alerts"] = _clean_map(raw.get("read_alerts", {}))
                state["starred_articles"] = _clean_map(raw.get("starred_articles", {}))
                state["updated_at"] = int(raw.get("updated_at", 0) or 0)
    except Exception:
        pass
    return state


def _write_state(state: Dict) -> Dict:
    state["updated_at"] = int(time.time())
    OPERATOR_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    _sync_starred_alerts(state)
    return state


def _sync_starred_alerts(state: Dict):
    conn = get_conn(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(alert_events)")
    columns = {row[1] for row in cur.fetchall()}
    if "is_starred" not in columns:
        cur.execute("ALTER TABLE alert_events ADD COLUMN is_starred INTEGER DEFAULT 0")
    if "status" not in columns:
        cur.execute("ALTER TABLE alert_events ADD COLUMN status TEXT DEFAULT 'open'")

    cur.execute("UPDATE alert_events SET is_starred = 0 WHERE COALESCE(is_starred, 0) != 0")
    starred = _clean_map(state.get("starred_articles", {}))
    keys = [str(key or "").strip() for key, enabled in starred.items() if enabled and str(key or "").strip()]
    for key in keys:
        cur.execute(
            """
            UPDATE alert_events
            SET is_starred = 1
            WHERE article_id IN (
                SELECT id
                FROM ingested_articles
                WHERE url = ? OR headline = ?
            )
            """,
            (key, key),
        )
    conn.commit()
    conn.close()


def get_operator_state() -> Dict:
    return _read_state()


def update_operator_state(patch: Dict) -> Dict:
    state = _read_state()
    if "watchlist" in patch:
        state["watchlist"] = _clean_list(patch.get("watchlist", []))
    if "read_alerts" in patch:
        state["read_alerts"] = _clean_map(patch.get("read_alerts", {}))
    if "starred_articles" in patch:
        state["starred_articles"] = _clean_map(patch.get("starred_articles", {}))
    return _write_state(state)


def merge_operator_state(patch: Dict) -> Dict:
    state = _read_state()
    if "watchlist" in patch:
        merged = state["watchlist"] + list(patch.get("watchlist", []) or [])
        state["watchlist"] = _clean_list(merged)
    if "read_alerts" in patch:
        state["read_alerts"].update(_clean_map(patch.get("read_alerts", {})))
    if "starred_articles" in patch:
        state["starred_articles"].update(_clean_map(patch.get("starred_articles", {})))
    return _write_state(state)
