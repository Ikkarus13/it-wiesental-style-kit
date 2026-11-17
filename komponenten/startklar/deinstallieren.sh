#!/bin/bash
# Entfernt Startklar wieder: Leiste wie vor der Installation, Programm, Befehl,
# D-Bus-Dienst, Autostart und Applet. Einstellungen (~/.config/startklar)
# bleiben, ausser mit --alles.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd -P)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"

python3 "$SRC/tools/leiste.py" zurueck "$CONF/startklar/leiste-vorher.txt" || true
"$HOME/.local/bin/startklar" --quit 2>/dev/null || true
rm -f "$HOME/.local/bin/startklar" "$DATA/dbus-1/services/org.startklar.App.service" \
      "$CONF/autostart/startklar.desktop"
rm -rf "$DATA/cinnamon/applets/startklar@sascha"
case "$SRC" in
    "$DATA/startklar") ;;                 # aus der installierten Kopie: zuletzt löschen
    *) rm -rf "$DATA/startklar" ;;
esac
[ "${1:-}" = "--alles" ] && rm -rf "$CONF/startklar"
[ "$SRC" = "$DATA/startklar" ] && rm -rf "$DATA/startklar"
echo "Startklar ist entfernt."
