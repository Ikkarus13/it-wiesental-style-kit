#!/bin/bash
# Kopiert das Applet nach ~/.local/share/cinnamon/applets/
set -e
UUID="tray-collapse@sascha"
DEST="$HOME/.local/share/cinnamon/applets/$UUID"
mkdir -p "$DEST"
cp "$(dirname "$0")/$UUID/"* "$DEST/"
echo "Installiert nach $DEST"
echo "Falls das Applet schon läuft: Rechtsklick auf den Pfeil → Entfernen und neu hinzufügen, oder Cinnamon neu starten."
