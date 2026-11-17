#!/bin/bash
# Richtet PWA-Tray fuer den aktuellen Benutzer ein (Autostart) und startet es neu.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$DIR/pwa_tray.py"
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/pwa-tray.desktop <<EOF
[Desktop Entry]
Type=Application
Name=PWA-Tray
Comment=Schliessen-Knopf von Teams (PWA) minimiert ins Tray
Exec=$DIR/pwa_tray.py
Icon=mail-send-receive
X-GNOME-Autostart-enabled=true
NoDisplay=false
EOF

# Native-Messaging-Host fuer die Edge-Erweiterung (Teams-Status/Badge)
EXT_ID="$(python3 -c "import json,base64,hashlib;k=json.load(open('$DIR/edge-extension/manifest.json'))['key'];print(''.join(chr(ord('a')+int(c,16)) for c in hashlib.sha256(base64.b64decode(k)).hexdigest()[:32]))")"
chmod +x "$DIR/native_host.py"
for d in ~/.config/microsoft-edge/NativeMessagingHosts ~/.config/google-chrome/NativeMessagingHosts; do
    [ -d "$(dirname "$d")" ] || continue
    mkdir -p "$d"
    cat > "$d/ch.sascha.pwa_tray.json" <<EOF
{
  "name": "ch.sascha.pwa_tray",
  "description": "PWA-Tray: Teams-Status ans Tray-Symbol",
  "path": "$DIR/native_host.py",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXT_ID/"]
}
EOF
done

pkill -f "^python3 $DIR/pwa_tray.py" 2>/dev/null || true
pkill -f '^python3 ./pwa_tray.py' 2>/dev/null || true
sleep 1
setsid "$DIR/pwa_tray.py" >/dev/null 2>&1 < /dev/null &
echo "PWA-Tray installiert und gestartet."
