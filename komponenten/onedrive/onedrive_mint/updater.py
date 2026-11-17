"""Nach neuen Versionen suchen und sie installieren (GitHub-Releases).

Ein Release besteht aus dem Tag ``vX.Y.Z``, einem Paket ``onedrive-mint-X.Y.Z.tar.gz`` und
dessen Prüfsumme ``….tar.gz.sha256`` (erstellt von ``tools/make_release.sh``). Fehlt
Paket oder Prüfsumme, wird nicht aktualisiert.

Installiert wird wie bei Synology Drive ohne Root-Rechte: das Programm liegt in
``~/.local/share/onedrive-mint`` und wird vom ``install.sh --update`` des neuen Pakets ersetzt.
Danach startet sich OneDrive neu.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

API = f"https://api.github.com/repos/{config.UPDATE_REPO}"
UPDATE_LOG = os.path.join(config.LOG_DIR, "update.log")
CHECK_INTERVAL = 24 * 3600


def kit_verwaltet():
    """Name des Style Kits, das dieses Programm verwaltet, sonst None.

    Ein Style Kit trägt seine Komponenten in ~/.config/style-kit/verwaltet.json ein.
    Dann kommen Updates nur noch über das Kit, nicht mehr über GitHub-Releases."""
    pfad = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "style-kit", "verwaltet.json")
    try:
        with open(pfad, encoding="utf-8") as f:
            d = json.load(f)
        if "onedrive" in (d.get("komponenten") or []):
            return str(d.get("kit") or "Style Kit")
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    # Kit-Fassung: Datei .kit-verwaltet im Programmordner (Inhalt: Name des Kits) – gilt auch,
    # wenn verwaltet.json fehlt oder gelöscht wurde
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kit-verwaltet"),
                  encoding="utf-8") as f:
            return f.read().strip()[:80] or "Style Kit"
    except OSError:
        return None


def kit_befehl():
    """Befehl, der die Verwaltung des Kits öffnet (Liste), oder None."""
    pfad = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "style-kit", "verwaltet.json")
    try:
        with open(pfad, encoding="utf-8") as f:
            b = json.load(f).get("verwalten")
        return [str(x) for x in b] if isinstance(b, list) and b else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


class UpdateError(Exception):
    pass


class KitVerwaltet(UpdateError):
    """Kein Fehler: Updates kommen über ein Style Kit."""


def _kit_pruefen():
    kit = kit_verwaltet()
    if kit:
        from . import marke
        raise KitVerwaltet(f"Updates für {marke.NAME} kommen über das {kit}.")
    if not config.UPDATE_REPO:
        raise UpdateError("Für diese Ausgabe ist keine Update-Quelle eingerichtet.")


class Release:
    def __init__(self, data):
        self.tag = data.get("tag_name") or ""
        self.version = self.tag.lstrip("vV")
        self.name = data.get("name") or self.tag
        self.notes = (data.get("body") or "").strip()
        self.page = data.get("html_url") or f"https://github.com/{config.UPDATE_REPO}/releases"
        self.published = data.get("published_at") or ""
        self.asset = self.checksum = None
        for a in data.get("assets") or []:
            if a.get("name", "").endswith(".tar.gz"):
                self.asset = a
            elif a.get("name", "").endswith(".sha256"):
                self.checksum = a
        self.tarball = data.get("tarball_url")

    @property
    def filename(self):
        return self.asset["name"] if self.asset else f"onedrive-mint-{self.version}.tar.gz"

    @property
    def size(self):
        return self.asset.get("size") if self.asset else None


def parse_version(text):
    """'v1.2.10' -> (1, 2, 10); Unbekanntes zaehlt als 0."""
    return tuple(int(p) for p in re.findall(r"\d+", text or "")[:3]) or (0,)


def is_newer(version, current=config.VERSION):
    return parse_version(version) > parse_version(current)


# ------------------------------------------------------------------ HTTP

TOKEN_FILE = os.path.join(config.CONFIG_DIR, "github-token")


def _token():
    """Nur fuer ein privates Repository: ein eigenes, nur lesendes Token (fine-grained,
    „Contents: Read“ fuer dieses Repo) in ~/.config/onedrive-mint/github-token oder
    ODM_GITHUB_TOKEN. Zugangsdaten anderer Programme (z. B. der GitHub-CLI) werden nie gelesen."""
    tok = os.environ.get("ODM_GITHUB_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Downloads leiten zu einem Speicherdienst mit signierter URL um; der darf das
    GitHub-Token weder sehen noch akzeptiert er es."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != \
                urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_DropAuthOnRedirect)


def _request(url, accept="application/vnd.github+json", token=None):
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": f"onedrive-mint/{config.VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if token and urllib.parse.urlsplit(url).hostname == "api.github.com":
        req.add_header("Authorization", f"Bearer {token}")
    return _opener.open(req, timeout=30)


def _api(path, token):
    try:
        with _request(API + path, token=token) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code in (401, 403):
            raise UpdateError("GitHub verweigert den Zugriff (Anmeldung oder Abfragelimit).") from e
        raise UpdateError(f"GitHub antwortet mit Fehler {e.code}.") from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"Keine Verbindung zu GitHub: {getattr(e, 'reason', e)}") from e


# ------------------------------------------------------------------ Suchen

def latest():
    """Neuestes Release (ohne Vorabversionen) oder None, wenn es noch keins gibt."""
    _kit_pruefen()
    token = _token()
    data = _api("/releases/latest", token)
    if data is None:
        if token is None:
            # private Repos sehen ohne Anmeldung aus wie nicht vorhanden
            raise UpdateError("Keine Veröffentlichung gefunden. Ist das Repository privat, "
                              "wird ein lesendes Token in ~/.config/onedrive-mint/github-token "
                              "gebraucht.")
        return None
    return Release(data)


def check():
    """(release, newer): newer ist True, wenn das Release neuer als die laufende Version ist."""
    rel = latest()
    return rel, bool(rel and is_newer(rel.version))


# ------------------------------------------------------------------ Herunterladen

def _download(url, dest, token, progress=None, accept="application/octet-stream"):
    with _request(url, accept=accept, token=token) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def download(rel, dest, progress=None):
    """Paket nach ``dest`` laden und die Prüfsumme kontrollieren."""
    _kit_pruefen()
    token = _token()
    tmp = dest + ".part"
    try:
        if rel.asset:
            _download(rel.asset["url"], tmp, token, progress)
        else:
            raise UpdateError("Das Release enthält kein Paket.")
        if not rel.checksum:
            raise UpdateError("Das Release enthält keine Prüfsumme – Update abgelehnt.")
        if rel.checksum:
            with _request(rel.checksum["url"], accept="application/octet-stream",
                          token=token) as r:
                expected = r.read().decode("utf-8", "replace").split()[0].lower()
            h = hashlib.sha256()
            with open(tmp, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            if h.hexdigest() != expected:
                raise UpdateError("Die Prüfsumme des heruntergeladenen Pakets stimmt nicht. "
                                  "Das Update wurde nicht installiert.")
        os.replace(tmp, dest)
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"Download fehlgeschlagen: {getattr(e, 'reason', e)}") from e
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


# ------------------------------------------------------------------ Installieren

def _extract(archive, target):
    with tarfile.open(archive) as tar:
        try:
            tar.extractall(target, filter="data")
        except TypeError:   # Python < 3.12 ohne Filter: Pfade selbst pruefen
            root = os.path.realpath(target)
            for m in tar.getmembers():
                p = os.path.realpath(os.path.join(target, m.name))
                if not p.startswith(root + os.sep) or m.issym() or m.islnk() or m.isdev():
                    raise UpdateError(f"Ungültiger Eintrag im Paket: {m.name}")
            tar.extractall(target)
    for dirpath, _dirs, files in os.walk(target):
        if "install.sh" in files and os.path.isdir(os.path.join(dirpath, "onedrive_mint")):
            return dirpath
    raise UpdateError("Im Paket fehlt install.sh.")


def install(archive, progress=None):
    """Paket entpacken und dessen Installer im Update-Modus ausfuehren.

    Gibt ``True`` zurueck, wenn sich die Dateimanager-Erweiterung geaendert hat
    (Nemo/Nautilus muss dann neu gestartet werden)."""
    _kit_pruefen()
    work = tempfile.mkdtemp(prefix="onedrive-mint-update-")
    try:
        if progress:
            progress("Paket wird entpackt …")
        src = _extract(archive, work)
        if progress:
            progress("Neue Version wird installiert …")
        os.makedirs(os.path.dirname(UPDATE_LOG), exist_ok=True)
        with open(UPDATE_LOG, "a") as log:
            log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} Update aus {archive}\n")
            log.flush()
            r = subprocess.run(["bash", os.path.join(src, "install.sh"), "--update"],
                               cwd=src, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, timeout=600)
            log.write(r.stdout)
        if r.returncode != 0:
            tail = "\n".join(r.stdout.strip().splitlines()[-5:])
            raise UpdateError(f"Die Installation ist fehlgeschlagen (Code {r.returncode}).\n{tail}")
        return "ODM_EXTENSION_CHANGED" in r.stdout
    except (OSError, subprocess.SubprocessError, tarfile.TarError) as e:
        raise UpdateError(f"Installation fehlgeschlagen: {e}") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)


def restart_command(pid):
    """Befehl, der wartet, bis dieser Prozess beendet ist, und OneDrive neu startet.

    Ueber systemd-run laeuft der Neustart unabhaengig vom beendeten Prozess; sonst als
    eigene Sitzung."""
    exe = os.path.expanduser("~/.local/bin/onedrive-mint")
    script = (f"while kill -0 {int(pid)} 2>/dev/null; do sleep 0.5; done; "
              f"exec {shlex.quote(exe)} --background")
    if shutil.which("systemd-run"):
        env = [f"--setenv={k}={os.environ[k]}" for k in
               ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
                "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "XDG_RUNTIME_DIR", "PATH", "LANG")
               if os.environ.get(k)]
        return ["systemd-run", "--user", "--collect", "--quiet",
                f"--unit=onedrive-mint-update-{int(time.time())}", *env, "sh", "-c", script]
    return ["sh", "-c", script]


def schedule_restart():
    subprocess.Popen(restart_command(os.getpid()), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
