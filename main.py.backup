from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from data import demo_articles
from datetime import datetime, timezone
from helpers import (
    filter_articles_by_field,
    search_articles_by_headline,
    get_article_by_id,
    get_summary,
)
from fetcher import fetch_live_articles
import json
import html
from fastapi.responses import JSONResponse
from config import DB_PATH, GEOCLAW_LOCAL_TOKEN, OPENAI_API_KEY
from services.logging_service import get_logger

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
logger = get_logger("main")


def _local_client(request: Request) -> bool:
    host = str(((request.client or {}).host if hasattr(request.client, "host") else "") or "")
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _mutation_guard(request: Request):
    token = str(GEOCLAW_LOCAL_TOKEN or "").strip()
    provided = str(request.headers.get("x-geoclaw-token", "") or request.query_params.get("token", "") or "").strip()
    if token:
        if provided == token or _local_client(request):
            return
        raise PermissionError("Local safety token required")
    if _local_client(request):
        return
    raise PermissionError("This route is limited to local requests")


def _get_query_engine():
    from services.query_engine import QueryEngine

    llm = None
    if OPENAI_API_KEY:
        from services.llm_analyst import LLMAnalyst

        llm = LLMAnalyst(str(DB_PATH))
    return QueryEngine(str(DB_PATH), llm_analyst=llm)


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
    return RedirectResponse(url="/dashboard", status_code=302)


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















# === GEOCLAW TERMINAL ROUTE v7 ===
@app.get("/terminal", response_class=HTMLResponse)
def geoclaw_terminal():
    from services.terminal_ui_service import render_terminal_page

    return HTMLResponse(render_terminal_page())


@app.get("/dashboard", response_class=HTMLResponse)
def geoclaw_dashboard():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("dashboard.html"))


@app.get("/ask", response_class=HTMLResponse)
def geoclaw_ask_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("ask.html"))


@app.get("/live", response_class=HTMLResponse)
def geoclaw_live_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("live.html"))


@app.get("/manifest.json")
def geoclaw_manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


@app.get("/theses", response_class=HTMLResponse)
def geoclaw_theses_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("theses.html"))


@app.get("/articles", response_class=HTMLResponse)
def geoclaw_articles_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("articles.html"))


@app.get("/agent-runs", response_class=HTMLResponse)
def geoclaw_agent_runs_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("agent_runs.html"))


@app.get("/briefings", response_class=HTMLResponse)
def geoclaw_briefings_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("briefings.html"))


@app.get("/contradictions", response_class=HTMLResponse)
def geoclaw_contradictions_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("contradictions.html"))


@app.get("/watchlist", response_class=HTMLResponse)
def geoclaw_watchlist_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("watchlist.html"))


@app.get("/portfolio", response_class=HTMLResponse)
def geoclaw_portfolio_page():
    from services.terminal_ui_service import render_terminal_asset

    return HTMLResponse(render_terminal_asset("portfolio.html"))


@app.get("/terminal-ui/terminal.css")
def terminal_css():
    from services.terminal_ui_service import render_terminal_asset

    return Response(render_terminal_asset("terminal.css"), media_type="text/css")


@app.get("/terminal-ui/terminal.js")
def terminal_js():
    from services.terminal_ui_service import render_terminal_asset

    return Response(render_terminal_asset("terminal.js"), media_type="application/javascript")


# === GEOCLAW TERMINAL DATA ROUTES v1 ===
@app.get("/operator-state", response_class=JSONResponse)
def operator_state():
    try:
        from services.operator_state_service import get_operator_state

        return JSONResponse({"status": "ok", "state": get_operator_state()})
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/operator-state",
                "error": str(exc),
            },
            status_code=500,
        )


@app.post("/operator-state", response_class=JSONResponse)
async def save_operator_state(request: Request):
    try:
        _mutation_guard(request)
        from services.operator_state_service import update_operator_state

        payload = await request.json()
        state = update_operator_state(payload if isinstance(payload, dict) else {})
        return JSONResponse({"status": "ok", "state": state})
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/operator-state",
                "error": str(exc),
            },
            status_code=500,
        )


@app.post("/system-reset", response_class=JSONResponse)
def system_reset(request: Request):
    try:
        _mutation_guard(request)
        from cleanup import run_cleanup

        run_cleanup()
        return JSONResponse({"status": "ok", "message": "System reset complete"})
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/system-reset",
                "error": str(exc),
            },
            status_code=500,
        )


@app.get("/terminal-data", response_class=JSONResponse)
def terminal_data():
    try:
        from services.presentation_service import get_terminal_payload_clean
        payload = get_terminal_payload_clean(limit=100)
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


@app.get("/terminal/theses", response_class=JSONResponse)
def terminal_theses(limit: int = 80):
    try:
        from services.cache_service import cache_get, cache_set
        from services.terminal_service import get_terminal_theses

        cache_key = f"terminal_theses:{int(limit)}"
        cached = cache_get(cache_key)
        if cached is not None:
            return JSONResponse(cached)
        theses = get_terminal_theses(limit=limit)
        payload = {"status": "ok", "items": theses, "theses": theses}
        cache_set(cache_key, payload, 30)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/terminal/theses", "error": str(exc)}, status_code=500)


