"""
SEC EDGAR source — fetches recent SEC filings (8-K, 10-Q, etc.) via the public EDGAR API.
No API key needed — just requires a User-Agent with contact email.
"""

import time
from typing import List, Optional

from models import RawArticle
from sources.base import NewsSource, clean_text, utc_now_iso

try:
    import requests
except ImportError:
    requests = None

EDGAR_FULL_TEXT_SEARCH = "https://efts.sec.gov/LATEST/search-index"
EDGAR_FILINGS_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar"

# EDGAR full-text search API
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions"

USER_AGENT = "GeoClaw/2.0 (geoclaw-intelligence@example.com)"
RATE_LIMIT_SEC = 1.0

# Major market-moving filing types
IMPORTANT_FORM_TYPES = {"8-K", "10-Q", "10-K", "S-1", "13F-HR", "SC 13D", "4"}

# Track mega-cap companies that move markets
DEFAULT_CIKS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
    "TSLA": "0001318605",
    "JPM": "0000019617",
    "GS": "0000886982",
    "XOM": "0000034088",
    "CVX": "0000093410",
}


class SECSource(NewsSource):
    name = "sec_edgar"

    def __init__(self, ciks: dict = None):
        self._ciks = ciks or DEFAULT_CIKS
        self._last_fetch = 0.0

    def fetch(self, query: Optional[str] = None, max_records: int = 20) -> List[RawArticle]:
        if requests is None:
            return []

        articles = []

        if query:
            articles.extend(self._search_filings(query, limit=max_records))
        else:
            for ticker, cik in self._ciks.items():
                try:
                    items = self._fetch_company_filings(cik, ticker, limit=3)
                    articles.extend(items)
                except Exception:
                    continue
                if len(articles) >= max_records:
                    break

        return self.unique(articles[:max_records])

    def _search_filings(self, query: str, limit: int = 10) -> List[RawArticle]:
        self._rate_limit()

        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": query,
            "dateRange": "custom",
            "startdt": "",
            "enddt": "",
            "forms": "8-K,10-Q,10-K",
        }

        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return self._fallback_search(query, limit)

        articles = []
        for hit in (data.get("hits") or {}).get("hits", [])[:limit]:
            source = hit.get("_source") or {}
            form_type = source.get("form_type", "")
            company = source.get("display_names", ["Unknown"])[0] if source.get("display_names") else "SEC Filing"
            file_date = source.get("file_date", "")
            description = clean_text(source.get("display_description", "") or "")

            articles.append(RawArticle(
                source_name=f"SEC/{form_type}",
                headline=f"{company}: {form_type} filed {file_date}",
                url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={company}&type={form_type}",
                published_at=file_date or utc_now_iso(),
                summary=description or f"{company} filed {form_type} with the SEC",
            ))

        return articles

    def _fallback_search(self, query: str, limit: int = 10) -> List[RawArticle]:
        """Fallback: use EDGAR full-text search RSS."""
        self._rate_limit()

        try:
            url = "https://efts.sec.gov/LATEST/search-index"
            params = {"q": query, "forms": "8-K", "dateRange": "custom"}
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=10)
            if resp.status_code != 200:
                return []
            data = resp.json()
            articles = []
            for hit in (data.get("hits") or {}).get("hits", [])[:limit]:
                source = hit.get("_source") or {}
                articles.append(RawArticle(
                    source_name="SEC/8-K",
                    headline=clean_text(source.get("display_description", query)),
                    url="https://www.sec.gov/cgi-bin/browse-edgar",
                    published_at=source.get("file_date", utc_now_iso()),
                    summary=clean_text(source.get("display_description", "")),
                ))
            return articles
        except Exception:
            return []

    def _fetch_company_filings(self, cik: str, ticker: str, limit: int = 5) -> List[RawArticle]:
        self._rate_limit()

        padded_cik = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{padded_cik}.json"

        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        company_name = data.get("name", ticker)
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        descriptions = recent.get("primaryDocDescription", [])

        articles = []
        for i in range(min(len(forms), limit * 3)):
            form_type = forms[i] if i < len(forms) else ""
            if form_type not in IMPORTANT_FORM_TYPES:
                continue

            filing_date = dates[i] if i < len(dates) else ""
            accession = accessions[i] if i < len(accessions) else ""
            desc = descriptions[i] if i < len(descriptions) else ""
            clean_accession = accession.replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{clean_accession}/{accession}-index.htm"

            articles.append(RawArticle(
                source_name=f"SEC/{form_type}",
                headline=f"{company_name} ({ticker}): {form_type} filed {filing_date}",
                url=filing_url,
                published_at=filing_date or utc_now_iso(),
                summary=clean_text(desc) or f"{company_name} filed {form_type}",
            ))

            if len(articles) >= limit:
                break

        return articles

    def _rate_limit(self):
        elapsed = time.time() - self._last_fetch
        if elapsed < RATE_LIMIT_SEC:
            time.sleep(RATE_LIMIT_SEC - elapsed)
        self._last_fetch = time.time()
