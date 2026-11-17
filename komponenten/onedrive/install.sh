#!/usr/bin/env bash
# Installiert den Synchronisierungs-Client für Microsoft OneDrive (Name: onedrive_mint/branding.json).
# Unterstützt: Linux Mint, Ubuntu, Debian (apt) · Fedora (dnf) · Arch/Manjaro (pacman)
# Systempakete werden per sudo installiert, alles andere landet in ~/.local.
#
#   ./install.sh            normale Installation (fragt bei Bedarf nach sudo)
#   ./install.sh --update   vom Updater aufgerufen: ohne sudo und ohne Rückfragen, startet
#                           das Programm nicht selbst (das macht der Updater)
set -euo pipefail

UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$HOME/.local/share/onedrive-mint"
BIN="$HOME/.local/bin"
ICONS="$HOME/.local/share/icons/hicolor"
APPS="$HOME/.local/share/applications"
MIN_RCLONE="1.63"
APPINDICATOR_EXT="appindicatorsupport@rgcjonas.gmail.com"
mkdir -p "$BIN"
# Produktname: Paket-Branding, überlagert von ~/.config/onedrive-mint/branding.json (z. B. vom Style Kit)
PRODUKT="$(PYTHONPATH="$SRC" python3 -c 'from onedrive_mint import marke; print(marke.NAME)' 2>/dev/null || echo "Cloud Sync")"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*"; }
has()  { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ System erkennen
. /etc/os-release 2>/dev/null || true
DISTRO="${ID:-linux}"
if has apt-get; then PM=apt
elif has dnf; then PM=dnf
elif has pacman; then PM=pacman
else PM=""
fi
DESKTOP="${XDG_CURRENT_DESKTOP:-}"
is_desktop() { [[ ":${DESKTOP,,}:" == *"$1"* ]]; }
say "System: ${PRETTY_NAME:-$DISTRO} · Desktop: ${DESKTOP:-unbekannt} · Paketverwaltung: ${PM:-keine}"

# ------------------------------------------------------------------ Paketlisten
# Paketnamen je Paketverwaltung: Basis, XApp (Cinnamon-Tray), Nemo, Nautilus, GNOME-Tray-Erweiterung
case "$PM" in
    apt)
        BASE=(python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 fuse3 libnotify-bin xdg-utils curl)
        XAPP=(gir1.2-xapp-1.0)
        # Linux Mint liefert die Nemo-Anbindung als "python-nemo"; Ubuntus "nemo-python" wuerde
        # dort python-nemo und nemo-emblems entfernen.
        if apt-cache show python-nemo >/dev/null 2>&1; then NEMO=(python-nemo); else NEMO=(nemo-python); fi
        NAUTILUS=(python3-nautilus)
        GNOME_TRAY=(gnome-shell-extension-appindicator)
        ;;
    dnf)
        BASE=(python3-gobject gtk3 libayatana-appindicator-gtk3 fuse3 libnotify xdg-utils curl)
        XAPP=(xapps)
        NEMO=(nemo-python)
        NAUTILUS=(nautilus-python)
        GNOME_TRAY=(gnome-shell-extension-appindicator)
        ;;
    pacman)
        BASE=(python-gobject gtk3 libayatana-appindicator fuse3 libnotify xdg-utils curl)
        XAPP=(xapp)
        NEMO=(nemo-python)
        NAUTILUS=(python-nautilus)
        GNOME_TRAY=(gnome-shell-extension-appindicator)
        ;;
    *)
        warn "Keine unterstützte Paketverwaltung gefunden (apt, dnf, pacman)."
        warn "Bitte manuell installieren: PyGObject, GTK 3, Ayatana-AppIndicator, fuse3, rclone,"
        warn "nautilus-python bzw. nemo-python. Danach dieses Skript erneut starten."
        BASE=(); XAPP=(); NEMO=(); NAUTILUS=(); GNOME_TRAY=()
        ;;
esac

PKGS=("${BASE[@]}")
if is_desktop cinnamon || is_desktop mate || is_desktop xfce || [ "$DISTRO" = linuxmint ]; then
    PKGS+=("${XAPP[@]}")
fi
has nemo && PKGS+=("${NEMO[@]}")
has nautilus && PKGS+=("${NAUTILUS[@]}")
# Unter GNOME gibt es ohne Erweiterung keinen Infobereich (Ubuntu bringt seine eigene mit)
NEED_GNOME_TRAY=0
if is_desktop gnome && ! gnome-extensions list 2>/dev/null | grep -q "ubuntu-appindicators@ubuntu.com"; then
    NEED_GNOME_TRAY=1
    PKGS+=("${GNOME_TRAY[@]}")
fi

