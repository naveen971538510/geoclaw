#!/usr/bin/env bash
# GeoClaw health check — run after login/restart to confirm everything is up.
#   ./status.sh

set -u

PORT=8000
LOCAL_LABEL="com.geoclaw.local"
BACKUP_LABEL="com.geoclaw.backup"
BACKUP_DIR="$HOME/Documents/GeoClaw-Backups"
ICLOUD_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GeoClaw-Backups"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m⚠\033[0m  %s\n" "$1"; }

echo ""
echo "━━━━━━━━ GeoClaw status ━━━━━━━━"

# 1. Postgres
if pgrep -qf "postgres -D" || /opt/homebrew/opt/postgresql@14/bin/pg_isready -q 2>/dev/null; then
  ok "Postgres is running"
else
  bad "Postgres is not running  (fix: brew services start postgresql@14)"
fi

# 2. Auto-start agent
if launchctl list 2>/dev/null | grep -q "$LOCAL_LABEL"; then
  PID=$(launchctl list | awk -v l="$LOCAL_LABEL" '$3==l {print $1}')
  if [[ "$PID" =~ ^[0-9]+$ ]]; then
    ok "Auto-start agent loaded  (pid $PID)"
  else
    warn "Auto-start agent loaded but not running  (check .geoclaw.log)"
  fi
else
  bad "Auto-start agent NOT loaded  (fix: ./install-autostart.sh)"
fi

# 3. HTTP
if curl -sf "http://localhost:$PORT/" -o /dev/null; then
  ok "Server responding at http://localhost:$PORT"
else
  bad "Server not responding on port $PORT  (check: tail -40 .geoclaw.log)"
fi

# 4. Backup agent
if launchctl list 2>/dev/null | grep -q "$BACKUP_LABEL"; then
  ok "Nightly backup scheduled  (03:30 daily)"
else
  bad "Backup agent NOT loaded  (fix: ./install-backup.sh)"
fi

# 5. Backups on disk
LOCAL_COUNT=$(find "$BACKUP_DIR" -name "geoclaw-*.sql.gz" -type f 2>/dev/null | wc -l | tr -d ' ')
ICLOUD_COUNT=$(find "$ICLOUD_DIR" -name "geoclaw-*.sql.gz" -type f 2>/dev/null | wc -l | tr -d ' ')
if (( LOCAL_COUNT > 0 )); then
  NEWEST=$(ls -t "$BACKUP_DIR"/geoclaw-*.sql.gz 2>/dev/null | head -1)
  ok "Local backups: $LOCAL_COUNT  (newest: $(basename "$NEWEST"))"
else
  warn "No local backups yet  (run: ./scripts/backup.sh)"
fi
if (( ICLOUD_COUNT > 0 )); then
  ok "iCloud mirror:  $ICLOUD_COUNT  (offsite copy active)"
else
  warn "No iCloud mirror yet"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
