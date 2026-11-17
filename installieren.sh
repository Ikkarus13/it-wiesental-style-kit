#!/bin/bash
# Style Kit (Linux Mint im Stil von Windows 11) – Installation starten (grafischer Assistent).
#   ./installieren.sh              Assistent
#   ./installieren.sh --help       Textmodus und alle Optionen
DIR="$(cd "$(dirname "$0")" && pwd -P)"
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null; then
    echo "Python-GTK fehlt – wird installiert (Administrator-Passwort nötig)."
    sudo apt-get install -y python3-gi gir1.2-gtk-3.0 || exit 1
fi
exec /usr/bin/python3 -I "$DIR/installer/main.py" "$@"
