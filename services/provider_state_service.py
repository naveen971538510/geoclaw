import json
import time
from typing import Dict

from config import PROVIDER_STATE_FILE


def _default_state() -> Dict:
    return {
        "providers": {},
        "last_self_test_at": 0,
        "updated_at": 0,
    }


def _read_state() -> Dict:
    try:
        if PROVIDER_STATE_FILE.exists():
            data = json.loads(PROVIDER_STATE_FILE.read_text())
            if isinstance(data, dict):
                base = _default_state()
                base.update(data)
                if "providers" not in base or not isinstance(base["providers"], dict):
                    base["providers"] = {}
                return base
    except Exception:
        pass
    return _default_state()


def _write_state(state: Dict):
    state["updated_at"] = int(time.time())
    PROVIDER_STATE_FILE.write_text(json.dumps(state))


def get_provider_state() -> Dict:
    return _read_state()


def _entry(state: Dict, provider: str) -> Dict:
    providers = state.setdefault("providers", {})
    item = providers.setdefault(provider, {})
    return item


def record_provider_success(provider: str):
    state = _read_state()
    item = _entry(state, provider)
    item["status"] = "ok"
    item["reason"] = ""
    item["retry_after"] = 0
    item["updated_at"] = int(time.time())
    _write_state(state)


def mark_provider_invalid(provider: str, reason: str = "unauthorized key"):
    state = _read_state()
    item = _entry(state, provider)
    item["status"] = "invalid"
    item["reason"] = str(reason or "unauthorized key")
    item["retry_after"] = 0
    item["updated_at"] = int(time.time())
    _write_state(state)


def mark_provider_limited(provider: str, reason: str = "rate limited", retry_after_seconds: int = 900):
    state = _read_state()
    item = _entry(state, provider)
    item["status"] = "limited"
    item["reason"] = str(reason or "rate limited")
    item["retry_after"] = int(time.time()) + int(retry_after_seconds)
    item["updated_at"] = int(time.time())
    _write_state(state)


def mark_provider_temp_issue(provider: str, reason: str = "temporary issue", retry_after_seconds: int = 300):
    state = _read_state()
    item = _entry(state, provider)
    item["status"] = "limited"
    item["reason"] = str(reason or "temporary issue")
    item["retry_after"] = int(time.time()) + int(retry_after_seconds)
    item["updated_at"] = int(time.time())
    _write_state(state)


def provider_ready(provider: str, configured: bool) -> bool:
    if not configured:
        return False
    state = _read_state()
    item = (state.get("providers") or {}).get(provider, {})
    status = str(item.get("status", "") or "")
    retry_after = int(item.get("retry_after", 0) or 0)
    now = int(time.time())

    if status == "invalid":
        return False
    if status == "limited" and retry_after and now < retry_after:
        return False
    return True


def self_test_due(min_interval_seconds: int) -> bool:
    state = _read_state()
    last = int(state.get("last_self_test_at", 0) or 0)
    return int(time.time()) >= (last + int(min_interval_seconds))


def mark_self_test_ran():
    state = _read_state()
    state["last_self_test_at"] = int(time.time())
    _write_state(state)
