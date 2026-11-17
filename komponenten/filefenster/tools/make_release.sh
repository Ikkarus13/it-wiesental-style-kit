#!/usr/bin/env bash
# Baut das Update-Paket für die Version aus filefenster/__init__.py und
# veröffentlicht es als GitHub-Release. Installierte FileFenster finden es über
# „··· → Nach Updates suchen“ (filefenster/updater.py).
#
#   tools/make_release.sh                  Paket bauen, Release vX.Y.Z anlegen, hochladen
#   tools/make_release.sh --build-only     nur das Paket bauen, nichts veröffentlichen
#   tools/make_release.sh --dry-run        bauen und zeigen, was veröffentlicht würde
#   tools/make_release.sh --notes "…"      Release-Notizen (sonst dist/NOTES.md, sonst
#                                          die Commit-Betreffs seit dem letzten Tag)
#   tools/make_release.sh --dist DIR       Paket nach DIR statt dist/
#
# Ablauf: VERSION erhöhen, committen, main pushen, Neuerungen nach dist/NOTES.md
# (deutsche Stichpunkte), dann dieses Skript. Das Paket enthält nur, was zum
# Laufen gehört (kein dev/, docs/, tools/) – aus dem Commit HEAD, nicht aus
# nicht eingecheckten Dateien.
#
# Signatur: Das Paket wird mit dem privaten Ed25519-Schlüssel signiert
# (filefenster-X.Y.Z.tar.gz.sig). Er liegt ausserhalb des Repositorys:
#   FILEFENSTER_SIGNING_KEY (Standard ~/.config/filefenster-release/signing-key.pem),
# einmalig erzeugt mit tools/make_signing_key.sh. Ist in filefenster/release_key.pem
# ein echter öffentlicher Schlüssel eingetragen, muss er dazu passen – sonst bricht
# das Skript ab (installierte FileFenster würden das Update ablehnen).
set -euo pipefail
CALLER_DIR="$PWD"
cd "$(dirname "$0")/.."

