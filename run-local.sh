#!/usr/bin/env bash
# GeoClaw — run on your Mac, for your Mac only.
# No cloud, no accounts, no card. Just your machine.
#
# First run: creates a persistent JWT secret (.env.local) and sets up
# the database. Subsequent runs: just starts the server.
#
# Stop: Ctrl-C, or close the Terminal window.

set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"
DB_NAME="geoclaw"
PY="${PYTHON:-/Users/naveenkumar/GeoClaw/venv/bin/python3}"

# --- 1. Persistent JWT secret (first run only) ---
if [[ ! -f .env.local ]]; then
  echo "▶ First run — generating persistent JWT secret…"
  cat > .env.local <<EOF
# Local-only secrets for GeoClaw. Do NOT commit. Already in .gitignore.
GEOCLAW_JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(40))')
GEOCLAW_ALLOW_LOCALHOST_AUTH=0
DATABASE_URL=postgresql://$USER@localhost:5432/$DB_NAME
EOF
  chmod 600 .env.local
  echo "✓ Saved .env.local (JWT secret persists across restarts)"
fi

# Load env
set -a
. ./.env.local
set +a

# --- 2. Postgres ---
if ! pg_isready -h localhost -q 2>/dev/null; then
  if command -v brew >/dev/null && brew services list 2>/dev/null | grep -q postgresql; then
    echo "▶ Starting Postgres…"
    brew services start postgresql@14 >/dev/null 2>&1 || brew services start postgresql >/dev/null 2>&1 || true
    for i in {1..10}; do
      pg_isready -h localhost -q && break
      sleep 1
    done
  fi
fi

if ! pg_isready -h localhost -q 2>/dev/null; then
  echo "✗ Postgres is not running. Install it with:"
  echo "    brew install postgresql@14 && brew services start postgresql@14"
  exit 1
fi

# --- 3. Ensure database exists ---
if ! /opt/homebrew/opt/postgresql@14/bin/psql -l 2>/dev/null | grep -q "^ $DB_NAME "; then
  echo "▶ Creating database '$DB_NAME'…"
  /opt/homebrew/opt/postgresql@14/bin/createdb "$DB_NAME"
fi

# --- 4. Ensure schema ---
echo "▶ Ensuring schema…"
"$PY" -c "from intelligence.db import ensure_intelligence_schema; ensure_intelligence_schema()" 2>/dev/null || {
  echo "✗ Schema init failed. Running with output:"
  "$PY" -c "from intelligence.db import ensure_intelligence_schema; ensure_intelligence_schema()"
  exit 1
}

# --- 5. Start the server ---
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  GeoClaw is starting at http://localhost:$PORT"
echo "  Login page will open in your browser in 3s."
echo "  Press Ctrl-C to stop."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Open browser after 3s (in background so it doesn't block the server)
( sleep 3 && open "http://localhost:$PORT/login" ) &

export PORT
exec "$PY" dashboard_api.py
