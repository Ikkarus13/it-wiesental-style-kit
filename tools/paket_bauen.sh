#!/bin/bash
# Baut das Installationspaket dist/<name>-<VERSION>.tar.gz aus dem committeten Stand.
# Alle Programme liegen fest in komponenten/ (siehe tools/komponente_uebernehmen.sh);
# hier wird nichts nachgeladen.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(cat VERSION)"
NAME="$(python3 -c 'import json,re; p=json.load(open("branding/branding.json"))["produkt"]; print(re.sub(r"[^a-z0-9]+","-",p.lower()).strip("-"))')-$VERSION"
[ -z "$(git status --porcelain)" ] || echo "Hinweis: nicht committete Änderungen werden NICHT eingepackt." >&2
mkdir -p dist
rm -f "dist/$NAME.tar.gz" "dist/$NAME.tar.gz.sha256"
git archive --format=tar.gz --prefix="$NAME/" -o "dist/$NAME.tar.gz" HEAD
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
ls -lh "dist/$NAME.tar.gz"
echo "dist/$NAME.tar.gz"
