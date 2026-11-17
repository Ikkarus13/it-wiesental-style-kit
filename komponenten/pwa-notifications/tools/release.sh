#!/bin/bash
# Neue Version veröffentlichen:   ./tools/release.sh 1.1.0
#
# Voraussetzung: In CHANGELOG.md steht ein Abschnitt «## 1.1.0», alles andere ist committet.
# Setzt die Version in metadata.json, baut dist/<uuid>-<version>.tar.gz, committet, taggt,
# pusht und legt das GitHub-Release an. Die installierten Erweiterungen finden es dann
# bei der nächsten Update-Suche.
set -euo pipefail
cd "$(dirname "$0")/.."

UUID="pwa-notifications@sascha"
VERSION="${1:?Version angeben, z. B. ./tools/release.sh 1.1.0}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Version im Format X.Y.Z angeben." >&2; exit 1; }
git rev-parse "v$VERSION" >/dev/null 2>&1 && { echo "v$VERSION gibt es schon." >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "Bitte zuerst alle Änderungen committen." >&2; exit 1; }

NOTES="$(awk -v v="$VERSION" '$0 == "## " v {f = 1; next} /^## / {f = 0} f' CHANGELOG.md | sed '/./,$!d')"
[ -n "$NOTES" ] || { echo "In CHANGELOG.md fehlt der Abschnitt «## $VERSION»." >&2; exit 1; }

python3 -I - "$UUID/metadata.json" "$VERSION" <<'PY'
import re, sys
path, version = sys.argv[1], sys.argv[2]
text = open(path, encoding='utf-8').read()
text = re.sub(r'("version"\s*:\s*")[^"]*(")', r'\g<1>' + version + r'\g<2>', text, count=1)
open(path, 'w', encoding='utf-8').write(text)
PY
git diff --quiet || git commit -qam "Version $VERSION"

PACKAGE="dist/$UUID-$VERSION.tar.gz"
mkdir -p dist
tar --exclude='__pycache__' --transform "s,^,pwa-notifications-$VERSION/," \
    -czf "$PACKAGE" install.sh uninstall.sh README.md CHANGELOG.md "$UUID"

git tag -a "v$VERSION" -m "Version $VERSION"
git push -q origin HEAD --follow-tags
gh release create "v$VERSION" "$PACKAGE" --title "Version $VERSION" --notes "$NOTES"
echo "Veröffentlicht: v$VERSION"
