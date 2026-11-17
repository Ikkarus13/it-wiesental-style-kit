#!/bin/bash
# Übernimmt den Installer-Code aus einem anderen Style Kit (gleicher Aufbau):
# installer/, dev/, tools/, installieren.sh. branding/, README, LICENSE, CHANGELOG,
# docs/ und komponenten/ bleiben unverändert.
#   ./tools/installer_abgleichen.sh ../anderes-style-kit
set -euo pipefail
cd "$(dirname "$0")/.."
QUELLE="${1:?Pfad zum anderen Kit angeben}"
[ -f "$QUELLE/installer/ablauf.py" ] || { echo "$QUELLE ist kein Style Kit." >&2; exit 1; }
rsync -a --delete --exclude __pycache__ "$QUELLE/installer/" installer/
rsync -a --delete --exclude __pycache__ "$QUELLE/dev/" dev/
rsync -a "$QUELLE/tools/" tools/
cp "$QUELLE/installieren.sh" installieren.sh
cp "$QUELLE/docs/ENTWICKLUNG.md" docs/ENTWICKLUNG.md
git status --short installer dev tools installieren.sh docs/ENTWICKLUNG.md
echo "Abgeglichen – prüfen, testen, committen."
