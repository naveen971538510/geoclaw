#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
import sys

ROOT = Path.cwd()
MAIN = ROOT / "main.py"


def ensure_import(text: str, line: str) -> str:
    if line in text:
        return text
    parts = text.splitlines()
    insert_at = 0
    for i, p in enumerate(parts):
        if p.startswith("import ") or p.startswith("from "):
            insert_at = i + 1
    parts.insert(insert_at, line)
    return "\n".join(parts) + ("\n" if text.endswith("\n") else "")


def build_helpers() -> str:
    return r'''
# === GEOCLAW TERMINAL HELPERS START ===
import re as _gc_re
from datetime import datetime as _gc_datetime, timezone as _gc_timezone
from html import escape as _gc_escape

def _gc_get(row, key, default=""):
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return default

def _gc_parse_dt(value):
    if not value:
        return None
    text = str(value).strip()
    for candidate in (
        text,
        text.replace("Z", "+00:00"),
        text.replace(" ", "T"),
        text.replace(" UTC", "+00:00"),
    ):
        try:
            dt = _gc_datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_gc_timezone.utc)
            return dt.astimezone(_gc_timezone.utc)
        except Exception:
            pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = _gc_datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_gc_timezone.utc)
            return dt.astimezone(_gc_timezone.utc)
        except Exception:
            pass
    return None

def _gc_relative_time(value):
    dt = _gc_parse_dt(value)
    if dt is None:
        return "time n/a"
    now = _gc_datetime.now(_gc_timezone.utc)
    mins = max(0, int((now - dt).total_seconds() // 60))
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} mins ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs} hrs ago"
    days = hrs // 24
    return f"{days} days ago"

def _gc_sentiment(headline):
    h = str(headline).lower()
    bull_words = [
        "rally","surge","jump","gain","beat","growth","record high","rise","rises",
        "up","optimism","boost","bullish","strong","expand","rebound","recover",
        "cooling inflation","rate cut","soft landing","deal reached","stimulus"
    ]
    bear_words = [
        "fall","drops","drop","slump","crash","fear","war","sanctions","cuts jobs",
        "miss","weak","bearish","inflation shock","rate hike","recession","selloff",
        "risk-off","shutdown","defaults","default","collapse","plunge","down"
    ]
    bull = sum(1 for w in bull_words if w in h)
    bear = sum(1 for w in bear_words if w in h)
    if bull > bear:
        return "Bullish"
    if bear > bull:
        return "Bearish"
    return "Neutral"

def _gc_asset(headline):
    h = str(headline).lower()
    checks = [
        ("OIL", ["oil","brent","wti","opec","crude"]),
        ("GOLD", ["gold","bullion","xau"]),
        ("FOREX", ["forex","currency","currencies","dollar","usd","gbp","eur","yen","jpy","fx","sterling"]),
        ("RATES", ["fed","ecb","boe","interest rate","rates","bond yield","yields","treasury"]),
        ("STOCKS", ["stock","stocks","equity","equities","shares","nasdaq","s&p","dow","ftse","nikkei","index"]),
    ]
    found = [name for name, words in checks if any(w in h for w in words)]
    return found or ["GENERAL"]

def _gc_alerts(headline):
    h = str(headline).lower()
    words = [
        "shutdown","record high","record low","rate hike","rate cut","war","sanctions",
        "crash","selloff","surge","plunge","default","recession","emergency","opec",
        "tariff","strike","inflation","stimulus"
    ]
    return [w.upper() for w in words if w in h]

def _gc_source_label(source):
    s = str(source or "").strip()
    if not s:
        return "Unknown"
    low = s.lower()
    if "bbc" in low:
        return "BBC"
    if "le monde" in low:
        return "Le Monde"
    if "reuters" in low:
        return "Reuters"
    if "financial times" in low or low == "ft":
        return "FT"
    if "bloomberg" in low:
        return "Bloomberg"
    return s[:24]

def _gc_row_to_view(row):
    headline = str(_gc_get(row, "headline", "") or "")
    source = _gc_source_label(_gc_get(row, "source", ""))
    url = str(_gc_get(row, "url", "#") or "#")
    published_at = _gc_get(row, "published_at", "")
    signal = _gc_sentiment(headline)
    assets = _gc_asset(headline)
    alerts = _gc_alerts(headline)
    return {
        "headline": headline,
        "source": source,
        "url": url,
        "published_at": str(published_at or ""),
        "relative_time": _gc_relative_time(published_at),
        "signal": signal,
        "assets": assets,
        "alerts": alerts,
    }
# === GEOCLAW TERMINAL HELPERS END ===
'''.strip() + "\n"


