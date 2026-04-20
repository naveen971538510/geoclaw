from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List

from config import DB_PATH, ENABLE_SOCIAL_MEDIA


BULLISH_TERMS = {
    "nikkei gains": 12,
    "nikkei rises": 12,
    "stocks rise": 10,
    "shares gain": 10,
    "risk on": 10,
    "risk-on": 10,
    "yen weakens": 12,
    "weak yen": 10,
    "oil falls": 9,
    "oil eases": 9,
    "us-iran talks": 8,
    "ceasefire": 9,
    "boj holds": 8,
    "semiconductor": 7,
    "chip": 6,
    "stimulus": 8,
}

BEARISH_TERMS = {
    "nikkei falls": 12,
    "nikkei drops": 12,
    "stocks fall": 10,
    "shares drop": 10,
    "risk off": 10,
    "risk-off": 10,
    "yen strengthens": 12,
    "strong yen": 10,
    "oil spike": 10,
    "oil surges": 9,
    "tariff": 8,
    "trade war": 9,
    "boj hike": 10,
    "rate hike": 7,
    "recession": 8,
    "war": 7,
    "escalation": 8,
}

JP225_QUERIES = [
    "Nikkei 225 Japan stocks today yen oil",
    "Japan equities today Nikkei 225 futures",
    "Nikkei 225 semiconductor yen BOJ today",
]

SOCIAL_FEEDS = [
    {"name": "Reddit r/worldnews", "url": "https://www.reddit.com/r/worldnews/.rss"},
    {"name": "Reddit r/geopolitics", "url": "https://www.reddit.com/r/geopolitics/.rss"},
    {"name": "Reddit r/economics", "url": "https://www.reddit.com/r/Economics/.rss"},
    {"name": "Reddit r/investing", "url": "https://www.reddit.com/r/investing/.rss"},
]

SOURCE_WEIGHTS = {
    "Google News": 0.72,
    "Reuters": 0.9,
    "Bloomberg": 0.9,
    "Financial Times": 0.86,
    "CNBC": 0.76,
    "MarketWatch": 0.7,
    "Reddit r/investing": 0.52,
    "Reddit r/economics": 0.52,
    "Reddit r/worldnews": 0.48,
    "Reddit r/geopolitics": 0.48,
}

