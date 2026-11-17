#!/bin/bash
# Entfernt das Emoji-Panel (Daten in ~/.local/share/emoji-panel bleiben erhalten).
DIR="$(dirname "$(readlink -f "$0")")"
BIN="$HOME/.local/bin/emoji-panel"

running() { gdbus introspect --session --dest org.emojipanel.App --object-path /org/emojipanel/App >/dev/null 2>&1; }
if running; then
  "$DIR/emoji-panel" --quit
  for _ in $(seq 50); do running || break; sleep 0.1; done
fi
python3 "$DIR/tastenkuerzel.py" entfernen || echo "Tastenkürzel konnten nicht entfernt werden."
rm -f "$BIN" \
      "$HOME/.local/share/applications/emoji-panel.desktop" \
      "$HOME/.config/autostart/emoji-panel.desktop"
echo "Emoji-Panel entfernt."
