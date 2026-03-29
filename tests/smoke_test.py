#!/usr/bin/env python3
"""Full route smoke test. Server must be running on port 8000."""

import json
import sys
import urllib.error
import urllib.request


BASE = "http://127.0.0.1:8000"
OK = 0
FAIL = 0


def chk(path, method="GET", code=200, key=None):
    global OK, FAIL
    try:
        req = urllib.request.Request(f"{BASE}{path}", method=method)
        with urllib.request.urlopen(req, timeout=10) as response:
            status = response.status
            raw = response.read().decode("utf-8", errors="ignore")
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                body = json.loads(raw) if raw else {}
            else:
                body = {"_text": raw}
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = {}
    except Exception as exc:
        print(f"  ✗ {path} — {exc}")
        FAIL += 1
        return
    if callable(key):
        key_ok = key(body)
    elif isinstance(key, (list, tuple, set)):
        key_ok = any(item in body for item in key)
    else:
        key_ok = key is None or key in body
    passed = status == code and key_ok
    print(f"  {'✓' if passed else '✗'} {path} → {status}")
    if passed:
        OK += 1
    else:
        FAIL += 1


if __name__ == "__main__":
    print("\nGeoClaw smoke test")
    print("=" * 50)
    for page in [
        "/dashboard",
        "/terminal",
        "/theses",
        "/articles",
        "/agent-runs",
        "/briefings",
        "/contradictions",
        "/watchlist",
    ]:
        chk(page)
    chk("/health", key="status")
    chk("/health/deep", key="status")
    chk("/api/articles", key="articles")
    chk("/api/clusters", key="clusters")
    chk("/api/watchlist", key="items")
    chk("/api/contradictions", key="contradictions")
    chk("/api/alerts", key="alerts")
    chk("/api/alerts/unread/count", key="count")
    chk("/api/search?q=oil", key="theses")
    chk("/api/agent/status", key="thesis_count")
    chk("/api/prices", key="status")
    chk("/api/intelligence/narratives", key="status")
    chk("/api/intelligence/regime", key="regime")
    chk("/api/scheduler/status", key=["scheduler_alive", "scheduler"])
    chk("/terminal/agent-summary", key="status")
    chk("/terminal/theses", key="theses")
    chk("/terminal/actions", key=["actions", "items"])
    chk("/terminal/diff", key="status")
    chk("/agent-briefing/latest", key="status")
    chk("/agent-journal")
    chk("/agent-reasoning")
    print(f"\n{'=' * 50}")
    print(f"{OK} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