installed() {
    case "$PM" in
        apt) dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed" ;;
        dnf) rpm -q "$1" >/dev/null 2>&1 ;;
        pacman) pacman -Q "$1" >/dev/null 2>&1 ;;
    esac
}
available() {
    case "$PM" in
        apt) [ -n "$(apt-cache policy "$1" 2>/dev/null | sed -n 's/ *Candidate: //p' | grep -v '(none)')" ] ;;
        dnf) dnf -q list --available "$1" >/dev/null 2>&1 ;;
        pacman) pacman -Si "$1" >/dev/null 2>&1 ;;
    esac
}

MISSING=()
for p in "${PKGS[@]}"; do
    installed "$p" && continue
    if available "$p"; then MISSING+=("$p"); else warn "Paket $p ist nicht verfügbar – wird übersprungen."; fi
done
if ((${#MISSING[@]})) && ((UPDATE)); then
    warn "Fehlende Systempakete: ${MISSING[*]} – bitte ./install.sh einmal von Hand ausführen."
elif ((${#MISSING[@]})); then
    say "Installiere Systempakete: ${MISSING[*]}"
    case "$PM" in
        apt) sudo apt-get update -qq && sudo apt-get install -y "${MISSING[@]}" ;;
        dnf) sudo dnf install -y "${MISSING[@]}" ;;
        pacman) sudo pacman -S --needed --noconfirm "${MISSING[@]}" ;;
    esac
fi

if ((NEED_GNOME_TRAY)) && has gnome-extensions; then
    if gnome-extensions enable "$APPINDICATOR_EXT" 2>/dev/null; then
        say "GNOME-Erweiterung für Tray-Symbole aktiviert."
    else
        warn "GNOME-Erweiterung „AppIndicator Support“ wird erst nach Ab- und Anmelden aktiv."
        warn "Danach einmal ausführen: gnome-extensions enable $APPINDICATOR_EXT"
    fi
fi

# ------------------------------------------------------------------ rclone
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }
rclone_version() { rclone version 2>/dev/null | head -1 | sed -n 's/^rclone v\([0-9.]*\).*/\1/p'; }
RCLONE_VER=""
has rclone && RCLONE_VER="$(rclone_version)"

# Arch hat stets ein aktuelles rclone, sonst zuerst die Paketquelle versuchen
if [ -z "$RCLONE_VER" ] && [ -n "$PM" ] && ((!UPDATE)) && available rclone; then
    say "Installiere rclone aus den Paketquellen"
    case "$PM" in
        apt) sudo apt-get install -y rclone ;;
        dnf) sudo dnf install -y rclone ;;
        pacman) sudo pacman -S --needed --noconfirm rclone ;;
    esac
    RCLONE_VER="$(rclone_version)"
fi

if ((UPDATE)); then
    [ -n "$RCLONE_VER" ] || warn "rclone ist nicht installiert."
elif [ -z "$RCLONE_VER" ] || ! version_ge "$RCLONE_VER" "$MIN_RCLONE"; then
    if [ -n "$RCLONE_VER" ]; then
        warn "rclone $RCLONE_VER ist zu alt (mindestens $MIN_RCLONE empfohlen)."
    else
        warn "rclone ist nicht installiert."
    fi
    read -r -p "   Aktuelles rclone von downloads.rclone.org installieren? [J/n] " ans
    if [[ "${ans:-j}" =~ ^[JjYy]$ ]]; then
        case "$(uname -m)" in
            x86_64) ARCH=amd64 ;; aarch64) ARCH=arm64 ;; armv7l) ARCH=arm-v7 ;; i686) ARCH=386 ;;
            *) ARCH="" ;;
        esac
        TMP="$(mktemp -d)"
        chmod 755 "$TMP"
        if [ -z "$ARCH" ]; then
            warn "Unbekannte Architektur – bitte rclone manuell installieren: https://rclone.org/install/"
        elif [ "$PM" = apt ]; then
            curl -fsSL -o "$TMP/rclone.deb" "https://downloads.rclone.org/rclone-current-linux-$ARCH.deb"
            sudo apt-get install -y "$TMP/rclone.deb"
        elif [ "$PM" = dnf ]; then
            curl -fsSL -o "$TMP/rclone.rpm" "https://downloads.rclone.org/rclone-current-linux-$ARCH.rpm"
            sudo dnf install -y "$TMP/rclone.rpm"
        else
            # ohne Paketformat: Binary nach ~/.local/bin
            curl -fsSL -o "$TMP/rclone.zip" "https://downloads.rclone.org/rclone-current-linux-$ARCH.zip"
            (cd "$TMP" && unzip -q rclone.zip)
            install -m 755 "$TMP"/rclone-*/rclone "$BIN/rclone"
        fi
        rm -rf "$TMP"
    fi
fi

