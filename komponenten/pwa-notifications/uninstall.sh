#!/bin/bash
# Entfernt die Erweiterung «PWA-Benachrichtigungen».
#   ./uninstall.sh          Erweiterung entfernen, Einstellungen behalten
#   ./uninstall.sh --alles  auch Einstellungen und Zwischenspeicher löschen
set -euo pipefail

UUID="pwa-notifications@sascha"

current="$(gsettings get org.cinnamon enabled-extensions)"
updated="$(python3 -I -c 'import ast, sys
items = ast.literal_eval(sys.argv[1].replace("@as ", ""))
print([i for i in items if i != sys.argv[2]])' "$current" "$UUID")"
gsettings set org.cinnamon enabled-extensions "$updated"

rm -rf "$HOME/.local/share/cinnamon/extensions/$UUID"
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/$UUID"
if [ "${1:-}" = "--alles" ]; then
    rm -rf "$HOME/.config/cinnamon/spices/$UUID"
    echo "Erweiterung und Einstellungen entfernt."
else
    echo "Erweiterung entfernt (Einstellungen bleiben für eine spätere Installation erhalten)."
fi
