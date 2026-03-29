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
from fastapi.responses import JSONResponse

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










# === GEOCLAW TERMINAL ROUTE v5 ===
@app.get("/terminal", response_class=HTMLResponse)
def geoclaw_terminal():
    return HTMLResponse("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GeoClaw Terminal</title>
  <style>
    :root{
      --bg:#07090d;
      --panel:#10151c;
      --panel2:#161d27;
      --line:#232c39;
      --text:#e8edf5;
      --muted:#90a0b7;
      --green:#18c37b;
      --red:#ff5f56;
      --yellow:#ffd166;
      --blue:#56a8ff;
      --cyan:#4de2ff;
      --purple:#a78bfa;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      background:
        radial-gradient(circle at top right, rgba(86,168,255,.08), transparent 24%),
        radial-gradient(circle at top left, rgba(77,226,255,.05), transparent 24%),
        var(--bg);
      color:var(--text);
      font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
    }
    .wrap{max-width:1420px;margin:0 auto;padding:18px}
    .topbar{
      display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap;
      margin-bottom:14px
    }
    .title{font-size:28px;font-weight:900;letter-spacing:.06em}
    .sub{font-size:12px;color:var(--muted);margin-top:6px}
    .btns{display:flex;gap:10px;flex-wrap:wrap}
    .btn,.pill{
      border:1px solid var(--line);
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      color:var(--text);
      border-radius:12px;
      padding:10px 14px;
      text-decoration:none;
      cursor:pointer;
      font-weight:800;
    }
    .btn.primary{
      background:linear-gradient(180deg,#102542,#153157);
      border-color:#245a96;
      box-shadow:0 0 0 1px rgba(86,168,255,.15) inset;
    }
    .btn.warn{
      background:linear-gradient(180deg,#2e1a10,#4b2a18);
      border-color:#8b4c2d;
      box-shadow:0 0 0 1px rgba(255,209,102,.12) inset;
    }
    .hero{
      display:grid;
      grid-template-columns:repeat(6,minmax(120px,1fr));
      gap:12px;
      margin-bottom:14px
    }
    .stat{
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      border:1px solid var(--line);
      border-radius:16px;
      padding:14px;
      min-height:88px;
    }
    .stat .k{font-size:11px;color:var(--muted);letter-spacing:.06em}
    .stat .v{font-size:26px;font-weight:900;margin-top:8px}
    .marketstrip{
      display:grid;
      grid-template-columns:repeat(6,minmax(120px,1fr));
      gap:12px;
      margin-bottom:14px
    }
    .ticker{
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      border:1px solid var(--line);
      border-radius:14px;
      padding:12px;
    }
    .ticker .n{font-size:11px;color:var(--muted)}
    .ticker .p{font-size:18px;font-weight:900;margin-top:4px}
    .ticker .c.up{color:var(--green)}
    .ticker .c.down{color:var(--red)}
    .ticker .c.flat{color:var(--yellow)}
    .controls{
      display:grid;
      grid-template-columns:2fr 1fr 1fr 1fr auto auto;
      gap:10px;
      margin-bottom:14px
    }
    .input,.select{
      width:100%;
      padding:12px 13px;
      border:1px solid var(--line);
      border-radius:12px;
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      color:var(--text);
      outline:none;
    }
    .toggle{
      display:flex;align-items:center;gap:8px;
      border:1px solid var(--line);
      border-radius:12px;
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      color:var(--muted);
      padding:12px 13px;
      white-space:nowrap;
    }
    .layout{
      display:grid;
      grid-template-columns:minmax(0,1fr) 390px;
      gap:14px
    }
    .feed{display:grid;gap:12px}
    .card,.panel{
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      border:1px solid var(--line);
      border-radius:16px;
      padding:14px;
      box-shadow:0 10px 30px rgba(0,0,0,.18);
    }
    .card.bullish{border-left:4px solid var(--green)}
    .card.bearish{border-left:4px solid var(--red)}
    .card.neutral{border-left:4px solid var(--yellow)}
    .row{
      display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap
    }
    .leftline,.rightline{
      display:flex;align-items:center;gap:8px;flex-wrap:wrap
    }
    .signal{
      padding:5px 9px;border-radius:999px;font-size:11px;font-weight:900;border:1px solid transparent
    }
    .signal.bullish{color:var(--green);border-color:rgba(24,195,123,.35);background:rgba(24,195,123,.10)}
    .signal.bearish{color:var(--red);border-color:rgba(255,95,86,.35);background:rgba(255,95,86,.10)}
    .signal.neutral{color:var(--yellow);border-color:rgba(255,209,102,.35);background:rgba(255,209,102,.10)}
    .badge{
      padding:4px 8px;border-radius:999px;font-size:11px;font-weight:800;
      border:1px solid var(--line);color:var(--muted)
    }
    .badge.asset{color:var(--blue)}
    .badge.alert{color:var(--red)}
    .badge.watch{color:var(--purple)}
    .score{
      padding:6px 9px;border-radius:10px;
      border:1px solid var(--line);
      background:rgba(255,255,255,.02);
      font-size:12px;font-weight:900;color:var(--cyan)
    }
    .headline{
      display:block;
      color:var(--text);
      text-decoration:none;
      font-size:18px;
      font-weight:900;
      line-height:1.42;
      margin:12px 0 10px 0;
    }
    .headline:hover{text-decoration:underline}
    .summary,.meta,.small,.footer,.time,.source{
      color:var(--muted);
      font-size:12px
    }
    .thesis{
      margin-top:10px;
      padding:12px;
      border:1px solid var(--line);
      border-radius:12px;
      background:rgba(255,255,255,.02);
    }
    .cases{
      display:grid;
      grid-template-columns:1fr 1fr;
      gap:10px;
      margin-top:10px;
    }
    .case{
      padding:10px;
      border:1px solid var(--line);
      border-radius:12px;
      background:rgba(255,255,255,.02);
    }
    .actions{
      display:flex;gap:10px;flex-wrap:wrap;margin-top:12px
    }
    .linkbtn{
      display:inline-flex;align-items:center;gap:6px;
      text-decoration:none;color:var(--text);
      border:1px solid var(--line);
      padding:8px 10px;border-radius:10px;
      background:rgba(255,255,255,.02)
    }
    .panel h3{
      margin:0 0 12px 0;
      font-size:12px;
      color:var(--muted);
      letter-spacing:.08em
    }
    .mini{
      padding:10px;
      border:1px solid var(--line);
      border-radius:12px;
      background:rgba(255,255,255,.02);
      margin-bottom:10px
    }
    .mini a{color:var(--text);text-decoration:none;font-size:13px;font-weight:800;line-height:1.35}
    .mini a:hover{text-decoration:underline}
    .barwrap{
      height:8px;border-radius:999px;background:rgba(255,255,255,.05);overflow:hidden;margin-top:8px
    }
    .barfill{
      height:100%;
      background:linear-gradient(90deg,var(--cyan),var(--blue));
      border-radius:999px
    }
    .sticky{position:sticky;top:12px}
    .empty{
      padding:28px;
      border:1px dashed var(--line);
      border-radius:16px;
      color:var(--muted);
      text-align:center;
      background:linear-gradient(180deg,var(--panel),var(--panel2));
    }
    .status-good{color:var(--green)}
    .status-warn{color:var(--yellow)}
    .status-bad{color:var(--red)}
    @media (max-width: 1100px){
      .layout{grid-template-columns:1fr}
      .hero{grid-template-columns:repeat(3,minmax(120px,1fr))}
      .marketstrip{grid-template-columns:repeat(3,minmax(120px,1fr))}
      .controls{grid-template-columns:1fr 1fr}
      .cases{grid-template-columns:1fr}
    }
    @media (max-width: 760px){
      .hero{grid-template-columns:repeat(2,minmax(120px,1fr))}
      .marketstrip{grid-template-columns:repeat(2,minmax(120px,1fr))}
      .controls{grid-template-columns:1fr}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <div class="title">GEOCLAW TERMINAL</div>
        <div class="sub">Read-only intelligence dashboard powered by /terminal-data, /agent-status, and /scheduler-status</div>
      </div>
      <div class="btns">
        <span class="pill">MODE: INTELLIGENCE</span>
        <button class="btn primary" id="refreshBtn">REFRESH ALL</button>
        <button class="btn warn" id="runAgentBtn">RUN AGENT NOW</button>
        <a class="btn" href="/terminal-data" target="_blank" rel="noopener noreferrer">RAW JSON</a>
        <a class="btn" href="/agent-status" target="_blank" rel="noopener noreferrer">AGENT STATUS</a>
        <a class="btn" href="/scheduler-status" target="_blank" rel="noopener noreferrer">SCHEDULER</a>
      </div>
    </div>

    <section class="hero">
      <div class="stat"><div class="k">ARTICLES</div><div class="v" id="sArticles">0</div></div>
      <div class="stat"><div class="k">BULLISH</div><div class="v" id="sBull">0</div></div>
      <div class="stat"><div class="k">BEARISH</div><div class="v" id="sBear">0</div></div>
      <div class="stat"><div class="k">NEUTRAL</div><div class="v" id="sNeutral">0</div></div>
      <div class="stat"><div class="k">ALERTS</div><div class="v" id="sAlerts">0</div></div>
      <div class="stat"><div class="k">WATCHLIST HITS</div><div class="v" id="sWatch">0</div></div>
    </section>

    <section id="marketstrip" class="marketstrip"></section>

    <section class="controls">
      <input id="q" class="input" placeholder="Search headline, source, thesis, asset..." />
      <select id="signal" class="select">
        <option value="">All signals</option>
        <option value="Bullish">Bullish</option>
        <option value="Bearish">Bearish</option>
        <option value="Neutral">Neutral</option>
      </select>
      <select id="asset" class="select">
        <option value="">All assets</option>
      </select>
      <select id="source" class="select">
        <option value="">All sources</option>
      </select>
      <select id="sort" class="select">
        <option value="impact">Sort: impact</option>
        <option value="newest">Sort: newest</option>
        <option value="source">Sort: source</option>
      </select>
      <label class="toggle"><input type="checkbox" id="alertsOnly"> alerts only</label>
    </section>

    <section class="layout">
      <div class="feed" id="feed">
        <div class="empty">Loading terminal…</div>
      </div>

      <div class="sticky">
        <div class="panel">
          <h3>AGENT CONTROL</h3>
          <div class="small" id="controlBox">Ready.</div>
        </div>

        <div class="panel" style="margin-top:12px">
          <h3>SCHEDULER STATUS</h3>
          <div id="schedulerBox" class="small">Loading…</div>
        </div>

        <div class="panel" style="margin-top:12px">
          <h3>RECENT AGENT RUNS</h3>
          <div id="runsBox"></div>
        </div>

        <div class="panel" style="margin-top:12px">
          <h3>TOP ALERTS</h3>
          <div id="alertList"></div>
        </div>

        <div class="panel" style="margin-top:12px">
          <h3>SOURCE DISTRIBUTION</h3>
          <div id="sourceList"></div>
        </div>

        <div class="panel" style="margin-top:12px">
          <h3>ASSET HEAT</h3>
          <div id="assetHeat"></div>
        </div>

        <div class="panel" style="margin-top:12px">
          <h3>STATUS</h3>
          <div class="small" id="statusBox">Waiting for first fetch…</div>
        </div>
      </div>
    </section>

    <div class="footer">Current limitation: market prices stay empty until ALPHAVANTAGE_KEY is set.</div>
  </div>

<script>
const state = {
  payload: null,
  agentStatus: null,
  schedulerStatus: null,
  cards: []
};

function esc(v){
  return String(v ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function parseMs(v){
  const t = Date.parse(String(v || ''));
  return Number.isNaN(t) ? 0 : t;
}

function relTime(v){
  const ms = parseMs(v);
  if (!ms) return String(v || 'time n/a');
  const mins = Math.max(0, Math.floor((Date.now() - ms) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + ' mins ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + ' hrs ago';
  return Math.floor(hrs / 24) + ' days ago';
}

function setOptions(id, values, firstText){
  const el = document.getElementById(id);
  const keep = el.value;
  el.innerHTML = '<option value="">' + firstText + '</option>' +
    values.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
  if (values.includes(keep)) el.value = keep;
}

function currentCards(){
  const q = document.getElementById('q').value.trim().toLowerCase();
  const signal = document.getElementById('signal').value;
  const asset = document.getElementById('asset').value;
  const source = document.getElementById('source').value;
  const sort = document.getElementById('sort').value;
  const alertsOnly = document.getElementById('alertsOnly').checked;

  let cards = state.cards.filter(x => {
    const search = (
      (x.headline || '') + ' ' +
      (x.source || '') + ' ' +
      (x.summary || '') + ' ' +
      (x.thesis || '') + ' ' +
      (x.asset_tags || []).join(' ') + ' ' +
      (x.alert_tags || []).join(' ') + ' ' +
      (x.watchlist_hits || []).join(' ')
    ).toLowerCase();

    if (q && !search.includes(q)) return false;
    if (signal && x.signal !== signal) return false;
    if (asset && !(x.asset_tags || []).includes(asset)) return false;
    if (source && x.source !== source) return false;
    if (alertsOnly && !((x.alert_tags || []).length)) return false;
    return true;
  });

  if (sort === 'impact'){
    cards.sort((a,b) => (b.impact_score || 0) - (a.impact_score || 0) || parseMs(b.published_at) - parseMs(a.published_at));
  } else if (sort === 'newest'){
    cards.sort((a,b) => parseMs(b.published_at) - parseMs(a.published_at) || (b.impact_score || 0) - (a.impact_score || 0));
  } else if (sort === 'source'){
    cards.sort((a,b) => String(a.source || '').localeCompare(String(b.source || '')) || (b.impact_score || 0) - (a.impact_score || 0));
  }
  return cards;
}

function renderMarket(){
  const box = document.getElementById('marketstrip');
  const items = ((state.payload || {}).market_snapshot || []);
  if (!items.length){
    box.innerHTML = '<div class="ticker"><div class="n">MARKET SNAPSHOT</div><div class="p">No data</div><div class="small">Set ALPHAVANTAGE_KEY later</div></div>';
    return;
  }
  box.innerHTML = items.map(x => {
    let cls = 'flat';
    const pct = x.change_pct;
    if (pct > 0) cls = 'up';
    if (pct < 0) cls = 'down';
    return `
      <div class="ticker">
        <div class="n">${esc(x.label || x.symbol)}</div>
        <div class="p">${esc(x.price == null ? 'n/a' : String(x.price))}</div>
        <div class="c ${cls}">${esc(x.change_pct == null ? 'n/a' : String(x.change_pct) + '%')}</div>
      </div>
    `;
  }).join('');
}

function renderAgentPanels(){
  const controlBox = document.getElementById('controlBox');
  const schedulerBox = document.getElementById('schedulerBox');
  const runsBox = document.getElementById('runsBox');

  const agent = state.agentStatus || {};
  const scheduler = ((state.schedulerStatus || {}).scheduler || {});
  const gdelt = agent.gdelt_state || {};

  controlBox.innerHTML =
    '<div>Runs visible: <strong>' + esc(String((agent.runs || []).length)) + '</strong></div>' +
    '<div style="margin-top:6px">Top alerts count: <strong>' + esc(String(agent.top_alerts_count || 0)) + '</strong></div>' +
    '<div style="margin-top:6px">Market count: <strong>' + esc(String(agent.market_count || 0)) + '</strong></div>' +
    '<div style="margin-top:6px">GDELT cooldown: <strong>' + esc(gdelt.cooldown_until ? 'ACTIVE' : 'idle') + '</strong></div>';

  const jobs = scheduler.jobs || [];
  if (!jobs.length){
    schedulerBox.innerHTML = '<div class="status-warn">No scheduler jobs visible.</div>';
  } else {
    schedulerBox.innerHTML =
      '<div class="' + (scheduler.running ? 'status-good' : 'status-bad') + '">Running: ' + esc(String(!!scheduler.running)) + '</div>' +
      '<div style="margin-top:8px">Jobs: ' + esc(String(scheduler.job_count || 0)) + '</div>' +
      jobs.map(j => (
        '<div class="mini" style="margin-top:8px">' +
        '<div><strong>' + esc(j.id || '') + '</strong></div>' +
        '<div class="small" style="margin-top:6px">Next: ' + esc(j.next_run_time || 'n/a') + '</div>' +
        '<div class="small" style="margin-top:4px">Trigger: ' + esc(j.trigger || 'n/a') + '</div>' +
        '</div>'
      )).join('');
  }

  const runs = agent.runs || [];
  if (!runs.length){
    runsBox.innerHTML = '<div class="small">No recent runs.</div>';
  } else {
    runsBox.innerHTML = runs.slice(0, 8).map(r => {
      let cls = 'status-good';
      if (r.status === 'partial') cls = 'status-warn';
      if (r.status === 'failed' || r.status === 'running') cls = 'status-bad';
      return `
        <div class="mini">
          <div class="row">
            <div><strong>${esc(r.run_type || 'run')}</strong></div>
            <div class="${cls}">${esc(r.status || 'unknown')}</div>
          </div>
          <div class="small" style="margin-top:8px">Fetched: ${esc(String(r.items_fetched || 0))} · Kept: ${esc(String(r.items_kept || 0))} · Alerts: ${esc(String(r.alerts_created || 0))}</div>
          <div class="small" style="margin-top:6px">Started: ${esc(r.started_at || 'n/a')}</div>
          <div class="small" style="margin-top:4px">Finished: ${esc(r.finished_at || 'n/a')}</div>
          ${r.error_text ? '<div class="small status-warn" style="margin-top:6px">Error: ' + esc(r.error_text) + '</div>' : ''}
        </div>
      `;
    }).join('');
  }
}

function renderSidebars(){
  const alerts = ((state.payload || {}).top_alerts || []);
  document.getElementById('alertList').innerHTML = alerts.length
    ? alerts.map(x => `<div class="mini"><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.headline)}</a><div class="small" style="margin-top:8px">${esc((x.priority || '').toUpperCase())} · ${esc(x.reason || '')}</div></div>`).join('')
    : '<div class="small">No stored alerts yet.</div>';

  const sources = ((state.payload || {}).source_distribution || []);
  const maxS = sources.length ? Math.max(...sources.map(x => x.count || 0)) : 1;
  document.getElementById('sourceList').innerHTML = sources.length
    ? sources.map(x => `<div class="mini"><div class="row"><div class="small">${esc(x.source)}</div><div>${esc(String(x.count))}</div></div><div class="barwrap"><div class="barfill" style="width:${((x.count || 0) / maxS) * 100}%"></div></div></div>`).join('')
    : '<div class="small">No source data.</div>';

  const assets = ((state.payload || {}).asset_heat || []);
  const maxA = assets.length ? Math.max(...assets.map(x => x.count || 0)) : 1;
  document.getElementById('assetHeat').innerHTML = assets.length
    ? assets.map(x => `<div class="mini"><div class="row"><div>${esc(x.asset)}</div><div class="small">${esc(String(x.count))}</div></div><div class="barwrap"><div class="barfill" style="width:${((x.count || 0) / maxA) * 100}%"></div></div></div>`).join('')
    : '<div class="small">No asset data.</div>';

  renderAgentPanels();
}

function renderCards(){
  const assetsAll = [...new Set(state.cards.flatMap(x => x.asset_tags || []))].sort();
  const sourcesAll = [...new Set(state.cards.map(x => x.source || 'Unknown'))].sort();
  setOptions('asset', assetsAll, 'All assets');
  setOptions('source', sourcesAll, 'All sources');

  const cards = currentCards();
  const feed = document.getElementById('feed');

  if (!cards.length){
    feed.innerHTML = '<div class="empty">No cards match the current filters.</div>';
  } else {
    feed.innerHTML = cards.map(x => {
      const sig = String(x.signal || 'Neutral').toLowerCase();
      const assets = (x.asset_tags || []).map(a => '<span class="badge asset">' + esc(a) + '</span>').join('');
      const alerts = (x.alert_tags || []).map(a => '<span class="badge alert">' + esc(a) + '</span>').join('');
      const watch = (x.watchlist_hits || []).map(a => '<span class="badge watch">' + esc(String(a).toUpperCase()) + '</span>').join('');
      return `
        <article class="card ${sig}">
          <div class="row">
            <div class="leftline">
              <span class="signal ${sig}">${esc(x.signal || 'Neutral')}</span>
              <span class="source">${esc(x.source || 'Unknown')}</span>
              ${assets}
              ${alerts}
              ${watch}
            </div>
            <div class="rightline">
              <span class="score">IMPACT ${esc(String(x.impact_score || 0))}</span>
              <span class="time">${esc(relTime(x.published_at || ''))}</span>
            </div>
          </div>

          <a class="headline" href="${esc(x.url || '#')}" target="_blank" rel="noopener noreferrer">${esc(x.headline || '')}</a>
          <div class="summary">${esc(x.summary || '')}</div>

          <div class="thesis">
            <div><strong>Thesis:</strong> ${esc(x.thesis || 'No thesis yet')}</div>
            <div style="margin-top:8px"><strong>What to watch:</strong> ${esc(x.what_to_watch || 'n/a')}</div>
          </div>

          <div class="cases">
            <div class="case"><strong>Bull case</strong><div class="small" style="margin-top:6px">${esc(x.bull_case || 'n/a')}</div></div>
            <div class="case"><strong>Bear case</strong><div class="small" style="margin-top:6px">${esc(x.bear_case || 'n/a')}</div></div>
          </div>

          <div class="actions">
            <a class="linkbtn" href="${esc(x.url || '#')}" target="_blank" rel="noopener noreferrer">OPEN ARTICLE</a>
            <a class="linkbtn" href="/terminal-data" target="_blank" rel="noopener noreferrer">OPEN JSON</a>
          </div>

          <div class="meta" style="margin-top:10px">Published: ${esc(x.published_at || 'n/a')} · Confidence: ${esc(String(x.confidence || 0))}</div>
        </article>
      `;
    }).join('');
  }

  renderSidebars();
}

function renderStats(){
  const s = ((state.payload || {}).stats || {});
  document.getElementById('sArticles').textContent = String(s.articles || 0);
  document.getElementById('sBull').textContent = String(s.bullish || 0);
  document.getElementById('sBear').textContent = String(s.bearish || 0);
  document.getElementById('sNeutral').textContent = String(s.neutral || 0);
  document.getElementById('sAlerts').textContent = String(s.alerts || 0);
  document.getElementById('sWatch').textContent = String(s.watchlist_hits || 0);
}

async function fetchJson(url){
  const res = await fetch(url, {cache:'no-store'});
  if (!res.ok) throw new Error(url + ' HTTP ' + res.status);
  const data = await res.json();
  if (data.status === 'error') throw new Error(url + ' ' + (data.error || 'error'));
  return data;
}

async function reloadAll(){
  try{
    document.getElementById('statusBox').textContent = 'Loading intelligence endpoints ...';

    const [terminalData, agentStatus, schedulerStatus] = await Promise.all([
      fetchJson('/terminal-data'),
      fetchJson('/agent-status'),
      fetchJson('/scheduler-status'),
    ]);

    state.payload = terminalData;
    state.cards = terminalData.cards || [];
    state.agentStatus = agentStatus;
    state.schedulerStatus = schedulerStatus;

    renderStats();
    renderMarket();
    renderCards();

    document.getElementById('statusBox').textContent =
      'Updated: ' + (terminalData.updated_at || 'n/a') +
      ' | Cards: ' + String((terminalData.cards || []).length) +
      ' | Alerts: ' + String(((terminalData.top_alerts || []).length)) +
      ' | Scheduler jobs: ' + String((((schedulerStatus || {}).scheduler || {}).job_count || 0));
  }catch(err){
    document.getElementById('feed').innerHTML = '<div class="empty">Load failed: ' + esc(err.message) + '</div>';
    document.getElementById('statusBox').textContent = 'Error: ' + err.message;
  }
}

async function runAgentNow(){
  const box = document.getElementById('controlBox');
  try{
    box.textContent = 'Running agent cycle now...';
    const data = await fetchJson('/agent-run-now');
    const result = data.result || {};
    box.innerHTML =
      '<div class="status-good">Agent cycle finished.</div>' +
      '<div style="margin-top:6px">Topic runs: <strong>' + esc(String(result.topic_runs || 0)) + '</strong></div>' +
      '<div style="margin-top:6px">Fetched: <strong>' + esc(String(result.items_fetched || 0)) + '</strong></div>' +
      '<div style="margin-top:6px">Kept: <strong>' + esc(String(result.items_kept || 0)) + '</strong></div>' +
      '<div style="margin-top:6px">Alerts: <strong>' + esc(String(result.alerts_created || 0)) + '</strong></div>';
    await reloadAll();
  }catch(err){
    box.innerHTML = '<div class="status-bad">Run failed: ' + esc(err.message) + '</div>';
  }
}

document.getElementById('refreshBtn').addEventListener('click', reloadAll);
document.getElementById('runAgentBtn').addEventListener('click', runAgentNow);

['q','signal','asset','source','sort','alertsOnly'].forEach(id => {
  document.getElementById(id).addEventListener('input', renderCards);
  document.getElementById(id).addEventListener('change', renderCards);
});

reloadAll();
setInterval(reloadAll, 60000);
</script>
</body>
</html>
    """)


# === GEOCLAW TERMINAL DATA ROUTES v1 ===
@app.get("/terminal-data", response_class=JSONResponse)
def terminal_data():
    try:
        from services.terminal_service import get_terminal_payload
        payload = get_terminal_payload(limit=100)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/terminal-data",
                "error": str(exc),
            },
            status_code=500,
        )


@app.get("/market-snapshot", response_class=JSONResponse)
def market_snapshot():
    try:
        from market import get_latest_market_snapshots
        payload = {
            "status": "ok",
            "items": get_latest_market_snapshots(),
        }
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/market-snapshot",
                "error": str(exc),
            },
            status_code=500,
        )


@app.get("/alerts", response_class=JSONResponse)
def alerts_data():
    try:
        from services.terminal_service import get_terminal_payload
        payload = get_terminal_payload(limit=100)
        return JSONResponse(
            {
                "status": "ok",
                "count": len(payload.get("top_alerts", [])),
                "items": payload.get("top_alerts", []),
            }
        )
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/alerts",
                "error": str(exc),
            },
            status_code=500,
        )


# === GEOCLAW AGENT ROUTES v1 ===
@app.get("/agent-status", response_class=JSONResponse)
def agent_status():
    try:
        from services.agent_service import get_agent_status
        payload = get_agent_status(limit=12)
        return JSONResponse(
            {
                "status": "ok",
                "runs": payload.get("runs", []),
                "terminal_stats": payload.get("terminal_stats", {}),
                "market_count": payload.get("market_count", 0),
                "top_alerts_count": payload.get("top_alerts_count", 0),
            }
        )
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/agent-status",
                "error": str(exc),
            },
            status_code=500,
        )


@app.post("/agent-run", response_class=JSONResponse)
def agent_run():
    try:
        from services.agent_service import run_agent_cycle
        result = run_agent_cycle(max_records_per_source=10)
        return JSONResponse(
            {
                "status": "ok",
                "route": "/agent-run",
                "result": result,
            }
        )
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/agent-run",
                "error": str(exc),
            },
            status_code=500,
        )


@app.get("/agent-run-now", response_class=JSONResponse)
def agent_run_now():
    try:
        from services.agent_service import run_agent_cycle
        result = run_agent_cycle(max_records_per_source=10)
        return JSONResponse(
            {
                "status": "ok",
                "route": "/agent-run-now",
                "result": result,
            }
        )
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/agent-run-now",
                "error": str(exc),
            },
            status_code=500,
        )



# === GEOCLAW SCHEDULER ROUTES v1 ===
@app.get("/scheduler-status", response_class=JSONResponse)
def scheduler_status():
    try:
        from services.scheduler_service import get_scheduler_status
        payload = get_scheduler_status()
        return JSONResponse(
            {
                "status": "ok",
                "scheduler": payload,
            }
        )
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/scheduler-status",
                "error": str(exc),
            },
            status_code=500,
        )



# === GEOCLAW SCHEDULER BOOT v1 ===
try:
    from services.scheduler_service import ensure_scheduler_started
    ensure_scheduler_started()
except Exception as exc:
    print("WARN: scheduler boot failed:", exc)

