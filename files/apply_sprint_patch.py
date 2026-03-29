#!/usr/bin/env python3
"""
apply_sprint_patch.py
─────────────────────
Run this FROM your GeoClaw project root:

    cd /Users/naveenkumar/GeoClaw
    python3 apply_sprint_patch.py

What it does
  1. Backs up app.py → app.py.bak and terminal.html → terminal.html.bak
  2. Appends the Blueprint import + register call to app.py
  3. Injects the widget snippet into terminal.html (before </body>)
  4. Copies geoclaw_terminal_routes.py into your project
  5. Runs py_compile on every patched .py file
  6. Prints a manual test checklist
"""

import os, sys, shutil, py_compile, re, datetime, pathlib, textwrap

ROOT        = pathlib.Path(__file__).parent
ROUTES_SRC  = ROOT / "geoclaw_terminal_routes.py"   # generated file
WIDGETS_SRC = ROOT / "geoclaw_terminal_widgets.html"

# ── locate app.py ────────────────────────────────────────────────────────────
APP_PY = ROOT / "app.py"
if not APP_PY.exists():
    candidates = list(ROOT.rglob("app.py"))
    if candidates:
        APP_PY = candidates[0]
    else:
        print("ERROR: cannot find app.py — run from your GeoClaw project root.")
        sys.exit(1)

# ── locate terminal.html ─────────────────────────────────────────────────────
TERM_HTML = None
for candidate in ["templates/terminal.html", "terminal.html"]:
    p = ROOT / candidate
    if p.exists():
        TERM_HTML = p
        break
if TERM_HTML is None:
    candidates = list(ROOT.rglob("terminal.html"))
    if candidates:
        TERM_HTML = candidates[0]
    else:
        print("WARNING: terminal.html not found — widget injection skipped.")

# ── destination for new routes file ──────────────────────────────────────────
ROUTES_DST = APP_PY.parent / "geoclaw_terminal_routes.py"

stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(path: pathlib.Path):
    bak = path.with_suffix(path.suffix + f".bak_{stamp}")
    shutil.copy2(path, bak)
    print(f"  ✓ backed up {path.name} → {bak.name}")
    return bak


def py_check(path: pathlib.Path):
    try:
        py_compile.compile(str(path), doraise=True)
        print(f"  ✓ py_compile OK: {path.name}")
    except py_compile.PyCompileError as e:
        print(f"  ✗ py_compile FAIL: {path.name}\n    {e}")
        sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
print("\n── Step 1: Copy geoclaw_terminal_routes.py ─────────────────────────")
if ROUTES_SRC.exists():
    shutil.copy2(ROUTES_SRC, ROUTES_DST)
    print(f"  ✓ copied → {ROUTES_DST}")
    py_check(ROUTES_DST)
else:
    print(f"  SKIP — {ROUTES_SRC} not found next to this script.")

# ════════════════════════════════════════════════════════════════════════════
print("\n── Step 2: Patch app.py ─────────────────────────────────────────────")
backup(APP_PY)
app_src = APP_PY.read_text(encoding="utf-8")

IMPORT_LINE   = "from geoclaw_terminal_routes import terminal_bp"
REGISTER_LINE = "app.register_blueprint(terminal_bp)"

# Already patched?
if IMPORT_LINE in app_src:
    print("  ↷ Blueprint import already present — skipping import injection.")
else:
    # Insert import after the last existing 'from … import' or 'import …' line
    lines = app_src.splitlines()
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import_idx = i
    lines.insert(last_import_idx + 1, IMPORT_LINE)
    app_src = "\n".join(lines)
    print(f"  ✓ inserted import after line {last_import_idx + 1}")

if REGISTER_LINE in app_src:
    print("  ↷ Blueprint register already present — skipping.")
else:
    # Insert register after `app = Flask(...)` line
    # Handles: app = Flask(__name__), app = Flask(__name__, ...)
    pattern = r"(app\s*=\s*Flask\s*\([^)]*\))"
    match = re.search(pattern, app_src)
    if match:
        insert_at = match.end()
        app_src = app_src[:insert_at] + "\n" + REGISTER_LINE + app_src[insert_at:]
        print("  ✓ inserted app.register_blueprint(terminal_bp) after Flask init")
    else:
        # Fallback: append at end before `if __name__`
        app_src += f"\n{REGISTER_LINE}\n"
        print("  ↷ Flask() pattern not found — appended register at end of file")

APP_PY.write_text(app_src, encoding="utf-8")
py_check(APP_PY)