@app.get("/terminal/actions", response_class=JSONResponse)
def terminal_actions():
    try:
        from services.terminal_service import get_terminal_actions

        return JSONResponse({"status": "ok", "items": get_terminal_actions(limit=80)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/terminal/actions", "error": str(exc)}, status_code=500)


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
        from services.presentation_service import get_terminal_payload_clean
        payload = get_terminal_payload_clean(limit=100)
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
@app.get("/agent-goals", response_class=JSONResponse)
def agent_goals():
    try:
        from services.goal_service import list_goals

        return JSONResponse({"status": "ok", "items": list_goals(active_only=False)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-goals", "error": str(exc)}, status_code=500)


@app.get("/agent-thesis/{thesis_key:path}/timeline", response_class=JSONResponse)
def agent_thesis_timeline(thesis_key: str):
    try:
        from services.thesis_service import get_thesis_timeline

        return JSONResponse({"status": "ok", "items": get_thesis_timeline(thesis_key)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-thesis/timeline", "error": str(exc)}, status_code=500)


@app.get("/agent-thesis/{thesis_key:path}", response_class=JSONResponse)
def agent_thesis_detail(thesis_key: str):
    try:
        from services.thesis_service import get_thesis_detail

        detail = get_thesis_detail(thesis_key)
        if not detail:
            return JSONResponse({"status": "error", "route": "/agent-thesis", "error": "not found"}, status_code=404)
        return JSONResponse({"status": "ok", "item": detail})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-thesis", "error": str(exc)}, status_code=500)


@app.post("/agent-goals", response_class=JSONResponse)
async def create_agent_goal(request: Request):
    try:
        _mutation_guard(request)
        from services.goal_service import create_goal

        payload = await request.json()
        goal = create_goal(
            name=payload.get("name", ""),
            description=payload.get("description", ""),
            priority=payload.get("priority", 50),
            watch_targets=payload.get("watch_targets", []),
            is_active=bool(payload.get("is_active", True)),
            source=payload.get("source", "manual"),
            status=payload.get("status", "active"),
            thesis_key=payload.get("thesis_key", ""),
            success_criteria=payload.get("success_criteria", ""),
        )
        return JSONResponse({"status": "ok", "item": goal})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-goals", "error": str(exc)}, status_code=500)


@app.get("/agent-actions", response_class=JSONResponse)
def agent_actions():
    try:
        from services.action_service import list_actions

        return JSONResponse({"status": "ok", "items": list_actions(limit=100)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-actions", "error": str(exc)}, status_code=500)


@app.get("/agent-actions/policy", response_class=JSONResponse)
def agent_actions_policy():
    try:
        from services.action_service import ACTION_POLICY

        return JSONResponse({"status": "ok", "policy": ACTION_POLICY})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-actions/policy", "error": str(exc)}, status_code=500)


@app.post("/agent-actions/propose", response_class=JSONResponse)
async def agent_actions_propose(request: Request):
    try:
        _mutation_guard(request)
        from services.action_service import propose_action

        payload = await request.json()
        item = propose_action(
            action_type=payload.get("action_type", ""),
            payload=payload.get("payload", {}),
            thesis_key=payload.get("thesis_key", ""),
            confidence=payload.get("confidence"),
            evidence_count=payload.get("evidence_count"),
            triggered_by=payload.get("triggered_by", "terminal"),
        )
        return JSONResponse({"status": "ok", "item": item})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-actions/propose", "error": str(exc)}, status_code=500)


@app.get("/agent-actions/{action_id}/preview", response_class=JSONResponse)
def agent_actions_preview(action_id: int):
    try:
        from services.action_service import preview_action

        item = preview_action(action_id)
        if not item:
            return JSONResponse({"status": "error", "route": "/agent-actions/preview", "error": "not found"}, status_code=404)
        return JSONResponse({"status": "ok", "item": item})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-actions/preview", "error": str(exc)}, status_code=500)


@app.post("/agent-actions/{action_id}/approve", response_class=JSONResponse)
async def agent_actions_approve(action_id: int, request: Request):
    try:
        _mutation_guard(request)
        from services.action_executor import ActionExecutor
        from services.action_service import approve_action, list_actions

        payload = await request.json() if request else {}
        item = approve_action(action_id, payload.get("approved_by", "terminal"))
        if not item:
            return JSONResponse({"status": "error", "route": "/agent-actions/approve", "error": "not found"}, status_code=404)
        execution = None
        try:
            action = next((entry for entry in list_actions(limit=200) if int(entry.get("id", 0) or 0) == int(action_id)), item)
            execution = ActionExecutor(str(DB_PATH)).execute_action(action)
            item = next((entry for entry in list_actions(limit=200) if int(entry.get("id", 0) or 0) == int(action_id)), action)
        except Exception:
            pass
        return JSONResponse({"status": "ok", "item": item, "execution": execution})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-actions/approve", "error": str(exc)}, status_code=500)


@app.post("/agent-actions/{action_id}/reject", response_class=JSONResponse)
async def agent_actions_reject(action_id: int, request: Request):
    try:
        _mutation_guard(request)
        from services.action_service import reject_action

        payload = await request.json() if request else {}
        item = reject_action(action_id, payload.get("reason", "Rejected from terminal"))
        if not item:
            return JSONResponse({"status": "error", "route": "/agent-actions/reject", "error": "not found"}, status_code=404)
        return JSONResponse({"status": "ok", "item": item})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-actions/reject", "error": str(exc)}, status_code=500)


@app.get("/agent-decisions", response_class=JSONResponse)
def agent_decisions():
    try:
        from services.decision_service import list_decisions

        return JSONResponse({"status": "ok", "items": list_decisions(limit=100, open_only=False)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-decisions", "error": str(exc)}, status_code=500)


@app.get("/agent-tasks", response_class=JSONResponse)
def agent_tasks():
    try:
        from services.task_service import list_tasks

        return JSONResponse({"status": "ok", "items": list_tasks(limit=100, status=None)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-tasks", "error": str(exc)}, status_code=500)


@app.get("/agent-journal", response_class=JSONResponse)
def agent_journal():
    try:
        from services.agent_loop_service import list_journal

        return JSONResponse({"status": "ok", "items": list_journal(limit=80)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-journal", "error": str(exc)}, status_code=500)


@app.get("/agent-metrics", response_class=JSONResponse)
def agent_metrics():
    try:
        from services.agent_loop_service import metrics_snapshot

        return JSONResponse({"status": "ok", "metrics": metrics_snapshot(limit=24)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-metrics", "error": str(exc)}, status_code=500)


@app.get("/terminal/agent-summary", response_class=JSONResponse)
def terminal_agent_summary():
    try:
        from services.terminal_service import get_terminal_agent_summary

        return JSONResponse({"status": "ok", "item": get_terminal_agent_summary()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/terminal/agent-summary", "error": str(exc)}, status_code=500)


@app.get("/terminal/diff", response_class=JSONResponse)
def terminal_diff():
    try:
        from services.terminal_service import get_terminal_diff

        return JSONResponse({"status": "ok", "item": get_terminal_diff()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/terminal/diff", "error": str(exc)}, status_code=500)


@app.get("/terminal/drilldown/{thesis_key:path}", response_class=JSONResponse)
def terminal_drilldown(thesis_key: str):
    try:
        from services.terminal_service import get_terminal_drilldown

        item = get_terminal_drilldown(thesis_key)
        if not item:
            return JSONResponse({"status": "error", "route": "/terminal/drilldown", "error": "not found"}, status_code=404)
        return JSONResponse({"status": "ok", "item": item})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/terminal/drilldown", "error": str(exc)}, status_code=500)


@app.get("/agent-outcomes", response_class=JSONResponse)
def agent_outcomes():
    try:
        from services.memory_service import outcome_summary

        return JSONResponse({"status": "ok", "outcomes": outcome_summary(limit=300)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-outcomes", "error": str(exc)}, status_code=500)


@app.get("/agent-queue", response_class=JSONResponse)
def agent_queue():
    try:
        from services.agent_loop_service import queue_snapshot

        return JSONResponse({"status": "ok", "items": queue_snapshot(limit=40)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-queue", "error": str(exc)}, status_code=500)


@app.get("/agent-reasoning", response_class=JSONResponse)
def agent_reasoning():
    try:
        from services.reasoning_service import list_reasoning_chains

        return JSONResponse({"status": "ok", "items": list_reasoning_chains(limit=20)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-reasoning", "error": str(exc)}, status_code=500)


@app.get("/agent-briefing/latest", response_class=JSONResponse)
def agent_briefing_latest(format: str = "trader"):
    try:
        from services.cache_service import cache_get, cache_set
        from services.briefing_service import get_latest_briefing

        fmt = str(format or "trader").strip().lower()
        cached = cache_get(f"agent_briefing_latest::{fmt}")
        if cached is not None:
            return JSONResponse(cached)
        item = get_latest_briefing(audience=fmt)
        if fmt == "raw_json":
            try:
                item_payload = json.loads(str(item.get("briefing_text", "") or "{}"))
            except Exception:
                item_payload = item
        else:
            item_payload = item
        payload = {"status": "ok", "format": fmt, "item": item_payload}
        cache_set(f"agent_briefing_latest::{fmt}", payload, 300)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-briefing/latest", "error": str(exc)}, status_code=500)


@app.get("/agent-briefing/history", response_class=JSONResponse)
def agent_briefing_history():
    try:
        from services.briefing_service import list_briefings

        return JSONResponse({"status": "ok", "items": list_briefings(limit=10)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-briefing/history", "error": str(exc)}, status_code=500)


@app.get("/agent-briefings", response_class=JSONResponse)
def agent_briefings():
    try:
        from services.briefing_service import list_briefings

        return JSONResponse({"status": "ok", "items": list_briefings(limit=7)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-briefings", "error": str(exc)}, status_code=500)


@app.get("/agent-calibration", response_class=JSONResponse)
def agent_calibration():
    try:
        from services.calibration_service import get_calibration_report

        return JSONResponse({"status": "ok", "report": get_calibration_report()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-calibration", "error": str(exc)}, status_code=500)


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
                "real_loop": payload.get("real_loop", {}),
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
def agent_run(request: Request):
    try:
        _mutation_guard(request)
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
def agent_run_now(request: Request):
    try:
        _mutation_guard(request)
        from services.cache_service import cache_clear_prefix
        from services.agent_service import run_agent_cycle
        result = run_agent_cycle(max_records_per_source=10)
        cache_clear_prefix("")
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


@app.post("/agent-run-real", response_class=JSONResponse)
def agent_run_real(request: Request):
    try:
        _mutation_guard(request)
        from services.cache_service import cache_clear_prefix
        from services.agent_loop_service import run_real_agent_loop

        result = run_real_agent_loop(max_records_per_source=8)
        cache_clear_prefix("")
        return JSONResponse({"status": "ok", "route": "/agent-run-real", "result": result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/agent-run-real", "error": str(exc)}, status_code=500)



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
    from config import GEOCLAW_AUTO_SCHEDULE, SCHEDULER_INTERVAL_MINUTES
    from services.scheduler_service import ensure_scheduler_started
    if GEOCLAW_AUTO_SCHEDULE:
        ensure_scheduler_started(interval_minutes=SCHEDULER_INTERVAL_MINUTES)
except Exception as exc:
    logger.warning("scheduler boot failed: %s", exc)



# === GEOCLAW HEALTH ROUTES v1 ===
@app.get("/health", response_class=JSONResponse)
def health():
    try:
        from services.health_service import get_health

        return JSONResponse({"status": "ok", "item": get_health()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/health", "error": str(exc)}, status_code=500)


@app.get("/health/deep", response_class=JSONResponse)
def health_deep():
    try:
        from services.health_service import get_deep_health

        return JSONResponse({"status": "ok", "item": get_deep_health()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/health/deep", "error": str(exc)}, status_code=500)


@app.get("/api/articles", response_class=JSONResponse)
def api_articles(limit: int = 20, offset: int = 0, days: int = 2, sentiment: str = "", q: str = "", source: str = ""):
    try:
        from services.terminal_service import list_terminal_articles

        payload = list_terminal_articles(limit=limit, offset=offset, days=days, sentiment=sentiment, q=q, source=source)
        return JSONResponse({"status": "ok", **payload})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/articles", "error": str(exc)}, status_code=500)


@app.get("/api/articles/{article_id}", response_class=JSONResponse)
def api_article_detail(article_id: int):
    try:
        from services.terminal_service import get_terminal_article_detail

        item = get_terminal_article_detail(article_id)
        if not item:
            return JSONResponse({"status": "error", "route": "/api/articles/detail", "error": "not found"}, status_code=404)
        return JSONResponse({"status": "ok", "item": item, "article": item})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/articles/detail", "error": str(exc)}, status_code=500)


@app.get("/api/theses/{thesis_key:path}/history", response_class=JSONResponse)
def api_thesis_history(thesis_key: str):
    try:
        from services.db_helpers import get_conn
        from config import DB_PATH
        from services.thesis_service import normalize_thesis_key

        conn = get_conn(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT confidence, recorded_at
            FROM thesis_confidence_log
            WHERE thesis_key = ?
            ORDER BY recorded_at DESC, id DESC
            LIMIT 20
            """,
            (normalize_thesis_key(thesis_key),),
        )
        history = [dict(row) for row in cur.fetchall()]
        conn.close()
        return JSONResponse({"status": "ok", "history": history})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/theses/history", "error": str(exc)}, status_code=500)


@app.get("/api/clusters", response_class=JSONResponse)
def api_clusters(limit: int = 30):
    try:
        from services.terminal_service import list_terminal_clusters

        return JSONResponse({"status": "ok", "clusters": list_terminal_clusters(limit=limit)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/clusters", "error": str(exc)}, status_code=500)


@app.get("/api/watchlist", response_class=JSONResponse)
def api_watchlist():
    try:
        from services.operator_state_service import get_operator_state

        state = get_operator_state()
        items = [
            {
                "id": item,
                "symbol": item,
                "asset_type": "",
                "thesis_key": "",
                "reason": "",
                "direction": "",
                "status": "active",
                "added_at": state.get("updated_at", 0),
            }
            for item in (state.get("watchlist", []) or [])
        ]
        return JSONResponse({"status": "ok", "items": items})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/watchlist", "error": str(exc)}, status_code=500)


@app.post("/api/watchlist", response_class=JSONResponse)
async def api_watchlist_add(request: Request):
    try:
        _mutation_guard(request)
        from services.operator_state_service import merge_operator_state

        payload = await request.json()
        symbol = str(payload.get("symbol", "") or payload.get("id", "") or "").strip().lower()
        if not symbol:
            return JSONResponse({"status": "error", "route": "/api/watchlist", "error": "symbol required"}, status_code=400)
        merge_operator_state({"watchlist": [symbol]})
        return JSONResponse({"status": "ok", "id": symbol})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/watchlist", "error": str(exc)}, status_code=500)


@app.delete("/api/watchlist/{watch_id:path}", response_class=JSONResponse)
def api_watchlist_remove(watch_id: str, request: Request):
    try:
        _mutation_guard(request)
        from services.operator_state_service import get_operator_state, update_operator_state

        state = get_operator_state()
        clean = str(watch_id or "").strip().lower()
        next_items = [item for item in (state.get("watchlist", []) or []) if str(item or "").strip().lower() != clean]
        update_operator_state({"watchlist": next_items})
        return JSONResponse({"status": "ok"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/watchlist/delete", "error": str(exc)}, status_code=500)


@app.get("/api/portfolio", response_class=JSONResponse)
def api_portfolio():
    try:
        from services.portfolio_service import PortfolioService

        summary = PortfolioService(str(DB_PATH)).get_pnl_summary()
        return JSONResponse({"status": "ok", "summary": summary})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/portfolio", "error": str(exc)}, status_code=500)


@app.post("/api/portfolio/positions", response_class=JSONResponse)
async def api_portfolio_add_position(request: Request):
    try:
        _mutation_guard(request)
        from services.portfolio_service import PortfolioService

        payload = await request.json()
        symbol = str(payload.get("symbol", "") or "").strip()
        if not symbol:
            return JSONResponse({"status": "error", "route": "/api/portfolio/positions", "error": "symbol required"}, status_code=400)
        position_id = PortfolioService(str(DB_PATH)).add_position(
            symbol=symbol,
            name=payload.get("name", ""),
            asset_type=payload.get("asset_type", "other"),
            direction=payload.get("direction", "long"),
            quantity=payload.get("quantity", 0),
            entry_price=payload.get("entry_price", 0),
            currency=payload.get("currency", "USD"),
            notes=payload.get("notes", ""),
            tags=payload.get("tags", []) if isinstance(payload.get("tags", []), list) else [],
        )
        return JSONResponse({"status": "ok", "id": position_id})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/portfolio/positions", "error": str(exc)}, status_code=500)


@app.delete("/api/portfolio/positions/{position_id:int}", response_class=JSONResponse)
def api_portfolio_close_position(position_id: int, request: Request):
    try:
        _mutation_guard(request)
        from datetime import datetime, timezone
        from services.db_helpers import get_conn

        conn = get_conn(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE portfolio_positions
            SET status='closed', updated_at=?
            WHERE id=?
            """,
            (datetime.now(timezone.utc).isoformat(), int(position_id)),
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "ok"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/portfolio/positions/close", "error": str(exc)}, status_code=500)


@app.get("/api/portfolio/threats", response_class=JSONResponse)
def api_portfolio_threats():
    try:
        from services.portfolio_service import PortfolioService

        threats = PortfolioService(str(DB_PATH)).get_thesis_threats()
        return JSONResponse({"status": "ok", "threats": threats})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/portfolio/threats", "error": str(exc)}, status_code=500)


@app.post("/api/portfolio/refresh-prices", response_class=JSONResponse)
def api_portfolio_refresh_prices(request: Request):
    try:
        _mutation_guard(request)
        from services.portfolio_service import PortfolioService

        result = PortfolioService(str(DB_PATH)).update_current_prices()
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/portfolio/refresh-prices", "error": str(exc)}, status_code=500)


@app.get("/api/debate/{thesis_key:path}", response_class=JSONResponse)
def api_debate(thesis_key: str):
    try:
        from services.debate_engine import DebateEngine

        llm = None
        if OPENAI_API_KEY:
            from services.llm_analyst import LLMAnalyst

            llm = LLMAnalyst(str(DB_PATH))
        debate = DebateEngine(str(DB_PATH), llm_analyst=llm).debate_thesis(thesis_key)
        return JSONResponse({"status": "ok", "debate": debate})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/debate", "error": str(exc)}, status_code=500)


@app.get("/api/export/theses.csv")
def api_export_theses_csv():
    try:
        from services.exporter import Exporter

        content = Exporter(str(DB_PATH)).export_theses_csv()
        return Response(content, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=geoclaw-theses.csv"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/export/theses.csv", "error": str(exc)}, status_code=500)


@app.get("/api/export/articles.csv")
def api_export_articles_csv(days: int = 7):
    try:
        from services.exporter import Exporter

        content = Exporter(str(DB_PATH)).export_articles_csv(int(days or 7))
        return Response(content, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=geoclaw-articles.csv"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/export/articles.csv", "error": str(exc)}, status_code=500)


@app.get("/api/export/briefing.txt")
def api_export_briefing_txt(id: int = 0):
    try:
        from services.exporter import Exporter

        content = Exporter(str(DB_PATH)).export_briefing_txt(int(id or 0) or None)
        return Response(content, media_type="text/plain", headers={"Content-Disposition": "attachment; filename=geoclaw-briefing.txt"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/export/briefing.txt", "error": str(exc)}, status_code=500)


@app.get("/api/export/full.json")
def api_export_full_json():
    try:
        from services.exporter import Exporter

        content = Exporter(str(DB_PATH)).export_full_json()
        return Response(content, media_type="application/json", headers={"Content-Disposition": "attachment; filename=geoclaw-export.json"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/export/full.json", "error": str(exc)}, status_code=500)


@app.get("/api/export/predictions.csv")
def api_export_predictions_csv():
    try:
        from services.exporter import Exporter

        content = Exporter(str(DB_PATH)).export_predictions_csv()
        return Response(content, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=geoclaw-predictions.csv"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/export/predictions.csv", "error": str(exc)}, status_code=500)


@app.post("/api/telegram/webhook", response_class=JSONResponse)
async def api_telegram_webhook(request: Request):
    try:
        from services.telegram_bot import TelegramBot

        update = await request.json()
        response = TelegramBot(str(DB_PATH)).process_incoming(update if isinstance(update, dict) else {})
        return JSONResponse({"status": "ok", "response": response})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/telegram/webhook", "error": str(exc)}, status_code=500)


@app.post("/api/telegram/send-brief", response_class=JSONResponse)
def api_telegram_send_brief(request: Request):
    try:
        _mutation_guard(request)
        from services.telegram_bot import TelegramBot

        success = TelegramBot(str(DB_PATH)).send_briefing()
        return JSONResponse({"status": "ok" if success else "error", "available": TelegramBot(str(DB_PATH)).available()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/telegram/send-brief", "error": str(exc)}, status_code=500)


@app.post("/api/telegram/test", response_class=JSONResponse)
def api_telegram_test(request: Request):
    try:
        _mutation_guard(request)
        from services.telegram_bot import TelegramBot

        bot = TelegramBot(str(DB_PATH))
        success = bot.send_message("✅ GeoClaw Telegram bot is connected.")
        return JSONResponse({"status": "ok" if success else "error", "available": bot.available()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/telegram/test", "error": str(exc)}, status_code=500)


@app.get("/api/contradictions", response_class=JSONResponse)
def api_contradictions(resolved: int = 0, limit: int = 20):
    try:
        from services.db_helpers import get_conn
        from config import DB_PATH

        conn = get_conn(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                ae.id,
                ae.article_id,
                ae.priority,
                ae.reason,
                ae.status,
                COALESCE(ae.resolved, 0) AS resolved,
                COALESCE(ae.resolution_note, '') AS resolution_note,
                COALESCE(ae.resolved_at, '') AS resolved_at,
                ae.created_at,
                ia.headline,
                ia.url,
                ia.source_name
            FROM alert_events ae
            LEFT JOIN ingested_articles ia ON ia.id = ae.article_id
            WHERE UPPER(COALESCE(ae.status, '')) LIKE '%CONTRADICTION%'
              AND COALESCE(ae.resolved, 0) = ?
            ORDER BY ae.created_at DESC, ae.id DESC
            LIMIT ?
            """,
            (1 if int(resolved or 0) else 0, int(limit or 20)),
        )
        items = [dict(row) for row in cur.fetchall()]
        conn.close()
        return JSONResponse({"status": "ok", "contradictions": items})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/contradictions", "error": str(exc)}, status_code=500)


@app.post("/api/contradictions/{contradiction_id}/resolve", response_class=JSONResponse)
async def api_contradiction_resolve(contradiction_id: int, request: Request):
    try:
        _mutation_guard(request)
        from services.db_helpers import get_conn
        from config import DB_PATH
        from services.goal_service import utc_now_iso

        payload = await request.json()
        note = str(payload.get("note", "") or "").strip()
        conn = get_conn(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_events
            SET resolved = 1,
                resolution_note = ?,
                resolved_at = ?,
                status = CASE
                    WHEN UPPER(COALESCE(status, '')) LIKE '%CONTRADICTION%' THEN 'RESOLVED_CONTRADICTION'
                    ELSE status
                END
            WHERE id = ?
            """,
            (note, utc_now_iso(), int(contradiction_id)),
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "ok"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/contradictions/resolve", "error": str(exc)}, status_code=500)


@app.get("/api/alerts", response_class=JSONResponse)
def api_alerts(limit: int = 30):
    try:
        from services.db_helpers import get_conn
        from config import DB_PATH

        conn = get_conn(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                COALESCE(alert_type, '') AS alert_type,
                COALESCE(title, '') AS title,
                COALESCE(body, '') AS body,
                created_at,
                COALESCE(resolved, 0) AS resolved
            FROM alert_events
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit or 30),),
        )
        items = [dict(row) for row in cur.fetchall()]
        conn.close()
        return JSONResponse({"status": "ok", "alerts": items})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/alerts", "error": str(exc)}, status_code=500)


@app.post("/api/alerts/{alert_id}/dismiss", response_class=JSONResponse)
def api_alert_dismiss(alert_id: int, request: Request):
    try:
        _mutation_guard(request)
        from services.db_helpers import get_conn
        from config import DB_PATH
        from services.goal_service import utc_now_iso

        conn = get_conn(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE alert_events
            SET resolved = 1,
                resolved_at = ?,
                status = CASE
                    WHEN COALESCE(status, '') = '' THEN 'resolved'
                    ELSE status
                END
            WHERE id = ?
            """,
            (utc_now_iso(), int(alert_id)),
        )
        conn.commit()
        conn.close()
        return JSONResponse({"status": "ok"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/alerts/dismiss", "error": str(exc)}, status_code=500)


@app.get("/api/alerts/unread/count", response_class=JSONResponse)
def api_alerts_unread_count():
    try:
        from services.db_helpers import get_conn
        from config import DB_PATH

        conn = get_conn(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM alert_events WHERE COALESCE(resolved, 0) = 0")
        count = int(cur.fetchone()[0] or 0)
        conn.close()
        return JSONResponse({"status": "ok", "count": count})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/alerts/unread/count", "error": str(exc)}, status_code=500)


@app.get("/api/search", response_class=JSONResponse)
def api_search(q: str = "", type: str = "all"):
    try:
        from services.terminal_service import search_terminal_records

        payload = search_terminal_records(q, limit=20)
        search_type = str(type or "all").strip().lower()
        if search_type == "articles":
            payload["theses"] = []
            payload["actions"] = []
        elif search_type == "theses":
            payload["articles"] = []
            payload["actions"] = []
        elif search_type == "actions":
            payload["articles"] = []
            payload["theses"] = []
        return JSONResponse({"status": "ok", **payload})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/search", "error": str(exc)}, status_code=500)


@app.get("/api/research/needs", response_class=JSONResponse)
def api_research_needs():
    try:
        from services.active_researcher import ActiveResearcher

        needs = ActiveResearcher(str(DB_PATH)).identify_research_needs()
        return JSONResponse({"status": "ok", "needs": needs, "count": len(needs)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/research/needs", "error": str(exc)}, status_code=500)


@app.post("/api/research/run", response_class=JSONResponse)
async def api_research_run(request: Request):
    try:
        _mutation_guard(request)
        from services.active_researcher import ActiveResearcher

        result = ActiveResearcher(str(DB_PATH)).run_full_research_cycle()
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/research/run", "error": str(exc)}, status_code=500)


@app.get("/api/research/log", response_class=JSONResponse)
def api_research_log():
    try:
        import sqlite3

        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT query, result_count, searched_at, triggered_by, thesis_key
            FROM web_search_log
            ORDER BY searched_at DESC, id DESC
            LIMIT 30
            """
        ).fetchall()
        conn.close()
        return JSONResponse({"status": "ok", "searches": [dict(row) for row in rows]})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/research/log", "error": str(exc)}, status_code=500)


@app.get("/api/research/articles", response_class=JSONResponse)
def api_research_articles():
    try:
        import sqlite3

        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT headline, source, thesis_key, is_reasoned, fetched_at, search_query, url
            FROM web_sourced_articles
            ORDER BY fetched_at DESC, id DESC
            LIMIT 30
            """
        ).fetchall()
        conn.close()
        return JSONResponse({"status": "ok", "articles": [dict(row) for row in rows]})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/research/articles", "error": str(exc)}, status_code=500)


@app.get("/api/dashboard/decision-view", response_class=JSONResponse)
def api_dashboard_decision_view():
    try:
        from services.dashboard_service import build_dashboard_decision_view

        return JSONResponse({"status": "ok", **build_dashboard_decision_view(str(DB_PATH))})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/dashboard/decision-view", "error": str(exc)}, status_code=500)


@app.get("/api/ask", response_class=JSONResponse)
def api_ask(q: str = ""):
    try:
        question = str(q or "").strip()
        if not question:
            return JSONResponse({"status": "error", "message": "q parameter required"}, status_code=400)
        result = _get_query_engine().ask(question)
        return JSONResponse({"status": "ok", **result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/ask", "error": str(exc)}, status_code=500)


@app.post("/api/ask", response_class=JSONResponse)
async def api_ask_post(request: Request):
    try:
        payload = await request.json()
        question = str((payload or {}).get("question", "") or "").strip()
        if not question:
            return JSONResponse({"status": "error", "message": "question required"}, status_code=400)
        result = _get_query_engine().ask(question)
        return JSONResponse({"status": "ok", **result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/ask:post", "error": str(exc)}, status_code=500)


@app.get("/api/ask/suggestions", response_class=JSONResponse)
def api_ask_suggestions():
    try:
        from services.query_engine import SUGGESTIONS

        return JSONResponse({"status": "ok", "suggestions": SUGGESTIONS})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/ask/suggestions", "error": str(exc)}, status_code=500)


@app.get("/api/events/stream")
async def api_events_stream(request: Request):
    from services.event_bus import get_bus
    import asyncio
    import json as jsonlib
    import queue as queue_module
    import time as time_module

    bus = get_bus()
    subscriber = bus.subscribe("*")

    async def generate():
        last_heartbeat = time_module.time()
        try:
            for event in bus.get_history(10):
                yield f"data: {jsonlib.dumps(event, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = subscriber.get(timeout=1.0)
                    yield f"data: {jsonlib.dumps(event, default=str)}\n\n"
                except queue_module.Empty:
                    now = time_module.time()
                    if now - last_heartbeat >= 15:
                        last_heartbeat = now
                        yield f"data: {jsonlib.dumps({'type': 'heartbeat', 'timestamp': now})}\n\n"
                await asyncio.sleep(0.05)
        finally:
            bus.unsubscribe(subscriber, "*")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/api/events/history", response_class=JSONResponse)
def api_events_history(limit: int = 50):
    try:
        from services.event_bus import get_bus

        events = get_bus().get_history(limit=int(limit or 50))
        return JSONResponse({"status": "ok", "events": events, "count": len(events)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/events/history", "error": str(exc)}, status_code=500)


@app.get("/api/events/types", response_class=JSONResponse)
def api_events_types():
    try:
        from services.event_bus import EVENT_TYPES

        return JSONResponse({"status": "ok", "types": EVENT_TYPES})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/events/types", "error": str(exc)}, status_code=500)


@app.get("/api/predictions", response_class=JSONResponse)
def api_predictions(outcome: str = "all", thesis_key: str = "", limit: int = 20):
    try:
        import sqlite3

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        params = []
        where = []
        clean_outcome = str(outcome or "all").strip().lower()
        clean_key = str(thesis_key or "").strip()
        if clean_outcome and clean_outcome != "all":
            where.append("LOWER(COALESCE(outcome, 'pending')) = ?")
            params.append(clean_outcome)
        if clean_key:
            where.append("LOWER(COALESCE(thesis_key, '')) = ?")
            params.append(clean_key.lower())
        sql = "SELECT * FROM thesis_predictions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY predicted_at DESC, id DESC LIMIT ?"
        params.append(int(limit or 20))
        rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
        conn.close()
        return JSONResponse({"status": "ok", "predictions": rows, "count": len(rows)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/predictions", "error": str(exc)}, status_code=500)


@app.get("/api/predictions/accuracy", response_class=JSONResponse)
def api_predictions_accuracy():
    try:
        from services.prediction_tracker import PredictionTracker

        report = PredictionTracker(str(DB_PATH)).get_accuracy_report()
        return JSONResponse({"status": "ok", "report": report})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/predictions/accuracy", "error": str(exc)}, status_code=500)


@app.get("/api/actions/execution-log", response_class=JSONResponse)
def api_actions_execution_log():
    try:
        import sqlite3

        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, action_type, status, metadata, thesis_key, created_at, approval_state, reason
            FROM agent_actions
            WHERE status IN ('completed', 'failed')
            ORDER BY created_at DESC, id DESC
            LIMIT 30
            """
        ).fetchall()
        conn.close()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except Exception:
                item["metadata"] = {}
            items.append(item)
        return JSONResponse({"status": "ok", "executions": items})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/actions/execution-log", "error": str(exc)}, status_code=500)


@app.post("/api/actions/execute-all-safe", response_class=JSONResponse)
async def api_actions_execute_all_safe(request: Request):
    try:
        _mutation_guard(request)
        from services.action_executor import ActionExecutor

        result = ActionExecutor(str(DB_PATH)).execute_auto_approved()
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/actions/execute-all-safe", "error": str(exc)}, status_code=500)


@app.get("/api/agent/status", response_class=JSONResponse)
def api_agent_status():
    try:
        from services.action_service import pending_action_count
        from services.health_service import get_health
        from services.scheduler_service import get_scheduler_status

        health_payload = get_health()
        return JSONResponse(
            {
                "status": "ok",
                "last_run_at": health_payload.get("last_run_time", ""),
                "thesis_count": int(health_payload.get("thesis_count", 0) or 0),
                "article_count": int(health_payload.get("article_count_24h", 0) or 0),
                "pending_actions": int(pending_action_count() or 0),
                "avg_confidence": float(health_payload.get("avg_confidence", 0.0) or 0.0),
                "scheduler": get_scheduler_status(),
            }
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/agent/status", "error": str(exc)}, status_code=500)


@app.get("/api/scheduler/status", response_class=JSONResponse)
def api_scheduler_status():
    try:
        from services.scheduler_service import get_scheduler_status

        return JSONResponse({"status": "ok", "scheduler": get_scheduler_status()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/scheduler/status", "error": str(exc)}, status_code=500)


@app.post("/api/scheduler/start", response_class=JSONResponse)
def api_scheduler_start(request: Request):
    try:
        _mutation_guard(request)
        from config import SCHEDULER_INTERVAL_MINUTES
        from services.scheduler_service import start_scheduler

        started = start_scheduler(interval_minutes=SCHEDULER_INTERVAL_MINUTES)
        return JSONResponse({"status": "started" if started else "already_running"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/scheduler/start", "error": str(exc)}, status_code=500)


@app.post("/api/scheduler/stop", response_class=JSONResponse)
def api_scheduler_stop(request: Request):
    try:
        _mutation_guard(request)
        from services.scheduler_service import stop_scheduler

        stop_scheduler()
        return JSONResponse({"status": "stopped"})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/scheduler/stop", "error": str(exc)}, status_code=500)


@app.get("/api/briefing/history", response_class=JSONResponse)
def api_briefing_history():
    try:
        from services.briefing_service import list_briefings

        items = list_briefings(limit=20)
        return JSONResponse({"status": "ok", "items": items, "briefings": items})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/briefing/history", "error": str(exc)}, status_code=500)


@app.get("/api/briefing/{briefing_id}", response_class=JSONResponse)
def api_briefing_detail(briefing_id: int):
    try:
        from services.briefing_service import list_briefings

        item = next((entry for entry in list_briefings(limit=100) if int(entry.get("id", 0) or 0) == int(briefing_id)), None)
        if not item:
            return JSONResponse({"status": "error", "route": "/api/briefing/detail", "error": "not found"}, status_code=404)
        return JSONResponse({"status": "ok", "item": item})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/briefing/detail", "error": str(exc)}, status_code=500)


@app.get("/api/prices", response_class=JSONResponse)
def api_prices(symbols: str = ""):
    try:
        from services.cache_service import cache_get, cache_set
        from services.price_feed import PriceFeed
        from services.goal_service import utc_now_iso

        cache_key = f"api_prices:{str(symbols or '').strip()}"
        cached = cache_get(cache_key)
        if cached is not None:
            return JSONResponse(cached)
        pf = PriceFeed()
        requested = [item.strip() for item in str(symbols or "").split(",") if item.strip()]
        snapshot = pf.get_snapshot(requested or None)
        payload = {"status": "ok", "prices": list(snapshot.values()), "captured_at": utc_now_iso()}
        cache_set(cache_key, payload, 60)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/prices", "error": str(exc)}, status_code=500)


@app.get("/api/prices/{thesis_key:path}", response_class=JSONResponse)
def api_thesis_prices(thesis_key: str):
    try:
        from services.price_feed import PriceFeed

        prices = PriceFeed().get_thesis_relevant_prices(thesis_key)
        return JSONResponse({"status": "ok", "thesis_key": thesis_key, "prices": prices})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/prices/thesis", "error": str(exc)}, status_code=500)


@app.get("/api/intelligence/narratives", response_class=JSONResponse)
def api_intelligence_narratives():
    try:
        from services.cache_service import cache_get, cache_set
        from services.pattern_detector import PatternDetector
        from services.terminal_service import get_terminal_theses

        cached = cache_get("intelligence_narratives")
        if cached is not None:
            return JSONResponse(cached)
        narratives = PatternDetector().detect_narrative_cluster(get_terminal_theses(limit=500))
        payload = {"status": "ok", "narratives": narratives}
        cache_set("intelligence_narratives", payload, 300)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/intelligence/narratives", "error": str(exc)}, status_code=500)


@app.get("/api/intelligence/momentum", response_class=JSONResponse)
def api_intelligence_momentum():
    try:
        from services.pattern_detector import PatternDetector
        from config import DB_PATH

        shifts = PatternDetector().detect_momentum_shifts(str(DB_PATH))
        return JSONResponse({"status": "ok", "shifts": shifts})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/intelligence/momentum", "error": str(exc)}, status_code=500)


@app.get("/api/intelligence/regime", response_class=JSONResponse)
def api_intelligence_regime():
    try:
        from services.cache_service import cache_get, cache_set
        from config import DB_PATH
        from services.db_helpers import get_conn
        from services.pattern_detector import PatternDetector
        from services.price_feed import PriceFeed
        from services.terminal_service import get_terminal_theses

        cached = cache_get("intelligence_regime")
        if cached is not None:
            return JSONResponse(cached)
        theses = get_terminal_theses(limit=500)
        snapshot = PriceFeed().get_snapshot(["^VIX", "GC=F", "CL=F", "SPY"])
        if not snapshot:
            conn = get_conn(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ps.symbol, ps.price
                FROM price_snapshots ps
                JOIN (
                    SELECT symbol, MAX(captured_at) AS latest_at
                    FROM price_snapshots
                    GROUP BY symbol
                ) latest
                  ON latest.symbol = ps.symbol
                 AND latest.latest_at = ps.captured_at
                """
            )
            snapshot = {row["symbol"]: {"symbol": row["symbol"], "price": row["price"]} for row in cur.fetchall()}
            conn.close()
        regime = PatternDetector().compute_market_regime(theses, snapshot)
        payload = {"status": "ok", "regime": regime}
        cache_set("intelligence_regime", payload, 300)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/intelligence/regime", "error": str(exc)}, status_code=500)


@app.get("/api/intelligence/calibration", response_class=JSONResponse)
def api_intelligence_calibration():
    try:
        from config import DB_PATH
        from services.self_calibrator import SelfCalibrator

        calibration = SelfCalibrator().evaluate_past_theses(str(DB_PATH))
        return JSONResponse({"status": "ok", "calibration": calibration})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/intelligence/calibration", "error": str(exc)}, status_code=500)


@app.get("/api/intelligence/duplicates", response_class=JSONResponse)
def api_intelligence_duplicates():
    try:
        from services.thesis_deduplicator import ThesisDeduplicator

        pairs = ThesisDeduplicator().find_duplicates(str(DB_PATH))
        return JSONResponse({"status": "ok", "pairs": pairs, "count": len(pairs)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/intelligence/duplicates", "error": str(exc)}, status_code=500)


@app.post("/api/intelligence/merge-duplicates", response_class=JSONResponse)
def api_intelligence_merge_duplicates(request: Request):
    try:
        _mutation_guard(request)
        from services.thesis_deduplicator import ThesisDeduplicator

        result = ThesisDeduplicator().merge_duplicates(str(DB_PATH), dry_run=False)
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/intelligence/merge-duplicates", "error": str(exc)}, status_code=500)


@app.get("/api/rules/learned", response_class=JSONResponse)
def api_rules_learned():
    try:
        from services.rule_learner import RuleLearner

        rules = RuleLearner(str(DB_PATH)).get_active_learned_rules()
        return JSONResponse({"status": "ok", "rules": rules, "count": len(rules)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/rules/learned", "error": str(exc)}, status_code=500)


@app.get("/api/rules/all", response_class=JSONResponse)
def api_rules_all():
    try:
        import sqlite3

        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM learned_rules ORDER BY accuracy_pct DESC, verification_count DESC, id DESC").fetchall()
        conn.close()
        return JSONResponse({"status": "ok", "rules": [dict(row) for row in rows]})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/rules/all", "error": str(exc)}, status_code=500)


@app.post("/api/rules/learn-now", response_class=JSONResponse)
async def api_rules_learn_now(request: Request):
    try:
        _mutation_guard(request)
        from services.rule_learner import RuleLearner

        result = RuleLearner(str(DB_PATH)).write_learned_rules()
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/rules/learn-now", "error": str(exc)}, status_code=500)


@app.get("/api/memory", response_class=JSONResponse)
def api_memory(type: str = "all", subject: str = ""):
    try:
        from services.agent_memory import AgentMemory

        memories = AgentMemory(str(DB_PATH)).recall(memory_type=type, subject=subject, limit=20)
        return JSONResponse({"status": "ok", "memories": memories})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/memory", "error": str(exc)}, status_code=500)


@app.get("/api/memory/context/{thesis_key:path}", response_class=JSONResponse)
def api_memory_context(thesis_key: str):
    try:
        from services.agent_memory import AgentMemory

        context = AgentMemory(str(DB_PATH)).get_context_for_thesis(thesis_key)
        return JSONResponse({"status": "ok", "context": context})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/memory/context", "error": str(exc)}, status_code=500)


@app.get("/api/goals/current", response_class=JSONResponse)
def api_goals_current():
    try:
        from services.agent_loop_service import list_journal

        latest = (list_journal(limit=1) or [{}])[0]
        metrics = latest.get("metrics", {}) or {}
        return JSONResponse({"status": "ok", "goals": list(metrics.get("run_goals", []) or []), "run_id": latest.get("run_id", 0) or 0})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/goals/current", "error": str(exc)}, status_code=500)


@app.get("/api/sources/reliability", response_class=JSONResponse)
def api_sources_reliability():
    try:
        from services.source_learner import SourceLearner

        sources = SourceLearner(str(DB_PATH)).get_leaderboard()
        return JSONResponse({"status": "ok", "sources": sources})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/sources/reliability", "error": str(exc)}, status_code=500)


@app.post("/api/sources/learn", response_class=JSONResponse)
def api_sources_learn(request: Request):
    try:
        _mutation_guard(request)
        from services.source_learner import SourceLearner

        result = SourceLearner(str(DB_PATH)).update_from_predictions()
        return JSONResponse({"status": "ok", "result": result})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/sources/learn", "error": str(exc)}, status_code=500)


@app.get("/api/calendar", response_class=JSONResponse)
def api_calendar(days: int = 7):
    try:
        from services.macro_calendar import MacroCalendar

        events = MacroCalendar().get_upcoming(int(days or 7))
        return JSONResponse({"status": "ok", "events": events, "count": len(events)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/calendar", "error": str(exc)}, status_code=500)


@app.get("/api/calendar/today", response_class=JSONResponse)
def api_calendar_today():
    try:
        from services.macro_calendar import MacroCalendar

        events = MacroCalendar().get_today_events()
        return JSONResponse({"status": "ok", "events": events})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/calendar/today", "error": str(exc)}, status_code=500)


@app.get("/api/calendar/high-impact", response_class=JSONResponse)
def api_calendar_high_impact():
    try:
        from services.macro_calendar import MacroCalendar

        events = MacroCalendar().get_high_impact_upcoming()
        return JSONResponse({"status": "ok", "events": events})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/calendar/high-impact", "error": str(exc)}, status_code=500)


@app.get("/api/sentiment/current", response_class=JSONResponse)
def api_sentiment_current():
    try:
        from services.sentiment_index import SentimentIndex

        index = SentimentIndex().compute(str(DB_PATH))
        return JSONResponse({"status": "ok", "index": index})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/sentiment/current", "error": str(exc)}, status_code=500)


@app.get("/api/sentiment/history", response_class=JSONResponse)
def api_sentiment_history(days: int = 30):
    try:
        from services.sentiment_index import SentimentIndex

        history = SentimentIndex().get_history(str(DB_PATH), int(days or 30))
        return JSONResponse({"status": "ok", "history": history})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/sentiment/history", "error": str(exc)}, status_code=500)


@app.get("/api/geo-risk", response_class=JSONResponse)
def api_geo_risk():
    try:
        from services.geo_risk import GeoRisk

        regions = GeoRisk().compute_region_risk(str(DB_PATH))
        return JSONResponse({"status": "ok", "regions": regions, "computed_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/geo-risk", "error": str(exc)}, status_code=500)


@app.get("/api/sectors", response_class=JSONResponse)
def api_sectors():
    try:
        from services.sector_rotation import SectorRotation

        sectors = SectorRotation().compute_signals(str(DB_PATH))
        return JSONResponse({"status": "ok", "sectors": sectors, "computed_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/sectors", "error": str(exc)}, status_code=500)


@app.get("/api/correlations", response_class=JSONResponse)
def api_correlations(hours: int = 48, symbols: str = ""):
    try:
        from services.correlation_engine import CorrelationEngine

        symbol_list = [item.strip() for item in str(symbols or "").split(",") if item.strip()]
        correlations = CorrelationEngine().compute_correlations(str(DB_PATH), symbol_list or None, int(hours or 48))
        return JSONResponse({"status": "ok", "correlations": correlations})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/correlations", "error": str(exc)}, status_code=500)


@app.get("/api/anomalies", response_class=JSONResponse)
def api_anomalies():
    try:
        from services.anomaly_detector import AnomalyDetector

        anomalies = AnomalyDetector().detect_all(str(DB_PATH))
        return JSONResponse({"status": "ok", "anomalies": anomalies, "count": len(anomalies)})
    except Exception as exc:
        return JSONResponse({"status": "error", "route": "/api/anomalies", "error": str(exc)}, status_code=500)


@app.get("/source-health", response_class=JSONResponse)
def source_health():
    try:
        from services.health_service import get_source_health
        payload = get_source_health()
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/source-health",
                "error": str(exc),
            },
            status_code=500,
        )




# === GEOCLAW WHAT CHANGED ROUTES v1 ===
@app.get("/what-changed", response_class=JSONResponse)
def what_changed():
    try:
        from services.change_service import get_what_changed
        payload = get_what_changed(window_minutes=30, limit=8)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/what-changed",
                "error": str(exc),
            },
            status_code=500,
        )


# === GEOCLAW PROVIDER SELF TEST ROUTES v1 ===
@app.get("/provider-self-test", response_class=JSONResponse)
def provider_self_test():
    try:
        from services.provider_self_test_service import run_provider_self_test
        payload = run_provider_self_test(force=True)
        return JSONResponse({"status": "ok", "result": payload})
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "route": "/provider-self-test",
                "error": str(exc),
            },
            status_code=500,
        )


# === GEOCLAW PROVIDER SELF TEST BOOT v1 ===
try:
    from services.provider_self_test_service import run_provider_self_test
    run_provider_self_test(force=False)
except Exception as exc:
    logger.warning("provider self-test boot failed: %s", exc)
