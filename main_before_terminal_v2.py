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



# === GEOCLAW TERMINAL ROUTE v2 ===
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
      --bg:#0a0c10;
      --panel:#12161d;
      --panel2:#171c24;
      --line:#232b36;
      --text:#e7edf5;
      --muted:#93a0b1;
      --green:#19c37d;
      --red:#ff5f56;
      --yellow:#ffd166;
      --blue:#4ea1ff;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      background:var(--bg);
      color:var(--text);
      font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
    }
    .wrap{max-width:1280px;margin:0 auto;padding:18px}
    .bar{
      display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
      margin-bottom:14px
    }
    .title{font-size:26px;font-weight:900;letter-spacing:.04em}
    .sub{color:var(--muted);font-size:12px;margin-top:4px}
    .actions,.modes{display:flex;gap:10px;flex-wrap:wrap}
    .btn,.mode{
      padding:10px 14px;border-radius:10px;border:1px solid var(--line);
      background:var(--panel);color:var(--text);text-decoration:none;cursor:pointer
    }
    .mode.active{background:#11233f;border-color:#1f6feb}
    .stats{
      display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin-bottom:14px
    }
    .stat{
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      border:1px solid var(--line);border-radius:14px;padding:14px
    }
    .stat .k{font-size:12px;color:var(--muted)}
    .stat .v{font-size:24px;font-weight:900;margin-top:6px}
    .controls{
      display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:10px;margin-bottom:14px
    }
    .input,.select{
      width:100%;padding:12px 13px;background:var(--panel);color:var(--text);
      border:1px solid var(--line);border-radius:10px;outline:none
    }
    .toggle{
      display:flex;align-items:center;gap:8px;padding:12px 13px;background:var(--panel);
      border:1px solid var(--line);border-radius:10px;color:var(--muted);white-space:nowrap
    }
    .grid{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:14px}
    .feed{display:grid;gap:12px}
    .panel{
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      border:1px solid var(--line);border-radius:14px;padding:14px
    }
    .panel h3{margin:0 0 10px 0;font-size:14px;color:var(--muted);letter-spacing:.04em}
    .card{
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      border:1px solid var(--line);border-radius:14px;padding:14px
    }
    .top{
      display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px
    }
    .leftline{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
    .signal{
      padding:5px 9px;border-radius:999px;font-size:12px;font-weight:900;border:1px solid transparent
    }
    .signal.bullish{color:var(--green);border-color:rgba(25,195,125,.35);background:rgba(25,195,125,.10)}
    .signal.bearish{color:var(--red);border-color:rgba(255,95,86,.35);background:rgba(255,95,86,.10)}
    .signal.neutral{color:var(--yellow);border-color:rgba(255,209,102,.35);background:rgba(255,209,102,.10)}
    .badge{
      padding:4px 8px;border-radius:999px;font-size:11px;font-weight:800;
      border:1px solid var(--line);color:var(--muted)
    }
    .badge.asset{color:var(--blue)}
    .badge.alert{color:var(--red)}
    .source,.time,.published,.small{color:var(--muted);font-size:12px}
    .headline{
      display:block;color:var(--text);text-decoration:none;font-size:18px;font-weight:800;
      line-height:1.45;margin-bottom:8px
    }
    .headline:hover{text-decoration:underline}
    .empty{
      padding:24px;border:1px dashed var(--line);border-radius:14px;color:var(--muted);
      text-align:center;background:var(--panel)
    }
    .list{display:grid;gap:10px}
    .mini{
      padding:10px;border:1px solid var(--line);border-radius:10px;background:rgba(255,255,255,.02)
    }
    .mini a{color:var(--text);text-decoration:none;font-size:13px;font-weight:700;line-height:1.35}
    .mini a:hover{text-decoration:underline}
    .footer{margin-top:14px;color:var(--muted);font-size:12px}
    @media (max-width: 980px){
      .grid{grid-template-columns:1fr}
      .stats{grid-template-columns:repeat(2,minmax(120px,1fr))}
      .controls{grid-template-columns:1fr}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="bar">
      <div>
        <div class="title">GEOCLAW TERMINAL</div>
        <div class="sub">Unified dashboard for LIVE feed and SAVED database</div>
      </div>
      <div class="actions">
        <div class="modes">
          <button class="mode active" data-mode="live">LIVE</button>
          <button class="mode" data-mode="saved">SAVED</button>
        </div>
        <a class="btn" href="/save-live-now" target="_blank" rel="noopener noreferrer">SAVE NOW</a>
        <a class="btn" href="/live-news-view" target="_blank" rel="noopener noreferrer">LIVE PAGE</a>
        <a class="btn" href="/saved-news-view" target="_blank" rel="noopener noreferrer">SAVED PAGE</a>
      </div>
    </div>

    <section class="stats">
      <div class="stat"><div class="k">Mode</div><div class="v" id="sMode">LIVE</div></div>
      <div class="stat"><div class="k">Articles</div><div class="v" id="sArticles">0</div></div>
      <div class="stat"><div class="k">Bullish</div><div class="v" id="sBull">0</div></div>
      <div class="stat"><div class="k">Bearish</div><div class="v" id="sBear">0</div></div>
      <div class="stat"><div class="k">Alerts</div><div class="v" id="sAlerts">0</div></div>
    </section>

    <section class="controls">
      <input id="q" class="input" placeholder="Search headline, source, asset, keyword..." />
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
      <label class="toggle"><input type="checkbox" id="auto" checked> auto refresh live</label>
    </section>

    <section class="grid">
      <div>
        <div class="feed" id="feed">
          <div class="empty">Loading terminal…</div>
        </div>
      </div>
      <aside class="list">
        <div class="panel">
          <h3>TOP ALERTS</h3>
          <div id="alertList" class="list"></div>
        </div>
        <div class="panel">
          <h3>SOURCES</h3>
          <div id="sourceList" class="list"></div>
        </div>
        <div class="panel">
          <h3>STATUS</h3>
          <div class="small" id="statusBox">Waiting for first fetch…</div>
        </div>
      </aside>
    </section>

    <div class="footer">Safe design: /terminal reads existing /live-news and /saved-news only</div>
  </div>

<script>
const state = {
  mode: 'live',
  items: [],
  timer: null
};

function esc(v){
  return String(v ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function norm(row){
  if (Array.isArray(row)) {
    return {
      id: row[0] ?? '',
      headline: String(row[1] ?? ''),
      source: String(row[2] ?? 'Unknown'),
      url: String(row[3] ?? '#'),
      published_at: String(row[4] ?? '')
    };
  }
  if (row && typeof row === 'object') {
    return {
      id: row.id ?? '',
      headline: String(row.headline ?? row.title ?? ''),
      source: String(row.source ?? row.feed ?? 'Unknown'),
      url: String(row.url ?? row.link ?? '#'),
      published_at: String(row.published_at ?? row.published ?? row.date ?? '')
    };
  }
  return null;
}

function sentiment(h){
  const s = h.toLowerCase();
  const bull = ['rally','surge','jump','gain','beat','growth','record high','rise','rises','rebound','recover','stimulus','rate cut'];
  const bear = ['fall','drop','drops','slump','crash','war','sanctions','miss','weak','recession','selloff','shutdown','plunge','rate hike'];
  let b1 = bull.filter(x => s.includes(x)).length;
  let b2 = bear.filter(x => s.includes(x)).length;
  if (b1 > b2) return 'Bullish';
  if (b2 > b1) return 'Bearish';
  return 'Neutral';
}

function assets(h){
  const s = h.toLowerCase();
  const out = [];
  if (['oil','brent','wti','opec','crude'].some(x => s.includes(x))) out.push('OIL');
  if (['gold','bullion','xau'].some(x => s.includes(x))) out.push('GOLD');
  if (['forex','currency','currencies','dollar','usd','gbp','eur','jpy','yen','sterling','fx'].some(x => s.includes(x))) out.push('FOREX');
  if (['fed','ecb','boe','interest rate','rates','bond yield','yields','treasury'].some(x => s.includes(x))) out.push('RATES');
  if (['stock','stocks','equity','equities','shares','nasdaq','s&p','dow','ftse','nikkei','index'].some(x => s.includes(x))) out.push('STOCKS');
  return out.length ? out : ['GENERAL'];
}

function alerts(h){
  const s = h.toLowerCase();
  const words = ['shutdown','record high','record low','rate hike','rate cut','war','sanctions','crash','selloff','surge','plunge','default','recession','opec','tariff','strike','inflation','stimulus'];
  return words.filter(x => s.includes(x)).map(x => x.toUpperCase());
}

function relTime(value){
  const t = String(value || '').trim();
  if (!t) return 'time n/a';
  const d = new Date(t);
  if (isNaN(d.getTime())) return t;
  const mins = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + ' mins ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + ' hrs ago';
  return Math.floor(hrs / 24) + ' days ago';
}

function score(item){
  return item.alerts.length * 10 + (item.signal === 'Bullish' || item.signal === 'Bearish' ? 2 : 0);
}

function enrich(row){
  const x = norm(row);
  if (!x || !x.headline) return null;
  x.signal = sentiment(x.headline);
  x.assets = assets(x.headline);
  x.alerts = alerts(x.headline);
  x.search = (x.headline + ' ' + x.source + ' ' + x.assets.join(' ') + ' ' + x.alerts.join(' ')).toLowerCase();
  x.score = score(x);
  return x;
}

async function fetchData(){
  const endpoint = state.mode === 'live' ? '/live-news' : '/saved-news';
  const res = await fetch(endpoint, {cache: 'no-store'});
  if (!res.ok) throw new Error('HTTP ' + res.status + ' from ' + endpoint);
  const data = await res.json();
  const arr = Array.isArray(data) ? data : (data.articles || data.items || data.results || []);
  state.items = arr.map(enrich).filter(Boolean).sort((a,b) => b.score - a.score);
  render();
  const now = new Date().toLocaleTimeString();
  document.getElementById('statusBox').textContent = 'Mode: ' + state.mode.toUpperCase() + ' | Fetched: ' + state.items.length + ' | Updated: ' + now;
}

function setOptions(id, values){
  const el = document.getElementById(id);
  const keep = el.value;
  const first = id === 'asset' ? '<option value="">All assets</option>' : '<option value="">All sources</option>';
  el.innerHTML = first + values.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
  if (values.includes(keep)) el.value = keep;
}

function currentItems(){
  const q = document.getElementById('q').value.trim().toLowerCase();
  const sig = document.getElementById('signal').value;
  const asset = document.getElementById('asset').value;
  const source = document.getElementById('source').value;

  return state.items.filter(x => {
    if (q && !x.search.includes(q)) return false;
    if (sig && x.signal !== sig) return false;
    if (asset && !x.assets.includes(asset)) return false;
    if (source && x.source !== source) return false;
    return true;
  });
}

function render(){
  const assetsAll = [...new Set(state.items.flatMap(x => x.assets))].sort();
  const sourcesAll = [...new Set(state.items.map(x => x.source))].sort();
  setOptions('asset', assetsAll);
  setOptions('source', sourcesAll);

  const items = currentItems();
  const bull = items.filter(x => x.signal === 'Bullish').length;
  const bear = items.filter(x => x.signal === 'Bearish').length;
  const alertsCount = items.filter(x => x.alerts.length).length;

  document.getElementById('sMode').textContent = state.mode.toUpperCase();
  document.getElementById('sArticles').textContent = String(items.length);
  document.getElementById('sBull').textContent = String(bull);
  document.getElementById('sBear').textContent = String(bear);
  document.getElementById('sAlerts').textContent = String(alertsCount);

  const feed = document.getElementById('feed');
  if (!items.length){
    feed.innerHTML = '<div class="empty">No articles match the current filters.</div>';
  } else {
    feed.innerHTML = items.map(x => {
      const assetBadges = x.assets.map(a => '<span class="badge asset">' + esc(a) + '</span>').join('');
      const alertBadges = x.alerts.map(a => '<span class="badge alert">' + esc(a) + '</span>').join('');
      const sigClass = x.signal.toLowerCase();
      return `
        <article class="card">
          <div class="top">
            <div class="leftline">
              <span class="signal ${sigClass}">${esc(x.signal)}</span>
              <span class="source">${esc(x.source)}</span>
              ${assetBadges}
              ${alertBadges}
            </div>
            <div class="time">${esc(relTime(x.published_at))}</div>
          </div>
          <a class="headline" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.headline)}</a>
          <div class="published">${esc(x.published_at || '')}</div>
        </article>
      `;
    }).join('');
  }

  const alertItems = items.filter(x => x.alerts.length).slice(0, 8);
  document.getElementById('alertList').innerHTML = alertItems.length
    ? alertItems.map(x => `<div class="mini"><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.headline)}</a><div class="small">${esc(x.alerts.join(', '))}</div></div>`).join('')
    : '<div class="small">No alert headlines in current filter.</div>';

  const sourceCounts = {};
  items.forEach(x => sourceCounts[x.source] = (sourceCounts[x.source] || 0) + 1);
  const sourceRows = Object.entries(sourceCounts).sort((a,b) => b[1] - a[1]).slice(0, 8);
  document.getElementById('sourceList').innerHTML = sourceRows.length
    ? sourceRows.map(([k,v]) => `<div class="mini"><div class="small">${esc(k)}</div><div>${esc(String(v))} articles</div></div>`).join('')
    : '<div class="small">No source data.</div>';

  document.querySelectorAll('.mode').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === state.mode);
  });
}

async function reloadNow(){
  try{
    await fetchData();
  }catch(err){
    document.getElementById('feed').innerHTML = '<div class="empty">Load failed: ' + esc(err.message) + '</div>';
    document.getElementById('statusBox').textContent = 'Error: ' + err.message;
  }
}

function restartTimer(){
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(() => {
    const auto = document.getElementById('auto').checked;
    if (state.mode === 'live' && auto) reloadNow();
  }, 60000);
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('.mode');
  if (!btn) return;
  state.mode = btn.dataset.mode;
  reloadNow();
});

['q','signal','asset','source'].forEach(id => {
  document.addEventListener('input', (e) => {
    if (e.target && e.target.id === id) render();
  });
  document.addEventListener('change', (e) => {
    if (e.target && e.target.id === id) render();
  });
});

document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'auto') restartTimer();
});

reloadNow();
restartTimer();
</script>
</body>
</html>
    """)

