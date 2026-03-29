import json
import time

import requests

from config import (
    NEWSAPI_KEY,
    GUARDIAN_API_KEY,
    ALPHAVANTAGE_KEY,
    ENABLE_RSS,
    ENABLE_GDELT,
    GDELT_STATE_FILE,
    PROVIDER_SELF_TEST_MIN_INTERVAL_SECONDS,
)
from services.provider_state_service import (
    get_provider_state,
    mark_provider_invalid,
    mark_provider_limited,
    mark_provider_temp_issue,
    mark_self_test_ran,
    record_provider_success,
    self_test_due,
)
from sources.rss_client import DEFAULT_RSS_FEEDS


def _set_gdelt_state(cooldown_until: int, reason: str = ""):
    try:
        GDELT_STATE_FILE.write_text(
            json.dumps(
                {
                    "cooldown_until": int(cooldown_until),
                    "reason": reason,
                    "updated_at": int(time.time()),
                }
            )
        )
    except Exception:
        pass


def _result(provider: str, status: str, reason: str = ""):
    payload = {"provider": provider, "status": status}
    if reason:
        payload["reason"] = reason
    return payload


def _rss_test():
    if not ENABLE_RSS:
        return _result("rss", "disabled")
    feed = (DEFAULT_RSS_FEEDS or [{}])[0]
    try:
        res = requests.get(
            str(feed.get("url", "") or ""),
            timeout=10,
            headers={"User-Agent": "GeoClaw/2.0"},
        )
        res.raise_for_status()
        body = (res.text or "").lower()
        if "<rss" not in body and "<feed" not in body:
            mark_provider_temp_issue("rss", "unexpected rss payload", retry_after_seconds=300)
            return _result("rss", "limited", "unexpected rss payload")
        record_provider_success("rss")
        return _result("rss", "ok", "rss feed reachable")
    except requests.exceptions.Timeout:
        mark_provider_temp_issue("rss", "timeout", retry_after_seconds=300)
        return _result("rss", "limited", "timeout")
    except Exception as exc:
        reason = str(exc) or "temporary issue"
        mark_provider_temp_issue("rss", reason, retry_after_seconds=300)
        return _result("rss", "limited", reason)


def _gdelt_test():
    if not ENABLE_GDELT:
        return _result("gdelt", "disabled")
    try:
        res = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": "(markets OR oil OR gold)",
                "mode": "ArtList",
                "format": "json",
                "sort": "DateDesc",
                "maxrecords": 1,
            },
            timeout=12,
            headers={"User-Agent": "GeoClaw/2.0"},
        )
        if res.status_code == 429:
            mark_provider_limited("gdelt", "rate limited", retry_after_seconds=900)
            _set_gdelt_state(int(time.time()) + 900, "429")
            return _result("gdelt", "limited", "rate limited")
        res.raise_for_status()
        data = res.json()
        articles = data.get("articles", []) or data.get("results", []) or []
        if not isinstance(articles, list):
            mark_provider_temp_issue("gdelt", "bad payload", retry_after_seconds=300)
            return _result("gdelt", "limited", "bad payload")
        record_provider_success("gdelt")
        _set_gdelt_state(0, "")
        return _result("gdelt", "ok", "live query ok")
    except requests.exceptions.Timeout:
        mark_provider_temp_issue("gdelt", "timeout", retry_after_seconds=300)
        _set_gdelt_state(int(time.time()) + 300, "timeout")
        return _result("gdelt", "limited", "timeout")
    except Exception as exc:
        reason = str(exc) or "temporary issue"
        low = reason.lower()
        if "429" in low or "rate" in low:
            mark_provider_limited("gdelt", "rate limited", retry_after_seconds=900)
            _set_gdelt_state(int(time.time()) + 900, "429-exception")
            return _result("gdelt", "limited", "rate limited")
        mark_provider_temp_issue("gdelt", reason, retry_after_seconds=300)
        _set_gdelt_state(int(time.time()) + 300, reason)
        return _result("gdelt", "limited", reason)


