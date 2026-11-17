#!/bin/bash
# Richtet den Native-Messaging-Host von PWA link organizer für Google Chrome ein.
#
#   ./install.sh           einrichten (nach dem Verschieben des Ordners erneut ausführen)
#   ./install.sh --remove  Host-Eintrag entfernen (Erweiterung in Chrome selbst entfernen)
#
# Die Erweiterung selbst lädt man einmalig in Chrome (siehe Ausgabe am Ende).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
NAME=ch.sascha.edge_links
HOST="$DIR/native-host/edge_links_host.py"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
BROWSER_DIRS=(google-chrome google-chrome-beta google-chrome-unstable chromium)

if [ "${1:-}" = "--remove" ]; then
    for b in "${BROWSER_DIRS[@]}"; do
        rm -fv "$CONF_DIR/$b/NativeMessagingHosts/$NAME.json"
    done
    echo "Host-Eintrag entfernt. Die Erweiterung unter chrome://extensions entfernen."
    exit 0
fi

chmod +x "$HOST"
installed=0
for b in "${BROWSER_DIRS[@]}"; do
    [ -f "$CONF_DIR/$b/Local State" ] || continue  # nur echte Profile (1Password legt leere Ordner an)
    mkdir -p "$CONF_DIR/$b/NativeMessagingHosts"
    python3 - "$DIR/extension/manifest.json" "$HOST" "$CONF_DIR/$b/NativeMessagingHosts/$NAME.json" <<'EOF'
import base64, hashlib, json, sys
manifest, host, target = sys.argv[1:4]
key = json.load(open(manifest))["key"]
ext_id = "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(base64.b64decode(key)).hexdigest()[:32])
with open(target, "w") as f:
    json.dump({"name": "ch.sascha.edge_links",
               "description": "PWA link organizer: Outlook-Links in Microsoft Edge öffnen",
               "path": host, "type": "stdio",
               "allowed_origins": [f"chrome-extension://{ext_id}/"]}, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"Host eingetragen: {target}\nErweiterungs-ID: {ext_id}")
EOF
    installed=1
done
if [ "$installed" = 0 ]; then
    echo "Kein Chrome-Profil unter $CONF_DIR gefunden – Chrome einmal starten und erneut ausführen." >&2
    exit 1
fi

# Edge-Profil festlegen (sonst öffnet Edge im zuletzt benutzten Profil)
CONFIG="$CONF_DIR/edge-links/config.json"
if [ ! -f "$CONFIG" ]; then
    mkdir -p "$(dirname "$CONFIG")"
    python3 - "$CONF_DIR/microsoft-edge/Local State" "$CONFIG" <<'EOF'
import json, sys
state_file, config = sys.argv[1:3]
try:
    profile = json.load(open(state_file))["profile"]
    chosen = profile.get("last_used") or "Default"
    names = {k: v.get("name", k) for k, v in profile.get("info_cache", {}).items()}
except (OSError, ValueError, KeyError):
    chosen, names = "Default", {}
with open(config, "w") as f:
    json.dump({"profile": chosen}, f, indent=2)
    f.write("\n")
print(f"Edge-Profil: {chosen} ({names.get(chosen, '?')}) – änderbar in {config}")
if len(names) > 1:
    print("  vorhandene Profile: " + ", ".join(f"{k} = {v}" for k, v in names.items()))
EOF
else
    echo "Edge-Einstellungen bleiben: $CONFIG"
fi

cat <<EOF

Jetzt die Erweiterung in Chrome laden (einmalig):
  1. In Chrome die Adresse  chrome://extensions  öffnen
  2. Oben rechts «Entwicklermodus» einschalten
  3. «Entpackte Erweiterung laden» → Ordner wählen:
     $DIR/extension
  4. Die Karte «PWA link organizer» erscheint (ID siehe oben). Fertig – kein Neustart nötig.
EOF
