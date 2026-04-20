#!/usr/bin/env bash
# GeoClaw daily backup.
# Dumps the Postgres database to ~/Documents/GeoClaw-Backups/ and
# keeps the last 14 days. Safe to run anytime — it's a snapshot.
#
# Manual:  ./scripts/backup.sh
# Auto:    installed via install-backup.sh → runs at 03:30 every day

set -euo pipefail

BACKUP_DIR="$HOME/Documents/GeoClaw-Backups"
ICLOUD_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GeoClaw-Backups"
KEEP_DAYS=14
DB_NAME="geoclaw"
PG_DUMP="/opt/homebrew/opt/postgresql@14/bin/pg_dump"
WORKDIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load DATABASE_URL if present (so we use the same creds as the app)
[[ -f "$WORKDIR/.env.local" ]] && { set -a; . "$WORKDIR/.env.local"; set +a; }

mkdir -p "$BACKUP_DIR"

TS="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/geoclaw-$TS.sql.gz"

if [[ -x "$PG_DUMP" ]]; then
  DUMP="$PG_DUMP"
elif command -v pg_dump >/dev/null 2>&1; then
  DUMP="$(command -v pg_dump)"
else
  echo "✗ pg_dump not found. Install postgresql@14 with: brew install postgresql@14" >&2
  exit 1
fi

# Prefer DATABASE_URL if set, else fall back to local socket
if [[ -n "${DATABASE_URL:-}" ]]; then
  "$DUMP" --no-owner --no-privileges --clean --if-exists "$DATABASE_URL" | gzip > "$OUT"
else
  "$DUMP" --no-owner --no-privileges --clean --if-exists "$DB_NAME" | gzip > "$OUT"
fi

SIZE=$(du -h "$OUT" | cut -f1)
echo "✓ Backup: $OUT ($SIZE)"

# Prune old backups
find "$BACKUP_DIR" -name "geoclaw-*.sql.gz" -type f -mtime +$KEEP_DAYS -delete 2>/dev/null || true

KEPT=$(find "$BACKUP_DIR" -name "geoclaw-*.sql.gz" -type f | wc -l | tr -d ' ')
echo "  Kept: $KEPT backup(s) in $BACKUP_DIR (retention: $KEEP_DAYS days)"

# Offsite mirror: copy into iCloud Drive so Apple syncs it off this machine.
# Silent no-op if iCloud Drive isn't set up on this Mac.
ICLOUD_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
if [[ -d "$ICLOUD_ROOT" ]]; then
  mkdir -p "$ICLOUD_DIR"
  cp "$OUT" "$ICLOUD_DIR/"
  # Same 14-day retention in the mirror
  find "$ICLOUD_DIR" -name "geoclaw-*.sql.gz" -type f -mtime +$KEEP_DAYS -delete 2>/dev/null || true
  MIRRORED=$(find "$ICLOUD_DIR" -name "geoclaw-*.sql.gz" -type f | wc -l | tr -d ' ')
  echo "  ☁  Mirrored to iCloud: $ICLOUD_DIR ($MIRRORED file(s))"
else
  echo "  ⚠  iCloud Drive not detected — skipping offsite mirror."
fi
