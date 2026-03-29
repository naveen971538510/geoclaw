from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from data import demo_articles
from helpers import (
    filter_articles_by_field,
    search_articles_by_headline,
    get_article_by_id,
    get_summary,
)
from fetcher import fetch_live_articles
import html

app = FastAPI()


def render_search_form(value: str = "") -> str:
    safe_value = html.escape(value)
    return f"""
    <form action="/live-news-search-view" method="get" class="search-box">
        <input
            type="text"
            name="word"
            value="{safe_value}"
            placeholder="Search live news, e.g. air or trump"
        />
        <button type="submit">Search</button>
    </form>
    """


def render_card(article: dict) -> str:
    article_id = html.escape(str(article.get("id", "")))
    headline = html.escape(str(article.get("headline", "No headline")))
    source = html.escape(str(article.get("source", "Unknown source")))
    published_at = html.escape(str(article.get("published_at", "No date")))
    url = html.escape(str(article.get("url", "#")))

    id_line = f"<p><strong>ID:</strong> {article_id}</p>" if article_id else ""
    view_link = f"/live-news/id-view/{article_id}" if article_id else "#"

    return f"""
    <div class="card">
        <h2>{headline}</h2>
        {id_line}
        <p><strong>Source:</strong> {source}</p>
        <p><strong>Published:</strong> {published_at}</p>
        <p>
            <a href="{view_link}">View article page</a>
            &nbsp;|&nbsp;
            <a href="{url}" target="_blank">Open full article</a>
        </p>
    </div>
    """


def render_page(title: str, subtitle: str, body: str) -> str:
    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle)

    return f"""
    <html>
        <head>
            <title>{safe_title}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background: #111;
                    color: #fff;
                    margin: 0;
                    padding: 30px;
                }}
                .container {{
                    max-width: 1000px;
                    margin: auto;
                }}
                .top-links {{
                    margin-bottom: 18px;
                }}
                .top-links a {{
                    color: #6cb4ff;
                    text-decoration: none;
                    margin-right: 16px;
                }}
                .top-links a:hover {{
                    text-decoration: underline;
                }}
                h1 {{
                    margin-bottom: 10px;
                }}
                .sub {{
                    color: #bbb;
                    margin-bottom: 18px;
                }}
                .search-box {{
                    display: flex;
                    gap: 10px;
                    margin-bottom: 30px;
                }}
                .search-box input {{
                    flex: 1;
                    padding: 12px 14px;
                    border-radius: 10px;
                    border: 1px solid #333;
                    background: #1e1e1e;
                    color: #fff;
                    font-size: 16px;
                }}
                .search-box button {{
                    padding: 12px 18px;
                    border: 0;
                    border-radius: 10px;
                    background: #6cb4ff;
                    color: #111;
                    font-weight: bold;
                    cursor: pointer;
                }}
                .card {{
                    background: #1e1e1e;
                    padding: 20px;
                    margin-bottom: 20px;
                    border-radius: 12px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
                }}
                h2 {{
                    margin-top: 0;
                    font-size: 22px;
                }}
                p {{
                    margin: 8px 0;
                }}
                a {{
                    color: #6cb4ff;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
                .note {{
                    color: #ddd;
                    margin-top: 20px;
                }}
                .error {{
                    color: #ff6b6b;
                    font-weight: bold;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="top-links">
                    <a href="/live-news-view">Live News</a>
                    <a href="/live-news">Live JSON</a>
                    <a href="/news">Demo JSON</a>
                    <a href="/news/summary">Demo Summary</a>
                </div>
                <h1>{safe_title}</h1>
                <p class="sub">{safe_subtitle}</p>
                {body}
            </div>
        </body>
    </html>
    """


def get_live_articles_or_error():
    data = fetch_live_articles()
    if "error" in data:
        return data, []
    return data, data.get("articles", [])


def find_live_matches(word: str):
    data, articles = get_live_articles_or_error()
    if "error" in data:
        return data, []

    matches = []
    for article in articles:
        headline = str(article.get("headline", ""))
        if word.lower() in headline.lower():
            matches.append(article)

    return data, matches


@app.get("/")
def home():
    return {"message": "Hello, GeoClaw is running"}


@app.get("/status")
def status():
    return {"status": "ok"}


@app.get("/news")
def news():
    return {
        "title": "GeoClaw News",
        "count": len(demo_articles),
        "articles": demo_articles
    }


@app.get("/news/id/{article_id}")
def news_by_id(article_id: int):
    article = get_article_by_id(article_id)

    if article is None:
        return {"error": "Article not found"}

    return {
        "id": article_id,
        "article": article
    }