# ════════════════════════════════════════════════════════════════════════════
print("\n── Step 3: Inject widgets into terminal.html ────────────────────────")
if TERM_HTML and WIDGETS_SRC.exists():
    backup(TERM_HTML)
    html_src = TERM_HTML.read_text(encoding="utf-8")
    widget_code = WIDGETS_SRC.read_text(encoding="utf-8")

    MARKER = "<!-- GC_SPRINT_WIDGETS -->"
    if MARKER in html_src:
        print("  ↷ Widget marker already present — skipping injection.")
    elif "</body>" in html_src:
        html_src = html_src.replace(
            "</body>",
            f"\n{MARKER}\n{widget_code}\n</body>"
        )
        TERM_HTML.write_text(html_src, encoding="utf-8")
        print(f"  ✓ injected widgets before </body> in {TERM_HTML.name}")
    else:
        # No </body> — append
        html_src += f"\n{MARKER}\n{widget_code}\n"
        TERM_HTML.write_text(html_src, encoding="utf-8")
        print(f"  ↷ No </body> found — appended widgets to end of {TERM_HTML.name}")
else:
    if not TERM_HTML:
        print("  SKIP — terminal.html not found.")
    if not WIDGETS_SRC.exists():
        print(f"  SKIP — {WIDGETS_SRC} not found.")

# ════════════════════════════════════════════════════════════════════════════
print("\n── Step 4: Schema verification hints ───────────────────────────────")
print(textwrap.dedent("""
  The route file uses these table/column names — verify they match yours:

    TABLE              COLUMNS EXPECTED
    ─────────────────────────────────────────────────────────────────
    agent_journal      id, run_id, journal_type, summary, created_at, metrics
    theses             thesis_key, confidence, status, last_update_reason,
                       evidence_count, updated_at, timeframe, terminal_risk,
                       watchlist_suggestion
    action_proposals   id, action_type, status, reason, approval_state,
                       created_at, run_id, thesis_key
    agent_reasoning    id, article_id, thesis_key, terminal_risk,
                       watchlist_suggestion, chain, created_at
    articles           id, headline, cluster_id, published_at
    clusters           id, label, article_count

  If any table/column name differs, edit geoclaw_terminal_routes.py
  (search for the relevant SELECT statement) and re-run py_compile:

      python3 -c "import py_compile; py_compile.compile('geoclaw_terminal_routes.py', doraise=True)"
"""))

# ════════════════════════════════════════════════════════════════════════════
print("\n── Step 5: db() helper note ─────────────────────────────────────────")
print(textwrap.dedent("""
  geoclaw_terminal_routes.py uses sqlite3 directly via _query().
  At the top of that file, set DATABASE in your Flask config:

      app.config["DATABASE"] = "geoclaw.db"   # or full path

  If you use SQLAlchemy instead of raw sqlite3, replace _query()
  with your ORM equivalent.  The route logic is unchanged.
"""))

# ════════════════════════════════════════════════════════════════════════════
print("\n── Manual test checklist (give this to Codex or run yourself) ──────")
print(textwrap.dedent("""
  Start server:
      python3 app.py   (or however you start it)

  1. Agent Summary
     GET http://127.0.0.1:8000/terminal/agent-summary
     Expected: JSON with summary.stories_reviewed, summary.top_belief_change, etc.

  2. Thesis Cards
     GET http://127.0.0.1:8000/terminal/theses
     Expected: JSON with theses[] array, each having thesis_key + confidence

  3. Action Visibility
     GET http://127.0.0.1:8000/terminal/actions
     Expected: JSON with actions[] array; empty list is OK if none proposed yet

  4. Drilldown (use an actual thesis_key from step 2)
     GET http://127.0.0.1:8000/terminal/drilldown/negative+tone+detected.+risk-off+or+downside+implications+may+matter+if+follow-up+headlines+confirm.
     Expected: JSON with chains[], each chain having article.headline, cluster.label,
               reasoning_chain (hops with from/to/mechanism/confidence/timeframe)

  5. Diff
     GET http://127.0.0.1:8000/terminal/diff
     Expected: JSON with diff.beliefs.new_theses_touched, diff.tasks.superseded_delta, etc.

  6. UI in browser
     Open http://127.0.0.1:8000/terminal
     - Should see "Current Theses" section with thesis cards (confidence %, colour bar)
     - Should see "Proposed Actions" section
     - Should see "Before / After Run Diff" section
     - Click a thesis card → drilldown panel expands below with hop chain
     - Click "Run Real Agent" → after ~4 s, Summary panel appears and all panels refresh

  7. Regression check — existing routes must still work:
     GET http://127.0.0.1:8000/agent-reasoning       → still returns JSON
     GET http://127.0.0.1:8000/agent-briefing/latest → still returns JSON
"""))

print("═" * 60)
print("Patch complete.")
