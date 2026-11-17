#!/bin/bash
# Neue Version veröffentlichen:   ./tools/release.sh 1.1.0
# Voraussetzung: Abschnitt «## 1.1.0» in CHANGELOG.md, alles committet.
# Setzt VERSION, baut das Paket mit den in komponenten/ übernommenen Programmen,
# taggt, pusht und legt das GitHub-Release an.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:?Version angeben, z. B. ./tools/release.sh 1.1.0}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Format X.Y.Z" >&2; exit 1; }
git rev-parse "v$V" >/dev/null 2>&1 && { echo "v$V gibt es schon." >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Bitte zuerst alles committen." >&2; exit 1; }
NOTES="$(awk -v v="$V" '$0 == "## " v {f = 1; next} /^## / {f = 0} f' CHANGELOG.md | sed '/./,$!d')"
[ -n "$NOTES" ] || { echo "In CHANGELOG.md fehlt «## $V»." >&2; exit 1; }
echo "$V" > VERSION
git diff --quiet || git commit -qam "Version $V

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
PAKET="$(./tools/paket_bauen.sh | tail -1)"
INHALT="$(python3 - <<'PY'
import json
m = json.load(open("komponenten/manifest.json"))
print("\n".join(f"- {k}: {v['version']}" for k, v in m.items()))
PY
)"
TITEL="$(python3 -c 'import json; print(json.load(open("branding/branding.json"))["produkt"])')"
git tag -a "v$V" -m "Version $V"
git push -q origin HEAD --follow-tags
# Signatur (Ed25519): Hat das Kit einen öffentlichen Schlüssel (branding/release_key.pem),
# prüft die Update-Funktion jedes Paket damit – dann muss hier signiert werden.
KURZ="$(basename "$PAKET" | sed -E 's/-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$//')"
SCHLUESSEL="${STYLE_KIT_SIGNING_KEY:-$HOME/.config/style-kit-release/$KURZ.pem}"
SIGS=()
if [ -f branding/release_key.pem ]; then
    [ -f "$SCHLUESSEL" ] || { echo "Privater Schlüssel $SCHLUESSEL fehlt – ohne Signatur kein Release." >&2; exit 1; }
    openssl pkeyutl -sign -inkey "$SCHLUESSEL" -rawin -in "$PAKET" -out "$PAKET.sig"
    openssl pkeyutl -verify -pubin -inkey branding/release_key.pem -rawin -in "$PAKET" \
        -sigfile "$PAKET.sig" | grep -q Success || { echo "Signatur passt nicht zu branding/release_key.pem." >&2; exit 1; }
    SIGS=("$PAKET.sig")
fi
# Zusätzlich unter festem Namen, damit …/releases/latest/download/<name>.tar.gz
# immer auf die neueste Version zeigt (Download-Link für die Homepage).
FEST="dist/$(basename "$PAKET" | sed -E 's/-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$/.tar.gz/')"
cp "$PAKET" "$FEST"
(cd dist && sha256sum "$(basename "$FEST")" > "$(basename "$FEST").sha256")
[ ${#SIGS[@]} -gt 0 ] && cp "$PAKET.sig" "$FEST.sig" && SIGS+=("$FEST.sig")
gh release create "v$V" "$PAKET" "$PAKET.sha256" "${SIGS[@]}" "$FEST" "$FEST.sha256" \
    --title "$TITEL $V" --notes "$NOTES

**Enthaltene Komponenten**
$INHALT"
echo "Veröffentlicht: v$V"
