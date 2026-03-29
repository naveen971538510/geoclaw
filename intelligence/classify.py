import re
from datetime import datetime, timezone
from typing import Dict, List

from config import DEFAULT_WATCHLIST
from sources.base import clean_text, content_hash


BULL_WORDS = [
    "rally", "surge", "jump", "gain", "beat", "growth", "record high",
    "rise", "rises", "rebound", "recover", "stimulus", "rate cut",
    "soft landing", "optimism", "boost",
]

BEAR_WORDS = [
    "fall", "drop", "drops", "slump", "crash", "war", "sanctions",
    "miss", "weak", "recession", "selloff", "shutdown", "plunge",
    "rate hike", "fear", "default", "tariff",
]

ALERT_PATTERNS = [
    ("WAR", ["war", "missile", "strike", "conflict"]),
    ("SANCTIONS", ["sanctions"]),
    ("RATE_HIKE", ["rate hike", "hawkish"]),
    ("RATE_CUT", ["rate cut", "dovish"]),
    ("CRASH", ["crash", "selloff", "plunge"]),
    ("DEFAULT", ["default"]),
    ("RECESSION", ["recession"]),
    ("INFLATION", ["inflation"]),
    ("OPEC", ["opec"]),
    ("TARIFF", ["tariff", "tariffs"]),
]

CONTRADICTION_PATTERNS = [
    "denies", "deny", "denied", "walks back", "walked back", "reverses", "reversed",
    "contradicts", "contradicted", "pushes back", "refutes", "refuted", "fails to confirm",
    "no evidence", "not confirmed", "false claim", "u-turn", "backtracks",
]

ASSET_PATTERNS = [
    ("OIL", ["oil", "brent", "wti", "opec", "crude"]),
    ("GOLD", ["gold", "bullion", "xau"]),
    ("FOREX", ["forex", "currency", "currencies", "dollar", "usd", "gbp", "eur", "jpy", "yen", "sterling", "fx"]),
    ("RATES", ["fed", "ecb", "boe", "interest rate", "rates", "bond yield", "yields", "treasury"]),
    ("STOCKS", ["stock", "stocks", "equity", "equities", "shares", "nasdaq", "s&p", "dow", "ftse", "nikkei", "index"]),
]

MACRO_PATTERNS = [
    ("CENTRAL_BANKS", ["fed", "ecb", "boe", "central bank", "rates"]),
    ("INFLATION", ["inflation", "cpi", "ppi"]),
    ("GROWTH", ["gdp", "growth", "manufacturing", "pmi"]),
    ("TRADE", ["tariff", "trade", "exports", "imports"]),
    ("GEOPOLITICS", ["war", "sanctions", "conflict", "missile", "strike"]),
]

