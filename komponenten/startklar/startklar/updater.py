"""Update-Suche über GitHub-Releases (Ikkarus13/startklar).

Ein Release enthält startklar-X.Y.Z.tar.gz, .sha256 und .sig. Installiert wird
nur, wenn Prüfsumme UND Ed25519-Signatur (openssl, öffentlicher Schlüssel in
release_key.pem) stimmen – ein übernommenes GitHub-Konto allein reicht also
nicht, um fremden Code einzuspielen. Danach führt das entpackte Paket
„installieren.sh --update“ aus (Leiste bleibt unverändert, Dienst startet neu).

Zum Testen: STARTKLAR_UPDATE_API=http://127.0.0.1:PORT (eigener Server).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from . import ROOT, VERSION
from .config import get_config

REPO = "Ikkarus13/startklar"
KEY = os.path.join(ROOT, "startklar", "release_key.pem")
MAX_PACKAGE = 30 * 1024 * 1024


class UpdateError(Exception):
    pass


# ------------------------------------------------------------ Style Kit
# Wurde Startklar über ein Style Kit installiert, kommen Updates nur noch über
# das Kit: keine eigene Update-Suche, keine Installation. Das Kit schreibt dazu
# ${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json mit «kit», «komponenten»
# und «verwalten» (Befehl als Liste). Die Datei wird bei jedem Aufruf neu gelesen.
KIT_KENNUNG = "startklar"


def _kit_datei():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "style-kit", "verwaltet.json")


def kit_info():
    """{"kit": Name, "verwalten": Befehl oder None}, wenn ein Style Kit Startklar verwaltet."""
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
    """Name des Style Kits, das Startklar verwaltet, sonst None."""
    info = kit_info()
    return info["kit"] if info else None


def kit_meldung(kit):
    return f"Updates für Startklar kommen über das {kit}."


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


def api_base():
    return os.environ.get("STARTKLAR_UPDATE_API", "https://api.github.com").rstrip("/")


def parse_version(text):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", (text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(version, current=VERSION):
    a, b = parse_version(version), parse_version(current)
    return bool(a and b and a > b)


def _token():
    tok = get_config().get("github_token")
    if tok:
        return tok
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=8)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Asset-Downloads leiten zu einem Speicher-Host weiter – dorthin kein Token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and (urllib.parse.urlparse(newurl).netloc
                                != urllib.parse.urlparse(req.full_url).netloc):
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_DropAuthOnRedirect)


def _get(url, token=None, accept="application/vnd.github+json", limit=1024 * 1024):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "Startklar"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with _opener.open(req, timeout=30) as resp:
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub antwortet mit Fehler {e.code}.") from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError("Keine Verbindung zu GitHub.") from e
    if len(data) > limit:
        raise UpdateError("Antwort zu gross.")
    return data


def latest():
    """Neuestes Release: {"version", "notes", "assets": {name: url}, "token"}."""
    url = f"{api_base()}/repos/{REPO}/releases/latest"
    token = None
    try:
        data = _get(url)
    except UpdateError:
        token = _token()            # privates Repo: nur mit Token sichtbar
        if not token:
            raise UpdateError("Kein Zugriff auf die Releases (GitHub-Anmeldung fehlt: „gh auth login“).")
        data = _get(url, token)
    try:
        rel = json.loads(data)
        version = rel["tag_name"].lstrip("v")
        assets = {a["name"]: a["url"] for a in rel.get("assets", [])}
    except (ValueError, KeyError, TypeError) as e:
        raise UpdateError("Unerwartete Antwort von GitHub.") from e
    if not parse_version(version):
        raise UpdateError("Unbekanntes Versionsformat: " + version)
    return {"version": version, "notes": rel.get("body") or "", "assets": assets, "token": token}


def check():
    """Liefert das Release, wenn es neuer ist, sonst None (auch wenn ein Style Kit verwaltet)."""
    if kit_verwaltet():
        return None
    rel = latest()
    return rel if is_newer(rel["version"]) else None


def verify(package, sha_text, sig_path, key=KEY):
    digest = hashlib.sha256()
    with open(package, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    expected = (sha_text or "").split()[0].lower() if sha_text else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest.hexdigest() != expected:
        raise UpdateError("Prüfsumme des Update-Pakets stimmt nicht.")
    exe = shutil.which("openssl")
    if not exe:
        raise UpdateError("openssl fehlt – die Signatur lässt sich nicht prüfen.")
    res = subprocess.run([exe, "pkeyutl", "-verify", "-pubin", "-inkey", key, "-rawin",
                          "-in", package, "-sigfile", sig_path],
                         capture_output=True, text=True, timeout=30)
    if res.returncode != 0 or "Success" not in res.stdout:
        raise UpdateError("Signatur des Update-Pakets ist ungültig.")


def _extract(package, work):
    try:
        with tarfile.open(package, "r:gz") as tar:
            tar.extractall(work, filter="data")       # verweigert ../, absolute Pfade, Geräte
    except TypeError:
        raise UpdateError("Python ist zu alt für sicheres Entpacken (3.12 nötig).")
    except (tarfile.TarError, OSError) as e:
        raise UpdateError("Update-Paket ist beschädigt oder unsicher.") from e
    dirs = [d for d in os.listdir(work) if os.path.isdir(os.path.join(work, d))]
    if len(dirs) != 1:
        raise UpdateError("Update-Paket hat einen unerwarteten Aufbau.")
    root = os.path.join(work, dirs[0])
    for need in ("startklar.py", "installieren.sh", os.path.join("startklar", "__init__.py")):
        if not os.path.isfile(os.path.join(root, need)):
            raise UpdateError("Update-Paket ist unvollständig.")
    return root


def install(rel):
    """Herunterladen, prüfen, entpacken und den Installer starten."""
    kit = kit_verwaltet()
    if kit:
        raise UpdateError(kit_meldung(kit))
    version = rel["version"]
    name = f"startklar-{version}.tar.gz"
    assets = rel["assets"]
    for need in (name, name + ".sha256", name + ".sig"):
        if need not in assets:
            raise UpdateError(f"Im Release fehlt {need}.")
    token = rel.get("token")
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "startklar")
    os.makedirs(cache, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f"update-{version}-", dir=cache)
    package = os.path.join(work, name)
    sig = package + ".sig"
    octet = "application/octet-stream"
    with open(package, "wb") as f:
        f.write(_get(assets[name], token, octet, MAX_PACKAGE))
    with open(sig, "wb") as f:
        f.write(_get(assets[name + ".sig"], token, octet, 4096))
    sha = _get(assets[name + ".sha256"], token, octet, 4096).decode("ascii", "replace")
    verify(package, sha, sig)
    root = _extract(package, os.path.join(work, "paket"))
    if not is_newer(_read_version(root)):
        raise UpdateError("Das Paket enthält keine neuere Version.")
    log = open(os.path.join(cache, "update.log"), "a")
    _spawn(["bash", os.path.join(root, "installieren.sh"), "--update"],
                     cwd=root, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     start_new_session=True)
    return version


def _spawn(args, **kw):
    return subprocess.Popen(args, **kw)


def _read_version(root):
    try:
        with open(os.path.join(root, "startklar", "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""
