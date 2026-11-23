"""rclone-Anbindung: Binary finden, Anmeldung, Mount-Prozess, Remote-Control-API."""

import base64
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

MIN_VERSION = (1, 63)

# Lokalisierte Namen des OneDrive-Ordners "Persoenlicher Tresor"
VAULT_NAMES = ("Personal Vault", "Persönlicher Tresor", "Coffre-fort personnel",
               "Cassaforte personale", "Almacén personal", "Persoonlijke kluis",
               "Cofre Pessoal", "Osobisty magazyn")


def find_rclone(settings=None):
    custom = settings["rclone_path"] if settings else ""
    for cand in (custom, shutil.which("rclone"), "/usr/bin/rclone",
                 os.path.expanduser("~/.local/bin/rclone")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def rclone_version(binary):
    try:
        out = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"rclone v(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def fusermount():
    return shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount3"


def is_stale_mount(path):
    try:
        os.stat(path)
        return False
    except OSError as e:
        return e.errno in (107, 103)   # ENOTCONN, ECONNABORTED


def is_mounted(path):
    path = os.path.realpath(path)
    try:
        with open("/proc/self/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 1 and parts[1].replace("\\040", " ") == path:
                    return True
    except OSError:
        pass
    return False


def unmount(path, lazy=True):
    args = [fusermount(), "-u"] + (["-z"] if lazy else []) + [path]
    subprocess.run(args, capture_output=True)


# ---------------------------------------------------------------- Anmeldung

class AuthorizeProcess:
    """Fuehrt `rclone authorize onedrive` aus und liefert das Token.

    Den Browser oeffnen wir selbst: Die von rclone erzeugte Microsoft-Adresse wird um
    prompt=select_account ergaenzt (sonst meldet Microsoft automatisch das zuletzt benutzte
    Konto an, z. B. das private statt des geschaeftlichen) und je nach Kontotyp auf
    Firmen- bzw. Microsoft-Konten eingeschraenkt.
    """

    def __init__(self, binary, client_id="", client_secret="", tenant="", kind="",
                 login_hint=""):
        args = [binary, "authorize", "onedrive"]
        if tenant:
            blob = {"client_id": client_id, "client_secret": client_secret, "tenant": tenant}
            # rclone erwartet Base64 ohne Fuellzeichen (sonst „illegal base64 data“)
            args.append(base64.urlsafe_b64encode(json.dumps(blob).encode()).decode().rstrip("="))
        elif client_id:
            args += [client_id, client_secret]
        self.args = args + ["--auth-no-open-browser"]
        self.kind = kind
        self.login_hint = login_hint
        self.proc = None
        self.cancelled = False

    def _microsoft_url(self, local_url):
        """Lokale rclone-Adresse -> Microsoft-Anmeldeadresse mit Kontoauswahl."""
        import http.client
        u = urllib.parse.urlparse(local_url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        conn.request("GET", u.path + "?" + u.query)
        loc = conn.getresponse().getheader("Location") or ""
        conn.close()
        if not loc.startswith("https://login.microsoftonline.com/"):
            return local_url
        if self.kind == "business":
            loc = loc.replace("/common/", "/organizations/", 1)
        elif self.kind == "personal":
            loc = loc.replace("/common/", "/consumers/", 1)
        extra = {"prompt": "select_account"}
        if self.login_hint:
            extra["login_hint"] = self.login_hint
        return loc + "&" + urllib.parse.urlencode(extra)

    @staticmethod
    def _open_browser(url):
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass

    def run(self):
        """Blockiert bis zur Anmeldung. Gibt (token_json_str, None) oder (None, fehler) zurueck."""
        try:
            self.proc = subprocess.Popen(self.args, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            return None, str(e)
        out = []
        opened = False
        for line in self.proc.stdout:
            out.append(line)
            if not opened:
                m = re.search(r"(http://127\.0\.0\.1:\d+/auth\?state=[\w-]+)", line)
                if m:
                    opened = True
                    try:
                        url = self._microsoft_url(m.group(1))
                    except (OSError, ValueError):
                        url = m.group(1)
                    self._open_browser(url)
        self.proc.wait()
        text = "".join(out)
        if self.cancelled:
            return None, "Abgebrochen"
        m = re.search(r"--->\s*(.*?)\s*<---", text, re.S)
        token = _extract_token(m.group(1)) if m else None
        if not token:
            lines = [l for l in text.strip().splitlines() if "NOTICE" not in l]
            # nie Token oder andere lange Zeichenketten anzeigen (und Dialog nicht sprengen)
            tail = re.sub(r"\S{100,}", "…", "\n".join(lines[-5:]))
            return None, tail or "Anmeldung fehlgeschlagen"
        return token, None

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def _extract_token(blob):
    """Token aus der Ausgabe von `rclone authorize`: aeltere Versionen geben das Token-JSON aus,
    neuere einen Base64-Block mit einem JSON, das das Token enthaelt."""
    blob = blob.strip()
    if blob.startswith("{"):
        try:
            data = json.loads(blob)
        except ValueError:
            return None
    else:
        try:
            raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
            data = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    if "access_token" in data:
        return json.dumps(data)
    tok = data.get("token")
    if isinstance(tok, dict) and "access_token" in tok:
        return json.dumps(tok)
    if isinstance(tok, str):
        return _extract_token(tok)
    return None


def write_remote_config(acc_id, token, drive_id, drive_type, client_id="", client_secret="",
                        tenant=""):
    """Schreibt die rclone-Config fuer ein Konto (eigene Datei pro Konto)."""
    path = config.account_rclone_conf(acc_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [f"[{acc_id}]", "type = onedrive", f"token = {token}",
             f"drive_id = {drive_id}", f"drive_type = {drive_type}"]
    if client_id:
        lines += [f"client_id = {client_id}", f"client_secret = {client_secret}"]
    if tenant:
        lines.append(f"tenant = {tenant}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")


def read_remote_option(acc_id, key):
    try:
        with open(config.account_rclone_conf(acc_id)) as f:
            for line in f:
                if line.startswith(key + " ="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def update_token(acc_id, token):
    """Nach erneuter Anmeldung nur das Token ersetzen."""
    path = config.account_rclone_conf(acc_id)
    with open(path) as f:
        lines = f.read().splitlines()
    lines = [f"token = {token}" if l.startswith("token =") else l for l in lines]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")


def set_app(acc_id, client_id, client_secret, tenant):
    """App-Registrierung (Client-ID/-Secret, Tenant) eines Kontos ersetzen; leer = rclone-Standard."""
    path = config.account_rclone_conf(acc_id)
    with open(path) as f:
        lines = [l for l in f.read().splitlines()
                 if not l.startswith(("client_id =", "client_secret =", "tenant ="))]
    while lines and not lines[-1].strip():
        lines.pop()
    if client_id:
        lines += [f"client_id = {client_id}", f"client_secret = {client_secret}"]
    if tenant:
        lines.append(f"tenant = {tenant}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")


def sync_publisher_app(acc_ids, client_id, client_secret):
    """Neuen Clientschlüssel der Herausgeber-App in Konten übernehmen, die diese App schon
    nutzen (Schlüssel laufen nach höchstens 24 Monaten ab). Gibt die geänderten IDs zurück."""
    changed = []
    if not client_id or not client_secret:
        return changed
    for acc_id in acc_ids:
        if read_remote_option(acc_id, "client_id") != client_id:
            continue
        if read_remote_option(acc_id, "client_secret") == client_secret:
            continue
        try:
            set_app(acc_id, client_id, client_secret, read_remote_option(acc_id, "tenant"))
            changed.append(acc_id)
        except OSError:
            pass
    return changed


def read_token(acc_id):
    """Aktuelles Token aus der rclone-Config (rclone erneuert es selbst)."""
    try:
        with open(config.account_rclone_conf(acc_id)) as f:
            for line in f:
                if line.startswith("token ="):
                    return json.loads(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return None


# ---------------------------------------------------------------- Remote Control

class RcError(Exception):
    pass


class RcClient:
    def __init__(self, port, user, password):
        self.url = f"http://127.0.0.1:{port}/"
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Content-Type": "application/json", "Authorization": "Basic " + auth}

    def call(self, command, timeout=10, **params):
        req = urllib.request.Request(self.url + command, data=json.dumps(params).encode(),
                                     headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", str(e))
            except ValueError:
                msg = str(e)
            raise RcError(msg) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RcError(str(e)) from None


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- Mount

def prune_empty_dirs(path):
    """Leere Unterordner entfernen (z. B. ein Speicherordner, den ein Programm beim Anmelden
    vorsorglich anlegt). Dateien werden nie angefasst. True, wenn path danach leer ist."""
    for dirpath, _dirs, _files in os.walk(path, topdown=False):
        if dirpath != path:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    return not os.listdir(path)


AUTH_EXPIRED = "Anmeldung abgelaufen – bitte Konto in den Einstellungen neu anmelden."


def is_auth_error(msg):
    """Token widerrufen/abgelaufen: Weitere Versuche koennen nicht gelingen."""
    low = msg.lower()
    return ("invalid_grant" in low or "aadsts" in low or "interaction_required" in low
            or ("token" in low and "expired" in low))


# So lange darf rclone fuer das Einhaengen brauchen, bevor es neu gestartet wird
MOUNT_TIMEOUT = 180


def rescue_local_files(mount_path, acc_id):
    """Inhalt des (nicht eingehaengten) Mount-Ordners in einen Rettungsordner verschieben."""
    dest = base = os.path.join(config.STATE_DIR, "gerettet", acc_id,
                               time.strftime("%Y-%m-%d_%H-%M-%S"))
    n = 2
    while os.path.exists(dest):
        dest, n = f"{base}-{n}", n + 1
    os.makedirs(dest)
    for name in os.listdir(mount_path):
        shutil.move(os.path.join(mount_path, name), os.path.join(dest, name))
    return dest


def link_mountpoint(acc):
    """Sichtbaren Ordner (~/OneDrive - …) als Verknuepfung auf den versteckten Einhaengepunkt
    anlegen. Ein Einhaengeordner aus Version 1.1 wird ersetzt; lag darin etwas, wird es gerettet
    und der Rettungsordner zurueckgegeben."""
    link, real = acc.mount_path, acc.real_path
    os.makedirs(real, exist_ok=True)
    rescued = None
    if os.path.islink(link):
        if os.readlink(link) == real:
            return None
        os.unlink(link)
    elif os.path.isdir(link):
        if is_stale_mount(link) or is_mounted(link):
            unmount(link)
            time.sleep(0.5)
        if not prune_empty_dirs(link):
            rescued = rescue_local_files(link, acc.id)
        os.rmdir(link)
    elif os.path.lexists(link):
        raise OSError(f"{link} ist eine Datei – bitte umbenennen oder entfernen.")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(real, link)
    return rescued


class Mount:
    """Ein laufender `rclone mount` fuer ein Konto, inkl. Neustart bei Absturz."""

    def __init__(self, binary, account, settings, on_event):
        self.binary = binary
        self.account = account
        self.settings = settings
        self.on_event = on_event          # callback(account_id, kind, text)
        self.proc = None
        self.rc = None
        self.port = None
        self.data_dir = ""
        self.meta_dir = ""
        self.error = ""
        self._stop = threading.Event()
        self._thread = None
        self._restarts = 0
        self._nonempty = 0       # Fehlversuche, weil kurz vor dem Einhaengen etwas angelegt wurde
        self.rescued = None      # Ordner mit geretteten lokalen Dateien

    # -- Lebenszyklus

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, daemon=True,
                                        name=f"mount-{self.account.id}")
        self._thread.start()

    def stop(self, timeout=15):
        self._stop.set()
        p = self.proc
        if p and p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                p.kill()
        if is_mounted(self.account.real_path) or is_stale_mount(self.account.real_path):
            unmount(self.account.real_path)

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None and self.rc is not None

    def _prepare_mountpoint(self):
        path = self.account.real_path
        if is_stale_mount(path) or is_mounted(path):
            unmount(path)
            time.sleep(0.5)
        os.makedirs(path, exist_ok=True)
        self.rescued = link_mountpoint(self.account) or self.rescued
        if not prune_empty_dirs(path):
            # Dateien, die vor dem Verbinden lokal im Ordner gespeichert wurden (z. B. direkt
            # nach der Anmeldung). Nicht ueberdecken, sondern sichern und nach dem Verbinden
            # in OneDrive uebernehmen (siehe Engine.merge_rescued).
            self.rescued = rescue_local_files(path, self.account.id)

    def _args(self):
        acc, s = self.account, self.settings
        self.port = _free_port()
        user, pw = "odm", secrets.token_urlsafe(24)
        self.rc = None
        self._pending_rc = RcClient(self.port, user, pw)
        # Zugangsdaten per Umgebung statt Kommandozeile (sonst fuer jeden in `ps` lesbar)
        self._env = dict(os.environ, RCLONE_RC_USER=user, RCLONE_RC_PASS=pw)
        os.makedirs(config.LOG_DIR, exist_ok=True)
        args = [
            self.binary, "mount", acc.remote, acc.real_path,
            "--config", config.account_rclone_conf(acc.id),
            "--cache-dir", config.account_cache_dir(acc.id),
            "--vfs-cache-mode", "full",
            # Verdraengung steuern wir selbst (Speicheroptimierung, "Speicher freigeben")
            "--vfs-cache-max-age", "876000h",
            "--vfs-cache-poll-interval", "1m",
            "--vfs-write-back", "5s",
            # Aenderungen meldet Microsoft jede Minute (--poll-interval). Geloeschte Dateien fehlen
            # darin bei Geschaeftskonten aber manchmal – spaetestens nach 5 Minuten wird eine
            # Ordnerliste deshalb beim naechsten Zugriff neu geholt.
            "--dir-cache-time", "5m",
            # kleiner Vorauslade-Puffer: ein kurzer Blick (Dateityp-Erkennung) laedt nicht 16 MB
            "--buffer-size", "2M",
            "--poll-interval", "1m",
            "--volname", acc.label,
            "--rc", "--rc-addr", f"127.0.0.1:{self.port}",
            "--log-file", config.account_log_file(acc.id),
            "--log-level", "INFO",
            "--stats", "0",
        ]
        if acc.kind == "personal":
            # Persoenlicher Tresor ist per API gesperrt (ObjectHandle is Invalid) -> ausblenden
            for name in VAULT_NAMES:
                args += ["--exclude", f"/{name}/**"]
        if s["cache_max_gb"]:
            args += ["--vfs-cache-max-size", f"{int(s['cache_max_gb'])}G"]
        up, down = s["upload_limit_kbps"], s["download_limit_kbps"]
        if up or down:
            args += ["--bwlimit", f"{up or 'off'}k:{down or 'off'}k".replace("offk", "off")]
        return args

    def _supervise(self):
        while not self._stop.is_set():
            try:
                self._prepare_mountpoint()
                args = self._args()
            except Exception as e:  # noqa: BLE001 - Fehler an die Oberflaeche melden
                self.error = str(e)
                self.on_event(self.account.id, "error", self.error)
                self._stop.wait(30)
                continue
            self._rotate_log()
            self.proc = subprocess.Popen(args, env=self._env,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True)
            if self._wait_ready():
                self.error = ""
                self._restarts = 0
                self._nonempty = 0
                self.on_event(self.account.id, "mounted", "")
            stderr = ""
            try:
                _, stderr = self.proc.communicate()
            except Exception:  # noqa: BLE001
                pass
            self.rc = None
            if self._stop.is_set():
                break
            error = self._last_error(stderr)
            if getattr(self, "_start_timeout", False):
                self._start_timeout = False
                error = ("Keine Antwort von Microsoft beim Verbinden (Netzwerk). "
                         "Wird automatisch erneut versucht.")
            if "is not empty" in error and self._nonempty < 10:
                # Ein Programm (z. B. beim Anmelden) hat zwischen Aufraeumen und Einhaengen
                # schon wieder etwas angelegt: ohne Fehlermeldung gleich nochmal versuchen.
                self._nonempty += 1
                self._stop.wait(1)
                continue
            self.error = error or "rclone wurde unerwartet beendet"
            self.on_event(self.account.id, "error", self.error)
            if is_stale_mount(self.account.real_path) or is_mounted(self.account.real_path):
                unmount(self.account.real_path)
            if self.error == AUTH_EXPIRED:
                # Nicht alle paar Minuten mit dem widerrufenen Token anklopfen (sieht in den
                # Entra-Anmeldeprotokollen wie ein gestohlenes Token aus): warten, bis sich der
                # Benutzer neu anmeldet (Neu anmelden startet das Konto neu).
                self._stop.wait()
                break
            self._restarts += 1
            self._stop.wait(min(300, 5 * 2 ** min(self._restarts, 6)))
        self.on_event(self.account.id, "stopped", "")

    def _wait_ready(self):
        """Warten, bis rclone eingehaengt ist und seine Steuerung antwortet.

        Eingehaengt, aber die Steuerung antwortet langsam (z. B. nach einem Netzausfall): weiter
        warten statt aufzugeben – sonst gilt das Konto als offline, obwohl es laeuft.
        Nach MOUNT_TIMEOUT immer noch nicht eingehaengt (rclone haengt beim Start, z. B. weil das
        Netz beim Start weg war): beenden; der Aufrufer startet es mit Wartezeit neu."""
        start = time.monotonic()
        while True:
            if self.proc.poll() is not None or self._stop.is_set():
                return False
            waited = time.monotonic() - start
            if not is_mounted(self.account.real_path):
                if waited > MOUNT_TIMEOUT:
                    self._start_timeout = True
                    self.proc.terminate()
                    try:
                        self.proc.wait(10)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                    return False
                time.sleep(0.5)
                continue
            try:
                stats = self._pending_rc.call("vfs/stats", timeout=5)
                if stats.get("diskCache"):
                    dc = stats.get("diskCache", {})
                    self.data_dir, self.meta_dir = dc.get("path", ""), dc.get("pathMeta", "")
                    self.rc = self._pending_rc
                    return True
            except RcError:
                pass
            time.sleep(0.5 if waited < 60 else 5)

    def _rotate_log(self):
        path = config.account_log_file(self.account.id)
        try:
            if os.path.getsize(path) > 5 * 1024 * 1024:
                os.replace(path, path + ".1")
        except OSError:
            pass

    def _last_error(self, stderr):
        text = stderr or ""
        try:
            with open(config.account_log_file(self.account.id)) as f:
                f.seek(max(0, os.path.getsize(f.name) - 4000))
                text += f.read()
        except OSError:
            pass
        errs = [l for l in text.splitlines() if "ERROR" in l or "CRITICAL" in l or "Fatal" in l]
        if not errs:
            return ""
        msg = errs[-1]
        msg = re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d (ERROR|CRITICAL) : ", "", msg)
        if is_auth_error(msg):
            return AUTH_EXPIRED
        return msg[:300]

    # -- Bequemlichkeit

    def call(self, command, **params):
        if not self.rc:
            raise RcError("OneDrive ist nicht verbunden")
        return self.rc.call(command, **params)
