#!/usr/bin/env bash
# Install/uninstall the daily backup LaunchAgent.
#   ./install-backup.sh             # install (runs daily 03:30)
#   ./install-backup.sh --uninstall # remove

set -euo pipefail

cd "$(dirname "$0")"
WORKDIR="$(pwd)"
LABEL="com.geoclaw.backup"
PLIST_SRC="$WORKDIR/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "✓ Daily backup disabled."
  exit 0
fi

# First run: prove the backup script itself works
echo "▶ Running backup once to verify…"
./scripts/backup.sh

mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__WORKDIR__|$WORKDIR|g" "$PLIST_SRC" > "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Daily backup installed"
echo ""
echo "  Runs every day at 03:30 local time."
echo "  Keeps the last 14 days in:"
echo "     ~/Documents/GeoClaw-Backups/"
echo ""
echo "  Manual backup:  ./scripts/backup.sh"
echo "  Restore:        ./scripts/restore.sh [path/to/backup.sql.gz]"
echo "  Uninstall:      ./install-backup.sh --uninstall"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
