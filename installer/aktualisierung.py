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
# Öffentlicher Ed25519-Schlüssel des Kits: Ist er vorhanden, wird jedes Update-Paket
# vor dem Entpacken gegen die .sig aus dem Release geprüft (sonst kein Update).
SCHLUESSEL = os.path.join(marke.ORDNER, "release_key.pem")
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
            "signatur": assets.get(name + ".sig"),
            "name": name, "seite": data.get("html_url") or ""}
    _stand_setzen(geprueft=time.time())
    if not neuer(version):
        return None
    if not info["paket"] or not info["pruefsumme"]:
        raise Fehler(f"Version {version}: Paket oder Prüfsumme fehlt im Release.")
    if os.path.exists(SCHLUESSEL) and not info["signatur"]:
        raise Fehler(f"Version {version} ist nicht signiert – Update abgelehnt.")
    return info


def _signatur_pruefen(paket, sig):
    import subprocess
    exe = shutil.which("openssl")
    if not exe:
        raise Fehler("openssl fehlt – die Signatur des Updates lässt sich nicht prüfen.")
    r = subprocess.run([exe, "pkeyutl", "-verify", "-pubin", "-inkey", SCHLUESSEL, "-rawin",
                        "-in", paket, "-sigfile", sig], capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or "Success" not in r.stdout:
        raise Fehler("Signatur des Update-Pakets ist ungültig – Update abgebrochen.")


def holen(info, log=print):
    """Paket laden, Prüfsumme und Signatur kontrollieren, entpacken. Gibt den Paketordner zurück."""
    try:
        return _holen(info, log)
    except Fehler:
        raise
    except (urllib.error.URLError, OSError, ValueError, tarfile.TarError) as e:
        raise Fehler(f"Update konnte nicht geladen werden: {getattr(e, 'reason', e)}") from e


def _holen(info, log):
    os.makedirs(CACHE, exist_ok=True)
    arbeit = tempfile.mkdtemp(prefix="update-", dir=CACHE)
    paket = os.path.join(arbeit, info["name"])
    log(f"Lade {info['name']} …")
    with _get(info["pruefsumme"], accept="application/octet-stream") as r:
        teile = r.read(4096).decode("ascii", "replace").split()
    soll = teile[0].lower() if teile else ""
    if not re.fullmatch(r"[0-9a-f]{64}", soll):
        raise Fehler("Prüfsummendatei des Releases ist ungültig.")
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
    if os.path.exists(SCHLUESSEL):
        if not info.get("signatur"):
            raise Fehler("Update ist nicht signiert – abgebrochen.")
        sig = paket + ".sig"
        with _get(info["signatur"], accept="application/octet-stream") as r, open(sig, "wb") as f:
            f.write(r.read(4096))
        _signatur_pruefen(paket, sig)
        log("Signatur in Ordnung.")
    log(f"Prüfsumme in Ordnung ({groesse // 1024} KB).")
    ziel = os.path.join(arbeit, "paket")
    os.makedirs(ziel)
    with tarfile.open(paket) as t:
        t.extractall(ziel, filter="data")
    ordner = [os.path.join(ziel, d) for d in os.listdir(ziel)]
    ordner = [d for d in ordner if os.path.isfile(os.path.join(d, "installer", "main.py"))]
    if len(ordner) != 1:
        raise Fehler("Paket hat einen unerwarteten Aufbau.")
    try:
        with open(os.path.join(ordner[0], "VERSION"), encoding="utf-8") as f:
            v = f.read().strip()
    except OSError:
        v = ""
    if v != info["version"]:
        raise Fehler(f"Paket enthält Version {v or '?'} statt {info['version']} – abgebrochen.")
    return ordner[0]


def befehl(ordner):
    return ["/usr/bin/python3", "-I", os.path.join(ordner, "installer", "main.py"),
            "--aktualisieren"]


def aufraeumen():
    shutil.rmtree(CACHE, ignore_errors=True)


def _stand():
    return ablauf.stand()


def _stand_setzen(**werte):
    ablauf.stand_setzen(**werte)


def _melden(titel, text, aktionen):
    """notify-send mit Knöpfen; gibt (zugestellt, gewählte Aktion) zurück."""
    import subprocess
    symbol = os.path.join(ablauf.ZIEL, "branding", "symbol.svg")
    cmd = ["notify-send", "-a", marke.PRODUKT, "-i", symbol]
    for name, beschriftung in aktionen:
        cmd += ["-A", f"{name}={beschriftung}"]
    try:
        p = subprocess.run(cmd + [titel, text], capture_output=True, text=True, timeout=6 * 3600)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return p.returncode == 0, p.stdout.strip()


def _oeffnen(cmd):
    import subprocess
    try:
        subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _update_melden():
    """True, wenn eine Update-Meldung gezeigt wurde."""
    try:
        info = pruefen()
    except Fehler:
        return False
    if not info or _stand().get("gemeldet") == info["version"]:
        return False
    ok, aktion = _melden(f"Update für {marke.PRODUKT}",
                         f"Version {info['version']} ist verfügbar "
                         f"(installiert: {installierte_version()}).",
                         [("oeffnen", "Aktualisieren")])
    if ok:
        _stand_setzen(gemeldet=info["version"])   # pro Version nur einmal melden
    if aktion == "oeffnen":
        _oeffnen(["/usr/bin/python3", "-I", os.path.join(ablauf.ZIEL, "installer", "main.py"),
                  "--verwalten"])
    return ok


def _erinnerung():
    """Einmal, einige Tage nach der Installation: «Gefällt es dir?» mit Bewerten/Unterstützen."""
    tage = marke.B.get("erinnerung_tage") or 0
    st = _stand()
    seit = st.get("installiert_am")
    if not marke.HAT_DANKE or not tage or st.get("erinnert") or not isinstance(seit, (int, float)):
        return
    if time.time() - seit < tage * 86400:
        return
    bew = marke.B.get("bewerten") or {}
    unt = marke.B.get("unterstuetzen") or {}
    aktionen = []
    if bew.get("link"):
        aktionen.append(("bewerten", bew.get("knopf") or "Bewerten"))
    if unt.get("link_frei"):
        aktionen.append(("unterstuetzen", "Unterstützen"))
    aktionen.append(("nein", "Nein danke"))
    ok, aktion = _melden(f"Gefällt dir das {marke.PRODUKT}?",
                         "Es ist kostenlos. Wenn es dir gefällt, freuen wir uns über eine "
                         "Bewertung – und wer mag, kann die Arbeit gerne unterstützen.",
                         aktionen)
    if ok:
        _stand_setzen(erinnert=True)          # nur einmal
    ziel = {"bewerten": bew.get("link"), "unterstuetzen": unt.get("link_frei")}.get(aktion)
    if ziel:
        _oeffnen(["xdg-open", ziel])


def melden_beim_anmelden():
    """Autostart: nach Updates suchen (Meldung mit «Aktualisieren», öffnet die Verwaltung);
    sonst einmalig, einige Tage nach der Installation, um eine Bewertung bitten."""
    if not _update_melden():
        _erinnerung()
    return 0
