import re

with open("main.py", "r") as f:
    text = f.read()

old = re.search(
    r'@app\.get\("/saved-news-view".*?(?=@app\.get)',
    text, re.DOTALL
)
if not old:
    print("ERROR: could not find /saved-news-view route")
    exit(1)

new_route = '''@app.get("/saved-news-view", response_class=HTMLResponse)
def saved_news_view(q: str = "", source: str = "all"):
    from db import get_connection
    import html
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, headline, source, published_at, url FROM articles ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    articles = [{"id": r[0], "headline": r[1], "source": r[2], "published_at": r[3], "url": r[4]} for r in rows]

    source_map = {"bbc": "BBC RSS", "lemonde": "Le Monde International"}
    selected_source = source if source in source_map else "all"
    q_lower = q.strip().lower()

    filtered = []
    for a in articles:
        if q_lower and q_lower not in a["headline"].lower():
            continue
        src = a.get("source", "")
        if selected_source != "all" and src != source_map[selected_source]:
            continue
        filtered.append(a)

    def filter_btn(label, val):
        active = selected_source == val
        bg = "#60a5fa" if active else "#1f2937"
        q_param = f"&q={html.escape(q)}" if q else ""
        return f\'\'\'<a href="/saved-news-view?source={val}{q_param}" style="display:inline-block;padding:10px 18px;border-radius:10px;background:{bg};color:white;text-decoration:none;font-weight:700;margin-right:8px;">{label}</a>\'\'\'

    cards = ""
    for a in filtered:
        cards += f"""
        <div style="background:#16181d;padding:24px;border-radius:16px;margin-bottom:18px;border:1px solid #2d3748;">
            <h2 style="margin:0 0 8px 0;color:white;font-size:20px;">{html.escape(a[\'headline\'])}</h2>
            <p style="color:#9ca3af;margin:0 0 12px 0;font-size:14px;">
                {html.escape(a[\'source\'])} &nbsp;|&nbsp; {html.escape(str(a[\'published_at\'] or \'\'))}
            </p>
            <a href="{html.escape(a[\'url\'])}" target="_blank" style="color:#60a5fa;text-decoration:none;font-weight:600;">Open full article ↗</a>
        </div>"""

    if not cards:
        cards = "<div style=\'background:#16181d;border-radius:16px;padding:22px;\'><p style=\'color:#9ca3af;\'>No articles found.</p></div>"

    q_escaped = html.escape(q)
    total = len(articles)
    shown = len(filtered)

    return HTMLResponse(f"""
<html>
<head><title>Saved News</title></head>
<body style="background:#0f1117;color:white;font-family:Arial,sans-serif;padding:30px;max-width:900px;margin:0 auto;">
    <h1 style="font-size:40px;margin:0 0 6px 0;">📰 Saved News</h1>
    <p style="color:#64748b;margin:0 0 24px 0;">{shown} of {total} articles</p>

    <form onsubmit="event.preventDefault();const q=document.getElementById(\'sq\').value.trim();window.location=\'/saved-news-view?q=\'+encodeURIComponent(q)+\'&source={selected_source}\';" style="margin-bottom:20px;">
        <input id="sq" type="text" value="{q_escaped}" placeholder="Search headlines..."
            style="padding:12px 16px;width:320px;font-size:16px;border-radius:8px;border:1px solid #374151;background:#1f2937;color:white;margin-right:8px;">
        <button type="submit" style="padding:12px 20px;background:#60a5fa;color:white;border:none;border-radius:8px;font-size:16px;cursor:pointer;font-weight:700;">Search</button>
        <a href="/saved-news-view" style="margin-left:12px;color:#9ca3af;font-size:14px;">Clear</a>
    </form>

    <div style="margin-bottom:24px;">
        {filter_btn("All", "all")}
        {filter_btn("BBC", "bbc")}
        {filter_btn("Le Monde", "lemonde")}
    </div>

    <div style="margin-bottom:20px;">
        <a href="/save-live-now" style="padding:10px 18px;background:#10b981;color:white;border-radius:8px;text-decoration:none;font-weight:700;">⬇ Save live feed now</a>
        &nbsp;
        <a href="/live-news-view" style="padding:10px 18px;background:#1f2937;color:white;border-radius:8px;text-decoration:none;font-weight:700;">📡 View live feed</a>
    </div>

    {cards}
</body>
</html>""")

'''

text = text[:old.start()] + new_route + "\n" + text[old.end():]

with open("main.py", "w") as f:
    f.write(text)

print("Done. Patch applied.")

