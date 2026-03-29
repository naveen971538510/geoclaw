
import feedparser

def fetch_live_articles():
    ...
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

FEEDS = [
    ("BBC RSS", "http://newsrss.bbc.co.uk/rss/newsonline_uk_edition/front_page/rss.xml"),
    ("Le Monde International", "https://www.lemonde.fr/en/international/rss_full.xml"),
]

def parse_date(date_str: str):
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def fetch_one_feed(source_name: str, feed_url: str):
    feed = feedparser.parse(feed_url)

    if getattr(feed, "bozo", 0) and not getattr(feed, "entries", []):
        raise Exception(str(getattr(feed, "bozo_exception", "Feed parse failed")))

    articles = []

    for entry in feed.entries:
        headline = str(getattr(entry, "title", "")).strip()
        url = str(getattr(entry, "link", "")).strip()
        published_at = str(getattr(entry, "published", "")).strip()

        if not headline or not url:
            continue

        articles.append({
            "headline": headline,
            "source": source_name,
            "url": url,
            "published_at": published_at,
            "_sort_dt": parse_date(published_at),
        })

    return articles

def fetch_live_articles(limit: int = 20):
    all_articles = []
    errors = []

    for source_name, feed_url in FEEDS:
        try:
            all_articles.extend(fetch_one_feed(source_name, feed_url))
        except Exception as e:
            errors.append(f"{source_name}: {str(e)}")

    if not all_articles and errors:
        return {
            "error": "All live feeds failed",
            "details": errors
        }

    deduped = []
    seen_urls = set()

    for article in all_articles:
        url = article.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(article)

    deduped.sort(
        key=lambda x: x["_sort_dt"] if x["_sort_dt"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    cleaned_articles = []
    for i, article in enumerate(deduped[:limit], start=1):
        cleaned_articles.append({
            "id": i,
            "headline": article["headline"],
            "source": article["source"],
            "url": article["url"],
            "published_at": article["published_at"],
        })

    result = {
        "count": len(cleaned_articles),
        "articles": cleaned_articles,
    }

    if errors:
        result["feed_errors"] = errors

    return result
