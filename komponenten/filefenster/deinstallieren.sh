#!/bin/bash
# Entfernt FileFenster für den aktuellen Benutzer: Programmkopie in
# ~/.local/share/filefenster, Befehl ~/.local/bin/filefenster, Startmenü-Eintrag
# und Symbol. War FileFenster der Standard-Dateimanager, wird wieder Nemo
# eingetragen. Die Einstellungen (~/.config/filefenster) bleiben erhalten.
#
#   ./deinstallieren.sh           entfernen, Einstellungen behalten
#   ./deinstallieren.sh --alles   auch Einstellungen und Zwischenspeicher löschen
#
# Ein git-Arbeitsverzeichnis von FileFenster wird nie angefasst.
set -uo pipefail

ALLES=0
for arg in "$@"; do
    case "$arg" in
        --alles) ALLES=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $arg (siehe --help)" >&2; exit 2 ;;
    esac
done

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"
BIN="$HOME/.local/bin"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor"

# ------------------------------------------------------------------ Standard-Dateimanager
if command -v xdg-mime >/dev/null 2>&1; then
    CUR="$(xdg-mime query default inode/directory 2>/dev/null || true)"
    if [ "$CUR" = filefenster.desktop ]; then
        if command -v nemo >/dev/null 2>&1 || [ -e /usr/share/applications/nemo.desktop ]; then
            xdg-mime default nemo.desktop inode/directory
            echo "Standard-Dateimanager ist wieder Nemo."
        elif [ -f "$CONF/mimeapps.list" ]; then
            sed -i '/^inode\/directory=filefenster\.desktop;\{0,1\}$/d' "$CONF/mimeapps.list"
            echo "FileFenster ist nicht mehr Standard-Dateimanager."
        fi
    fi
fi

# ------------------------------------------------------------------ Dateien
# Den Befehl nur entfernen, wenn er FileFenster startet
if [ -L "$BIN/filefenster" ] || grep -q 'filefenster\.py' "$BIN/filefenster" 2>/dev/null; then
    rm -f "$BIN/filefenster"
fi
rm -f "$APPS/filefenster.desktop" "$ICONS/scalable/apps/filefenster.svg"
# Reste vom alten Namen "Durchblick"
rm -f "$BIN/durchblick" "$APPS/durchblick.desktop" "$ICONS/scalable/apps/durchblick.svg"
rm -rf "$DATA/filefenster" "$DATA/.filefenster-vorher" \
       "$DATA"/.filefenster-neu-* "$DATA"/.filefenster-alt-*
rm -rf "$CACHE/filefenster/updates" "$CACHE/filefenster/update.log"
update-desktop-database "$APPS" >/dev/null 2>&1 || true
gtk-update-icon-cache -q "$ICONS" >/dev/null 2>&1 || true

if ((ALLES)); then
    rm -rf "$CONF/filefenster" "$CACHE/filefenster"
    echo "FileFenster und seine Einstellungen wurden entfernt."
else
    echo "FileFenster wurde entfernt. Die Einstellungen in $CONF/filefenster bleiben"
    echo "erhalten (alles löschen: --alles)."
fi
if pgrep -u "$(id -u)" -f 'python3?[^ ]* [^ ]*filefenster\.py( |$)' >/dev/null 2>&1; then
    echo "Hinweis: FileFenster läuft noch – offene Fenster bleiben bis zum Schliessen."
fi
