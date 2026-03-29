import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple
from urllib.parse import parse_qsl, urlparse, urlunparse


TRUST_WEIGHTS = {
    "reuters": 9,
    "bloomberg": 9,
    "financial times": 9,
    "ft": 8,
    "wsj": 8,
    "bbc": 8,
    "the guardian": 7,
    "guardian": 7,
    "cnbc": 7,
    "marketwatch": 6,
    "yahoo": 5,
    "investing.com": 5,
    "associated press": 8,
    "ap": 6,
    "le monde": 5,
}

LOW_QUALITY_HINTS = [
    "163.com",
    "epochtimes.com.ua",
    "jagonews24",
    "tiflo",
    "n.yam.com",
    "dostor",
]

DROP_QUERY_PREFIXES = (
    "utm_",
    "ga_",
    "fb",
    "gclid",
    "igshid",
    "mc_",
    "mkt_",
    "oref",
    "outbrain",
    "ref",
    "share",
)

HEADLINE_NOISE_PATTERNS = [
    r"\blive updates?\b",
    r"\bnewsletter\b",
    r"\bwatch live\b",
]


def parse_dt(value: str):
    s = str(value or "").strip()
    if not s:
        return None
    for candidate in (s, s.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def ts(value: str) -> float:
    dt = parse_dt(value)
    return dt.timestamp() if dt else 0.0


def source_domain(url: str) -> str:
    try:
        host = urlparse(str(url or "")).netloc.lower()
        return host.replace("www.", "")
    except Exception:
        return ""


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        netloc = parsed.netloc.lower().replace("www.", "")
        path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
        pairs = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            low = key.lower()
            if any(low == prefix or low.startswith(prefix) for prefix in DROP_QUERY_PREFIXES):
                continue
            pairs.append((key, value))
        pairs.sort()
        query = "&".join(f"{k}={v}" for k, v in pairs)
        return urlunparse((parsed.scheme.lower() or "https", netloc, path, "", query, ""))
    except Exception:
        return raw.lower()


def normalize_headline(text: str) -> str:
    s = str(text or "").lower().strip()
    s = re.sub(r"\s+[-|]\s+(reuters|ap|associated press|bloomberg|cnbc|marketwatch)\b.*$", "", s)
    s = re.sub(r'https?://\S+', ' ', s)
    s = re.sub(r'[^a-z0-9\s]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def headline_signature(text: str, max_words: int = 10) -> Tuple[str, ...]:
    words = [w for w in normalize_headline(text).split() if len(w) > 2]
    if not words:
        return tuple()
    stop = {
        "about", "after", "amid", "from", "into", "more", "over", "says",
        "than", "that", "their", "this", "update", "will", "with",
    }
    tokens = [w for w in words if w not in stop]
    return tuple((tokens or words)[:max_words])


def source_text(source_name: str, url: str) -> str:
    return (str(source_name or "") + " " + source_domain(url)).lower()


def trust_score(source_name: str, url: str = "") -> int:
    text = source_text(source_name, url)
    for key, weight in TRUST_WEIGHTS.items():
        if key in text:
            return weight
    return 1


def looks_low_quality(source_name: str, url: str, headline: str = "", summary: str = "") -> bool:
    joined = " ".join(
        [
            source_text(source_name, url),
            str(headline or "").lower(),
            str(summary or "").lower(),
        ]
    )
    if any(hint in joined for hint in LOW_QUALITY_HINTS):
        return True

    domain = source_domain(url)
    if domain and any(ch.isdigit() for ch in domain) and trust_score(source_name, url) <= 1:
        return True
    if domain.count("-") >= 3 and trust_score(source_name, url) <= 1:
        return True

    headline_low = str(headline or "").lower()
    if any(re.search(pattern, headline_low) for pattern in HEADLINE_NOISE_PATTERNS):
        return True
    if headline_low.startswith(("sponsored:", "opinion:")) and trust_score(source_name, url) <= 2:
        return True
    if not headline_low or len(headline_low) < 12:
        return True
    return False


def headline_similarity(left: str, right: str) -> float:
    a = set(headline_signature(left, max_words=12))
    b = set(headline_signature(right, max_words=12))
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    union = len(a | b)
    return overlap / union if union else 0.0


def should_suppress_article(article: Dict) -> Tuple[bool, str]:
    headline = str(article.get("headline", "") or "")
    url = str(article.get("url", "") or "")
    source_name = str(article.get("source_name", "") or "")
    summary = str(article.get("summary", "") or "")

    if not headline or not url:
        return True, "missing headline or url"
    if looks_low_quality(source_name, url, headline=headline, summary=summary):
        return True, "low-quality source or headline"
    return False, ""


def suppress_articles(items: Iterable[Dict]) -> Tuple[List[Dict], List[Dict]]:
    kept: List[Dict] = []
    suppressed: List[Dict] = []
    for item in items:
        drop, reason = should_suppress_article(item)
        if drop:
            suppressed.append(
                {
                    "headline": str(item.get("headline", "") or ""),
                    "source_name": str(item.get("source_name", "") or ""),
                    "url": str(item.get("url", "") or ""),
                    "reason": reason,
                }
            )
            continue
        kept.append(item)
    return kept, suppressed
