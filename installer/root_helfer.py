#!/usr/bin/env python3
"""Systemteil der Installation – läuft einmal mit Administratorrechten
(pkexec bzw. sudo) und liest seinen Auftrag aus einer JSON-Datei:

    {"pakete": [...], "browser": ["edge", "chrome"], "rclone": true,
     "webapps": {"edge": ["teams"], "chrome": ["outlook"]},
     "webapps_entfernen": false}

Paketnamen, Browser und Web-Apps werden gegen den Katalog geprüft; URLs,
Schlüssel und Paketquellen stehen fest in diesem Skript. Ausgabe zeilenweise,
«@@SCHRITT <text>» kennzeichnet einen neuen Abschnitt für die Oberfläche.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import katalog  # noqa: E402

ENV = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C.UTF-8")
RICHTLINIEN_DATEI = "style-kit.json"   # Standard, sonst auftrag["name"] + .json
RICHTLINIEN_ORDNER = {"edge": "/etc/opt/edge/policies/managed",
                      "chrome": "/etc/opt/chrome/policies/managed"}

# Paketquellen genau so, wie sie die Browser-Pakete selbst anlegen – dann
# überschreibt das Paket die Datei später mit gleichem Inhalt (kein
# «Signed-By»-Konflikt bei apt update).
QUELLEN = {
    "edge": {
        "schluessel_url": "https://packages.microsoft.com/keys/microsoft.asc",
        "schluessel": "/usr/share/keyrings/microsoft-edge.gpg",
        "datei": "/etc/apt/sources.list.d/microsoft-edge.sources",
        "inhalt": ("### THIS FILE IS AUTOMATICALLY CONFIGURED ###\n"
                   "# Changes to this file will not be preserved.\n"
                   "# This file will not be recreated if removed.\n"
                   "X-Repolib-Name: Microsoft Edge\n"
                   "Types: deb\n"
                   "URIs: https://packages.microsoft.com/repos/edge-stable\n"
                   "Suites: stable\nComponents: main\nArchitectures: amd64\n"
                   "Signed-By: /usr/share/keyrings/microsoft-edge.gpg\n"),
    },
    "chrome": {
        "schluessel_url": "https://dl.google.com/linux/linux_signing_key.pub",
        "schluessel": "/usr/share/keyrings/google-chrome.gpg",
        "datei": "/etc/apt/sources.list.d/google-chrome.sources",
        "inhalt": ("### THIS FILE IS AUTOMATICALLY CONFIGURED ###\n"
                   "# Changes to this file will not be preserved.\n"
                   "# This file will not be recreated if removed.\n"
                   "X-Repolib-Name: Google Chrome\n"
                   "Types: deb\n"
                   "URIs: https://dl.google.com/linux/chrome-stable/deb/\n"
                   "Suites: stable\nComponents: main\nArchitectures: amd64\n"
                   "Signed-By: /usr/share/keyrings/google-chrome.gpg\n"),
    },
}
RCLONE_MIN = (1, 63)


def schritt(text):
    print(f"@@SCHRITT {text}", flush=True)


def sag(text):
    print(text, flush=True)


def lauf(cmd, **kw):
    sag("$ " + " ".join(cmd))
    p = subprocess.run(cmd, env=ENV, stdin=subprocess.DEVNULL, stdout=sys.stdout,
                       stderr=subprocess.STDOUT, **kw)
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} endete mit Fehler {p.returncode}")


def laden(url, ziel):
    sag(f"Lade {url}")
    with urllib.request.urlopen(url, timeout=60) as r, open(ziel, "wb") as f:
        shutil.copyfileobj(r, f)


def installiert(paket):
    p = subprocess.run(["dpkg-query", "-W", "-f=${Status}", paket], env=ENV,
                       capture_output=True, text=True)
    return "install ok installed" in p.stdout


def verfuegbar(paket):
    p = subprocess.run(["apt-cache", "policy", paket], env=ENV, capture_output=True, text=True)
    m = re.search(r"Candidate: (\S+)|Installationskandidat: (\S+)", p.stdout)
    return bool(m) and "(none)" not in m.group(0) and "(keine)" not in m.group(0)


def quelle_einrichten(bid):
    q = QUELLEN[bid]
    with tempfile.TemporaryDirectory() as tmp:
        asc = os.path.join(tmp, "key.asc")
        laden(q["schluessel_url"], asc)
        gpg = os.path.join(tmp, "key.gpg")
        lauf(["gpg", "--batch", "--yes", "--dearmor", "-o", gpg, asc])
        os.makedirs(os.path.dirname(q["schluessel"]), exist_ok=True)
        shutil.copyfile(gpg, q["schluessel"])
        os.chmod(q["schluessel"], 0o644)
    # alte .list-Variante desselben Pakets entfernen (sonst doppelte Quelle)
    alt = q["datei"].replace(".sources", ".list")
    if os.path.exists(alt):
        os.unlink(alt)
    with open(q["datei"], "w", encoding="utf-8") as f:
        f.write(q["inhalt"])
    os.chmod(q["datei"], 0o644)


def rclone_version():
    exe = shutil.which("rclone")
    if not exe:
        return None
    out = subprocess.run([exe, "version"], capture_output=True, text=True).stdout
    m = re.match(r"rclone v(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def rclone_installieren():
    v = rclone_version()
    if v and v >= RCLONE_MIN:
        sag(f"rclone {v[0]}.{v[1]} ist aktuell genug.")
        return
    if verfuegbar("rclone"):
        lauf(["apt-get", "install", "-y", "rclone"])
        v = rclone_version()
        if v and v >= RCLONE_MIN:
            return
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(os.uname().machine)
    if not arch:
        raise RuntimeError("rclone: unbekannte Architektur – bitte von rclone.org installieren.")
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o755)  # apt liest die Datei als Benutzer _apt
        deb = os.path.join(tmp, "rclone.deb")
        laden(f"https://downloads.rclone.org/rclone-current-linux-{arch}.deb", deb)
        os.chmod(deb, 0o644)
        lauf(["apt-get", "install", "-y", deb])


def webapps_richtlinie(bid, apps, name=None):
    ordner = RICHTLINIEN_ORDNER[bid]
    pfad = os.path.join(ordner, f"{name}.json" if name else RICHTLINIEN_DATEI)
    if not apps:
        if os.path.exists(pfad):
            os.unlink(pfad)
            sag(f"Richtlinie entfernt: {pfad}")
        return
    liste = []
    for wid in apps:
        w = katalog.WEBAPP_NACH_ID[wid]
        liste.append({"url": w.url, "default_launch_container": "window",
                      "create_desktop_shortcut": False, "fallback_app_name": w.name})
    os.makedirs(ordner, exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump({"WebAppInstallForceList": liste}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(pfad, 0o644)
    sag(f"Richtlinie geschrieben: {pfad}")


def pruefen(auftrag):
    erlaubt = set(katalog.BASIS_PAKETE)
    for k in katalog.KOMPONENTEN:
        erlaubt.update(k.pakete)
    erlaubt.update({"python-nemo", "nemo-python"})
    for p in auftrag.get("pakete", []):
        if p not in erlaubt:
            raise SystemExit(f"Unbekanntes Paket im Auftrag: {p!r}")
    for b in auftrag.get("browser", []):
        if b not in katalog.BROWSER_NACH_ID:
            raise SystemExit(f"Unbekannter Browser: {b!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", auftrag.get("name", "style-kit")):
        raise SystemExit("Ungültiger Name im Auftrag.")
    for b, apps in auftrag.get("webapps", {}).items():
        if b not in RICHTLINIEN_ORDNER or any(a not in katalog.WEBAPP_NACH_ID for a in apps):
            raise SystemExit("Ungültige Web-App-Angabe im Auftrag.")


def main():
    if os.geteuid() != 0:
        sys.exit("Dieser Teil braucht Administratorrechte.")
    with open(sys.argv[1], encoding="utf-8") as f:
        auftrag = json.load(f)
    pruefen(auftrag)
    fehler = []

    browser = auftrag.get("browser", [])
    if browser:
        schritt("Paketquellen für Browser einrichten")
        for bid in browser:
            try:
                quelle_einrichten(bid)
            except Exception as e:  # noqa: BLE001 – weiter mit dem Rest
                fehler.append(f"Paketquelle {bid}: {e}")
                sag(f"FEHLER: {e}")

    schritt("Paketlisten aktualisieren")
    try:
        lauf(["apt-get", "update"])
    except RuntimeError as e:
        sag(f"Warnung: {e} – es wird trotzdem versucht.")

    pakete = [p for p in dict.fromkeys(auftrag.get("pakete", [])) if not installiert(p)]
    nicht_da = [p for p in pakete if not verfuegbar(p)]
    for p in nicht_da:
        sag(f"Hinweis: Paket {p} gibt es hier nicht – wird übersprungen.")
    pakete = [p for p in pakete if p not in nicht_da]
    if pakete:
        schritt("Systempakete installieren")
        try:
            lauf(["apt-get", "install", "-y", "--no-install-recommends", *pakete])
        except RuntimeError as e:
            fehler.append(f"Systempakete: {e}")

    for bid in browser:
        b = katalog.BROWSER_NACH_ID[bid]
        schritt(f"{b.name} installieren")
        try:
            lauf(["apt-get", "install", "-y", b.paket])
        except RuntimeError as e:
            fehler.append(f"{b.name}: {e}")

    if auftrag.get("rclone"):
        schritt("rclone für OneDrive")
        try:
            rclone_installieren()
        except Exception as e:  # noqa: BLE001
            fehler.append(f"rclone: {e}")
            sag(f"FEHLER: {e}")

    if "webapps" in auftrag:
        schritt("Outlook/Teams als Apps einrichten")
        for bid in RICHTLINIEN_ORDNER:
            try:
                webapps_richtlinie(bid, auftrag["webapps"].get(bid, []), auftrag.get("name"))
            except OSError as e:
                fehler.append(f"Richtlinie {bid}: {e}")

    for f in fehler:
        print(f"@@FEHLER {f}", flush=True)
    sys.exit(1 if fehler else 0)


if __name__ == "__main__":
    main()
