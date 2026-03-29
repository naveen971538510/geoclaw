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

from apscheduler.schedulers.background import BackgroundScheduler
from db import save_live_articles
import logging

def auto_fetch_job():
    try:
        result = save_live_articles()
        logging.info(f"Auto-fetch: {result}")
    except Exception as e:
        logging.error(f"Auto-fetch failed: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(auto_fetch_job, "interval", minutes=10)
scheduler.start()
auto_fetch_job()


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
def saved_news_view(q: str = "", source: str = "all", asset: str = "all", signal: str = "all"):
    from db import get_connection
    import html as h
    from datetime import datetime, timezone

    BEARISH = ["shutdown","crash","fall","drop","decline","recession","ban","sanction",
        "deficit","loss","risk","war","conflict","attack","crisis","inflation","rate hike",
        "fear","sell-off","plunge","tumble","slump","cut","downgrade","layoff","bankrupt",
        "tariff","default","collapse","warning","slowdown"]
    BULLISH = ["rise","rally","surge","gain","growth","record high","recovery","beat",
        "strong","profit","upgrade","deal","agreement","boost","positive","optimistic",
        "expand","hiring","rebound","jump","soar","outperform","all-time high",
        "rate cut","stimulus","ceasefire","peace"]
    ALERTS = ["shutdown","record high","rate hike","rate cut","war","sanctions","crash",
        "default","ceasefire","emergency","crisis","all-time high","collapse","invasion"]
    ASSETS = {
        "oil":    ["oil","crude","opec","brent","wti","petroleum","energy","barrel"],
        "gold":   ["gold","xau","bullion","precious metal","silver"],
        "forex":  ["dollar","euro","pound","yen","currency","forex","usd","eur","gbp","jpy",
                   "dxy","exchange rate","fed","federal reserve","ecb","interest rate","rate cut","rate hike"],
        "stocks": ["stock","equit","share","nasdaq","s&p","dow","market","ipo","earnings","wall street"],
        "rates":  ["bond","yield","treasury","central bank","monetary","inflation","cpi","gdp"],
    }

    def sentiment(hl):
        low = hl.lower()
        for w in BEARISH:
            if w in low: return "BEARISH"
        for w in BULLISH:
            if w in low: return "BULLISH"
        return "NEUTRAL"

    def assets(hl):
        low = hl.lower()
        return [k for k,words in ASSETS.items() if any(w in low for w in words)]

    def alerts(hl):
        low = hl.lower()
        return [w for w in ALERTS if w in low]

    def ago(ts):
        if not ts: return ""
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(ts)
            d = int((datetime.now(timezone.utc) - dt).total_seconds())
            if d < 60: return str(d) + "s ago"
            if d < 3600: return str(d//60) + "m ago"
            if d < 86400: return str(d//3600) + "h ago"
            return str(d//86400) + "d ago"
        except:
            return ts[:16] if ts else ""

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, headline, source, published_at, url FROM articles ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    arts = [{"id":r[0],"headline":r[1],"source":r[2],"published_at":r[3],"url":r[4]} for r in rows]
    src_map = {"bbc":"BBC RSS","lemonde":"Le Monde International"}
    sel_src = source if source in src_map else "all"
    ql = q.strip().lower()

    for a in arts:
        a["s"] = sentiment(a["headline"])
        a["aa"] = assets(a["headline"])
        a["al"] = alerts(a["headline"])
        a["ago"] = ago(a["published_at"])

    filt = [a for a in arts
        if (not ql or ql in a["headline"].lower())
        and (sel_src == "all" or a["source"] == src_map[sel_src])
        and (asset == "all" or asset in a["aa"])
        and (signal == "all" or a["s"] == signal.upper())]

    total = len(arts)
    shown = len(filt)
    bc = sum(1 for a in arts if a["s"]=="BULLISH")
    rc = sum(1 for a in arts if a["s"]=="BEARISH")
    nc = sum(1 for a in arts if a["s"]=="NEUTRAL")
    ac = sum(1 for a in arts if a["al"])

    def sbtn(label, val, param, cur_val, extra):
        on = cur_val == val
        colors = {"all":("#8b949e","#30363d"),"BULLISH":("#39d353","#1a4a2e"),
                  "BEARISH":("#f85149","#4a1a1a"),"NEUTRAL":("#8b949e","#30363d"),
                  "bbc":("#58a6ff","#1a3a5c"),"lemonde":("#58a6ff","#1a3a5c"),
                  "oil":("#e3b341","#3a2e00"),"gold":("#39d353","#0d2119"),
                  "forex":("#58a6ff","#0d2040"),"stocks":("#bc8cff","#2a1a4a"),
                  "rates":("#f0883e","#3a1e00")}
        c,b = colors.get(val, ("#8b949e","#30363d"))
        border = b if on else "#30363d"
        color  = c if on else "#8b949e"
        bg     = "#0d1117" if on else "#161b22"
        url    = "/saved-news-view?" + param + "=" + val + "&" + extra
        return ('<a href="' + url + '" style="display:inline-block;padding:5px 13px;'
                'font-size:11px;border:1px solid ' + border + ';border-radius:3px;'
                'background:' + bg + ';color:' + color + ';text-decoration:none;'
                'font-family:monospace;margin-right:5px;margin-bottom:5px;">' + label + '</a>')

    qe = h.escape(q)
    sig_extra  = "q=" + qe + "&source=" + source + "&asset=" + asset
    src_extra  = "q=" + qe + "&signal=" + signal + "&asset=" + asset
    ast_extra  = "q=" + qe + "&source=" + source + "&signal=" + signal

    sig_row = (sbtn("ALL","all","signal",signal,sig_extra) +
               sbtn("BULLISH","BULLISH","signal",signal,sig_extra) +
               sbtn("BEARISH","BEARISH","signal",signal,sig_extra) +
               sbtn("NEUTRAL","NEUTRAL","signal",signal,sig_extra))
    src_row = (sbtn("ALL SOURCES","all","source",sel_src,src_extra) +
               sbtn("BBC","bbc","source",sel_src,src_extra) +
               sbtn("LE MONDE","lemonde","source",sel_src,src_extra))
    ast_row = (sbtn("ALL","all","asset",asset,ast_extra) +
               sbtn("OIL","oil","asset",asset,ast_extra) +
               sbtn("GOLD","gold","asset",asset,ast_extra) +
               sbtn("FOREX","forex","asset",asset,ast_extra) +
               sbtn("STOCKS","stocks","asset",asset,ast_extra) +
               sbtn("RATES","rates","asset",asset,ast_extra))

    APILL = {"oil":"#e3b341","gold":"#39d353","forex":"#58a6ff","stocks":"#bc8cff","rates":"#f0883e"}

    cards = ""
    for a in filt:
        s = a["s"]
        bcol = {"BULLISH":"#39d353","BEARISH":"#f85149","NEUTRAL":"#30363d"}.get(s,"#30363d")
        tstyle = {"BULLISH":"background:#0d2119;color:#39d353;border:1px solid #1a4a2e;",
                  "BEARISH":"background:#200d0d;color:#f85149;border:1px solid #4a1a1a;",
                  "NEUTRAL":"background:#161b22;color:#8b949e;border:1px solid #30363d;"}.get(s,"")
        cbg = "#0f0d00" if a["al"] else "#0d1117"
        alrt = ""
        if a["al"]:
            alrt = ('<span style="font-size:10px;color:#e3b341;margin-left:8px;'
                    'font-family:monospace;">&#9888; ' + h.escape(", ".join(a["al"][:2])) + '</span>')
        pills = "".join(
            '<span style="font-size:10px;padding:1px 7px;border:1px solid ' + APILL.get(x,"#8b949e") +
            ';border-radius:2px;color:' + APILL.get(x,"#8b949e") +
            ';margin-right:4px;font-family:monospace;">' + x.upper() + '</span>'
            for x in a["aa"])
        cards += (
            '<div style="border-left:3px solid ' + bcol + ';background:' + cbg +
            ';margin-bottom:6px;padding:10px 14px;border-radius:0 4px 4px 0;">'
            '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:5px;">'
            '<span style="font-size:13px;color:#e6edf3;line-height:1.4;flex:1;font-family:monospace;">' +
            h.escape(a["headline"]) + '</span>'
            '<span style="font-size:10px;padding:2px 8px;border-radius:2px;flex-shrink:0;'
            'font-weight:700;font-family:monospace;' + tstyle + '">' + s + '</span>'
            '</div>'
            '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
            '<span style="font-size:11px;color:#58a6ff;font-family:monospace;">' + h.escape(a["source"]) + '</span>'
            '<span style="font-size:11px;color:#484f58;font-family:monospace;">' + h.escape(a["ago"]) + '</span>' +
            alrt + '</div>'
            '<div style="margin-top:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">' +
            pills +
            '<a href="' + h.escape(a["url"]) + '" target="_blank" style="font-size:11px;color:#484f58;'
            'text-decoration:none;font-family:monospace;border:1px solid #21262d;'
            'padding:1px 8px;border-radius:2px;">open &#8599;</a>'
            '</div></div>')

    if not cards:
        cards = '<div style="background:#0d1117;border:1px solid #21262d;padding:20px;border-radius:4px;font-family:monospace;color:#484f58;">NO ARTICLES MATCH FILTERS</div>'

    astrip = ""
    if ac:
        kws = list(set(kw for a in arts for kw in a["al"]))[:5]
        astrip = ('<div style="background:#1a1000;border:1px solid #e3b341;border-radius:3px;'
                  'padding:7px 12px;font-size:11px;color:#e3b341;margin-bottom:12px;font-family:monospace;">'
                  '&#9888; ' + str(ac) + ' KEYWORD ALERTS &mdash; ' + h.escape(", ".join(kws)) + '</div>')

    page = (
        "<!DOCTYPE html><html><head><title>GeoClaw Terminal</title></head>"
        '<body style="background:#0a0c10;color:#c9d1d9;font-family:monospace;padding:16px;margin:0;">'
        '<div style="max-width:960px;margin:0 auto;">'
        '<div style="display:flex;align-items:center;justify-content:space-between;'
        'border-bottom:1px solid #21262d;padding-bottom:10px;margin-bottom:14px;">'
        '<span style="font-size:15px;font-weight:700;color:#58a6ff;letter-spacing:2px;">&#9679; GEOCLAW TERMINAL</span>'
        '<span style="font-size:11px;color:#484f58;">AUTO-FETCH ON &mdash; EVERY 10 MIN &mdash; ' + str(total) + ' ARTICLES</span>'
        '</div>'
        '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px;">'
        '<div style="background:#161b22;border:1px solid #21262d;border-radius:3px;padding:8px 12px;">'
        '<div style="font-size:22px;font-weight:700;color:#39d353;">' + str(bc) + '</div>'
        '<div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin-top:2px;">Bullish</div></div>'
        '<div style="background:#161b22;border:1px solid #21262d;border-radius:3px;padding:8px 12px;">'
        '<div style="font-size:22px;font-weight:700;color:#f85149;">' + str(rc) + '</div>'
        '<div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin-top:2px;">Bearish</div></div>'
        '<div style="background:#161b22;border:1px solid #21262d;border-radius:3px;padding:8px 12px;">'
        '<div style="font-size:22px;font-weight:700;color:#8b949e;">' + str(nc) + '</div>'
        '<div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin-top:2px;">Neutral</div></div>'
        '<div style="background:#161b22;border:1px solid #21262d;border-radius:3px;padding:8px 12px;">'
        '<div style="font-size:22px;font-weight:700;color:#e3b341;">' + str(ac) + '</div>'
        '<div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin-top:2px;">Alerts</div></div>'
        '</div>'
        '<form onsubmit="event.preventDefault();var q=document.getElementById(\'sq\').value.trim();'
        'window.location=\'/saved-news-view?q=\'+encodeURIComponent(q)+\'&source=' + source + '&asset=' + asset + '&signal=' + signal + '\';" '
        'style="display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap;">'
        '<input id="sq" type="text" value="' + qe + '" placeholder="SEARCH HEADLINES..." '
        'style="flex:1;min-width:180px;padding:7px 12px;font-size:12px;background:#0d1117;'
        'border:1px solid #30363d;color:#c9d1d9;border-radius:3px;font-family:monospace;">'
        '<button type="submit" style="padding:7px 16px;background:#161b22;border:1px solid #30363d;'
        'color:#c9d1d9;border-radius:3px;font-family:monospace;cursor:pointer;font-size:12px;">SEARCH</button>'
        '<a href="/saved-news-view" style="padding:7px 12px;background:#161b22;border:1px solid #30363d;'
        'color:#8b949e;border-radius:3px;font-family:monospace;text-decoration:none;font-size:12px;">CLEAR</a>'
        '<a href="/save-live-now" style="padding:7px 12px;background:#0d2119;border:1px solid #1a4a2e;'
        'color:#39d353;border-radius:3px;font-family:monospace;text-decoration:none;font-size:12px;">&#11015; SAVE NOW</a>'
        '</form>'
        '<div style="margin-bottom:8px;">' + sig_row + '</div>'
        '<div style="margin-bottom:8px;">' + src_row + '</div>'
        '<div style="margin-bottom:14px;"><span style="font-size:10px;color:#484f58;margin-right:6px;">ASSET:</span>' + ast_row + '</div>' +
        astrip +
        '<div style="font-size:10px;color:#484f58;margin-bottom:8px;">SHOWING ' + str(shown) + ' OF ' + str(total) + ' ARTICLES</div>' +
        cards +
        '<div style="margin-top:16px;border-top:1px solid #21262d;padding-top:8px;font-size:10px;'
        'color:#484f58;display:flex;justify-content:space-between;">'
        '<span>GEOCLAW v2.0 &mdash; TRADING TERMINAL</span>'
        '<span>AUTO-FETCH: ON &mdash; INTERVAL: 10 MIN</span>'
        '</div></div></body></html>')
    return HTMLResponse(page)


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
def saved_news_search_view(word: str, limit: int = 20, source: str = "all"):
    from db import search_saved_articles
    articles = search_saved_articles(word, limit)

    selected_source = source.strip().lower()
    if selected_source not in {"all", "bbc", "lemonde"}:
        selected_source = "all"

    filtered = []
    for a in articles:
        src = str(a.get("source", ""))
        if selected_source == "all":
            filtered.append(a)
        elif selected_source == "bbc" and src == "BBC RSS":
            filtered.append(a)
        elif selected_source == "lemonde" and src == "Le Monde International":
            filtered.append(a)

    def filter_btn(label, value):
        active = selected_source == value
        bg = "#60a5fa" if active else "#1f2937"
        return f"""
        <a href="/saved-news-search-view/{html.escape(word)}?source={value}"
           style="
             display:inline-block;
             padding:10px 16px;
             border-radius:10px;
             background:{bg};
             color:white;
             text-decoration:none;
             font-weight:700;
             margin-right:8px;
           ">
           {label}
        </a>
        """

    cards = ""
    for a in filtered:
        headline = html.escape(str(a.get("headline", "")))
        src = html.escape(str(a.get("source", "")))
        published_at = html.escape(str(a.get("published_at", "")))
        url = html.escape(str(a.get("url", "")), quote=True)

        cards += f"""
        <div style="background:#16181d;border-radius:18px;padding:22px;margin-bottom:18px;">
            <h2 style="margin:0 0 12px 0;color:#f3f4f6;">{headline}</h2>
            <p style="margin:6px 0;color:#d1d5db;"><strong>Source:</strong> {src}</p>
            <p style="margin:6px 0 14px 0;color:#d1d5db;"><strong>Published:</strong> {published_at}</p>
            <a href="{url}" target="_blank" style="color:#60a5fa;text-decoration:none;font-weight:600;">Open full article</a>
        </div>
        """

    if not cards:
        cards = "<div style='background:#16181d;border-radius:18px;padding:22px;'><p style='margin:0;color:#f3f4f6;'>No saved articles found</p></div>"

    return HTMLResponse(f"""
    <html>
    <body style="background:black;color:white;font-family:Arial;padding:30px;">
        <h1 style="font-size:52px;margin:0 0 10px 0;">Saved Search: {html.escape(word)}</h1>
        <p style="color:#cbd5e1;margin:0 0 18px 0;">Results from SQLite database</p>

        <form onsubmit="event.preventDefault(); const q=document.getElementById('q').value.trim(); if(q){{ window.location='/saved-news-search-view/' + encodeURIComponent(q); }}" style="margin:0 0 18px 0;">
            <input id="q" type="text" value="{html.escape(word, quote=True)}" placeholder="Search saved news"
                   style="padding:12px;width:320px;font-size:16px;border-radius:8px;border:1px solid #444;background:#111;color:white;">
            <button type="submit"
                    style="padding:12px 18px;font-size:16px;border:none;border-radius:8px;background:#60a5fa;color:black;font-weight:bold;margin-left:8px;">
                Search
            </button>
        </form>

        <div style="margin:0 0 24px 0;">
            {filter_btn("All", "all")}
            {filter_btn("BBC", "bbc")}
            {filter_btn("Le Monde", "lemonde")}
        </div>

        {cards}
    </body>
    </html>
    """)



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


@app.get("/save-live-home", response_class=HTMLResponse)
def save_live_home():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:white;font-family:Arial;padding:30px;">
        <h1>Save Live Feed</h1>
        <p style="color:#cbd5e1;">Click the button to fetch latest live articles and save them into SQLite.</p>
        <button onclick="window.location='/save-live-now'"
                style="padding:14px 20px;font-size:16px;border:none;border-radius:10px;background:#60a5fa;color:black;font-weight:bold;cursor:pointer;">
            Save live feed now
        </button>
        <div style="margin-top:20px;">
            <a href="/live-news-view" style="color:#60a5fa;text-decoration:none;">Back to Live News</a>
            <span style="color:#6b7280;"> | </span>
            <a href="/saved-news-view" style="color:#60a5fa;text-decoration:none;">Saved News</a>
        </div>
    </body>
    </html>
    """)

@app.get("/save-live-now", response_class=HTMLResponse)
def save_live_now():
    from db import init_db, save_live_articles, count_saved_articles

    init_db()
    result = save_live_articles()
    total = count_saved_articles()

    if "error" in result:
        return HTMLResponse(f"""
        <html>
        <body style="background:black;color:white;font-family:Arial;padding:30px;">
            <h1>Save Failed</h1>
            <p style="color:#fca5a5;">{html.escape(str(result.get("error", "unknown error")))}</p>
            <a href="/save-live-home" style="color:#60a5fa;text-decoration:none;">Back</a>
        </body>
        </html>
        """)

    fetched = result.get("fetched", 0)
    inserted = result.get("inserted", 0)

    return HTMLResponse(f"""
    <html>
    <body style="background:black;color:white;font-family:Arial;padding:30px;">
        <h1>Save Complete</h1>
        <div style="background:#16181d;border-radius:18px;padding:22px;max-width:700px;">
            <p><strong>Fetched:</strong> {fetched}</p>
            <p><strong>Inserted:</strong> {inserted}</p>
            <p><strong>Total saved in DB:</strong> {total}</p>
        </div>
        <div style="margin-top:20px;">
            <a href="/save-live-home" style="color:#60a5fa;text-decoration:none;">Save again</a>
            <span style="color:#6b7280;"> | </span>
            <a href="/saved-news-view" style="color:#60a5fa;text-decoration:none;">View saved news</a>
            <span style="color:#6b7280;"> | </span>
            <a href="/live-news-view" style="color:#60a5fa;text-decoration:none;">Back to live news</a>
        </div>
    </body>
    </html>
    """)