OPPOSITE_WORD_PAIRS = [
    ("rising", "falling"),
    ("buy", "sell"),
    ("upgrade", "downgrade"),
    ("bullish", "bearish"),
    ("rally", "crash"),
    ("growth", "contraction"),
    ("recovery", "recession"),
    ("surplus", "deficit"),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_get(obj, key, default=""):
    try:
        return obj.get(key, default)
    except Exception:
        return getattr(obj, key, default)


def normalize_article(raw) -> Dict:
    source_name = clean_text(_safe_get(raw, "source_name", "Unknown"))
    headline = clean_text(_safe_get(raw, "headline", ""))
    url = clean_text(_safe_get(raw, "url", ""))
    published_at = clean_text(_safe_get(raw, "published_at", ""))
    summary = clean_text(_safe_get(raw, "summary", ""))
    external_id = clean_text(_safe_get(raw, "external_id", ""))
    language = clean_text(_safe_get(raw, "language", ""))
    country = clean_text(_safe_get(raw, "country", ""))

    return {
        "source_name": source_name or "Unknown",
        "headline": headline,
        "url": url,
        "published_at": published_at,
        "summary": summary,
        "external_id": external_id,
        "language": language,
        "country": country,
        "fetched_at": utc_now_iso(),
        "content_hash": content_hash(headline, url),
        "is_duplicate": 0,
    }


def _hits(text: str, words: List[str]) -> List[str]:
    low = text.lower()
    return [w for w in words if w in low]


def _ensure_alert_columns(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(alert_events)")
    columns = {row[1] for row in cur.fetchall()}
    if "is_starred" not in columns:
        cur.execute("ALTER TABLE alert_events ADD COLUMN is_starred INTEGER DEFAULT 0")
    if "status" not in columns:
        cur.execute("ALTER TABLE alert_events ADD COLUMN status TEXT DEFAULT 'open'")
    conn.commit()


def _contains_term(text: str, term: str) -> bool:
    return bool(re.search(r"\b" + re.escape(str(term or "").lower()) + r"\b", str(text or "").lower()))


def check_contradiction(new_article_text: str, db_conn) -> bool:
    if db_conn is None:
        return False

    _ensure_alert_columns(db_conn)
    cur = db_conn.cursor()
    cutoff = datetime.now(timezone.utc).timestamp() - (4 * 60 * 60)
    cur.execute(
        """
        SELECT
            ae.id,
            ae.reason,
            ia.headline,
            ia.summary
        FROM alert_events ae
        JOIN ingested_articles ia
          ON ia.id = ae.article_id
        WHERE COALESCE(ae.is_starred, 0) = 1
          AND strftime('%s', COALESCE(ae.created_at, '')) >= ?
        ORDER BY ae.id DESC
        """,
        (int(cutoff),),
    )
    rows = cur.fetchall()
    joined_new = str(new_article_text or "").lower()
    matched_ids = set()

    for row in rows:
        existing_text = " ".join(
            [
                str(row["headline"] or ""),
                str(row["summary"] or ""),
                str(row["reason"] or ""),
            ]
        ).lower()
        for left, right in OPPOSITE_WORD_PAIRS:
            if (_contains_term(joined_new, left) and _contains_term(existing_text, right)) or (
                _contains_term(joined_new, right) and _contains_term(existing_text, left)
            ):
                matched_ids.add(int(row["id"]))
                break

    if matched_ids:
        cur.executemany(
            "UPDATE alert_events SET status = 'CRITICAL_CONTRADICTION' WHERE id = ?",
            [(item_id,) for item_id in sorted(matched_ids)],
        )
        db_conn.commit()
        return True
    return False


def classify_article(article: Dict, watchlist: List[str] = None) -> Dict:
    watchlist = watchlist or DEFAULT_WATCHLIST
    headline = str(article.get("headline", "") or "")
    source_name = str(article.get("source_name", "") or "")
    summary = str(article.get("summary", "") or "")
    joined = (headline + " " + summary + " " + source_name).lower()

    bull_score = len(_hits(joined, BULL_WORDS))
    bear_score = len(_hits(joined, BEAR_WORDS))

    if bull_score > bear_score:
        signal = "Bullish"
    elif bear_score > bull_score:
        signal = "Bearish"
    else:
        signal = "Neutral"

    sentiment_score = float(bull_score - bear_score)

    asset_tags = []
    for tag, patterns in ASSET_PATTERNS:
        if any(p in joined for p in patterns):
            asset_tags.append(tag)
    if not asset_tags:
        asset_tags.append("GENERAL")

    macro_tags = []
    for tag, patterns in MACRO_PATTERNS:
        if any(p in joined for p in patterns):
            macro_tags.append(tag)

    alert_tags = []
    for tag, patterns in ALERT_PATTERNS:
        if any(p in joined for p in patterns):
            alert_tags.append(tag)
    contradiction_hits = _hits(joined, CONTRADICTION_PATTERNS)
    if contradiction_hits:
        alert_tags.append("CONTRADICTION")

    watchlist_hits = []
    for word in watchlist:
        w = str(word or "").strip().lower()
        if w and w in joined:
            watchlist_hits.append(w)

    thesis = ""
    if signal == "Bullish":
        thesis = "Positive tone detected. Market-sensitive upside narrative may matter if price action confirms."
    elif signal == "Bearish":
        thesis = "Negative tone detected. Risk-off or downside implications may matter if follow-up headlines confirm."
    else:
        thesis = "Mixed or neutral headline. Monitor context, asset exposure, and follow-up developments."

    bull_case = "Bull case strengthens if follow-up headlines and prices move in the same direction."
    bear_case = "Bear case strengthens if negative follow-up headlines widen and cross-asset weakness appears."
    what_to_watch = "Watch next headlines, rate language, commodity moves, FX reaction, and source confirmation."

    if contradiction_hits:
        thesis += " A contradiction marker is present, so treat this as a thesis-check instead of a clean confirmation."
        what_to_watch = "Watch whether follow-up reporting confirms, weakens, or reverses the current claim before acting."

    return {
        "signal": signal,
        "sentiment_score": sentiment_score,
        "asset_tags": asset_tags,
        "macro_tags": macro_tags,
        "watchlist_hits": watchlist_hits,
        "alert_tags": alert_tags,
        "contradiction_hits": contradiction_hits,
        "thesis": thesis,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "what_to_watch": what_to_watch,
    }
