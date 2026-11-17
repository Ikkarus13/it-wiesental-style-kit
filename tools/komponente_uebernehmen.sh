#!/bin/bash
# Übernimmt eine Komponente in einer bestimmten Version fest in dieses Repo
# (komponenten/<id>/, vollständiger Quelltext). Die Tools werden in ihren eigenen
# Repos weiterentwickelt; hierher kommt eine neue Version nur, wenn man sie
# bewusst übernimmt.
#
#   ./tools/komponente_uebernehmen.sh startklar          neuestes Release
#   ./tools/komponente_uebernehmen.sh startklar v0.3.1   bestimmte Version (Tag oder Commit)
#   ./tools/komponente_uebernehmen.sh design             Designs aus ${WST_THEMES:-~/.themes}
#   ./tools/komponente_uebernehmen.sh --liste            Stand aller Komponenten
#   ./tools/komponente_uebernehmen.sh --anpassen         nur anpassungen/ erneut anwenden
#
# Woher die Programme kommen, steht in komponenten/quellen.json ({"id": "Besitzer/Repo"}).
# Nach dem Übernehmen werden die Dateien aus anpassungen/ darübergelegt:
#   anpassungen/alle/<datei>       in jede Komponente (z. B. LICENSE), ausser design
#   anpassungen/<id>/<datei>       nur in diese Komponente (z. B. Branding, README)
#   anpassungen/<id>/ENTFERNEN     Pfade (je Zeile), die im Kit nicht mitkommen
#
# Danach: Änderungen prüfen (git diff --stat), CHANGELOG ergänzen, committen.
set -euo pipefail
cd "$(dirname "$0")/.."
THEMES="${WST_THEMES:-$HOME/.themes}"
QUELLEN=komponenten/quellen.json
MANIFEST=komponenten/manifest.json
mkdir -p komponenten
[ -f "$MANIFEST" ] || echo '{}' > "$MANIFEST"
[ -f "$QUELLEN" ] || { echo "$QUELLEN fehlt." >&2; exit 1; }
declare -A REPO=()
while IFS='=' read -r k v; do REPO[$k]="$v"; done < <(python3 -c '
import json, sys
for k, v in json.load(open(sys.argv[1])).items(): print(f"{k}={v}")' "$QUELLEN")

anpassen() {    # id
    local kid="$1" q
    for q in anpassungen/alle "anpassungen/$kid"; do
        [ -d "$q" ] || continue
        [ "$kid" = design ] && [ "$q" = anpassungen/alle ] && continue
        rsync -a --exclude ENTFERNEN "$q/" "komponenten/$kid/"
        if [ -f "$q/ENTFERNEN" ]; then
            while read -r p; do
                p="${p%%#*}"; p="$(echo "$p" | xargs)"
                [ -n "$p" ] && [[ "$p" != *..* ]] && rm -rf "komponenten/$kid/$p"
            done < "$q/ENTFERNEN"
        fi
    done
}

eintragen() {   # id version quelle ref
    python3 - "$MANIFEST" "$@" <<'PY'
import json, sys
pfad, kid, version, quelle, ref = sys.argv[1:6]
m = json.load(open(pfad))
m[kid] = {"version": version, "quelle": quelle, "ref": ref}
json.dump(dict(sorted(m.items())), open(pfad, "w"), indent=2, ensure_ascii=False)
open(pfad, "a").write("\n")
PY
}

if [ "${1:-}" = "--anpassen" ]; then
    for d in komponenten/*/; do anpassen "$(basename "$d")"; done
    git status --short komponenten | head -40
    exit 0
fi

if [ "${1:-}" = "--liste" ]; then
    python3 - "$MANIFEST" "${!REPO[@]}" <<'PY'
import json, sys, subprocess
m = json.load(open(sys.argv[1]))
for kid in sorted(set(sys.argv[2:]) | {"design"}):
    e = m.get(kid, {})
    print(f"{kid:20} {e.get('version', '–'):12} {e.get('quelle', '')}")
PY
    exit 0
fi

KID="${1:?Komponente angeben (z. B. startklar, design) oder --liste}"
REF="${2:-}"

if [ "$KID" = design ]; then
    for t in Win11-Dark Win11-Light; do
        [ -f "$THEMES/$t/index.theme" ] || { echo "Design $t fehlt in $THEMES" >&2; exit 1; }
    done
    rm -rf komponenten/design
    mkdir -p komponenten/design
    cp -a "$THEMES/Win11-Dark" "$THEMES/Win11-Light" komponenten/design/
    V="$(date -r "$THEMES/Win11-Dark/index.theme" +%Y.%m.%d)"
    eintragen design "$V" "yeyushengfan258/Win11-gtk-theme (aus ${THEMES/#$HOME/\~})" "49e30de"
    anpassen design
    echo "design $V übernommen."
    exit 0
fi

QR="${REPO[$KID]:-}"
[ -n "$QR" ] || { echo "Unbekannte Komponente: $KID (siehe $QUELLEN)" >&2; exit 1; }
OWNER="${QR%%/*}"; R="${QR#*/}"
if [ -z "$REF" ]; then
    if REF="$(gh api "repos/$OWNER/$R/releases/latest" --jq .tag_name 2>/dev/null)" && [ -n "$REF" ]; then :
    else REF="$(gh api "repos/$OWNER/$R/commits/$(gh api "repos/$OWNER/$R" --jq .default_branch)" --jq .sha)"
    fi
fi
SHA="$(gh api "repos/$OWNER/$R/commits/$REF" --jq .sha)"
case "$REF" in v[0-9]*) V="${REF#v}" ;; *) V="${SHA:0:7}" ;; esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
gh api "repos/$OWNER/$R/tarball/$SHA" > "$TMP/a.tar.gz"
mkdir "$TMP/x"
tar -xzf "$TMP/a.tar.gz" -C "$TMP/x" --strip-components=1
rm -rf "komponenten/$KID"
mv "$TMP/x" "komponenten/$KID"
anpassen "$KID"
eintragen "$KID" "$V" "$OWNER/$R" "${SHA:0:12}"
echo "$KID $V ($OWNER/$R @ ${SHA:0:7}) übernommen."
