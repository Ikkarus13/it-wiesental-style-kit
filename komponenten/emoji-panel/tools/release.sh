#!/bin/bash
# Neue Version veröffentlichen:   ./tools/release.sh 1.1.0
#
# Voraussetzung: In CHANGELOG.md steht ein Abschnitt «## 1.1.0», alles andere ist committet.
# Setzt die Version in version.py, baut dist/emoji-panel-<version>.tar.gz, committet, taggt,
# pusht und legt das GitHub-Release an. Installierte Panels finden es bei der nächsten Update-Suche.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?Version angeben, z. B. ./tools/release.sh 1.1.0}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Version im Format X.Y.Z angeben." >&2; exit 1; }
git rev-parse "v$VERSION" >/dev/null 2>&1 && { echo "v$VERSION gibt es schon." >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Bitte zuerst alle Änderungen committen (auch neue Dateien)." >&2; exit 1; }

NOTES="$(awk -v v="$VERSION" '$0 == "## " v {f = 1; next} /^## / {f = 0} f' CHANGELOG.md | sed '/./,$!d')"
[ -n "$NOTES" ] || { echo "In CHANGELOG.md fehlt der Abschnitt «## $VERSION»." >&2; exit 1; }

echo "VERSION = '$VERSION'" > version.py
git diff --quiet || git commit -qam "Version $VERSION"

PACKAGE="dist/emoji-panel-$VERSION.tar.gz"
mkdir -p dist
git archive --format=tar.gz --prefix="emoji-panel-$VERSION/" -o "$PACKAGE" HEAD

git tag -a "v$VERSION" -m "Version $VERSION"
git push -q origin HEAD --follow-tags
gh release create "v$VERSION" "$PACKAGE" --title "Emoji-Panel $VERSION" --notes "$NOTES"
echo "Veröffentlicht: v$VERSION"
