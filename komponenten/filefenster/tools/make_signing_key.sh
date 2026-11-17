#!/usr/bin/env bash
# Erzeugt einmalig das Schlüsselpaar, mit dem tools/make_release.sh die
# Update-Pakete signiert (Ed25519, openssl).
#
#   tools/make_signing_key.sh           Schlüssel erzeugen, öffentlichen Schlüssel zeigen
#   tools/make_signing_key.sh --pin     dazu den öffentlichen Schlüssel in
#                                       filefenster/release_key.pem eintragen
#   tools/make_signing_key.sh --show    nur den öffentlichen Schlüssel des
#                                       vorhandenen privaten Schlüssels zeigen
#
# Der private Schlüssel liegt AUSSERHALB des Repositorys:
#   ${FILEFENSTER_SIGNING_KEY:-~/.config/filefenster-release/signing-key.pem}
# (Ordner 0700, Datei 0600). Ein vorhandener Schlüssel wird nie überschrieben.
# Sichern (z. B. Passwortmanager, Offline-Kopie): Wer ihn verliert, kann keine
# Updates mehr ausliefern, die bestehende Installationen annehmen; wer ihn
# stiehlt, kann es.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
KEY="${FILEFENSTER_SIGNING_KEY:-$HOME/.config/filefenster-release/signing-key.pem}"
PIN=0
SHOW=0
for a in "$@"; do
    case "$a" in
        --pin) PIN=1 ;;
        --show) SHOW=1 ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $a (siehe --help)" >&2; exit 2 ;;
    esac
done
command -v openssl >/dev/null || { echo "openssl fehlt." >&2; exit 1; }

KEY="$(realpath -m -- "$KEY")"
KEY_DIR="$(dirname "$KEY")"
case "$KEY_DIR/" in
    "$REPO"/*) echo "Der private Schlüssel darf nicht im Repository liegen ($KEY)." >&2; exit 1 ;;
esac

if [ "$SHOW" = 0 ]; then
    if [ -e "$KEY" ]; then
        echo "$KEY existiert schon – wird nicht überschrieben (--show zeigt den öffentlichen Schlüssel)." >&2
        exit 1
    fi
    # nur einen selbst angelegten Ordner auf 0700 setzen (nie z. B. das Home)
    [ -d "$KEY_DIR" ] || mkdir -p -m 700 "$KEY_DIR"
    (umask 077 && openssl genpkey -algorithm ed25519 -out "$KEY")
    chmod 600 "$KEY"
    echo "Privater Schlüssel: $KEY (nur für Sie lesbar – sicher aufbewahren, nie einchecken)"
elif [ ! -r "$KEY" ]; then
    echo "$KEY nicht gefunden." >&2
    exit 1
fi

PUB="$(openssl pkey -in "$KEY" -pubout)"
echo
echo "Öffentlicher Schlüssel (gehört nach filefenster/release_key.pem):"
echo "$PUB"

if [ "$PIN" = 1 ]; then
    {
        echo "# Öffentlicher Schlüssel für FileFenster-Updates (Ed25519)."
        echo "# Der Updater installiert nur Pakete, deren Signatur (.sig) zu diesem"
        echo "# Schlüssel passt. Erzeugt mit tools/make_signing_key.sh am $(date +%Y-%m-%d)."
        echo "$PUB"
    } > "$REPO/filefenster/release_key.pem"
    echo
    echo "Eingetragen in $REPO/filefenster/release_key.pem – committen und mit dem"
    echo "nächsten Release ausliefern (ab dann sind nur noch signierte Updates möglich)."
fi
