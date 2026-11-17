"""Nach neuen Versionen suchen und sie installieren (GitHub-Releases).

Ein Release besteht aus dem Tag ``vX.Y.Z``, dem Paket ``filefenster-X.Y.Z.tar.gz``,
dessen Prüfsumme ``filefenster-X.Y.Z.tar.gz.sha256`` und der Ed25519-Signatur
``filefenster-X.Y.Z.tar.gz.sig`` (gebaut von ``tools/make_release.sh``). Ohne
Prüfsumme wird nichts installiert.

Echtheit: Der öffentliche Schlüssel des Herausgebers ist in
``filefenster/release_key.pem`` fest eingetragen. Ist dort ein echter Schlüssel
hinterlegt, wird ein Paket nur installiert, wenn seine Signatur dazu passt
(geprüft mit ``openssl pkeyutl -verify``) – ein übernommenes GitHub-Konto allein
genügt dann nicht, um fremden Code auszuliefern. Steht dort nur der Platzhalter,
gilt wie bisher nur die Prüfsumme; das sagen Dialog und Protokoll ausdrücklich.

Installiert ist FileFenster als Kopie in ``~/.local/share/filefenster``
(``installieren.sh``). Ein Update wird zuerst daneben in einen versteckten
Ordner entpackt und geprüft und erst dann mit einem einzigen rename(2)
(RENAME_EXCHANGE) gegen die laufende Kopie getauscht. Bricht vorher etwas ab
(Netz, Prüfsumme, Absturz, volle Platte), bleibt die alte Version unverändert;
liegengebliebene Entpack-Ordner räumt die nächste Installation weg. Die
vorherige Version bleibt als ``~/.local/share/.filefenster-vorher`` liegen.

Alle Funktionen hier blockieren (Netzwerk, Dateien) – die Oberfläche
(update_ui.py) ruft sie nur in Threads auf.

Für Tests:
    FILEFENSTER_UPDATE_API      statt https://api.github.com/repos/Ikkarus13/filefenster
    FILEFENSTER_GITHUB_TOKEN    Zugangsdaten (sonst GITHUB_TOKEN, GH_TOKEN, gh auth token)

GitHub wird zuerst ohne Anmeldung gefragt. Zugangsdaten werden nur gelesen und
gesendet, wenn das Repository ohne sie nicht sichtbar ist (privat: 404/401) –
und nie an andere Rechner als GitHub (auch nicht nach einer Umleitung).
"""
import base64
import ctypes
import ctypes.util
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from . import APP_NAME, VERSION

REPO = "Ikkarus13/filefenster"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
# Ordner, aus dem dieses FileFenster läuft (der mit filefenster.py)
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_INTERVAL = 24 * 3600       # automatische Suche höchstens einmal am Tag
RETRY_INTERVAL = 3600            # nach einem Fehler (offline) frühestens nach einer Stunde
STALE_AGE = 3600                 # liegengebliebene Entpack-Ordner älter als das: weg
RUNTIME_FILES = ("filefenster", "assets", "filefenster.py", "installieren.sh",
                 "deinstallieren.sh", "README.md")
# Öffentlicher Schlüssel, mit dem Releases signiert sein müssen (siehe Datei)
RELEASE_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "release_key.pem")
KEY_PLACEHOLDER = "FILEFENSTER-PLATZHALTER"
# DER-Kopf eines öffentlichen Ed25519-Schlüssels (SubjectPublicKeyInfo, 44 Byte)
_ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")


class UpdateError(Exception):
    pass


class Cancelled(UpdateError):
    def __init__(self):
        super().__init__("Abgebrochen.")


# ---------------------------------------------------------------------- Style Kit
# Wurde FileFenster über ein Style Kit installiert, kommen Updates nur noch über
# das Kit: keine eigene Suche, kein Hinweis, keine Installation. Das Kit schreibt
# ${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json mit «kit», «komponenten»
# und «verwalten» (Befehl als Liste). Die Datei wird bei jedem Aufruf neu gelesen.
KIT_KENNUNG = "filefenster"


def _kit_datei():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")
    return os.path.join(base, "style-kit", "verwaltet.json")


def kit_info():
    """{"kit": Name, "verwalten": Befehl oder None}, wenn ein Style Kit FileFenster verwaltet."""
    try:
        with open(_kit_datei(), encoding="utf-8") as fh:
            data = json.load(fh)
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
    """Name des Style Kits, das FileFenster verwaltet, sonst None."""
    info = kit_info()
    return info["kit"] if info else None


