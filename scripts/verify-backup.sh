#!/usr/bin/env bash
# Verify the newest backup is actually restorable.
# Creates a throwaway DB, restores the newest snapshot into it, compares
# row counts against the live DB, and drops the scratch DB.
#
# Run monthly (or any time you want reassurance):
#   ./scripts/verify-backup.sh
#
# Exits non-zero if the restore fails or row counts diverge.

set -uo pipefail

BACKUP_DIR="$HOME/Documents/GeoClaw-Backups"
ICLOUD_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GeoClaw-Backups"
LIVE_DB="geoclaw"
SCRATCH="geoclaw_verify_$$"
PG_BIN="/opt/homebrew/opt/postgresql@14/bin"
PSQL="$PG_BIN/psql"
CREATEDB="$PG_BIN/createdb"
DROPDB="$PG_BIN/dropdb"

# Fallback to PATH if the pinned Homebrew copy isn't installed
[[ -x "$PSQL" ]] || PSQL=$(command -v psql)
[[ -x "$CREATEDB" ]] || CREATEDB=$(command -v createdb)
[[ -x "$DROPDB" ]] || DROPDB=$(command -v dropdb)

NEWEST=$(ls -t "$BACKUP_DIR"/geoclaw-*.sql.gz "$ICLOUD_DIR"/geoclaw-*.sql.gz 2>/dev/null | head -1)
if [[ -z "$NEWEST" ]]; then
  echo "✗ No backup found." >&2
  exit 1
fi

echo "▶ Verifying: $(basename "$NEWEST")"
echo ""

cleanup() { "$DROPDB" --if-exists "$SCRATCH" 2>/dev/null || true; }
trap cleanup EXIT

"$CREATEDB" "$SCRATCH"

if ! gunzip -c "$NEWEST" | "$PSQL" -q -v ON_ERROR_STOP=1 "$SCRATCH" > /tmp/verify-backup.log 2>&1; then
  echo "✗ Restore FAILED. Tail:" >&2
  tail -20 /tmp/verify-backup.log >&2
  exit 1
fi

# Compare every public table
TABLES=$("$PSQL" -At "$SCRATCH" -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")

FAIL=0
for T in $TABLES; do
  LIVE=$("$PSQL" -At "$LIVE_DB" -c "SELECT COUNT(*) FROM $T" 2>/dev/null || echo "N/A")
  REST=$("$PSQL" -At "$SCRATCH" -c "SELECT COUNT(*) FROM $T" 2>/dev/null || echo "ERR")
  if [[ "$LIVE" == "$REST" ]]; then
    printf "  ✓ %-28s live=%s backup=%s\n" "$T" "$LIVE" "$REST"
  else
    printf "  ✗ %-28s live=%s backup=%s  MISMATCH\n" "$T" "$LIVE" "$REST"
    FAIL=1
  fi
done

echo ""
if (( FAIL == 0 )); then
  echo "✓ Backup verified: $(basename "$NEWEST")"
else
  echo "✗ Verification failed — see mismatches above." >&2
  exit 1
fi
