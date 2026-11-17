#!/usr/bin/env bash
# Veröffentlicht die Version aus extension/manifest.json als GitHub-Release (Tag vX.Y.Z + Paket).
#
#   tools/release.sh       Tests, Paket bauen, Tag pushen, Release anlegen
#   Notizen: dist/NOTES.md (deutsche Stichpunkte), sonst aus den Commits seit dem letzten Tag
#
# Bricht ein Lauf nach dem Tag ab (z. B. gh nicht angemeldet), macht ein zweiter Lauf
# dort weiter, solange der Tag noch auf HEAD zeigt.
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH=master
VERSION="$(python3 -c 'import json;print(json.load(open("extension/manifest.json"))["version"])')"
HOST_VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' native-host/edge_links_host.py)"
TAG="v$VERSION"
NAME="pwa-link-organizer-$VERSION"

if [ "$VERSION" != "$HOST_VERSION" ]; then
    echo "Version uneinheitlich: manifest.json $VERSION, Host $HOST_VERSION." >&2
    exit 1
fi
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
    echo "Release nur von $BRANCH (jetzt: $(git rev-parse --abbrev-ref HEAD))." >&2
    exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
    echo "Nicht eingecheckte Änderungen – bitte zuerst committen." >&2
    exit 1
fi
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "Release $TAG gibt es schon – Version in manifest.json und Host erhöhen." >&2
    exit 1
fi
RESUME=0
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    if [ "$(git rev-parse "$TAG^{commit}")" != "$(git rev-parse HEAD)" ]; then
        echo "Tag $TAG zeigt nicht auf HEAD – Version in manifest.json und Host erhöhen." >&2
        exit 1
    fi
    RESUME=1
    echo "Tag $TAG gibt es schon (auf HEAD), Release fehlt – mache dort weiter."
fi

python3 -m unittest -q tests/test_host.py

mkdir -p dist
git archive --format=tar.gz --prefix="$NAME/" -o "dist/$NAME.tar.gz" HEAD
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")

NOTES=dist/NOTES.md
if [ ! -s "$NOTES" ]; then
    NOTES="$(mktemp)"
    trap 'rm -f "$NOTES"' EXIT
    LAST="$(git describe --tags --abbrev=0 --exclude "$TAG" 2>/dev/null || true)"
    git log --no-merges --format='- %s' ${LAST:+$LAST..}HEAD > "$NOTES"
fi
git push origin "$BRANCH"
if [ "$RESUME" = 0 ]; then
    git tag -a "$TAG" -m "Version $VERSION"
fi
git push origin "$TAG"
gh release create "$TAG" "dist/$NAME.tar.gz" "dist/$NAME.tar.gz.sha256" \
    --title "PWA link organizer $VERSION" --notes-file "$NOTES"
rm -f dist/NOTES.md
echo "Release $TAG veröffentlicht."