def kit_meldung(kit):
    return f"Updates für {APP_NAME} kommen über das {kit}."


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


def _kit_sperre():
    kit = kit_verwaltet()
    if kit:
        raise UpdateError(kit_meldung(kit))


# ---------------------------------------------------------------------- Orte
def _home():
    return os.path.expanduser("~")


def data_home():
    d = os.environ.get("XDG_DATA_HOME")
    return d if d and os.path.isabs(d) else os.path.join(_home(), ".local", "share")


def cache_home():
    d = os.environ.get("XDG_CACHE_HOME")
    return d if d and os.path.isabs(d) else os.path.join(_home(), ".cache")


def install_dir():
    return os.path.join(data_home(), "filefenster")


def backup_dir():
    return os.path.join(data_home(), ".filefenster-vorher")


def download_dir():
    return os.path.join(cache_home(), "filefenster", "updates")


def log_file():
    return os.path.join(cache_home(), "filefenster", "update.log")


def launcher():
    return os.path.join(_home(), ".local", "bin", "filefenster")


def run_mode(root=None):
    """"installed" (Kopie in ~/.local/share/filefenster – nur die wird
    aktualisiert), "checkout" (Git-Arbeitsverzeichnis) oder "other"."""
    root = os.path.realpath(root or APP_ROOT)
    if root == os.path.realpath(install_dir()):
        return "installed"
    if os.path.exists(os.path.join(root, ".git")):
        return "checkout"
    return "other"


def log(text):
    try:
        os.makedirs(os.path.dirname(log_file()), exist_ok=True)
        with open(log_file(), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text.rstrip()}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------- Versionen
