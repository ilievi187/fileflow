#!/usr/bin/env bash
# Register FileFlow in the desktop app launcher.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="$HOME/.local/share/applications"

mkdir -p "$APPS"
chmod +x "$DIR/run.sh"

cat > "$APPS/fileflow.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=FileFlow
Comment=Sort a messy folder into clean, categorized folders
Exec=$DIR/run.sh
Icon=$DIR/icons/fileflow.svg
Terminal=false
Categories=Utility;
Keywords=organizer;sorter;files;cleanup;fileflow;
StartupNotify=true
DESKTOP

chmod 644 "$APPS/fileflow.desktop"
update-desktop-database "$APPS" 2>/dev/null || true
echo "installed: $APPS/fileflow.desktop"
echo "Exec: $DIR/run.sh"
