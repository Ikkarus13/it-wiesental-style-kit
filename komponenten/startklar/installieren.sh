#!/bin/bash
# Installiert Startklar für den aktuellen Benutzer (ohne root):
#   Programm als Kopie nach ~/.local/share/startklar, Befehl ~/.local/bin/startklar,
#   D-Bus-Dienst (startet bei Bedarf von selbst), Autostart, Cinnamon-Applet
#   startklar@sascha – und setzt den Startknopf an die Stelle des bisherigen
#   Menüs in der Leiste (Cinnamenu oder Cinnamon-Menü). Die Super-Taste geht
#   damit an Startklar.
#
#   ./installieren.sh                     installieren + altes Menü in der Leiste ersetzen
#   ./installieren.sh --leiste-lassen     installieren, Leiste nicht ändern
#   ./installieren.sh --altes-menue       Leiste wieder wie vorher (altes Menü zurück)
#   ./installieren.sh --update            (vom Updater) installieren, Leiste nicht ändern
#
# Entfernen: ./deinstallieren.sh
set -euo pipefail

MODE=ersetzen
for arg in "$@"; do
    case "$arg" in
        --leiste-lassen) MODE=lassen ;;
        --altes-menue) MODE=zurueck ;;
        --update) MODE=update ;;
        -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $arg (siehe --help)" >&2; exit 2 ;;
    esac
done

SRC="$(cd "$(dirname "$0")" && pwd -P)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
PREFIX="$DATA/startklar"
BIN="$HOME/.local/bin"
APPLET="$DATA/cinnamon/applets/startklar@sascha"
BACKUP="$CONF/startklar/leiste-vorher.txt"

panel() { python3 "$SRC/tools/leiste.py" "$@"; }

if [ "$MODE" = zurueck ]; then
    panel zurueck "$BACKUP"
    exit 0
fi

[ -f "$SRC/startklar.py" ] && [ -f "$SRC/startklar/__init__.py" ] || {
    echo "In $SRC liegt kein Startklar." >&2; exit 1; }

# ------------------------------------------------------------ Programm kopieren
mkdir -p "$DATA" "$BIN" "$CONF/startklar"
if [ "$SRC" != "$(cd "$PREFIX" 2>/dev/null && pwd -P || echo "$PREFIX")" ]; then
    NEW="$(mktemp -d "$DATA/.startklar-neu-XXXXXX")"
    trap 'rm -rf "$NEW"' EXIT
    cp -R "$SRC/startklar" "$SRC/assets" "$SRC/startklar.py" "$SRC/README.md" "$NEW/"
    mkdir -p "$NEW/tools" && cp "$SRC/tools/leiste.py" "$NEW/tools/"
    cp "$SRC/installieren.sh" "$SRC/deinstallieren.sh" "$NEW/"
    find "$NEW" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} +
    chmod 755 "$NEW"
    if [ -e "$PREFIX" ]; then
        OLD="$DATA/.startklar-alt-$$"
        mv "$PREFIX" "$OLD"
        mv "$NEW" "$PREFIX" || { mv "$OLD" "$PREFIX"; echo "Installation fehlgeschlagen." >&2; exit 1; }
        rm -rf "$OLD"
    else
        mv "$NEW" "$PREFIX"
    fi
    trap - EXIT
fi

cat > "$BIN/startklar" <<EOF
#!/bin/sh
exec python3 "$PREFIX/startklar.py" "\$@"
EOF
chmod 755 "$BIN/startklar"

# D-Bus-Dienst: Klick auf den Startknopf startet Startklar notfalls selbst
mkdir -p "$DATA/dbus-1/services"
cat > "$DATA/dbus-1/services/org.startklar.App.service" <<EOF
[D-BUS Service]
Name=org.startklar.App
Exec=$BIN/startklar --gapplication-service
EOF

# Autostart: Menü liegt nach der Anmeldung schon bereit (schnelles erstes Öffnen)
mkdir -p "$CONF/autostart"
cat > "$CONF/autostart/startklar.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Startklar
Comment=Startmenü im Stil von Windows 11
Exec=$BIN/startklar --hidden
Icon=$PREFIX/assets/start.svg
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

# ------------------------------------------------------------ Applet
# Dateien an Ort und Stelle ersetzen – ein gelöschter Ordner lässt Cinnamon
# das laufende Applet entladen, ohne es wieder zu laden
mkdir -p "$APPLET"
for f in "$SRC/applet/startklar@sascha/"* "$SRC/assets/start.svg"; do
    cp -f "$f" "$APPLET/.$(basename "$f").neu" && mv -f "$APPLET/.$(basename "$f").neu" "$APPLET/$(basename "$f")"
done
python3 - "$SRC/assets/start.svg" "$APPLET/icon.png" <<'EOF'
import sys
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
GdkPixbuf.Pixbuf.new_from_file_at_size(sys.argv[1], 48, 48).savev(sys.argv[2], "png", [], [])
EOF

# ------------------------------------------------------------ Dienst neu starten
"$BIN/startklar" --quit 2>/dev/null || true
sleep 0.5
# über D-Bus starten: läuft dann unabhängig von diesem Terminal
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.StartServiceByName org.startklar.App 0 >/dev/null 2>&1 || true

# alte Update-Reste aufräumen
find "${XDG_CACHE_HOME:-$HOME/.cache}/startklar" -maxdepth 1 -name 'update-*' -mmin +60 \
    -exec rm -rf {} + 2>/dev/null || true

# ------------------------------------------------------------ Leiste
if [ "$MODE" = ersetzen ]; then
    panel ersetzen "$BACKUP"
fi
# Applet neu laden, falls es schon in der Leiste ist (und prüfen, ob es wieder läuft)
running_applets() {
    gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.Cinnamon.Eval \
        'imports.ui.appletManager.getRunningInstancesForUuid("startklar@sascha").length' 2>/dev/null \
        | sed -n "s/.*'\([0-9]*\)'.*/\1/p"
}
if python3 "$SRC/tools/leiste.py" ist-drin; then
    for _ in 1 2 3; do
        gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon \
            --method org.Cinnamon.ReloadXlet "startklar@sascha" "APPLET" >/dev/null 2>&1 || true
        sleep 1.5
        n="$(running_applets)"
        [ -z "$n" ] || [ "$n" -gt 0 ] && break
    done
fi

echo "Startklar $(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/startklar/__init__.py") ist installiert."
