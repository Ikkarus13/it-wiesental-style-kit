"""Pfade, Kontenliste und Einstellungen.

Alles liegt im Benutzerverzeichnis:
  ~/.config/onedrive-mint/accounts.json         Kontenliste
  ~/.config/onedrive-mint/settings.json         globale Einstellungen
  ~/.config/onedrive-mint/accounts/<id>/rclone.conf   eigene rclone-Config pro Konto
  ~/.config/onedrive-mint/accounts/<id>/pins.json     "Immer behalten"/"Nur online"-Entscheidungen
  ~/.cache/onedrive-mint/<id>/                  rclone VFS-Cache pro Konto
  ~/.local/state/onedrive-mint/state.json       Laufzeitstatus fuer die Dateimanager-Erweiterung
"""

import json
import os
import re
import tempfile
import threading
from . import marke

APP_ID = "io.github.OneDriveMint"
APP_NAME = marke.NAME
VERSION = "2.4.2"
# Hier werden Updates gesucht (GitHub-Releases, siehe updater.py)
UPDATE_REPO = marke.UPDATE_REPO          # leer = keine eigene Update-Quelle

DBUS_NAME = APP_ID
DBUS_PATH = "/io/github/OneDriveMint"
DBUS_IFACE = "io.github.OneDriveMint"


def _xdg(var, default):
    return os.environ.get(var) or os.path.expanduser(default)


CONFIG_DIR = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "onedrive-mint")
CACHE_DIR = os.path.join(_xdg("XDG_CACHE_HOME", "~/.cache"), "onedrive-mint")
STATE_DIR = os.path.join(_xdg("XDG_STATE_HOME", "~/.local/state"), "onedrive-mint")
ACCOUNTS_FILE = os.path.join(CONFIG_DIR, "accounts.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOG_DIR = os.path.join(STATE_DIR, "logs")
# Echte Einhaengepunkte. Sie liegen unter einem Punkt-Ordner, damit GIO die Laufwerke weder auf
# dem Desktop noch in der Geraeteliste zeigt; ~/OneDrive - … ist eine Verknuepfung darauf.
MOUNT_DIR = os.path.join(STATE_DIR, "mnt")

DEFAULT_SETTINGS = {
    # Leistensymbol: auto (Cinnamon: weiss/schwarz nach Design, sonst symbolisch),
    # weiss, schwarz oder symbolisch
    "tray_icon": "auto",
    "autostart": True,
    "notifications": True,
    # Speicheroptimierung: nicht geoeffnete Dateien nach N Tagen wieder "nur online" (0 = nie)
    "storage_sense_days": 0,
    # Harte Obergrenze fuer den rclone-Cache in GB (0 = unbegrenzt).
    # Achtung: rclone verdraengt dabei auch "Immer behalten"-Dateien; die werden dann neu geladen.
    "cache_max_gb": 0,
    # In Nemo/Nautilus "In den Papierkorb" -> OneDrive-Papierkorb (online)
    "redirect_trash": True,
    "upload_limit_kbps": 0,
    "download_limit_kbps": 0,
    "paused": False,
    "rclone_path": "",
    # Ordnersicherung (Desktop/Dokumente/Bilder) – aus, bis in den Einstellungen aktiviert
    "folder_backup": None,
    # Einmal taeglich nach neuen Versionen suchen; uebersprungene Version
    "update_check": True,
    "update_skip": "",
    "updated_to": "",
    # OneDrive-Ordner einmalig zu den Nemo-Favoriten hinzugefuegt (seit 1.2)
    "favorites_added": False,
}

_lock = threading.RLock()


def atomic_write_json(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def account_dir(acc_id):
    return os.path.join(CONFIG_DIR, "accounts", acc_id)


def account_rclone_conf(acc_id):
    return os.path.join(account_dir(acc_id), "rclone.conf")


def account_pins_file(acc_id):
    return os.path.join(account_dir(acc_id), "pins.json")


def account_cache_dir(acc_id):
    return os.path.join(CACHE_DIR, acc_id)


def account_log_file(acc_id):
    return os.path.join(LOG_DIR, acc_id + ".log")


class Settings:
    def __init__(self):
        self.data = dict(DEFAULT_SETTINGS)
        self.data.update(read_json(SETTINGS_FILE, {}))

    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        with _lock:
            atomic_write_json(SETTINGS_FILE, self.data)


class Account:
    """Ein OneDrive-Konto (Personal oder Business)."""

    FIELDS = ("id", "kind", "display_name", "email", "org", "mount_path",
              "drive_id", "drive_type", "web_url", "enabled",
              "parent_id", "site_name", "library_name", "remote_path", "site_url", "host")

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.kind = kw.get("kind", "personal")          # personal | business
        self.display_name = kw.get("display_name", "")
        self.email = kw.get("email", "")
        self.org = kw.get("org", "")
        self.mount_path = kw.get("mount_path", "")
        self.drive_id = kw.get("drive_id", "")
        self.drive_type = kw.get("drive_type", "")
        self.web_url = kw.get("web_url", "")
        self.enabled = kw.get("enabled", True)
        # nur fuer kind == "library" (Teams-/SharePoint-Bibliothek)
        self.parent_id = kw.get("parent_id", "")
        self.site_name = kw.get("site_name", "")
        self.library_name = kw.get("library_name", "")
        self.remote_path = kw.get("remote_path", "")
        self.site_url = kw.get("site_url", "")
        self.host = kw.get("host", "")

    @property
    def remote(self):
        return self.id + ":" + self.remote_path

    @property
    def real_path(self):
        """Versteckter Einhaengepunkt; mount_path ist die sichtbare Verknuepfung darauf."""
        return os.path.join(MOUNT_DIR, self.id)

    @property
    def is_library(self):
        return self.kind == "library"

    @property
    def label(self):
        """Name wie im Windows-Client: 'OneDrive - Persoenlich' / 'OneDrive - Firma'."""
        if self.kind == "library":
            folder = self.remote_path.rsplit("/", 1)[-1] if self.remote_path else self.library_name
            return f"{self.site_name} - {folder}"
        if self.kind == "business":
            return "OneDrive - " + (self.org or "Geschäftlich")
        return "OneDrive - Persönlich"

    def to_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}


def load_accounts():
    data = read_json(ACCOUNTS_FILE, {"accounts": []})
    return [Account(**a) for a in data.get("accounts", [])]


def save_accounts(accounts):
    with _lock:
        atomic_write_json(ACCOUNTS_FILE, {"accounts": [a.to_dict() for a in accounts]})


def new_account_id(kind, hint, existing):
    base = re.sub(r"[^a-z0-9]+", "-", (hint or kind).lower()).strip("-")[:24] or kind
    base = f"{kind}-{base}" if not base.startswith(kind) else base
    acc_id, n = base, 2
    ids = {a.id for a in existing}
    while acc_id in ids:
        acc_id, n = f"{base}-{n}", n + 1
    return acc_id


def org_dir(org):
    """Ordner fuer Teams-/SharePoint-Bibliotheken einer Organisation, wie unter Windows: ~/Firma."""
    safe = (org or "SharePoint").replace("/", "-").strip() or "SharePoint"
    return os.path.join(os.path.expanduser("~"), safe)