@app.get("/news/region/{region_name}")
def news_by_region(region_name: str):
    filtered_articles = filter_articles_by_field("region", region_name)

    return {
        "title": f"GeoClaw News - Region: {region_name}",
        "count": len(filtered_articles),
        "articles": filtered_articles
    }


@app.get("/news/topic/{topic_name}")
def news_by_topic(topic_name: str):
    filtered_articles = filter_articles_by_field("topic", topic_name)

    return {
        "title": f"GeoClaw News - Topic: {topic_name}",
        "count": len(filtered_articles),
        "articles": filtered_articles
    }


@app.get("/news/search/{word}")
def search_news(word: str):
    filtered_articles = search_articles_by_headline(word)

    return {
        "title": f"GeoClaw News - Search: {word}",
        "count": len(filtered_articles),
        "articles": filtered_articles
    }


@app.get("/news/summary")
def news_summary():
    return get_summary()


@app.get("/live-news")
def live_news():
    return fetch_live_articles()


@app.get("/live-news-view", response_class=HTMLResponse)
def live_news_view():
    data, articles = get_live_articles_or_error()

    if "error" in data:
        body = (
            render_search_form()
            + f'<p class="error">{html.escape(str(data.get("error", "Unknown error")))}</p>'
        )
        return render_page("GeoClaw Live News", "Live feed error", body)

    cards = "".join(render_card(article) for article in articles)
    body = render_search_form() + cards
    return render_page("GeoClaw Live News", "Latest live headlines from BBC RSS and Le Monde International", body)


@app.get("/live-news-search/{word}")
def live_news_search(word: str):
    data, matched_articles = find_live_matches(word)

    if "error" in data:
        return data

    return {
        "search_word": word,
        "count": len(matched_articles),
        "articles": matched_articles
    }


@app.get("/live-news-search-view", response_class=HTMLResponse)
def live_news_search_view(word: str = ""):
    word = word.strip()

    if not word:
        body = render_search_form() + '<p class="note">Type a word and search the current live feed.</p>'
        return render_page("GeoClaw Live News Search", "Search live headlines", body)

    data, matched_articles = find_live_matches(word)

    if "error" in data:
        body = (
            render_search_form(word)
            + f'<p class="error">{html.escape(str(data.get("error", "Unknown error")))}</p>'
        )
        return render_page("GeoClaw Live News Search", f"Search word: {word}", body)

    if matched_articles:
        cards = "".join(render_card(article) for article in matched_articles)
    else:
        cards = f'<p class="note">No live articles found for: <strong>{html.escape(word)}</strong></p>'

    body = render_search_form(word) + cards
    return render_page("GeoClaw Live News Search", f"Search word: {word}", body)


@app.get("/live-news-search-view/{word}", response_class=HTMLResponse)
def live_news_search_view_path(word: str):
    return live_news_search_view(word)


@app.get("/live-news/id/{article_id}")
def live_news_by_id(article_id: int):
    data, articles = get_live_articles_or_error()

    if "error" in data:
        return data

    if article_id < 1 or article_id > len(articles):
        return {"error": "Live article not found"}

    article = articles[article_id - 1]

    return {
        "id": article_id,
        "article": article
    }


@app.get("/live-news/id-view/{article_id}", response_class=HTMLResponse)
def live_news_by_id_view(article_id: int):
    data, articles = get_live_articles_or_error()

    if "error" in data:
        body = f'<p class="error">{html.escape(str(data.get("error", "Unknown error")))}</p>'
        return render_page("GeoClaw Live Article", "Live feed error", body)

    if article_id < 1 or article_id > len(articles):
        body = f'<p class="note">No live article found for ID: <strong>{article_id}</strong></p>'
        return render_page("GeoClaw Live Article", "Article lookup", body)

    article = articles[article_id - 1]
    body = render_card(article)
    return render_page("GeoClaw Live Article", f"Showing article ID {article_id}", body)


@app.get("/saved-news")
def saved_news(limit: int = 20):
    from db import get_saved_articles
    articles = get_saved_articles(limit)
    return {
        "count": len(articles),
        "articles": articles
    }


@app.get("/saved-news-view", response_class=HTMLResponse)
def saved_news_view(limit: int = 20):
    from db import get_saved_articles
    articles = get_saved_articles(limit)

    items = ""
    for a in articles:
        items += f"<li>{html.escape(str(a.get('headline', '')))} - {html.escape(str(a.get('source', '')))}</li>"

    return HTMLResponse(f"<html><body style='background:black;color:white;font-family:Arial;padding:20px;'><h1>Saved News</h1><ul>{items}</ul></body></html>")


