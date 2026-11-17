#!/bin/bash
# Installiert FileFenster für den aktuellen Benutzer (ohne root):
#   Programm als Kopie nach ~/.local/share/filefenster (aktualisiert sich über
#   „··· → Nach Updates suchen“), Befehl ~/.local/bin/filefenster,
#   Startmenü-Eintrag und Symbol; trägt FileFenster als Standard-Dateimanager
#   ein (Ordner aus anderen Programmen öffnen sich dann in FileFenster).
#
#   ./installieren.sh                         installieren + als Standard eintragen
#   ./installieren.sh --nicht-als-standard    nur installieren
#   ./installieren.sh --nemo-als-standard     Standard wieder auf Nemo zurückstellen
#   ./installieren.sh --update                (vom Updater) Befehl, Startmenü-Eintrag
#                                             und Symbol erneuern, Standard nicht ändern
#
# Entfernen: ./deinstallieren.sh
set -euo pipefail

MODE=standard
for arg in "$@"; do
    case "$arg" in
        --nicht-als-standard) MODE=nicht ;;
        --nemo-als-standard) MODE=nemo ;;
        --update) MODE=update ;;
        -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $arg (siehe --help)" >&2; exit 2 ;;
    esac
done

if [ "$MODE" = nemo ]; then
    xdg-mime default nemo.desktop inode/directory
    echo "Standard-Dateimanager ist wieder Nemo."
    exit 0
fi

SRC="$(cd "$(dirname "$0")" && pwd -P)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA/filefenster"
BIN="$HOME/.local/bin"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor"
# Was zum Programm gehört (wie das Release-Paket von tools/make_release.sh)
FILES=(filefenster assets filefenster.py installieren.sh deinstallieren.sh README.md)

if [ ! -f "$SRC/filefenster.py" ] || [ ! -f "$SRC/filefenster/__init__.py" ]; then
    echo "In $SRC liegt kein FileFenster." >&2
    exit 1
fi
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/filefenster/__init__.py")"

# ------------------------------------------------------------------ Programm kopieren
# Läuft das Skript schon aus der installierten Kopie (Updater), gibt es nichts zu kopieren.
mkdir -p "$DATA"
if [ "$SRC" != "$(cd "$PREFIX" 2>/dev/null && pwd -P || echo "$PREFIX")" ]; then
    # Reste abgebrochener Installationen
    find "$DATA" -maxdepth 1 \( -name '.filefenster-neu-*' -o -name '.filefenster-alt-*' \) \
        -mmin +60 -exec rm -rf {} + 2>/dev/null || true
    # Erst vollständig daneben kopieren, dann austauschen: bricht etwas ab,
    # bleibt die bisherige Installation unverändert.
    NEW="$(mktemp -d "$DATA/.filefenster-neu-XXXXXX")"
    trap 'rm -rf "$NEW"' EXIT
    for f in "${FILES[@]}"; do
        if [ -e "$SRC/$f" ]; then cp -R "$SRC/$f" "$NEW/"; fi
    done
    for f in "$SRC"/LICENSE* "$SRC"/COPYING*; do
        if [ -e "$f" ]; then cp "$f" "$NEW/"; fi
    done
    find "$NEW" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} +
    chmod 755 "$NEW"
    if [ -e "$PREFIX" ] || [ -L "$PREFIX" ]; then
        OLD="$DATA/.filefenster-alt-$$"
        mv "$PREFIX" "$OLD"
        if ! mv "$NEW" "$PREFIX"; then
            mv "$OLD" "$PREFIX"
            echo "Installation fehlgeschlagen – die bisherige Version bleibt." >&2
            exit 1
        fi
        rm -rf "$OLD"
    else
        mv "$NEW" "$PREFIX"
    fi
    trap - EXIT
    echo "FileFenster $VERSION nach $PREFIX kopiert."
fi

# ------------------------------------------------------------------ Befehl
mkdir -p "$BIN" "$APPS" "$ICONS/scalable/apps"

# Reste vom alten Namen "Durchblick" (bis 0.3.0) entfernen
rm -f "$BIN/durchblick" "$APPS/durchblick.desktop" "$ICONS/scalable/apps/durchblick.svg"

# Bis 0.13 zeigte der Befehl direkt in das Arbeitsverzeichnis (git-Checkout)
OLD_TARGET=""
if [ -f "$BIN/filefenster" ]; then
    OLD_TARGET="$(sed -n 's/^exec python3 "\(.*\)" "\$@"$/\1/p' "$BIN/filefenster" | head -n 1)"
fi
# Erst entfernen: war der Befehl ein Symlink, würde ">" sonst das Ziel überschreiben
rm -f "$BIN/filefenster"
cat > "$BIN/filefenster" <<EOF
#!/bin/sh
# FileFenster – installiert von installieren.sh (Programm in $PREFIX)
exec python3 "$PREFIX/filefenster.py" "\$@"
EOF
chmod 755 "$BIN/filefenster"
if [ -n "$OLD_TARGET" ] && [ "$OLD_TARGET" != "$PREFIX/filefenster.py" ]; then
    echo "Der Befehl filefenster startete bisher $OLD_TARGET –"
    echo "jetzt die installierte Kopie. Das Arbeitsverzeichnis bleibt unverändert."
fi

# ------------------------------------------------------------------ Symbol, Startmenü
cp "$PREFIX/assets/filefenster.svg" "$ICONS/scalable/apps/filefenster.svg"

cat > "$APPS/filefenster.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=FileFenster
GenericName=Datei-Explorer
Comment=Dateien und Ordner im Stil von Windows 11 durchsuchen
Exec=$BIN/filefenster %U
TryExec=$BIN/filefenster
Icon=filefenster
Terminal=false
Categories=System;FileTools;FileManager;Utility;Core;GTK;
MimeType=inode/directory;
StartupNotify=true
StartupWMClass=filefenster.py
Keywords=Explorer;Dateien;Ordner;Dateimanager;Durchblick;
Actions=new-window;

[Desktop Action new-window]
Name=Neues Fenster
Exec=$BIN/filefenster
EOF

update-desktop-database "$APPS" >/dev/null 2>&1 || true
gtk-update-icon-cache -q "$ICONS" >/dev/null 2>&1 || true

if [ "$MODE" = update ]; then
    echo "FILEFENSTER_INSTALLIERT $VERSION"
    exit 0
fi

# ------------------------------------------------------------------ Standard-Dateimanager
if [ "$MODE" = standard ]; then
    if command -v xdg-mime >/dev/null 2>&1; then
        xdg-mime default filefenster.desktop inode/directory
        echo "FileFenster ist jetzt der Standard-Dateimanager (zurück: ./installieren.sh --nemo-als-standard)."
    else
        echo "xdg-mime fehlt – FileFenster wurde nicht als Standard-Dateimanager eingetragen." >&2
    fi
fi

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Hinweis: $BIN ist nicht im PATH – der Start über das Startmenü funktioniert trotzdem." ;;
esac
# Eine laufende Instanz (eine pro Sitzung) nimmt neue Fenster weiter selbst an
if pgrep -u "$(id -u)" -f 'python3?[^ ]* [^ ]*filefenster\.py( |$)' >/dev/null 2>&1; then
    echo "Hinweis: FileFenster läuft noch. Nach dem Schliessen aller FileFenster-Fenster"
    echo "startet beim nächsten Öffnen die installierte Version."
fi
echo "FileFenster $VERSION installiert: Startmenü → FileFenster oder Befehl 'filefenster'."
echo "Updates: ··· → Nach Updates suchen. Entfernen: $PREFIX/deinstallieren.sh"