def _newsapi_test():
    if not NEWSAPI_KEY:
        return _result("newsapi", "missing")
    try:
        res = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": "markets", "pageSize": 1, "apiKey": NEWSAPI_KEY},
            timeout=12,
            headers={"User-Agent": "GeoClaw/2.0"},
        )
        if res.status_code in (401, 403):
            mark_provider_invalid("newsapi", "unauthorized key")
            return _result("newsapi", "invalid", "unauthorized key")
        if res.status_code == 429:
            mark_provider_limited("newsapi", "rate limited", retry_after_seconds=900)
            return _result("newsapi", "limited", "rate limited")
        res.raise_for_status()
        data = res.json()
        if data.get("status") == "error":
            low = f'{data.get("code", "")} {data.get("message", "")}'.lower()
            if "api key" in low or "apikey" in low or "unauthorized" in low:
                mark_provider_invalid("newsapi", "unauthorized key")
                return _result("newsapi", "invalid", "unauthorized key")
            if "rate" in low or "limit" in low:
                mark_provider_limited("newsapi", "rate limited", retry_after_seconds=900)
                return _result("newsapi", "limited", "rate limited")
            mark_provider_temp_issue("newsapi", "api error", retry_after_seconds=300)
            return _result("newsapi", "limited", "api error")
        record_provider_success("newsapi")
        return _result("newsapi", "ok")
    except requests.exceptions.Timeout:
        mark_provider_temp_issue("newsapi", "timeout", retry_after_seconds=300)
        return _result("newsapi", "limited", "timeout")
    except Exception as exc:
        reason = str(exc) or "temporary issue"
        mark_provider_temp_issue("newsapi", reason, retry_after_seconds=300)
        return _result("newsapi", "limited", reason)


def _guardian_test():
    if not GUARDIAN_API_KEY:
        return _result("guardian", "missing")
    try:
        res = requests.get(
            "https://content.guardianapis.com/search",
            params={"q": "markets", "page-size": 1, "api-key": GUARDIAN_API_KEY},
            timeout=12,
            headers={"User-Agent": "GeoClaw/2.0"},
        )
        if res.status_code in (401, 403):
            mark_provider_invalid("guardian", "unauthorized key")
            return _result("guardian", "invalid", "unauthorized key")
        if res.status_code == 429:
            mark_provider_limited("guardian", "rate limited", retry_after_seconds=900)
            return _result("guardian", "limited", "rate limited")
        res.raise_for_status()
        data = res.json()
        response = data.get("response") or {}
        if response.get("status") == "error":
            mark_provider_temp_issue("guardian", "api error", retry_after_seconds=300)
            return _result("guardian", "limited", "api error")
        record_provider_success("guardian")
        return _result("guardian", "ok")
    except requests.exceptions.Timeout:
        mark_provider_temp_issue("guardian", "timeout", retry_after_seconds=300)
        return _result("guardian", "limited", "timeout")
    except Exception as exc:
        reason = str(exc) or "temporary issue"
        mark_provider_temp_issue("guardian", reason, retry_after_seconds=300)
        return _result("guardian", "limited", reason)


def _alphavantage_test():
    if not ALPHAVANTAGE_KEY:
        return _result("alphavantage", "missing")
    try:
        res = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": "GLD", "apikey": ALPHAVANTAGE_KEY},
            timeout=12,
            headers={"User-Agent": "GeoClaw/2.0"},
        )
        res.raise_for_status()
        data = res.json()
        if data.get("Error Message"):
            mark_provider_invalid("alphavantage", "invalid key or symbol")
            return _result("alphavantage", "invalid", "invalid key or symbol")
        if data.get("Note") or data.get("Information"):
            low = (str(data.get("Note", "")) + " " + str(data.get("Information", ""))).lower()
            if "api key" in low or "premium" in low:
                mark_provider_invalid("alphavantage", "unauthorized or unsupported")
                return _result("alphavantage", "invalid", "unauthorized or unsupported")
            mark_provider_limited("alphavantage", "rate limited", retry_after_seconds=900)
            return _result("alphavantage", "limited", "rate limited")
        quote = data.get("Global Quote", {}) or {}
        price = quote.get("05. price")
        if price in (None, "", "0", "0.0"):
            mark_provider_temp_issue("alphavantage", "empty quote", retry_after_seconds=300)
            return _result("alphavantage", "limited", "empty quote")
        record_provider_success("alphavantage")
        return _result("alphavantage", "ok")
    except requests.exceptions.Timeout:
        mark_provider_temp_issue("alphavantage", "timeout", retry_after_seconds=300)
        return _result("alphavantage", "limited", "timeout")
    except Exception as exc:
        reason = str(exc) or "temporary issue"
        mark_provider_temp_issue("alphavantage", reason, retry_after_seconds=300)
        return _result("alphavantage", "limited", reason)


def run_provider_self_test(force: bool = False):
    if not force and not self_test_due(PROVIDER_SELF_TEST_MIN_INTERVAL_SECONDS):
        return {"status": "cached", "state": get_provider_state()}

    results = [
        _rss_test(),
        _gdelt_test(),
        _newsapi_test(),
        _guardian_test(),
        _alphavantage_test(),
    ]
    mark_self_test_ran()
    return {
        "status": "ok",
        "results": results,
        "summary": {
            "ok": len([x for x in results if x.get("status") == "ok"]),
            "limited": len([x for x in results if x.get("status") == "limited"]),
            "invalid": len([x for x in results if x.get("status") == "invalid"]),
            "missing": len([x for x in results if x.get("status") == "missing"]),
            "disabled": len([x for x in results if x.get("status") == "disabled"]),
        },
        "state": get_provider_state(),
    }