def build_route() -> str:
    return r'''
@app.get("/terminal", response_class=HTMLResponse)
@app.get("/saved-news-view", response_class=HTMLResponse)
def saved_news_view():
    rows = get_saved_articles()
    items = [_gc_row_to_view(r) for r in rows]
    bull = sum(1 for x in items if x["signal"] == "Bullish")
    bear = sum(1 for x in items if x["signal"] == "Bearish")
    neutral = sum(1 for x in items if x["signal"] == "Neutral")
    alerts_total = sum(1 for x in items if x["alerts"])

    def render_card(x):
        signal_class = x["signal"].lower()
        asset_badges = "".join(
            f'<span class="badge asset">{_gc_escape(a)}</span>' for a in x["assets"]
        )
        alert_badges = "".join(
            f'<span class="badge alert">{_gc_escape(a)}</span>' for a in x["alerts"]
        )
        search_blob = (x["headline"] + " " + x["source"] + " " + " ".join(x["assets"])).lower()
        assets_joined = " ".join(x["assets"])
        return f"""
        <article class=\"card\" data-signal=\"{_gc_escape(x['signal'])}\" data-source=\"{_gc_escape(x['source'])}\" data-assets=\"{_gc_escape(assets_joined)}\" data-search=\"{_gc_escape(search_blob)}\">
          <div class=\"row top\">
            <div class=\"leftline\">
              <span class=\"signal {signal_class}\">{_gc_escape(x['signal'])}</span>
              <span class=\"source\">{_gc_escape(x['source'])}</span>
              {asset_badges}
              {alert_badges}
            </div>
            <div class=\"time\">{_gc_escape(x['relative_time'])}</div>
          </div>
          <a class=\"headline\" href=\"{_gc_escape(x['url'])}\" target=\"_blank\" rel=\"noopener noreferrer\">{_gc_escape(x['headline'])}</a>
        </article>
        """

    cards = "".join(render_card(x) for x in items) or '<div class="empty">No saved articles yet.</div>'

    sources = sorted({x["source"] for x in items})
    assets = sorted({a for x in items for a in x["assets"]})
    source_options = ''.join(f'<option value="{_gc_escape(s)}">{_gc_escape(s)}</option>' for s in sources)
    asset_options = ''.join(f'<option value="{_gc_escape(a)}">{_gc_escape(a)}</option>' for a in assets)

    return f"""
    <!doctype html>
    <html>
    <head>
      <meta charset=\"utf-8\">
      <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
      <title>GeoClaw Terminal</title>
      <style>
        :root {{
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
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        }}
        .wrap {{ max-width: 1180px; margin: 0 auto; padding: 20px; }}
        .titlebar {{
          display: flex; gap: 12px; align-items: center; justify-content: space-between;
          margin-bottom: 14px;
        }}
        .title {{ font-size: 22px; font-weight: 800; letter-spacing: .04em; }}
        .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        .btn {{
          display: inline-flex; align-items: center; justify-content: center;
          padding: 10px 14px; border: 1px solid var(--line); border-radius: 10px;
          background: var(--panel); color: var(--text); text-decoration: none; cursor: pointer;
        }}
        .btn.primary {{ border-color: #1f6feb; background: #11233f; }}
        .summary {{
          display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px;
          margin: 0 0 14px 0;
        }}
        .stat {{
          background: linear-gradient(180deg, var(--panel), var(--panel2));
          border: 1px solid var(--line);
          border-radius: 14px;
          padding: 14px;
        }}
        .stat .label {{ color: var(--muted); font-size: 12px; }}
        .stat .value {{ font-size: 24px; font-weight: 800; margin-top: 6px; }}
        .filters {{
          display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 10px;
          margin-bottom: 14px;
        }}
        .input, .select {{
          width: 100%; padding: 12px 13px; background: var(--panel);
          color: var(--text); border: 1px solid var(--line); border-radius: 10px;
          outline: none;
        }}
        .cards {{ display: grid; gap: 12px; }}
        .card {{
          background: linear-gradient(180deg, var(--panel), var(--panel2));
          border: 1px solid var(--line);
          border-radius: 14px;
          padding: 14px;
        }}
        .row.top {{
          display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px;
        }}
        .leftline {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
        .signal {{
          padding: 5px 9px; border-radius: 999px; font-size: 12px; font-weight: 800;
          border: 1px solid transparent;
        }}
        .signal.bullish {{ color: var(--green); border-color: rgba(25,195,125,.35); background: rgba(25,195,125,.10); }}
        .signal.bearish {{ color: var(--red); border-color: rgba(255,95,86,.35); background: rgba(255,95,86,.10); }}
        .signal.neutral {{ color: var(--yellow); border-color: rgba(255,209,102,.35); background: rgba(255,209,102,.10); }}
        .badge {{
          padding: 4px 8px; border-radius: 999px; font-size: 11px; font-weight: 700;
          border: 1px solid var(--line); color: var(--muted);
        }}
        .badge.asset {{ color: var(--blue); }}
        .badge.alert {{ color: var(--red); }}
        .source {{ color: var(--muted); font-size: 12px; }}
        .time {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
        .headline {{
          color: var(--text); text-decoration: none; font-size: 16px; font-weight: 700; line-height: 1.45;
        }}
        .headline:hover {{ text-decoration: underline; }}
        .footer {{ color: var(--muted); font-size: 12px; margin-top: 16px; }}
        .empty {{
          padding: 24px; border: 1px dashed var(--line); border-radius: 14px; color: var(--muted);
          text-align: center; background: var(--panel);
        }}
        @media (max-width: 860px) {{
          .summary {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
          .filters {{ grid-template-columns: 1fr; }}
          .titlebar {{ flex-direction: column; align-items: stretch; }}
        }}
      </style>
    </head>
    <body>
      <div class=\"wrap\">
        <div class=\"titlebar\">
          <div>
            <div class=\"title\">GEOCLAW TRADING TERMINAL</div>
            <div class=\"footer\">Saved market headlines with sentiment, asset tagging, alerts, filters, and search</div>
          </div>
          <div class=\"actions\">
            <a class=\"btn primary\" href=\"/save-live-now\">SAVE NOW</a>
            <a class=\"btn\" href=\"/live-news-view\">LIVE NEWS</a>
            <a class=\"btn\" href=\"/saved-search-home\">SAVED SEARCH</a>
          </div>
        </div>

        <section class=\"summary\">
          <div class=\"stat\"><div class=\"label\">Bullish</div><div class=\"value\">{bull}</div></div>
          <div class=\"stat\"><div class=\"label\">Bearish</div><div class=\"value\">{bear}</div></div>
          <div class=\"stat\"><div class=\"label\">Neutral</div><div class=\"value\">{neutral}</div></div>
          <div class=\"stat\"><div class=\"label\">Alerts</div><div class=\"value\">{alerts_total}</div></div>
        </section>

        <section class=\"filters\">
          <input id=\"q\" class=\"input\" placeholder=\"Search headline, source, asset...\" />
          <select id=\"signal\" class=\"select\">
            <option value=\"\">All signals</option>
            <option value=\"Bullish\">Bullish</option>
            <option value=\"Bearish\">Bearish</option>
            <option value=\"Neutral\">Neutral</option>
          </select>
          <select id=\"source\" class=\"select\">
            <option value=\"\">All sources</option>
            {source_options}
          </select>
          <select id=\"asset\" class=\"select\">
            <option value=\"\">All assets</option>
            {asset_options}
          </select>
        </section>

        <section id=\"cards\" class=\"cards\">{cards}</section>
        <div class=\"footer\">Route aliases: /saved-news-view and /terminal</div>
      </div>

      <script>
        const q = document.getElementById('q');
        const signal = document.getElementById('signal');
        const source = document.getElementById('source');
        const asset = document.getElementById('asset');
        const cards = Array.from(document.querySelectorAll('.card'));

        function applyFilters() {{
          const qv = (q.value || '').trim().toLowerCase();
          const sv = signal.value;
          const srcv = source.value;
          const av = asset.value;

          for (const card of cards) {{
            const text = card.dataset.search || '';
            const cSignal = card.dataset.signal || '';
            const cSource = card.dataset.source || '';
            const cAssets = card.dataset.assets || '';
            const okQ = !qv || text.includes(qv);
            const okS = !sv || cSignal === sv;
            const okSrc = !srcv || cSource === srcv;
            const okA = !av || cAssets.split(' ').includes(av);
            card.style.display = (okQ && okS && okSrc && okA) ? '' : 'none';
          }}
        }}

        q.addEventListener('input', applyFilters);
        signal.addEventListener('change', applyFilters);
        source.addEventListener('change', applyFilters);
        asset.addEventListener('change', applyFilters);
      </script>
    </body>
    </html>
    """
'''.strip() + "\n"


