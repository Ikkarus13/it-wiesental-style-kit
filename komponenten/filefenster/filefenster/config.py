"""Einstellungen als JSON in ~/.config/filefenster/settings.json.

Zum Testen kann der Ordner mit FILEFENSTER_CONFIG_DIR umgebogen werden
(NIE XDG_CONFIG_HOME umbiegen).
"""
import json
import os

from gi.repository import GLib

CONFIG_DIR = os.environ.get("FILEFENSTER_CONFIG_DIR") or os.path.join(
    GLib.get_user_config_dir(), "filefenster")

# Bis 0.3.0 hiess das Programm "Durchblick": Einstellungen einmalig übernehmen
_OLD_DIR = os.path.join(GLib.get_user_config_dir(), "durchblick")
if not os.environ.get("FILEFENSTER_CONFIG_DIR") and not os.path.exists(CONFIG_DIR) \
        and os.path.isdir(_OLD_DIR):
    try:
        os.rename(_OLD_DIR, CONFIG_DIR)
    except OSError:
        pass
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    "view_mode": "details",        # Standardansicht für neue Ordner
    "folder_views": {},            # uri -> Ansicht
    "folder_sort": {},             # uri -> [spalte, aufsteigend]
    "folder_group": {},            # uri -> [spalte oder None, aufsteigend] (Gruppieren nach)
    "show_hidden": False,
    "show_extensions": True,
    "compact": False,              # "Kompaktansicht" (geringere Zeilenabstände)
    "nav_width": 240,
    "details_pane": False,
    "details_width": 300,
    "preview_pane": False,         # Vorschaufenster (Alt+P); schliesst den Detailbereich aus
    "preview_width": 360,
    "window_size": [1240, 780],
    "window_maximized": False,
    "pinned": None,                # None = Standard (XDG-Ordner); sonst Liste von URIs
    "address_history": [],         # zuletzt eingegebene Adressen
    "columns": ["mtime", "type", "size"],   # sichtbare Spalten der Detailansicht
    "network_drives": [],          # gemerkte Netzlaufwerke [{"uri": "smb://server/freigabe/"}]
    "theme": "auto",               # auto | light | dark
    # Ordneroptionen (folderoptions.py)
    "start_location": "home",      # „Datei-Explorer öffnen für“: home | thispc
    "open_new_window": False,      # Jeden Ordner in einem eigenen Fenster öffnen
    "single_click": False,         # Öffnen durch einfachen Klick (Auswahl durch Zeigen)
    "show_recent": True,           # Start: Zuletzt verwendete Dateien anzeigen
    "show_frequent": True,         # Start: Häufig verwendete Ordner anzeigen
    "folder_visits": {},           # uri -> [Anzahl, letzter Besuch] (häufig verwendete Ordner)
    "full_path_title": False,      # Vollständigen Pfad in der Titelleiste anzeigen
    "item_checkboxes": False,      # Kontrollkästchen zur Auswahl von Elementen verwenden
    "status_bar": True,            # Statusleiste anzeigen
    "mica": True,                  # Tab-Leiste/Navigationsbereich nach dem Desktop-Hintergrund tönen
    # Katalog (gallery.py)
    "gallery_folders": None,       # None = „Bilder“; sonst [{"path": …, "enabled": true}]
    "gallery_size": 160,           # Kantenlänge der Vorschaubilder
    # Updates (updater.py, update_ui.py)
    "update_check": True,          # täglich im Hintergrund nach Updates suchen
    "update_last_check": 0,        # Zeitpunkt der letzten erfolgreichen Suche
    "update_last_attempt": 0,      # letzter Versuch (auch offline)
    "update_known": "",            # zuletzt gefundene neuere Version (Hinweis an „···“)
}


def _plausible(key, value):
    """Gespeicherten Wert nur übernehmen, wenn er zum Typ des Standards passt
    (eine kaputte oder fremd geschriebene settings.json darf das Öffnen von
    Fenstern nicht verhindern). Unbekannte Schlüssel bleiben erhalten."""
    if key not in DEFAULTS:
        return True
    default = DEFAULTS[key]
    if default is None:
        return value is None or isinstance(value, (list, dict, str, int, float))
    if value is None:
        return False
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, (list, tuple)):
        if key == "window_size":
            return (isinstance(value, list) and len(value) == 2
                    and all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in value))
        return isinstance(value, list)
    return isinstance(value, type(default))


class Config:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self._save_id = 0
        self._listeners = []
        try:
            with open(CONFIG_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k, v in data.items():
                    if _plausible(k, v):
                        self._data[k] = v
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        if key in self._data:
            return self._data[key]
        return default

    def set(self, key, value):
        self._data[key] = value
        self._schedule_save()
        for cb in list(self._listeners):
            try:        # ein fehlerhafter Zuhörer darf die anderen nicht aufhalten
                cb(key, value)
            except Exception as exc:
                print(f"[filefenster] Einstellung {key}: {exc}")

    def connect_changed(self, callback):
        """callback(key, value) wird bei jedem set() aufgerufen."""
        self._listeners.append(callback)

    def disconnect_changed(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _schedule_save(self):
        if self._save_id:
            return
        self._save_id = GLib.timeout_add(400, self._save)

    def _save(self):
        self._save_id = 0
        self.save_now()
        return False

    def save_now(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_FILE + ".tmp"
            text = json.dumps(self._data, indent=1, ensure_ascii=False)
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                # Pfad mit ungültigem UTF-8 (z. B. Katalogordner): als \udcxx
                # schreiben – sonst schlüge jedes weitere Speichern fehl
                text = json.dumps(self._data, indent=1)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass


_config = None


def get_config():
    global _config
    if _config is None:
        _config = Config()
    return _config
