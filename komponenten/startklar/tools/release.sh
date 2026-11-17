#!/bin/bash
# Neue Version veröffentlichen:   ./tools/release.sh 0.3.0
#
# Voraussetzung: In CHANGELOG.md steht ein Abschnitt «## 0.3.0», alles ist committet.
# Setzt die Version (startklar/__init__.py, Applet), baut dist/startklar-<v>.tar.gz
# samt .sha256 und Ed25519-Signatur (.sig), committet, taggt, pusht und legt das
# GitHub-Release an. Installierte Kopien finden es bei der Update-Suche.
# Privater Schlüssel: ${STARTKLAR_SIGNING_KEY:-~/.config/startklar-release/signing-key.pem}
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?Version angeben, z. B. ./tools/release.sh 0.3.0}"
KEY="${STARTKLAR_SIGNING_KEY:-$HOME/.config/startklar-release/signing-key.pem}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Version im Format X.Y.Z angeben." >&2; exit 1; }
git rev-parse "v$VERSION" >/dev/null 2>&1 && { echo "v$VERSION gibt es schon." >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Bitte zuerst alles committen." >&2; exit 1; }
[ -f "$KEY" ] || { echo "Signaturschlüssel fehlt: $KEY" >&2; exit 1; }
# passt der Schlüssel zum öffentlichen Schlüssel im Programm?
diff -q <(openssl pkey -in "$KEY" -pubout) startklar/release_key.pem >/dev/null \
    || { echo "Signaturschlüssel passt nicht zu startklar/release_key.pem." >&2; exit 1; }

NOTES="$(awk -v v="$VERSION" '$0 == "## " v {f = 1; next} /^## / {f = 0} f' CHANGELOG.md | sed '/./,$!d')"
[ -n "$NOTES" ] || { echo "In CHANGELOG.md fehlt der Abschnitt «## $VERSION»." >&2; exit 1; }

sed -i "s/^VERSION = \".*\"/VERSION = \"$VERSION\"/" startklar/__init__.py
sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSION\"/" "applet/startklar@sascha/metadata.json"
git diff --quiet || git commit -qam "Version $VERSION

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

NAME="startklar-$VERSION"
mkdir -p dist
git archive --format=tar.gz --prefix="$NAME/" -o "dist/$NAME.tar.gz" HEAD
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
openssl pkeyutl -sign -inkey "$KEY" -rawin -in "dist/$NAME.tar.gz" -out "dist/$NAME.tar.gz.sig"
openssl pkeyutl -verify -pubin -inkey startklar/release_key.pem -rawin \
    -in "dist/$NAME.tar.gz" -sigfile "dist/$NAME.tar.gz.sig" >/dev/null

git tag -a "v$VERSION" -m "Version $VERSION"
git push -q origin HEAD --follow-tags
gh release create "v$VERSION" "dist/$NAME.tar.gz" "dist/$NAME.tar.gz.sha256" \
    "dist/$NAME.tar.gz.sig" --title "Startklar $VERSION" --notes "$NOTES"
echo "Veröffentlicht: v$VERSION"
