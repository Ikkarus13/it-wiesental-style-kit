#!/bin/bash
# Installiert die Cinnamon-Erweiterung «PWA-Benachrichtigungen» für den aktuellen Benutzer.
#
#   ./install.sh                 Dateien kopieren (normale Installation, Updates über die Erweiterung)
#   ./install.sh --entwicklung   Symlink auf diesen Ordner: Änderungen sind nach dem Neuladen aktiv,
#                                Updates kommen per «git pull»
set -euo pipefail

UUID="pwa-notifications@sascha"
SRC="$(cd "$(dirname "$0")" && pwd)/$UUID"
DEST_DIR="$HOME/.local/share/cinnamon/extensions"
DEST="$DEST_DIR/$UUID"

[ -f "$SRC/metadata.json" ] || { echo "Der Ordner $UUID fehlt neben install.sh." >&2; exit 1; }
command -v cinnamon >/dev/null || echo "Hinweis: Cinnamon wurde nicht gefunden – die Erweiterung läuft nur unter Cinnamon."
for tool in curl tar; do
    command -v "$tool" >/dev/null || echo "Hinweis: «$tool» fehlt – die Update-Funktion braucht es."
done

mkdir -p "$DEST_DIR"
rm -rf "$DEST"
if [ "${1:-}" = "--entwicklung" ]; then
    ln -s "$SRC" "$DEST"
    echo "Entwicklerinstallation: $DEST → $SRC"
else
    cp -r "$SRC" "$DEST"
    rm -rf "$DEST/__pycache__"
    echo "Installiert nach $DEST"
fi

# Aktivieren bzw. neu laden, falls schon aktiv
current="$(gsettings get org.cinnamon enabled-extensions)"
if [[ "$current" == *"'$UUID'"* ]]; then
    gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon \
        --method org.Cinnamon.ReloadXlet "$UUID" "EXTENSION" >/dev/null 2>&1 \
        || echo "Bitte Cinnamon neu laden (Strg+Alt+Esc), damit die neue Version aktiv wird."
    echo "Neu geladen."
else
    updated="$(python3 -I -c 'import ast, sys
items = ast.literal_eval(sys.argv[1].replace("@as ", ""))
print(items + [sys.argv[2]])' "$current" "$UUID")"
    gsettings set org.cinnamon enabled-extensions "$updated"
    echo "Aktiviert."
fi
echo "Einstellungen: Systemeinstellungen → Erweiterungen → PWA-Benachrichtigungen → Zahnrad."