MODE=publish
DIST=dist
NOTES_TEXT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --build-only) MODE=build ;;
        --dry-run) MODE=dry ;;
        --notes) NOTES_TEXT="${2:?--notes braucht einen Text}"; shift ;;
        --notes=*) NOTES_TEXT="${1#--notes=}" ;;
        --dist) DIST="${2:?--dist braucht einen Ordner}"; shift
                [[ "$DIST" = /* ]] || DIST="$CALLER_DIR/$DIST" ;;
        --dist=*) DIST="${1#--dist=}"
                  [[ "$DIST" = /* ]] || DIST="$CALLER_DIR/$DIST" ;;
        -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $1 (siehe --help)" >&2; exit 2 ;;
    esac
    shift
done

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' filefenster/__init__.py)"
if [ -z "$VERSION" ]; then
    echo "VERSION in filefenster/__init__.py nicht gefunden." >&2
    exit 1
fi
TAG="v$VERSION"
NAME="filefenster-$VERSION"

# Laufzeitdateien (wie installieren.sh), dazu Lizenzdateien im Hauptordner
FILES=(filefenster assets filefenster.py installieren.sh deinstallieren.sh README.md)
while IFS= read -r f; do
    FILES+=("$f")
done < <(git ls-tree --name-only HEAD | grep -E '^(LICENSE|COPYING)' || true)
for f in "${FILES[@]}"; do
    if ! git cat-file -e "HEAD:$f" 2>/dev/null; then
        echo "$f fehlt im Commit HEAD – erst committen." >&2
        exit 1
    fi
done

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    if [ "$MODE" = publish ]; then
        echo "Arbeitsverzeichnis hat nicht eingecheckte Änderungen – bitte zuerst committen." >&2
        exit 1
    fi
    echo "Hinweis: nicht eingecheckte Änderungen sind nicht im Paket (es wird aus HEAD gebaut)." >&2
fi

mkdir -p "$DIST"
git archive --format=tar.gz --prefix="$NAME/" -o "$DIST/$NAME.tar.gz" HEAD -- "${FILES[@]}"
(cd "$DIST" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "Gebaut: $DIST/$NAME.tar.gz ($(du -h "$DIST/$NAME.tar.gz" | cut -f1))"
echo "Prüfsumme: $(cut -d' ' -f1 "$DIST/$NAME.tar.gz.sha256")"

# ------------------------------------------------------------------ Signatur
SIGN_KEY="${FILEFENSTER_SIGNING_KEY:-$HOME/.config/filefenster-release/signing-key.pem}"
_b64() { sed -n '/-----BEGIN PUBLIC KEY-----/,/-----END PUBLIC KEY-----/p' | grep -v -- '-----' | tr -d ' \t\r\n'; }
PINNED_B64="$(git show HEAD:filefenster/release_key.pem 2>/dev/null | _b64 || true)"
SIGNED=0
rm -f "$DIST/$NAME.tar.gz.sig"
if [ -f "$SIGN_KEY" ]; then
    case "$(cd "$(dirname "$SIGN_KEY")" && pwd -P)/" in
        "$(pwd -P)"/*) echo "Der private Schlüssel $SIGN_KEY liegt im Repository – abgebrochen." >&2; exit 1 ;;
    esac
    if [ $(( 8#$(stat -L -c %a "$SIGN_KEY") & 8#077 )) -ne 0 ]; then
        echo "$SIGN_KEY ist für andere lesbar – erst „chmod 600 $SIGN_KEY“." >&2
        exit 1
    fi
    PUB_FILE="$(mktemp)"
    openssl pkey -in "$SIGN_KEY" -pubout -out "$PUB_FILE"
    if [ -n "$PINNED_B64" ] && [ "$(_b64 < "$PUB_FILE")" != "$PINNED_B64" ]; then
        rm -f "$PUB_FILE"
        echo "$SIGN_KEY passt nicht zum öffentlichen Schlüssel in filefenster/release_key.pem – abgebrochen." >&2
        exit 1
    fi
    openssl pkeyutl -sign -inkey "$SIGN_KEY" -rawin -in "$DIST/$NAME.tar.gz" -out "$DIST/$NAME.tar.gz.sig"
    if ! openssl pkeyutl -verify -pubin -inkey "$PUB_FILE" -rawin -in "$DIST/$NAME.tar.gz" \
            -sigfile "$DIST/$NAME.tar.gz.sig" >/dev/null; then
        rm -f "$PUB_FILE" "$DIST/$NAME.tar.gz.sig"
        echo "Die eben erstellte Signatur lässt sich nicht prüfen – abgebrochen." >&2
        exit 1
    fi
    rm -f "$PUB_FILE"
    SIGNED=1
    echo "Signiert: $DIST/$NAME.tar.gz.sig"
    [ -n "$PINNED_B64" ] || echo "Hinweis: In filefenster/release_key.pem steht noch der Platzhalter – installierte FileFenster prüfen die Signatur erst, wenn dort der öffentliche Schlüssel eingetragen ist (tools/make_signing_key.sh --show)." >&2
elif [ -n "$PINNED_B64" ]; then
    echo "Signaturschlüssel $SIGN_KEY fehlt – installierte FileFenster nehmen ein unsigniertes Update nicht an." >&2
    [ "$MODE" = build ] || exit 1
else
    echo "Hinweis: kein Signaturschlüssel ($SIGN_KEY) und in filefenster/release_key.pem nur der Platzhalter – Release ohne Signatur (nur Prüfsumme). Einmalig: tools/make_signing_key.sh --pin" >&2
fi
[ "$MODE" = build ] && exit 0

# ------------------------------------------------------------------ Release-Notizen
NOTES_FILE="$(mktemp)"
trap 'rm -f "$NOTES_FILE"' EXIT
if [ -n "$NOTES_TEXT" ]; then
    printf '%s\n' "$NOTES_TEXT" > "$NOTES_FILE"
elif [ -s dist/NOTES.md ]; then
    cp dist/NOTES.md "$NOTES_FILE"
else
    LAST="$(git describe --tags --abbrev=0 2>/dev/null || true)"
    git log --no-merges --format='- %s' ${LAST:+"$LAST"..}HEAD > "$NOTES_FILE"
fi

ASSETS=("$DIST/$NAME.tar.gz" "$DIST/$NAME.tar.gz.sha256")
[ "$SIGNED" = 1 ] && ASSETS+=("$DIST/$NAME.tar.gz.sig")
GH_ARGS=(release create "$TAG" "${ASSETS[@]}"
         --target main --title "FileFenster $VERSION" --notes-file "$NOTES_FILE")

if [ "$MODE" = dry ]; then
    echo "Probelauf – würde ausführen:"
    printf '  gh'
    printf ' %q' "${GH_ARGS[@]}"
    printf '\n--- Release-Notizen ---\n'
    cat "$NOTES_FILE"
    exit 0
fi

# ------------------------------------------------------------------ Veröffentlichen
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || gh release view "$TAG" >/dev/null 2>&1; then
    echo "Tag/Release $TAG existiert schon – VERSION in filefenster/__init__.py erhöhen." >&2
    exit 1
fi
# Das Release-Tag entsteht auf main bei GitHub: main muss genau HEAD sein
git fetch -q origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    echo "HEAD ist nicht origin/main – erst auf main committen und pushen (git push origin main)." >&2
    exit 1
fi
gh "${GH_ARGS[@]}"
git fetch -q --tags origin || true
rm -f dist/NOTES.md
echo "Release $TAG veröffentlicht."