@app.get("/saved-news-search/{word}")
def saved_news_search(word: str, limit: int = 20):
    from db import search_saved_articles
    articles = search_saved_articles(word, limit)
    return {
        "search_word": word,
        "count": len(articles),
        "articles": articles
    }


@app.get("/saved-news-search-view/{word}", response_class=HTMLResponse)
def saved_news_search_view(word: str, limit: int = 20):
    from db import search_saved_articles
    articles = search_saved_articles(word, limit)

    items = ""
    for a in articles:
        items += f"<li>{html.escape(str(a.get('headline', '')))} - {html.escape(str(a.get('source', '')))}</li>"

    return HTMLResponse(
        f"<html><body style='background:black;color:white;font-family:Arial;padding:20px;'><h1>Saved Search: {html.escape(word)}</h1><ul>{items}</ul></body></html>"
    )


@app.get("/saved-search-home", response_class=HTMLResponse)
def saved_search_home():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:white;font-family:Arial;padding:30px;">
        <h1>Saved News Search</h1>
        <form onsubmit="event.preventDefault(); const q=document.getElementById('q').value.trim(); if(q){ window.location='/saved-news-search-view/' + encodeURIComponent(q); }">
            <input id="q" type="text" placeholder="Search saved news, e.g. iran"
                   style="padding:12px;width:320px;font-size:16px;border-radius:8px;border:1px solid #444;background:#111;color:white;">
            <button type="submit"
                    style="padding:12px 18px;font-size:16px;border:none;border-radius:8px;background:#60a5fa;color:black;font-weight:bold;margin-left:8px;">
                Search
            </button>
        </form>
    </body>
    </html>
    """)


# === GEOCLAW TERMINAL ROUTE v1 ===
@app.get("/terminal", response_class=HTMLResponse)
def geoclaw_terminal():
    from html import escape
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    from db import get_saved_articles
    rows = get_saved_articles()

    def val(row, key, default=""):
        try:
            if isinstance(row, dict):
                return row.get(key, default)
        except Exception:
            pass
        try:
            return row[key]
        except Exception:
            pass
        try:
            if hasattr(row, "keys") and key in row.keys():
                return row[key]
        except Exception:
            pass
        if isinstance(row, (list, tuple)):
            idx_map = {"id": 0, "headline": 1, "source": 2, "url": 3, "published_at": 4}
            idx = idx_map.get(key)
            if idx is not None and len(row) > idx:
                return row[idx]
        try:
            return getattr(row, key)
        except Exception:
            return default

    def rel_time(value):
        s = str(value or "").strip()
        if not s:
            return "time n/a"
        dt = None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
        if dt is None:
            try:
                dt = parsedate_to_datetime(s)
            except Exception:
                pass
        if dt is None:
            return s
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        mins = max(0, int((now - dt.astimezone(timezone.utc)).total_seconds() // 60))
        if mins < 1:
            return "just now"
        if mins < 60:
            return str(mins) + " mins ago"
        hrs = mins // 60
        if hrs < 24:
            return str(hrs) + " hrs ago"
        days = hrs // 24
        return str(days) + " days ago"

    cards = []
    bull = 0
    bear = 0
    neutral = 0
    alerts_total = 0
    sources = set()
    all_assets = set()

    for row in rows:
        headline = str(val(row, "headline", "") or "")
        source = str(val(row, "source", "") or "Unknown")
        url = str(val(row, "url", "#") or "#")
        published_at = str(val(row, "published_at", "") or "")

        h = headline.lower()

        bull_words = [
            "rally", "surge", "jump", "gain", "beat", "growth", "record high",
            "rise", "rises", "rebound", "recover", "stimulus", "rate cut"
        ]
        bear_words = [
            "fall", "drop", "drops", "slump", "crash", "war", "sanctions",
            "miss", "weak", "recession", "selloff", "shutdown", "plunge", "rate hike"
        ]

        bull_score = sum(1 for w in bull_words if w in h)
        bear_score = sum(1 for w in bear_words if w in h)

        if bull_score > bear_score:
            signal = "Bullish"
            bull += 1
        elif bear_score > bull_score:
            signal = "Bearish"
            bear += 1
        else:
            signal = "Neutral"
            neutral += 1

        assets = []
        if any(w in h for w in ["oil", "brent", "wti", "opec", "crude"]):
            assets.append("OIL")
        if any(w in h for w in ["gold", "bullion", "xau"]):
            assets.append("GOLD")
        if any(w in h for w in ["forex", "currency", "currencies", "dollar", "usd", "gbp", "eur", "jpy", "yen", "sterling", "fx"]):
            assets.append("FOREX")
        if any(w in h for w in ["fed", "ecb", "boe", "interest rate", "rates", "bond yield", "yields", "treasury"]):
            assets.append("RATES")
        if any(w in h for w in ["stock", "stocks", "equity", "equities", "shares", "nasdaq", "s&p", "dow", "ftse", "nikkei", "index"]):
            assets.append("STOCKS")
        if not assets:
            assets.append("GENERAL")

        alert_words = [
            "shutdown", "record high", "record low", "rate hike", "rate cut", "war",
            "sanctions", "crash", "selloff", "surge", "plunge", "default",
            "recession", "opec", "tariff", "strike", "inflation", "stimulus"
        ]
        alerts = [w.upper() for w in alert_words if w in h]
        if alerts:
            alerts_total += 1

        sources.add(source)
        for a in assets:
            all_assets.add(a)

        asset_html = "".join('<span class="badge asset">' + escape(a) + '</span>' for a in assets)
        alert_html = "".join('<span class="badge alert">' + escape(a) + '</span>' for a in alerts)

        signal_class = signal.lower()
        asset_text = " ".join(assets)
        search_text = (headline + " " + source + " " + asset_text).lower()

        card = f"""
        <article class="card" data-signal="{escape(signal, quote=True)}" data-source="{escape(source, quote=True)}" data-assets="{escape(asset_text, quote=True)}" data-search="{escape(search_text, quote=True)}">
          <div class="row top">
            <div class="leftline">
              <span class="signal {signal_class}">{escape(signal)}</span>
              <span class="source">{escape(source)}</span>
              {asset_html}
              {alert_html}
            </div>
            <div class="time">{escape(rel_time(published_at))}</div>
          </div>
          <a class="headline" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(headline)}</a>
          <div class="published">{escape(published_at)}</div>
        </article>
        """
        cards.append(card)

    cards_html = "".join(cards) if cards else '<div class="empty">No saved articles yet.</div>'
    source_options = "".join('<option value="' + escape(s, quote=True) + '">' + escape(s) + '</option>' for s in sorted(sources))
    asset_options = "".join('<option value="' + escape(a, quote=True) + '">' + escape(a) + '</option>' for a in sorted(all_assets))

    TEMPLATE = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>GeoClaw Terminal</title>
      <style>
        :root {
          --bg: #0a0c10;
          --panel: #12161d;
          --panel2: #171c24;
          --text: #e7edf5;
          --muted: #93a0b1;
          --line: #232b36;
          --green: #19c37d;
          --red: #ff5f56;
          --yellow: #ffd166;
          --blue: #4ea1ff;
        }
        * { box-sizing: border-box; }
        body {
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }
        .wrap { max-width: 1180px; margin: 0 auto; padding: 20px; }
        .titlebar {
          display: flex; gap: 12px; align-items: center; justify-content: space-between;
          margin-bottom: 14px; flex-wrap: wrap;
        }
        .title { font-size: 22px; font-weight: 800; letter-spacing: .04em; }
        .actions { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn {
          display: inline-flex; align-items: center; justify-content: center;
          padding: 10px 14px; border: 1px solid var(--line); border-radius: 10px;
          background: var(--panel); color: var(--text); text-decoration: none; cursor: pointer;
        }
        .btn.primary { border-color: #1f6feb; background: #11233f; }
        .summary {
          display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px;
          margin: 0 0 14px 0;
        }
        .stat {
          background: linear-gradient(180deg, var(--panel), var(--panel2));
          border: 1px solid var(--line);
          border-radius: 14px;
          padding: 14px;
        }
        .stat .label { color: var(--muted); font-size: 12px; }
        .stat .value { font-size: 24px; font-weight: 800; margin-top: 6px; }
        .filters {
          display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 10px;
          margin-bottom: 14px;
        }
        .input, .select {
          width: 100%; padding: 12px 13px; background: var(--panel);
          color: var(--text); border: 1px solid var(--line); border-radius: 10px;
          outline: none;
        }
        .cards { display: grid; gap: 12px; }
        .card {
          background: linear-gradient(180deg, var(--panel), var(--panel2));
          border: 1px solid var(--line);
          border-radius: 14px;
          padding: 14px;
        }
        .row.top {
          display: flex; align-items: center; justify-content: space-between;
          gap: 10px; margin-bottom: 10px;
        }
        .leftline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .signal {
          padding: 5px 9px; border-radius: 999px; font-size: 12px; font-weight: 800;
          border: 1px solid transparent;
        }
        .signal.bullish { color: var(--green); border-color: rgba(25,195,125,.35); background: rgba(25,195,125,.10); }
        .signal.bearish { color: var(--red); border-color: rgba(255,95,86,.35); background: rgba(255,95,86,.10); }
        .signal.neutral { color: var(--yellow); border-color: rgba(255,209,102,.35); background: rgba(255,209,102,.10); }
        .badge {
          padding: 4px 8px; border-radius: 999px; font-size: 11px; font-weight: 700;
          border: 1px solid var(--line); color: var(--muted);
        }
        .badge.asset { color: var(--blue); }
        .badge.alert { color: var(--red); }
        .source, .time, .published { color: var(--muted); font-size: 12px; }
        .headline {
          color: var(--text); text-decoration: none; font-size: 16px; font-weight: 700; line-height: 1.45;
          display: block; margin-bottom: 8px;
        }
        .headline:hover { text-decoration: underline; }
        .footer { color: var(--muted); font-size: 12px; margin-top: 16px; }
        .empty {
          padding: 24px; border: 1px dashed var(--line); border-radius: 14px; color: var(--muted);
          text-align: center; background: var(--panel);
        }
        @media (max-width: 860px) {
          .summary { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
          .filters { grid-template-columns: 1fr; }
        }
      </style>
    </head>
    <body>
      <div class="wrap">
        <div class="titlebar">
          <div>
            <div class="title">GEOCLAW TRADING TERMINAL</div>
            <div class="footer">Saved market headlines with sentiment, asset tags, alerts, filters, and search</div>
          </div>
          <div class="actions">
            <a class="btn primary" href="/save-live-now">SAVE NOW</a>
            <a class="btn" href="/live-news-view">LIVE NEWS</a>
            <a class="btn" href="/saved-news-view">OLD SAVED PAGE</a>
          </div>
        </div>

        <section class="summary">
          <div class="stat"><div class="label">Bullish</div><div class="value">__BULL__</div></div>
          <div class="stat"><div class="label">Bearish</div><div class="value">__BEAR__</div></div>
          <div class="stat"><div class="label">Neutral</div><div class="value">__NEUTRAL__</div></div>
          <div class="stat"><div class="label">Alerts</div><div class="value">__ALERTS__</div></div>
        </section>

        <section class="filters">
          <input id="q" class="input" placeholder="Search headline, source, asset..." />
          <select id="signal" class="select">
            <option value="">All signals</option>
            <option value="Bullish">Bullish</option>
            <option value="Bearish">Bearish</option>
            <option value="Neutral">Neutral</option>
          </select>
          <select id="source" class="select">
            <option value="">All sources</option>
            __SOURCE_OPTIONS__
          </select>
          <select id="asset" class="select">
            <option value="">All assets</option>
            __ASSET_OPTIONS__
          </select>
        </section>

        <section id="cards" class="cards">__CARDS__</section>
        <div class="footer">Safe route: /terminal</div>
      </div>

      <script>
        const q = document.getElementById('q');
        const signal = document.getElementById('signal');
        const source = document.getElementById('source');
        const asset = document.getElementById('asset');
        const cards = Array.from(document.querySelectorAll('.card'));

        function applyFilters() {
          const qv = (q.value || '').trim().toLowerCase();
          const sv = signal.value;
          const srcv = source.value;
          const av = asset.value;

          for (const card of cards) {
            const text = card.dataset.search || '';
            const cSignal = card.dataset.signal || '';
            const cSource = card.dataset.source || '';
            const cAssets = card.dataset.assets || '';
            const okQ = !qv || text.includes(qv);
            const okS = !sv || cSignal === sv;
            const okSrc = !srcv || cSource === srcv;
            const okA = !av || cAssets.split(' ').includes(av);
            card.style.display = (okQ && okS && okSrc && okA) ? '' : 'none';
          }
        }

        q.addEventListener('input', applyFilters);
        signal.addEventListener('change', applyFilters);
        source.addEventListener('change', applyFilters);
        asset.addEventListener('change', applyFilters);
      </script>
    </body>
    </html>
    """

    page = TEMPLATE
    page = page.replace("__BULL__", str(bull))
    page = page.replace("__BEAR__", str(bear))
    page = page.replace("__NEUTRAL__", str(neutral))
    page = page.replace("__ALERTS__", str(alerts_total))
    page = page.replace("__SOURCE_OPTIONS__", source_options)
    page = page.replace("__ASSET_OPTIONS__", asset_options)
    page = page.replace("__CARDS__", cards_html)

    return HTMLResponse(page)

