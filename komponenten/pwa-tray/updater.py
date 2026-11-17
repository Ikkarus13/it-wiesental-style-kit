"""Updates ueber GitHub-Releases: pruefen, herunterladen, einspielen.

Neue Version = neues Release mit Tag "vX.Y.Z". Ein Git-Checkout wird per "git pull" aktualisiert,
eine entpackte Installation durch Ueberschreiben der Dateien. Danach startet install.sh den
Helfer neu. Privates Repo: Token aus der Konfiguration ("update_token") oder von `gh auth token`.
"""
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from version import REPO, VERSION

APP_DIR = Path(__file__).resolve().parent

# ---------- Style Kit ----------
# Wurde PWA-Tray ueber ein Style Kit installiert, kommen Updates nur noch ueber das Kit:
# keine eigene Suche, keine Meldung, keine Installation. Das Kit schreibt
# ${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json mit "kit", "komponenten" und
# "verwalten" (Befehl als Liste). Die Datei wird bei jedem Aufruf neu gelesen.
KIT_KENNUNG = "pwa-tray"


def _kit_datei():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "style-kit", "verwaltet.json")


def kit_info():
    """{"kit": Name, "verwalten": Befehl oder None}, wenn ein Style Kit PWA-Tray verwaltet."""
    try:
        with open(_kit_datei(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    kit, komp, cmd = data.get("kit"), data.get("komponenten"), data.get("verwalten")
    if not isinstance(kit, str) or not kit.strip() or not isinstance(komp, list) \
            or KIT_KENNUNG not in komp:
        return None
    if not (isinstance(cmd, list) and cmd and all(isinstance(x, str) and x for x in cmd)):
        cmd = None
    return {"kit": kit.strip(), "verwalten": cmd}


def kit_verwaltet():
    """Name des Style Kits, das PWA-Tray verwaltet, sonst None."""
    info = kit_info()
    return info["kit"] if info else None


def kit_meldung(kit):
    return f"Updates für PWA-Tray kommen über das {kit}."


def kit_oeffnen():
    """Startet die Verwaltung des Kits (Befehl aus verwaltet.json, ohne Shell)."""
    info = kit_info()
    if not info or not info["verwalten"]:
        return False
    try:
        subprocess.Popen(info["verwalten"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def _ver(s):
    return tuple(int(p) if p.isdigit() else 0 for p in s.lstrip("vV").split("."))


def token(cfg_token=None):
    if cfg_token:
        return cfg_token
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _get(url, tok=None, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": "PWA-Tray/" + VERSION})
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    return urllib.request.urlopen(req, timeout=15)


def check(repo=REPO, tok=None):
    """Neueres Release als dict (version, notes, url, tarball) oder None. Wirft bei Netzfehlern.
    Verwaltet ein Style Kit PWA-Tray, ohne Anfrage None."""
    if kit_verwaltet():
        return None
    with _get(f"https://api.github.com/repos/{repo}/releases/latest", tok) as r:
        rel = json.load(r)
    v = rel.get("tag_name", "").lstrip("vV")
    if not v or _ver(v) <= _ver(VERSION):
        return None
    asset = next((a["url"] for a in rel.get("assets", []) if a["name"].endswith(".tar.gz")), None)
    return {"version": v, "notes": rel.get("body") or "", "url": rel.get("html_url"),
            "tarball": asset or rel["tarball_url"], "is_asset": bool(asset)}


def is_git():
    return (APP_DIR / ".git").exists()


def install(info, tok=None):
    """Spielt die neue Version ins Programmverzeichnis ein (danach run_installer)."""
    kit = kit_verwaltet()
    if kit:
        raise RuntimeError(kit_meldung(kit))
    if is_git():
        subprocess.run(["git", "-C", str(APP_DIR), "pull", "--ff-only"],
                       check=True, capture_output=True, text=True, timeout=120)
        return
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pkg.tar.gz"
        accept = "application/octet-stream" if info.get("is_asset") else "application/vnd.github+json"
        with _get(info["tarball"], tok, accept) as r, open(pkg, "wb") as f:
            shutil.copyfileobj(r, f)
        out = Path(tmp) / "x"
        with tarfile.open(pkg) as t:
            t.extractall(out, filter="data")
        src = next((p.parent for p in out.rglob("pwa_tray.py")
                    if (p.parent / "install.sh").exists()), None)
        if not src:
            raise RuntimeError("Paket enthält kein PWA-Tray")
        shutil.copytree(src, APP_DIR, dirs_exist_ok=True)


def run_installer():
    """install.sh losgeloest starten: beendet diese Instanz und startet die neue Version."""
    if kit_verwaltet():
        return
    subprocess.Popen(["bash", str(APP_DIR / "install.sh")], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, cwd=os.path.expanduser("~"))