def parse_version(text):
    """'v1.2.10' -> (1, 2, 10); fehlende Teile zählen als 0."""
    parts = [int(p) for p in re.findall(r"\d+", text or "")[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def is_newer(version, current=None):
    if not version:
        return False
    return parse_version(version) > parse_version(current or VERSION)


class Release:
    def __init__(self, data, needs_auth=False):
        self.tag = data.get("tag_name") or ""
        self.version = self.tag.lstrip("vV")
        self.name = data.get("name") or self.tag
        self.notes = (data.get("body") or "").strip()
        self.page = data.get("html_url") or RELEASES_PAGE
        self.published = data.get("published_at") or ""
        self.asset = self.checksum = self.signature = None
        self.needs_auth = needs_auth    # privates Repository: Downloads mit Zugangsdaten
        self.trust = None               # nach download(): "signatur" oder "pruefsumme"
        assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
        tars = [a for a in assets if str(a.get("name", "")).endswith(".tar.gz")]
        want = f"filefenster-{self.version}.tar.gz"
        self.asset = next((a for a in tars if a.get("name") == want), tars[0] if tars else None)
        if self.asset is not None:
            self.checksum = next((a for a in assets
                                  if a.get("name") == self.asset["name"] + ".sha256"), None)
            self.signature = next((a for a in assets
                                   if a.get("name") == self.asset["name"] + ".sig"), None)

    @property
    def filename(self):
        name = os.path.basename(str(self.asset.get("name", ""))) if self.asset else ""
        if not re.fullmatch(r"[\w.+-]+\.tar\.gz", name):
            name = "filefenster-%d.%d.%d.tar.gz" % parse_version(self.version)
        return name

    @property
    def size(self):
        return (self.asset or {}).get("size") or 0


# ---------------------------------------------------------------------- HTTP
def api_base():
    return (os.environ.get("FILEFENSTER_UPDATE_API")
            or f"https://api.github.com/repos/{REPO}").rstrip("/")


def _token():
    """Zugangsdaten für ein privates Repository: Umgebung oder angemeldete GitHub-CLI.
    Nur aufrufen, wenn GitHub ohne Anmeldung nichts zeigt (siehe latest())."""
    for var in ("FILEFENSTER_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    gh = shutil.which("gh")
    if gh:
        try:
            r = subprocess.run([gh, "auth", "token"], capture_output=True, text=True,
                               timeout=10, stdin=subprocess.DEVNULL)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _auth_host(netloc):
    api = urllib.parse.urlsplit(api_base()).netloc
    return netloc == api or netloc == "github.com" or netloc.endswith(".github.com")


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Downloads leiten zu einem Speicherdienst mit signierter URL um; der darf
    das GitHub-Token nicht sehen (und lehnt es auch ab)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != \
                urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_DropAuthOnRedirect)


def _request(url, accept="application/vnd.github+json", token=None, timeout=30):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": f"filefenster/{VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if token and _auth_host(urllib.parse.urlsplit(url).netloc):
        req.add_header("Authorization", f"Bearer {token}")
    return _opener.open(req, timeout=timeout)


def _offline(exc):
    reason = getattr(exc, "reason", None) or exc
    return UpdateError(f"Keine Verbindung zu GitHub ({reason}). "
                       "Später erneut versuchen.")


def _api(path, token):
    """JSON von der API. None bei 404 – und bei 401 ohne Zugangsdaten (privat)."""
    try:
        with _request(api_base() + path, token=token) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404 or (e.code == 401 and token is None):
            return None
        if e.code in (401, 403):
            if token is None:
                raise UpdateError("GitHub verweigert den Zugriff (Abfragelimit erreicht). "
                                  "Später erneut versuchen.") from e
            raise UpdateError("GitHub verweigert den Zugriff (Anmeldung abgelaufen oder "
                              "Abfragelimit erreicht). Mit „gh auth login“ anmelden.") from e
        raise UpdateError(f"GitHub antwortet mit Fehler {e.code}.") from e
    except (urllib.error.URLError, OSError) as e:
        raise _offline(e) from e
    except ValueError as e:
        raise UpdateError("Unerwartete Antwort von GitHub.") from e


# ---------------------------------------------------------------------- Suchen
def latest():
    """Neuestes Release (ohne Vorabversionen) oder None, wenn es noch keins gibt.

    Zuerst ohne Anmeldung. Nur wenn GitHub dann nichts zeigt und auch das
    Repository selbst nicht sichtbar ist (privat), werden Zugangsdaten gelesen."""
    data = _api("/releases/latest", None)
    needs_auth = False
    if data is None:
        if isinstance(_api("", None), dict):
            return None                 # öffentlich, aber noch kein Release
        token = _token()
        if token is None:
            # Private Repositories sehen ohne Anmeldung aus wie nicht vorhanden
            raise UpdateError("Keine Veröffentlichung gefunden. Ist das Repository privat, "
                              "muss dafür die GitHub-CLI angemeldet sein (gh auth login).")
        needs_auth = True
        data = _api("/releases/latest", token)
        if data is None:
            return None
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError("Unerwartete Antwort von GitHub.")
    return Release(data, needs_auth)


def check():
    """(release, newer): newer ist True, wenn das Release neuer als diese Version ist.
    Verwaltet ein Style Kit FileFenster, ohne Anfrage (None, False)."""
    if kit_verwaltet():
        return None, False
    rel = latest()
    return rel, bool(rel and is_newer(rel.version))


# ---------------------------------------------------------------------- Signatur
def release_key():
    """Fest eingetragener öffentlicher Ed25519-Schlüssel als PEM-Text oder None,
    solange in release_key.pem nur der Platzhalter steht.

    Fehlt die Datei oder ist der Schlüssel unbrauchbar, ist das ein Fehler –
    nie ein stiller Rückfall auf die Prüfsumme."""
    try:
        with open(RELEASE_KEY_FILE, encoding="ascii", errors="replace") as fh:
            text = fh.read(16384)
    except OSError as e:
        raise UpdateError("Der Release-Schlüssel (filefenster/release_key.pem) fehlt – "
                          "Updates lassen sich nicht prüfen.") from e
    m = re.search(r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", text, re.S)
    if not m:
        if KEY_PLACEHOLDER in text:
            return None
        raise UpdateError("Der Release-Schlüssel (filefenster/release_key.pem) ist ungültig.")
    try:
        der = base64.b64decode("".join(m.group(1).split()), validate=True)
    except ValueError:
        der = b""
    if len(der) != len(_ED25519_SPKI) + 32 or not der.startswith(_ED25519_SPKI):
        raise UpdateError("Der Release-Schlüssel (filefenster/release_key.pem) ist kein "
                          "gültiger öffentlicher Ed25519-Schlüssel.")
    return ("-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode("ascii")
            + "\n-----END PUBLIC KEY-----\n")


def trust_note():
    """Hinweis für den Update-Dialog, wenn Updates nicht signaturgeprüft werden."""
    try:
        if release_key() is not None:
            return ""
    except UpdateError as e:
        return f"{e} Das Update wird nicht installiert."
    return ("Hinweis: In dieser Version ist noch kein Signaturschlüssel hinterlegt. Das "
            "Update wird nur über die Prüfsumme kontrolliert – dass es wirklich vom "
            "Herausgeber stammt, lässt sich so nicht bestätigen.")


def verify_signature(path, sig, key_pem):
    """True, wenn sig (64 Byte, Ed25519) die Datei path mit key_pem signiert."""
    if len(sig) != 64:
        return False
    exe = shutil.which("openssl")
    if not exe:
        raise UpdateError("openssl fehlt – die Signatur des Updates lässt sich nicht prüfen. "
                          "Bitte das Paket „openssl“ installieren.")
    os.makedirs(download_dir(), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sig-", dir=download_dir()) as d:
        kp, sp = os.path.join(d, "key.pem"), os.path.join(d, "paket.sig")
        with open(kp, "w", encoding="ascii") as fh:
            fh.write(key_pem)
        with open(sp, "wb") as fh:
            fh.write(sig)
        try:
            r = subprocess.run([exe, "pkeyutl", "-verify", "-pubin", "-inkey", kp, "-rawin",
                                "-in", path, "-sigfile", sp],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
                               env=dict(os.environ, LC_ALL="C"))
        except (OSError, subprocess.SubprocessError) as e:
            raise UpdateError(f"Die Signatur liess sich nicht prüfen: {e}") from e
    return r.returncode == 0 and b"Verified Successfully" in r.stdout


# ---------------------------------------------------------------------- Herunterladen
def _fetch_small(asset, token, limit=4096):
    with _request(asset["url"], accept="application/octet-stream", token=token) as r:
        return r.read(limit + 1)


def _expected_sum(rel, token):
    text = _fetch_small(rel.checksum, token).decode("utf-8", "replace")
    m = re.match(r"\s*([0-9a-fA-F]{64})\b", text)
    if not m:
        raise UpdateError("Die Prüfsummendatei des Releases ist ungültig.")
    return m.group(1).lower()


def download(rel, progress=None, cancel=None):
    """Paket nach download_dir() laden, die SHA-256-Prüfsumme und – sobald ein
    Release-Schlüssel eingetragen ist – die Ed25519-Signatur kontrollieren.

    progress(done, total) wird im Thread aufgerufen; cancel ist ein
    threading.Event. Gibt den Pfad des geprüften Pakets zurück; rel.trust sagt,
    wie es geprüft wurde ("signatur" oder nur "pruefsumme")."""
    _kit_sperre()
    if not rel.asset:
        raise UpdateError("Das Release enthält kein Installationspaket "
                          f"(filefenster-{rel.version}.tar.gz).")
    if not rel.checksum:
        raise UpdateError("Das Release enthält keine Prüfsumme (.sha256). "
                          "Aus Sicherheitsgründen wird es nicht installiert.")
    key = release_key()
    if key is not None and not rel.signature:
        log(f"Signatur fehlt für {rel.filename} – nicht installiert")
        raise UpdateError("Das Release ist nicht signiert (.sig fehlt). "
                          "Aus Sicherheitsgründen wird es nicht installiert.")
    rel.trust = None
    os.makedirs(download_dir(), exist_ok=True)
    dest = os.path.join(download_dir(), rel.filename)
    tmp = dest + ".part"
    token = _token() if rel.needs_auth else None
    try:
        expected = _expected_sum(rel, token)
        h = hashlib.sha256()
        with _request(rel.asset["url"], accept="application/octet-stream", token=token,
                      timeout=60) as r, open(tmp, "wb") as fh:
            total = int(r.headers.get("Content-Length") or rel.size or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = r.read(128 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if h.hexdigest() != expected:
            log(f"Prüfsumme falsch für {rel.filename}: {h.hexdigest()} statt {expected}")
            raise UpdateError("Die Prüfsumme des heruntergeladenen Pakets stimmt nicht. "
                              "Das Update wurde nicht installiert.")
        if key is not None:
            sig = _fetch_small(rel.signature, token, 1024)
            if not verify_signature(tmp, sig, key):
                log(f"Signatur ungültig für {rel.filename} – nicht installiert")
                raise UpdateError("Die Signatur des Pakets ist ungültig: Es stammt nicht vom "
                                  "Herausgeber von FileFenster oder wurde verändert. "
                                  "Das Update wurde nicht installiert.")
            rel.trust = "signatur"
            log(f"Signatur geprüft: {rel.filename}")
        else:
            rel.trust = "pruefsumme"
            log(f"WARNUNG: Signatur von {rel.filename} nicht geprüft – in "
                "filefenster/release_key.pem steht noch der Platzhalter statt eines "
                "Release-Schlüssels. Nur die Prüfsumme wurde kontrolliert.")
        os.replace(tmp, dest)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"Download fehlgeschlagen: GitHub meldet Fehler {e.code}.") from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"Download fehlgeschlagen: {getattr(e, 'reason', e)}") from e
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return dest


# ---------------------------------------------------------------------- Installieren
class Staged:
    """Entpacktes, geprüftes Paket neben der Installation (noch nicht aktiv)."""

    def __init__(self, work, root, version):
        self.work = work        # versteckter Ordner ~/.local/share/.filefenster-neu-…
        self.root = root        # darin der Programmordner
        self.version = version

    def discard(self):
        shutil.rmtree(self.work, ignore_errors=True)


def read_version(root):
    try:
        with open(os.path.join(root, "filefenster", "__init__.py"), encoding="utf-8") as fh:
            m = re.search(r'^VERSION\s*=\s*"([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def cleanup_stale(max_age=STALE_AGE):
    """Reste abgebrochener Installationen entfernen (nur ältere als max_age,
    damit ein gleichzeitig laufender Updater nicht gestört wird)."""
    now = time.time()
    for pat in (".filefenster-neu-*", ".filefenster-alt-*"):
        for p in glob.glob(os.path.join(data_home(), pat)):
            try:
                if now - os.lstat(p).st_mtime >= max_age:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass


def _extract(archive, work):
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(work, filter="data")
        except TypeError:   # Python ohne Entpack-Filter: Einträge selbst prüfen
            root = os.path.realpath(work)
            for m in tar.getmembers():
                p = os.path.realpath(os.path.join(work, m.name))
                if not p.startswith(root + os.sep) or m.issym() or m.islnk() or m.isdev():
                    raise UpdateError(f"Ungültiger Eintrag im Paket: {m.name}")
            tar.extractall(work)


def _find_root(work):
    for cand in [work] + sorted(glob.glob(os.path.join(work, "*"))):
        if os.path.isfile(os.path.join(cand, "filefenster.py")) and \
                os.path.isfile(os.path.join(cand, "filefenster", "__init__.py")):
            return cand
    raise UpdateError("Das Paket enthält kein FileFenster.")


def _verify_tree(root):
    """Jede .py-Datei muss sich übersetzen lassen (ohne .pyc zu schreiben)."""
    for missing in ("assets", "installieren.sh"):
        if not os.path.exists(os.path.join(root, missing)):
            raise UpdateError(f"Im Paket fehlt {missing}.")
    for dirpath, _dirs, files in os.walk(os.path.join(root, "filefenster")):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(dirpath, f)
                try:
                    with open(path, encoding="utf-8") as fh:
                        compile(fh.read(), path, "exec")
                except (SyntaxError, ValueError, UnicodeDecodeError, OSError) as e:
                    raise UpdateError(f"Das Paket ist beschädigt ({f}: {e}).") from e


def stage(archive, version=None):
    """Paket neben die Installation entpacken und prüfen. Ändert nichts an der
    laufenden Version; bei jedem Fehler bleibt nichts liegen."""
    _kit_sperre()
    base = data_home()
    os.makedirs(base, exist_ok=True)
    cleanup_stale()
    work = tempfile.mkdtemp(prefix=".filefenster-neu-", dir=base)
    try:
        _extract(archive, work)
        root = _find_root(work)
        got = read_version(root)
        if not got:
            raise UpdateError("Im Paket fehlt die Versionsnummer.")
        if version and parse_version(got) != parse_version(version):
            raise UpdateError(f"Das Paket enthält Version {got} statt {version}.")
        _verify_tree(root)
        os.chmod(root, 0o755)
        return Staged(work, root, got)
    except (tarfile.TarError, EOFError) as e:
        shutil.rmtree(work, ignore_errors=True)
        raise UpdateError(f"Das Paket lässt sich nicht entpacken: {e}") from e
    except OSError as e:
        shutil.rmtree(work, ignore_errors=True)
        raise UpdateError(f"Entpacken fehlgeschlagen: {e}") from e
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


def _exchange(a, b):
    """Zwei Ordner in einem Schritt tauschen (renameat2 mit RENAME_EXCHANGE).
    False, wenn das System oder Dateisystem das nicht kann."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fn = libc.renameat2
    except (OSError, AttributeError):
        return False
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(_AT_FDCWD, os.fsencode(a), _AT_FDCWD, os.fsencode(b), _RENAME_EXCHANGE) == 0:
        return True
    err = ctypes.get_errno()
    if err in (22, 38, 95):     # EINVAL, ENOSYS, EOPNOTSUPP: nicht unterstützt
        return False
    raise OSError(err, os.strerror(err), b)


def activate(staged):
    """Entpackte Version an die Stelle der installierten setzen.

    Mit RENAME_EXCHANGE ist der Tausch ein einziger Schritt; sonst zwei
    Umbenennungen, wobei ein Fehler beim zweiten Schritt zurückgerollt wird.
    Die alte Version wird zu backup_dir()."""
    _kit_sperre()
    target = install_dir()
    new = staged.root
    try:
        if os.path.lexists(target):
            if _exchange(new, target):
                old = new                   # liegt jetzt am Platz der neuen
            else:
                old = os.path.join(staged.work, ".alt")
                os.rename(target, old)
                try:
                    os.rename(new, target)
                except OSError:
                    os.rename(old, target)  # zurück: alte Version bleibt
                    raise
            shutil.rmtree(backup_dir(), ignore_errors=True)
            try:
                os.rename(old, backup_dir())
            except OSError:
                pass
        else:
            os.rename(new, target)
    except OSError as e:
        raise UpdateError(f"Die neue Version liess sich nicht einsetzen: {e}") from e
    finally:
        staged.discard()
    log(f"Version {staged.version} installiert in {target}")


def refresh_launcher():
    """installieren.sh --update der neuen Version: Befehl, Startmenü-Eintrag
    und Symbol erneuern (Standard-Dateimanager bleibt, wie er ist).
    Gibt (ok, ausgabe) zurück; ein Fehler hier lässt das Update bestehen."""
    script = os.path.join(install_dir(), "installieren.sh")
    try:
        r = subprocess.run(["bash", script, "--update"], cwd=install_dir(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=120)
        out = (r.stdout + r.stderr).strip()
        log(f"installieren.sh --update (Code {r.returncode}):\n{out}")
        return r.returncode == 0, out
    except (OSError, subprocess.SubprocessError) as e:
        log(f"installieren.sh --update fehlgeschlagen: {e}")
        return False, str(e)


def install(archive, version=None):
    """stage + activate + refresh_launcher in einem (für Tests und Skripte)."""
    staged = stage(archive, version)
    activate(staged)
    return refresh_launcher()


# ---------------------------------------------------------------------- Neustart
def restart_command(pid, wait_max=60):
    """Befehl, der wartet, bis Prozess pid beendet ist (höchstens wait_max
    Sekunden), und dann die installierte Version startet."""
    exe = launcher()
    cmd = [exe] if os.access(exe, os.X_OK) else \
        [sys.executable, os.path.join(install_dir(), "filefenster.py")]
    script = ('pid="$1"; n=0; shift; '
              'while kill -0 "$pid" 2>/dev/null && [ "$n" -lt ' + str(wait_max * 5) + ' ]; '
              'do sleep 0.2; n=$((n+1)); done; exec "$@"')
    return ["sh", "-c", script, f"{APP_NAME.lower()}-neustart", str(pid), *cmd]


def schedule_restart(session_file=None):
    """Neustart vormerken: läuft als eigene Sitzung weiter, wenn dieser
    Prozess endet. Die neue Instanz öffnet die Fenster aus session_file."""
    env = dict(os.environ)
    for k in ("FILEFENSTER_NO_UNIQUE", "FILEFENSTER_SNAPSHOT", "FILEFENSTER_SNAPSHOT_QUIT",
              "FILEFENSTER_START"):
        env.pop(k, None)
    if session_file:
        env["FILEFENSTER_SITZUNG"] = session_file
    subprocess.Popen(restart_command(os.getpid()), env=env, cwd=_home(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
