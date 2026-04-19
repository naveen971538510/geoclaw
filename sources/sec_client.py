"""
SEC EDGAR public API client — no API keys required.

Tracks recent filings for major tickers. Uses EDGAR's full-text search and
company filings JSON endpoint.

User-Agent must include a contact email per SEC EDGAR Terms of Service.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

_TRACKED_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "JPM", "GS", "XOM", "CVX"]
_FORM_TYPES = ["8-K", "10-Q", "10-K", "S-1", "13F-HR", "SC 13D", "4"]
_EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_FILINGS_URL = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_FULL_TEXT = "https://efts.sec.gov/LATEST/search-index"
_TIMEOUT = 12
_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "GeoClaw/1.0 (macro-intelligence agent; contact@geoclaw.dev)",
)


def _fetch_recent_filings(ticker: str, form_types: Optional[List[str]] = None, limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch recent filings for a single ticker via EDGAR full-text search."""
    forms = form_types or _FORM_TYPES
    # Use the EDGAR EFTS (full-text search) API
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": f'"{ticker}"',
        "dateRange": "custom",
        "startdt": "2024-01-01",
        "forms": ",".join(forms),
    }
    try:
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        # EDGAR sometimes returns 403 for rapid requests; fall back to
        # the simpler company search endpoint
        if resp.status_code == 403:
            return _fetch_via_company_search(ticker, limit)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])[:limit]
        results = []
        for hit in hits:
            src = hit.get("_source") or {}
            results.append({
                "headline": f"[SEC {src.get('form_type', '?')}] {src.get('display_names', [ticker])[0] if src.get('display_names') else ticker}: {str(src.get('file_description', ''))[:200]}",
                "source": f"sec:{ticker}",
                "url": f"https://www.sec.gov/Archives/edgar/data/{src.get('file_num', '')}/{src.get('file_name', '')}",
                "published_at": str(src.get("file_date") or datetime.now(timezone.utc).isoformat()),
                "form_type": str(src.get("form_type") or ""),
                "content": str(src.get("file_description") or "")[:500],
            })
        return results
    except Exception:
        return _fetch_via_company_search(ticker, limit)


def _fetch_via_company_search(ticker: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Fallback: use the EDGAR company tickers JSON and recent filings RSS."""
    try:
        # Use the EDGAR company search
        resp = requests.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={
                "action": "getcompany",
                "company": ticker,
                "type": "8-K",
                "dateb": "",
                "owner": "include",
                "count": str(limit),
                "search_text": "",
                "output": "atom",
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        # Parse Atom feed for filing links
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)[:limit]
        results = []
        for entry in entries:
            title_el = entry.find("atom:title", ns)
            link_el = entry.find("atom:link", ns)
            updated_el = entry.find("atom:updated", ns)
            summary_el = entry.find("atom:summary", ns)
            results.append({
                "headline": f"[SEC] {title_el.text if title_el is not None else ticker}",
                "source": f"sec:{ticker}",
                "url": link_el.attrib.get("href", "") if link_el is not None else "",
                "published_at": updated_el.text if updated_el is not None else datetime.now(timezone.utc).isoformat(),
                "content": (summary_el.text or "")[:500] if summary_el is not None else "",
            })
        return results
    except Exception:
        return []


def fetch_sec_filings(
    tickers: Optional[List[str]] = None,
    limit_per_ticker: int = 3,
    delay: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Fetch recent SEC filings for tracked tickers.
    Gated by ENABLE_SEC env var (default true).
    """
    if os.environ.get("ENABLE_SEC", "true").strip().lower() not in {"1", "true", "yes"}:
        return []

    targets = tickers or _TRACKED_TICKERS
    results: List[Dict[str, Any]] = []

    for ticker in targets:
        try:
            filings = _fetch_recent_filings(ticker, limit=limit_per_ticker)
            results.extend(filings)
        except Exception:
            pass
        if delay > 0:
            time.sleep(delay)

    return results


if __name__ == "__main__":
    filings = fetch_sec_filings(tickers=["AAPL"], limit_per_ticker=3)
    for f in filings:
        print(f"[{f['source']}] {f['headline'][:120]}")
    print(f"\nFetched {len(filings)} filings")