RECENCY_HOURS = 24
RECENCY_DAYS = max(1, int((RECENCY_HOURS + 23) // 24))


class MarketSignalScanner:
    """Independent JP225 web/social signal scanner for dashboard evidence."""

    CACHE_TTL_SECONDS = 180
    _cache: Dict[str, Any] = {"ts": 0.0, "payload": None}

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or DB_PATH)

    def scan(self, force: bool = False, max_items: int = 24) -> Dict[str, Any]:
        now = time.time()
        cached = self._cache.get("payload")
        if cached and not force and now - float(self._cache.get("ts") or 0.0) < self.CACHE_TTL_SECONDS:
            return {**cached, "cached": True}

        started = time.time()
        raw_items: List[Dict[str, Any]] = []
        errors: List[str] = []

        for query in JP225_QUERIES:
            try:
                raw_items.extend(self._fetch_google_news(query, limit=8))
            except Exception as exc:
                errors.append(f"google_news:{exc.__class__.__name__}")

        if ENABLE_SOCIAL_MEDIA:
            try:
                raw_items.extend(self._fetch_social(limit=16))
            except Exception as exc:
                errors.append(f"social:{exc.__class__.__name__}")

        try:
            raw_items.extend(self._fetch_recent_db(limit=16))
        except Exception as exc:
            errors.append(f"db:{exc.__class__.__name__}")

        deduped = self._dedupe(raw_items)
        fresh_items, freshness_stats = self._filter_fresh_items(deduped)
        items = fresh_items[: max(1, int(max_items or 24))]
        scored = [self._score_item(item) for item in items]
        payload = self._summarize(
            scored,
            errors,
            elapsed=time.time() - started,
            freshness_stats=freshness_stats,
        )
        self._save_snapshot(payload)
        self._cache = {"ts": time.time(), "payload": payload}
        return payload

    def _fetch_google_news(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)).date().isoformat()
        search_query = f"{query} after:{since}"
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": search_query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        )
        request = urllib.request.Request(url, headers={"User-Agent": "GeoClaw/market-signal-scanner"})
        with urllib.request.urlopen(request, timeout=8) as response:
            xml_text = response.read()
        items: List[Dict[str, Any]] = []
        for row in self._parse_feed_items(xml_text, default_source="Google News", limit=limit):
            items.append(
                {
                    "title": row["title"],
                    "url": row["url"],
                    "source": row["source"],
                    "channel": "internet",
                    "published_at": row["published_at"],
                    "summary": row["summary"],
                    "query": search_query,
                }
            )
        return items

    def _fetch_social(self, limit: int = 16) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        per_feed_limit = max(2, int(limit / max(1, len(SOCIAL_FEEDS))) + 1)
        for feed in SOCIAL_FEEDS:
            request = urllib.request.Request(feed["url"], headers={"User-Agent": "GeoClaw/social-scan"})
            try:
                with urllib.request.urlopen(request, timeout=6) as response:
                    xml_text = response.read()
            except Exception:
                continue
            for row in self._parse_feed_items(xml_text, default_source=feed["name"], limit=per_feed_limit):
                text = f"{row['title']} {row['summary']}".lower()
                if not any(term in text for term in ("nikkei", "japan", "yen", "boj", "semiconductor", "asia", "stocks", "market")):
                    continue
                out.append(
                    {
                        "title": row["title"],
                        "url": row["url"],
                        "source": feed["name"],
                        "channel": "social",
                        "published_at": row["published_at"],
                        "summary": row["summary"],
                        "query": "social_rss",
                    }
                )
                if len(out) >= limit:
                    return out
        return out[:limit]

    def _parse_feed_items(self, xml_text: bytes | str, default_source: str, limit: int) -> List[Dict[str, str]]:
        """Small RSS/Atom parser that avoids pyexpat dependency issues."""
        text = xml_text.decode("utf-8", errors="ignore") if isinstance(xml_text, bytes) else str(xml_text or "")
        blocks = re.findall(r"<item\b[^>]*>(.*?)</item>", text, flags=re.IGNORECASE | re.DOTALL)
        atom_blocks = re.findall(r"<entry\b[^>]*>(.*?)</entry>", text, flags=re.IGNORECASE | re.DOTALL)
        rows: List[Dict[str, str]] = []
        for block in (blocks + atom_blocks)[:limit]:
            title = self._xml_tag(block, "title")
            url = self._xml_tag(block, "link")
            if not url:
                match = re.search(r"<link\b[^>]*href=[\"']([^\"']+)", block, flags=re.IGNORECASE)
                url = match.group(1).strip() if match else ""
            source = self._xml_tag(block, "source") or default_source
            published = self._xml_tag(block, "pubDate") or self._xml_tag(block, "published") or self._xml_tag(block, "updated")
            summary = self._xml_tag(block, "description") or self._xml_tag(block, "summary") or self._xml_tag(block, "content")
            if title:
                rows.append(
                    {
                        "title": title,
                        "url": url,
                        "source": source,
                        "published_at": published,
                        "summary": summary,
                    }
                )
        return rows

    def _xml_tag(self, block: str, tag: str) -> str:
        match = re.search(rf"<(?:[A-Za-z0-9_]+:)?{re.escape(tag)}\b[^>]*>(.*?)</(?:[A-Za-z0-9_]+:)?{re.escape(tag)}>", block, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        value = match.group(1)
        value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value, flags=re.DOTALL)
        value = re.sub(r"<[^>]+>", " ", value)
        value = html.unescape(value)
        return re.sub(r"\s+", " ", value).strip()

    def _fetch_recent_db(self, limit: int = 16) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT headline, url, source_name, published_at, fetched_at, summary
                FROM ingested_articles
                WHERE fetched_at >= datetime('now', '-24 hours')
                  AND (
                    lower(headline) LIKE '%nikkei%'
                    OR lower(headline) LIKE '%japan%'
                    OR lower(headline) LIKE '%yen%'
                    OR lower(headline) LIKE '%boj%'
                    OR lower(headline) LIKE '%asia%'
                  )
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [
                {
                    "title": row["headline"],
                    "url": row["url"],
                    "source": row["source_name"] or "GeoClaw DB",
                    "channel": "agent_db",
                    "published_at": row["published_at"] or "",
                    "fetched_at": row["fetched_at"] or "",
                    "summary": row["summary"] or "",
                    "query": "recent_agent_articles",
                }
                for row in rows
            ]
        finally:
            conn.close()

    def _dedupe(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        for item in items:
            key = str(item.get("url") or "").strip().lower()
            if not key:
                key = hashlib.sha1(str(item.get("title") or "").lower().encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def _filter_fresh_items(self, items: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Keep only items with a recent timestamp so stale headlines cannot drive live signals."""
        fresh: List[Dict[str, Any]] = []
        stale_filtered = 0
        unknown_date_filtered = 0
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=RECENCY_HOURS)

        for item in items:
            item_dt = self._item_datetime(item)
            if item_dt is None:
                unknown_date_filtered += 1
                continue

            age_hours = max(0.0, (now - item_dt).total_seconds() / 3600.0)
            if item_dt < cutoff:
                stale_filtered += 1
                continue

            enriched = dict(item)
            enriched["published_at_iso"] = item_dt.isoformat()
            enriched["age_hours"] = round(age_hours, 1)
            fresh.append(enriched)

        fresh.sort(key=lambda item: float(item.get("age_hours", 999999) or 999999))
        return fresh, {
            "raw_results_found": len(items),
            "stale_filtered": stale_filtered,
            "unknown_date_filtered": unknown_date_filtered,
        }

    def _item_datetime(self, item: Dict[str, Any]) -> datetime | None:
        for key in ("published_at", "fetched_at"):
            parsed = self._parse_datetime(str(item.get(key) or ""))
            if parsed:
                return parsed
        return None

    def _parse_datetime(self, value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None

        try:
            parsed = parsedate_to_datetime(text)
            if parsed:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
        except Exception:
            pass

        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    def _score_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        text = f"{item.get('title','')} {item.get('summary','')}".lower()
        bull_hits = [term for term in BULLISH_TERMS if term in text]
        bear_hits = [term for term in BEARISH_TERMS if term in text]
        bull = sum(BULLISH_TERMS[term] for term in bull_hits)
        bear = sum(BEARISH_TERMS[term] for term in bear_hits)
        source_weight = self._source_weight(str(item.get("source") or ""))
        score = round((bull - bear) * source_weight, 2)
        direction = "bullish" if score > 2 else ("bearish" if score < -2 else "neutral")
        return {
            **item,
            "signal_score": score,
            "direction": direction,
            "source_weight": source_weight,
            "bullish_hits": bull_hits[:5],
            "bearish_hits": bear_hits[:5],
        }

    def _source_weight(self, source: str) -> float:
        clean = str(source or "").lower()
        for key, value in SOURCE_WEIGHTS.items():
            if key.lower() in clean or clean in key.lower():
                return value
        return 0.62 if source else 0.5

    def _summarize(
        self,
        scored: List[Dict[str, Any]],
        errors: List[str],
        elapsed: float,
        freshness_stats: Dict[str, int],
    ) -> Dict[str, Any]:
        total = round(sum(float(item.get("signal_score", 0.0) or 0.0) for item in scored), 2)
        bullish = sum(1 for item in scored if item.get("direction") == "bullish")
        bearish = sum(1 for item in scored if item.get("direction") == "bearish")
        neutral = max(0, len(scored) - bullish - bearish)
        source_count = len({str(item.get("source") or "") for item in scored if item.get("source")})
        channel_count = len({str(item.get("channel") or "") for item in scored if item.get("channel")})
        predictability = min(95, max(5, int(35 + abs(total) * 1.8 + min(source_count, 6) * 4 + channel_count * 3)))
        if total >= 12:
            bias = "BULLISH"
        elif total <= -12:
            bias = "BEARISH"
        elif bullish > bearish:
            bias = "LEAN_BULLISH"
        elif bearish > bullish:
            bias = "LEAN_BEARISH"
        else:
            bias = "MIXED"
        top = sorted(scored, key=lambda item: abs(float(item.get("signal_score", 0.0) or 0.0)), reverse=True)[:8]
        if not scored and freshness_stats.get("stale_filtered", 0):
            errors = [*errors, "freshness:no_recent_usable_results"]
        summary = self._summary_text(bias, bullish, bearish, neutral, source_count)
        return {
            "status": "ok",
            "symbol": "JP225",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "scan_seconds": round(max(0.0, elapsed), 3),
            "recency_hours": RECENCY_HOURS,
            "recency_days": RECENCY_DAYS,
            "raw_results_found": int(freshness_stats.get("raw_results_found", len(scored)) or 0),
            "stale_filtered": int(freshness_stats.get("stale_filtered", 0) or 0),
            "unknown_date_filtered": int(freshness_stats.get("unknown_date_filtered", 0) or 0),
            "items_scanned": len(scored),
            "source_count": source_count,
            "channel_count": channel_count,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "net_score": total,
            "bias": bias,
            "predictability_score": predictability,
            "summary": summary,
            "top_evidence": top,
            "errors": errors[:5],
            "disclaimer": "Decision support only. This scan is not investment advice and can be wrong.",
        }

    def _summary_text(self, bias: str, bullish: int, bearish: int, neutral: int, sources: int) -> str:
        direction = bias.replace("_", " ").lower()
        return (
            f"Independent web/social scan is {direction}: {bullish} bullish, "
            f"{bearish} bearish, {neutral} neutral signals across {sources} sources."
        )

    def _save_snapshot(self, payload: Dict[str, Any]) -> None:
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_signal_scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    generated_at TEXT,
                    bias TEXT,
                    predictability_score INTEGER,
                    net_score REAL,
                    payload_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO market_signal_scans
                    (symbol, generated_at, bias, predictability_score, net_score, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("symbol", "JP225"),
                    payload.get("generated_at", ""),
                    payload.get("bias", ""),
                    int(payload.get("predictability_score", 0) or 0),
                    float(payload.get("net_score", 0.0) or 0.0),
                    json.dumps(payload, default=str),
                ),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
