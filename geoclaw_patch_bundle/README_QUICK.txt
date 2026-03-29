GEOCLAW PATCH BUNDLE

What this does
- Patches main.py
- Rebuilds /saved-news-view into terminal UI
- Adds /terminal alias route
- Adds sentiment, asset, alert tagging helpers
- Keeps SAVE NOW / LIVE NEWS / SAVED SEARCH buttons

Run inside your project:
1) cd ~/GeoClaw
2) source venv/bin/activate
3) cp main.py main_before_patch.py
4) cp /path/to/gc_apply_terminal_patch.py .
5) cp /path/to/gc_verify_patch.py .
6) python3 gc_apply_terminal_patch.py
7) python3 gc_verify_patch.py
8) python3 -m py_compile main.py db.py fetcher.py
9) pkill -f "uvicorn main:app" || true
10) sleep 1
11) uvicorn main:app --host 127.0.0.1 --port 8000

Then open:
- http://127.0.0.1:8000/saved-news-view
- http://127.0.0.1:8000/terminal

Notes
- This bundle only patches main.py.
- It does not change db.py or fetcher.py.
- Reuters / FT / Bloomberg feed additions are still unverified and should be patched separately.
