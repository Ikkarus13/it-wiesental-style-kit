"""Updates des Kits über die GitHub-Releases seines Repositorys (branding.json «update_repo»).

Die enthaltenen Programme suchen nicht selbst nach Updates, solange sie in
~/.config/style-kit/verwaltet.json eingetragen sind (schreibt ablauf._abschluss).
Neue Versionen kommen nur mit einem neuen Kit-Release:

    pruefen()          → Release-Info oder None (keine neuere Version)
    holen(info, log)   → Ordner des entpackten, geprüften neuen Pakets
    befehl(ordner)     → Aufruf, der die installierten Komponenten aktualisiert

Das Repository muss öffentlich sein; ein Token wird bewusst nie verwendet.
"""
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

from . import ablauf, marke

REPO = (marke.B.get("update_repo") or "").strip()
# WST_UPDATE_API nur für Tests (lokaler Server statt GitHub)
API = os.environ.get("WST_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases/latest"
STAND = os.path.join(ablauf.ZIEL, "update.json")
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                     marke.KURZ)
_MAX_PAKET = 200 * 1024 * 1024


class Fehler(Exception):
    pass


def _version_tupel(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def installierte_version():
    try:
        with open(os.path.join(ablauf.ZIEL, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ablauf.version()


def neuer(v, gegen=None):
    return _version_tupel(v) > _version_tupel(gegen or installierte_version())


def _get(url, accept="application/vnd.github+json", timeout=20):
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": f"{marke.KURZ}-updater"})
    return urllib.request.urlopen(req, timeout=timeout)


def pruefen():
    """Neuestes Release, wenn es neuer ist als die installierte Version, sonst None."""
    if not REPO and not os.environ.get("WST_UPDATE_API"):
        raise Fehler("Für dieses Kit ist keine Update-Quelle eingerichtet.")
    try:
        with _get(API) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Fehler("Keine Veröffentlichung gefunden.") from e
        raise Fehler(f"GitHub antwortet mit Fehler {e.code}.") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Fehler(f"Keine Verbindung: {getattr(e, 'reason', e)}") from e
    version = (data.get("tag_name") or "").lstrip("vV")
    name = f"{marke.KURZ}-{version}.tar.gz"
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    info = {"version": version, "notizen": (data.get("body") or "").strip(),
            "paket": assets.get(name), "pruefsumme": assets.get(name + ".sha256"),
            "name": name, "seite": data.get("html_url") or ""}
    _stand_setzen(geprueft=time.time())
    if not neuer(version):
        return None
    if not info["paket"] or not info["pruefsumme"]:
        raise Fehler(f"Version {version}: Paket oder Prüfsumme fehlt im Release.")
    return info


def holen(info, log=print):
    """Paket laden, Prüfsumme kontrollieren, entpacken. Gibt den Paketordner zurück."""
    os.makedirs(CACHE, exist_ok=True)
    arbeit = tempfile.mkdtemp(prefix="update-", dir=CACHE)
    paket = os.path.join(arbeit, info["name"])
    log(f"Lade {info['name']} …")
    with _get(info["pruefsumme"], accept="application/octet-stream") as r:
        soll = r.read(4096).decode("ascii", "replace").split()[0].lower()
    h = hashlib.sha256()
    groesse = 0
    with _get(info["paket"], accept="application/octet-stream", timeout=60) as r, \
            open(paket, "wb") as f:
        while True:
            block = r.read(1 << 16)
            if not block:
                break
            groesse += len(block)
            if groesse > _MAX_PAKET:
                raise Fehler("Paket ist unerwartet gross.")
            h.update(block)
            f.write(block)
    if h.hexdigest() != soll:
        raise Fehler("Prüfsumme stimmt nicht – Update abgebrochen.")
    log(f"Prüfsumme in Ordnung ({groesse // 1024} KB).")
    ziel = os.path.join(arbeit, "paket")
    os.makedirs(ziel)
    with tarfile.open(paket) as t:
        t.extractall(ziel, filter="data")
    ordner = [os.path.join(ziel, d) for d in os.listdir(ziel)]
    ordner = [d for d in ordner if os.path.isfile(os.path.join(d, "installer", "main.py"))]
    if len(ordner) != 1:
        raise Fehler("Paket hat einen unerwarteten Aufbau.")
    return ordner[0]


def befehl(ordner):
    return ["/usr/bin/python3", "-I", os.path.join(ordner, "installer", "main.py"),
            "--aktualisieren"]


def aufraeumen():
    shutil.rmtree(CACHE, ignore_errors=True)


def _stand():
    try:
        with open(STAND, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _stand_setzen(**werte):
    d = _stand()
    d.update(werte)
    try:
        os.makedirs(ablauf.ZIEL, exist_ok=True)
        with open(STAND, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass


def melden_beim_anmelden():
    """Autostart: einmal prüfen und bei neuer Version eine Meldung zeigen.
    Ein Klick auf «Aktualisieren» öffnet die Verwaltung des Kits."""
    import subprocess
    try:
        info = pruefen()
    except Fehler:
        return 0
    if not info or _stand().get("gemeldet") == info["version"]:
        return 0
    symbol = os.path.join(ablauf.ZIEL, "branding", "symbol.svg")
    try:
        p = subprocess.run(
            ["notify-send", "-a", marke.PRODUKT, "-i", symbol, "-A", "oeffnen=Aktualisieren",
             f"Update für {marke.PRODUKT}",
             f"Version {info['version']} ist verfügbar (installiert: {installierte_version()})."],
            capture_output=True, text=True, timeout=6 * 3600)
    except (OSError, subprocess.SubprocessError):
        return 0
    if p.returncode == 0:
        _stand_setzen(gemeldet=info["version"])   # pro Version nur einmal melden
    if p.stdout.strip() == "oeffnen":
        subprocess.Popen(["/usr/bin/python3", "-I", os.path.join(ablauf.ZIEL, "installer", "main.py"),
                          "--verwalten"], start_new_session=True)
    return 0
