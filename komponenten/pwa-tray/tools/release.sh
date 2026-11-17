#!/usr/bin/env bash
# Veröffentlicht die Version aus version.py als GitHub-Release (Tag vX.Y.Z + Paket).
# Die installierten Helfer finden es über "Nach Updates suchen" bzw. die tägliche Prüfung.
#
#   tools/release.sh            Paket bauen, Tag pushen, Release anlegen
#   Notizen: dist/NOTES.md (deutsche Stichpunkte), sonst aus den Commits seit dem letzten Tag
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' version.py)"
TAG="v$VERSION"
NAME="pwa-tray-$VERSION"

if [ -n "$(git status --porcelain)" ]; then
    echo "Nicht eingecheckte Änderungen – bitte zuerst committen." >&2
    exit 1
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "Tag $TAG existiert schon – VERSION in version.py erhöhen." >&2
    exit 1
fi

mkdir -p dist
git archive --format=tar.gz --prefix="$NAME/" -o "dist/$NAME.tar.gz" HEAD
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")

NOTES=dist/NOTES.md
if [ ! -s "$NOTES" ]; then
    LAST="$(git describe --tags --abbrev=0 2>/dev/null || true)"
    git log --no-merges --format='- %s' ${LAST:+$LAST..}HEAD > "$NOTES"
fi
git push origin HEAD
git tag -a "$TAG" -m "Version $VERSION"
git push origin "$TAG"
gh release create "$TAG" "dist/$NAME.tar.gz" "dist/$NAME.tar.gz.sha256" \
    --title "PWA-Tray $VERSION" --notes-file "$NOTES"
rm -f "$NOTES"
echo "Release $TAG veröffentlicht."