def main():
    if not MAIN.exists():
        print("ERROR: main.py not found in current directory")
        sys.exit(1)

    text = MAIN.read_text()
    backup = ROOT / "main_before_patch.py"
    shutil.copy2(MAIN, backup)
    print(f"Backup created: {backup}")

    text = ensure_import(text, "from fastapi.responses import HTMLResponse")

    if "# === GEOCLAW TERMINAL HELPERS START ===" not in text:
        m = re.search(r'^\s*@app\.(get|post|put|delete)\(', text, flags=re.M)
        if not m:
            print("ERROR: no route decorators found to anchor helper insertion")
            sys.exit(1)
        text = text[:m.start()] + build_helpers() + "\n" + text[m.start():]
        print("Inserted terminal helpers")
    else:
        print("Helpers already present")

    route_pat = re.compile(
        r'@app\.get\("/saved-news-view",\s*response_class=HTMLResponse\)\s*'
        r'def\s+saved_news_view\s*\([^)]*\)\s*:\s*.*?(?=\n@app\.|\Z)',
        flags=re.S,
    )

    if route_pat.search(text):
        text = route_pat.sub(build_route(), text, count=1)
        print("Replaced saved_news_view route")
    else:
        text = text.rstrip() + "\n\n" + build_route() + "\n"
        print("Appended saved_news_view route")

    MAIN.write_text(text)
    print("main.py patched")


if __name__ == "__main__":
    main()