# ------------------------------------------------------------------ Programm
# Hat sich der Code der Dateimanager-Erweiterung geaendert? Dann muss Nemo/Nautilus neu starten.
ext_sum() { { cat "$1"/onedrive_mint/{fm_extension,states,thumbs}.py 2>/dev/null || true; } | sha256sum; }
OLD_EXT="$(ext_sum "$PREFIX")"
NEW_EXT="$(ext_sum "$SRC")"

say "Kopiere Programm nach $PREFIX"
rm -rf "$PREFIX/onedrive_mint"
mkdir -p "$PREFIX" "$BIN"
cp -r "$SRC/onedrive_mint" "$PREFIX/"
find "$PREFIX" -name '__pycache__' -prune -exec rm -rf {} +
# Programmcode wird in Nemo/Nautilus geladen: nur fuer den Besitzer beschreibbar
chmod -R u+rwX,go-w,go+rX "$PREFIX"

cat > "$BIN/onedrive-mint" <<EOF
#!/bin/sh
PYTHONPATH="$PREFIX\${PYTHONPATH:+:\$PYTHONPATH}" exec /usr/bin/python3 -m onedrive_mint "\$@"
EOF
chmod +x "$BIN/onedrive-mint"

# ------------------------------------------------------------------ Symbole
say "Installiere Symbole"
python3 "$SRC/data/make_icons.py" >/dev/null
# farbige Leistensymbole bis 2.2.0 (jetzt *-symbolic, passen sich der Leiste an)
rm -f "$ICONS/scalable/status/"odm-tray-{ok,sync,paused,error,warning,offline,setup}.svg
for sub in apps status emblems; do
    mkdir -p "$ICONS/scalable/$sub"
    cp "$SRC/data/icons/hicolor/scalable/$sub/"*.svg "$ICONS/scalable/$sub/"
done
[ -f "$ICONS/index.theme" ] || cp /usr/share/icons/hicolor/index.theme "$ICONS/" 2>/dev/null || true
gtk-update-icon-cache -f -t "$ICONS" >/dev/null 2>&1 || true

# ------------------------------------------------------------------ Starter
mkdir -p "$APPS"
cat > "$APPS/io.github.OneDriveMint.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$PRODUKT
GenericName=Cloud-Synchronisierung
Comment=Dateien mit Microsoft OneDrive synchronisieren (Dateien bei Bedarf)
Exec=$BIN/onedrive-mint
Icon=onedrive-mint
Categories=Network;FileTransfer;
Keywords=OneDrive;Cloud;Sync;SharePoint;$PRODUKT;
StartupNotify=false
Actions=Settings;AddAccount;

[Desktop Action Settings]
Name=Einstellungen
Exec=$BIN/onedrive-mint --settings

[Desktop Action AddAccount]
Name=Konto hinzufügen
Exec=$BIN/onedrive-mint --setup
EOF
# Handler fuer odopen:// – die Schaltflaeche „Synchronisieren“ in SharePoint/Teams
cat > "$APPS/io.github.OneDriveMint.odopen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$PRODUKT (Synchronisieren)
Comment=Ordner aus SharePoint/Teams synchronisieren
Exec=$BIN/onedrive-mint --odopen %u
Icon=onedrive-mint
NoDisplay=true
MimeType=x-scheme-handler/odopen;
EOF
update-desktop-database "$APPS" >/dev/null 2>&1 || true
xdg-mime default io.github.OneDriveMint.odopen.desktop x-scheme-handler/odopen

# ------------------------------------------------------------------ Dateimanager
if command -v nemo >/dev/null; then
    say "Installiere Nemo-Erweiterung"
    mkdir -p "$HOME/.local/share/nemo-python/extensions"
    cp "$SRC/extensions/onedrive_mint_nemo.py" "$HOME/.local/share/nemo-python/extensions/"
fi
if command -v nautilus >/dev/null; then
    say "Installiere Nautilus-Erweiterung"
    mkdir -p "$HOME/.local/share/nautilus-python/extensions"
    cp "$SRC/extensions/onedrive_mint_nautilus.py" "$HOME/.local/share/nautilus-python/extensions/"
fi

if ((UPDATE)); then
    # Der Updater startet die Dateimanager neu, wenn noetig
    [ "$OLD_EXT" = "$NEW_EXT" ] || echo "ODM_EXTENSION_CHANGED"
    say "Update installiert."
    exit 0
fi

# Dateimanager neu laden, damit die Erweiterung aktiv wird
command -v nemo >/dev/null && nemo -q >/dev/null 2>&1 || true
command -v nautilus >/dev/null && nautilus -q >/dev/null 2>&1 || true

case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN ist nicht im PATH – Start über das Menü funktioniert trotzdem." ;; esac

say "Fertig. Starte $PRODUKT …"
nohup "$BIN/onedrive-mint" >/dev/null 2>&1 &
