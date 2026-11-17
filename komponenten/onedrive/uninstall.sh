#!/usr/bin/env bash
# Entfernt den Synchronisierungs-Client für Microsoft OneDrive. Konten/Cache werden nur auf Nachfrage gelöscht.
set -uo pipefail

"$HOME/.local/bin/onedrive-mint" --quit 2>/dev/null
sleep 3

# Versteckte Einhaengepunkte loesen und die Verknuepfungen ~/OneDrive - … entfernen
MNT="$HOME/.local/state/onedrive-mint/mnt"
for m in "$MNT"/*; do
    [ -d "$m" ] || continue
    if grep -qF " $m " /proc/mounts; then
        fusermount3 -u -z "$m" 2>/dev/null || fusermount -u -z "$m" 2>/dev/null
    fi
done
find "$HOME" -maxdepth 2 -type l -lname "$MNT/*" -delete 2>/dev/null

rm -rf "$HOME/.local/share/onedrive-mint"
rm -f "$HOME/.local/bin/onedrive-mint"
rm -f "$HOME/.local/share/applications/io.github.OneDriveMint.desktop"
rm -f "$HOME/.local/share/applications/io.github.OneDriveMint.odopen.desktop"
rm -f "$HOME/.config/autostart/onedrive-mint.desktop"
rm -f "$HOME/.local/share/nemo-python/extensions/onedrive_mint_nemo.py"
rm -f "$HOME/.local/share/nautilus-python/extensions/onedrive_mint_nautilus.py"
for f in apps/onedrive-mint apps/odm-folder apps/odm-org status/odm-tray-{ok,sync,paused,error,warning,offline,setup} \
         emblems/emblem-odm-{cloud,local,pinned,sync,error}; do
    rm -f "$HOME/.local/share/icons/hicolor/scalable/$f.svg"
done
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

read -r -p "Auch Kontodaten (Anmeldung) und lokalen Cache löschen? Nicht hochgeladene Änderungen gehen verloren! [j/N] " ans
if [[ "${ans:-n}" =~ ^[JjYy]$ ]]; then
    if grep -qF " $MNT/" /proc/mounts; then
        echo "Ein OneDrive-Ordner ist noch eingehängt – Kontodaten und Cache bleiben erhalten." >&2
    else
        # --one-file-system: niemals in ein noch eingehaengtes OneDrive hinein loeschen
        rm -rf --one-file-system "$HOME/.config/onedrive-mint" "$HOME/.cache/onedrive-mint" \
            "$HOME/.local/state/onedrive-mint"
    fi
fi
nemo -q >/dev/null 2>&1 || true
echo "Deinstalliert. Leere OneDrive-Ordner in Ihrem Persönlichen Ordner können Sie selbst löschen."
