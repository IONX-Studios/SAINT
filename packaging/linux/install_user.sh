#!/usr/bin/env bash
# Add SAINT to this user's application menu and (optionally) start it with Plasma.
# Per-user only (~/.local/share/applications, ~/.config/systemd/user); no root.
#
#   packaging/linux/install_user.sh              # menu entry
#   packaging/linux/install_user.sh --autostart  # menu entry + start with the session
#   packaging/linux/install_user.sh --remove     # undo both
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

case "${1:-}" in
  --remove)
    systemctl --user disable --now saint.service 2>/dev/null || true
    rm -f "$APPS/saint.desktop" "$UNITS/saint.service"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed SAINT's menu entry and user service."; exit 0 ;;
esac

mkdir -p "$APPS" "$UNITS"
sed "s#@SAINT_ROOT@#$ROOT#g" packaging/linux/desktop/saint.desktop.in > "$APPS/saint.desktop"
chmod +x run.sh packaging/linux/*.sh
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
echo "Menu entry: $APPS/saint.desktop"

sed "s#@SAINT_ROOT@#$ROOT#g" packaging/linux/systemd/saint.service.in > "$UNITS/saint.service"
systemctl --user daemon-reload
if [ "${1:-}" = "--autostart" ]; then
  systemctl --user enable saint.service
  echo "SAINT will start with your Plasma session (systemctl --user disable saint to stop that)."
else
  echo "User service installed but not enabled: systemctl --user start saint"
fi
