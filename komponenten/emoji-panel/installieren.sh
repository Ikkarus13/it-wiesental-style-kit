#!/bin/bash
# Installiert das Emoji-Panel für den aktuellen Benutzer (Cinnamon/X11) und startet es (neu).
# Bei Updates (EMOJIPANEL_UPDATE=1) bleiben Autostart-Einstellung und Tastenkürzel unverändert.
DIR="$(dirname "$(readlink -f "$0")")"
BIN="$HOME/.local/bin/emoji-panel"

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.config/autostart"
ln -sf "$DIR/emoji-panel" "$BIN"

cat > "$HOME/.local/share/applications/emoji-panel.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Emoji-Panel
Comment=Emoji, Kaomoji, Symbole und Zwischenablage-Verlauf (wie Win + .)
Exec="$BIN"
Icon=face-smile
Categories=Utility;
Actions=settings;update;

[Desktop Action settings]
Name=Einstellungen
Exec="$BIN" --settings

[Desktop Action update]
Name=Nach Updates suchen
Exec="$BIN" --update
EOF

AUTOSTART="$HOME/.config/autostart/emoji-panel.desktop"
if [ -z "$EMOJIPANEL_UPDATE" ] || [ ! -e "$AUTOSTART" ]; then
  cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Emoji-Panel
Exec="$BIN" --hidden
Icon=face-smile
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
fi

# Tastenkürzel (eine vorhandene, evtl. geänderte Belegung bleibt erhalten)
python3 "$DIR/tastenkuerzel.py" installieren "$BIN" || echo "Tastenkürzel konnten nicht gesetzt werden."

# laufende Instanz sauber beenden und auf das Ende warten
running() { gdbus introspect --session --dest org.emojipanel.App --object-path /org/emojipanel/App >/dev/null 2>&1; }
if running; then
  "$BIN" --quit
  for _ in $(seq 50); do running || break; sleep 0.1; done
  if running; then
    pid=$(gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
          --method org.freedesktop.DBus.GetConnectionUnixProcessID org.emojipanel.App 2>/dev/null \
          | grep -o '[0-9]\+' | tail -1)
    [ -n "$pid" ] && kill "$pid" 2>/dev/null
    for _ in $(seq 30); do running || break; sleep 0.1; done
  fi
fi

setsid "$BIN" --hidden >/dev/null 2>&1 < /dev/null &
echo "Emoji-Panel installiert und gestartet."
