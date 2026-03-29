#!/usr/bin/env python3
from pathlib import Path
import re
import sys

main = Path("main.py")
if not main.exists():
    print("main.py not found")
    sys.exit(1)

text = main.read_text()
markers = [
    "GEOCLAW TRADING TERMINAL",
    "/terminal",
    "SAVE NOW",
    "Bullish",
    "Bearish",
    "Neutral",
    "#0a0c10",
]
found = [m for m in markers if m in text]
print("Markers found:", ", ".join(found) if found else "none")

route_ok = bool(
    re.search(
        r'@app\.get\("/terminal", response_class=HTMLResponse\)\s*@app\.get\("/saved-news-view", response_class=HTMLResponse\)\s*def saved_news_view',
        text,
        flags=re.S,
    )
)
print("Route alias present:", route_ok)
