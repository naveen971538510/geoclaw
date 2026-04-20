#!/usr/bin/env bash
# Install/uninstall the LaunchAgent that auto-starts GeoClaw when you log in.
#
#   ./install-autostart.sh             # install
#   ./install-autostart.sh --uninstall # remove

set -euo pipefail

cd "$(dirname "$0")"
WORKDIR="$(pwd)"
LABEL="com.geoclaw.local"
PLIST_SRC="$WORKDIR/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

uninstall() {
  if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    echo "✓ Stopped and unloaded $LABEL"
  fi
  if [[ -f "$PLIST_DST" ]]; then
    rm -f "$PLIST_DST"
    echo "✓ Removed $PLIST_DST"
  fi
  echo "Uninstalled. GeoClaw will no longer auto-start on login."
}

if [[ "${1:-}" == "--uninstall" ]]; then
  uninstall
  exit 0
fi

# --- Install ---
mkdir -p "$HOME/Library/LaunchAgents"

# Materialise the plist with this workdir baked in
sed "s|__WORKDIR__|$WORKDIR|g" "$PLIST_SRC" > "$PLIST_DST"

# Reload if already installed
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ GeoClaw LaunchAgent installed"
echo ""
echo "  It will auto-start every time you log in to your Mac,"
echo "  and restart itself if it crashes."
echo ""
echo "  Open:      http://localhost:8000/login"
echo "  Logs:      tail -f $WORKDIR/.geoclaw.log"
echo "  Uninstall: ./install-autostart.sh --uninstall"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Verify the server responds (more reliable than parsing launchctl list)
echo "Waiting for GeoClaw to come up…"
for i in {1..30}; do
  if curl -sf http://localhost:8000/ -o /dev/null 2>&1; then
    echo "✓ GeoClaw is running at http://localhost:8000"
    exit 0
  fi
  sleep 1
done
echo "⚠  Server didn't respond within 30s. Check the log:"
echo "   tail -40 $WORKDIR/.geoclaw.log"
echo "   (Agent is loaded — it may still be finishing first-run setup.)"
