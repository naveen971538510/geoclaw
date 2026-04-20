#!/usr/bin/env bash
# Restore a GeoClaw backup.
# Usage: ./scripts/restore.sh [path/to/geoclaw-YYYYMMDD-HHMMSS.sql.gz]
# If no path given, restores the most recent backup.
#
# This REPLACES the current database. Confirmation required.

set -euo pipefail

BACKUP_DIR="$HOME/Documents/GeoClaw-Backups"
ICLOUD_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GeoClaw-Backups"
DB_NAME="geoclaw"
PSQL="/opt/homebrew/opt/postgresql@14/bin/psql"
WORKDIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ -f "$WORKDIR/.env.local" ]] && { set -a; . "$WORKDIR/.env.local"; set +a; }

FILE="${1:-}"
if [[ -z "$FILE" ]]; then
  # Consider both local and iCloud backup locations, pick newest overall
  FILE=$(ls -t "$BACKUP_DIR"/geoclaw-*.sql.gz "$ICLOUD_DIR"/geoclaw-*.sql.gz 2>/dev/null | head -1)
  if [[ -z "$FILE" ]]; then
    echo "✗ No backups found in $BACKUP_DIR or $ICLOUD_DIR" >&2
    exit 1
  fi
  echo "→ Using most recent: $FILE"
fi

if [[ ! -f "$FILE" ]]; then
  echo "✗ Not found: $FILE" >&2
  exit 1
fi

read -rp "This will REPLACE the current '$DB_NAME' database. Type 'yes' to continue: " CONFIRM
[[ "$CONFIRM" == "yes" ]] || { echo "Aborted."; exit 0; }

if command -v psql >/dev/null 2>&1 && [[ ! -x "$PSQL" ]]; then
  PSQL="$(command -v psql)"
fi

if [[ -n "${DATABASE_URL:-}" ]]; then
  gunzip -c "$FILE" | "$PSQL" "$DATABASE_URL"
else
  gunzip -c "$FILE" | "$PSQL" "$DB_NAME"
fi

echo "✓ Restored from $FILE"
