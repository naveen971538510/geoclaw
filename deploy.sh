#!/usr/bin/env bash
# GeoClaw production deploy — one-shot.
# Usage: ./deploy.sh
#
# Prerequisites:
#   - flyctl installed (this script will install it if missing)
#   - You are logged in to Fly (`flyctl auth login`)
#   - `geoclaw` app exists on your Fly account (or this script will create it)
#
# What it does:
#   1. Installs flyctl if missing
#   2. Verifies auth
#   3. Creates the app if it doesn't exist
#   4. Sets required secrets (prompts if missing)
#   5. Deploys
#   6. Waits for health check
#   7. Smoke-tests signup + login against the live URL
#   8. Tails logs for 30s to catch startup errors

set -euo pipefail

APP="geoclaw"
REGION="lhr"

# --- 1. flyctl ---
export PATH="$HOME/.fly/bin:$PATH"
if ! command -v flyctl >/dev/null 2>&1; then
  echo "▶ Installing flyctl…"
  curl -sL https://fly.io/install.sh | sh
  export PATH="$HOME/.fly/bin:$PATH"
fi

# --- 2. Auth ---
if ! flyctl auth whoami >/dev/null 2>&1; then
  echo "✗ Not logged in. Run: flyctl auth login"
  exit 1
fi
echo "✓ Logged in as: $(flyctl auth whoami)"

# --- 3. App ---
if ! flyctl apps list 2>/dev/null | grep -q "^$APP "; then
  echo "▶ Creating app '$APP' in region $REGION…"
  flyctl apps create "$APP" --org personal
fi

# --- 4. Secrets ---
have_secret() { flyctl secrets list -a "$APP" 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1"; }

if ! have_secret GEOCLAW_JWT_SECRET; then
  echo "▶ Generating and setting GEOCLAW_JWT_SECRET…"
  JWT="$(python3 -c 'import secrets; print(secrets.token_hex(40))')"
  flyctl secrets set -a "$APP" --stage GEOCLAW_JWT_SECRET="$JWT"
fi

if ! have_secret DATABASE_URL; then
  echo "⚠  DATABASE_URL not set. You need a Postgres database."
  echo "   Option A: Attach a Fly Postgres cluster:"
  echo "     flyctl postgres create --name ${APP}-db --region $REGION"
  echo "     flyctl postgres attach ${APP}-db --app $APP"
  echo "   Option B: Set an external URL:"
  echo "     flyctl secrets set -a $APP DATABASE_URL='postgres://user:pass@host:5432/db'"
  read -rp "Paste a DATABASE_URL now (or press Enter to skip and attach manually): " DBURL
  if [[ -n "${DBURL:-}" ]]; then
    flyctl secrets set -a "$APP" --stage DATABASE_URL="$DBURL"
  else
    echo "✗ Aborting — DATABASE_URL is required."
    exit 1
  fi
fi

# --- 5. Deploy ---
echo "▶ Deploying…"
flyctl deploy -a "$APP" --remote-only

# --- 6. Health check ---
URL="https://${APP}.fly.dev"
echo "▶ Waiting for health check at $URL …"
for i in {1..30}; do
  if curl -sf -o /dev/null "$URL/"; then
    echo "✓ Health check OK ($URL)"
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    echo "✗ Health check timed out after 60s"
    flyctl logs -a "$APP" --no-tail | tail -40
    exit 1
  fi
done

# --- 7. Smoke test ---
EMAIL="smoke-$(date +%s)@example.com"
PASS="SmokeTest123456!"

echo "▶ Smoke test: signup…"
SIGNUP=$(curl -s -X POST "$URL/api/auth/signup" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"display_name\":\"Smoke\"}")
echo "  signup response: $SIGNUP"
TOKEN=$(echo "$SIGNUP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "")
[[ -n "$TOKEN" ]] && echo "  ✓ got access_token" || { echo "  ✗ no access_token in response"; exit 1; }

echo "▶ Smoke test: login with same creds…"
LOGIN=$(curl -s -X POST "$URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}")
echo "  login response: $LOGIN"
echo "$LOGIN" | grep -q '"status":"ok"' && echo "  ✓ login OK" || { echo "  ✗ login failed"; exit 1; }

echo "▶ Smoke test: authenticated request…"
PING=$(curl -s -H "Authorization: Bearer $TOKEN" "$URL/api/me" || echo "")
echo "  /api/me response: $PING"

# --- 8. Tail logs ---
echo ""
echo "✓ Deploy complete. Live at $URL"
echo "▶ Tailing logs for 30s (Ctrl-C to stop early)…"
( flyctl logs -a "$APP" & LOGS_PID=$!; sleep 30; kill $LOGS_PID 2>/dev/null ) || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  GeoClaw is live at $URL"
echo "  Smoke user: $EMAIL / $PASS"
echo "  Tail logs:  flyctl logs -a $APP --follow"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
