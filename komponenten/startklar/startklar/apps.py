"""Alle Programme (.desktop-Dateien) – Liste, Kategorien, Suche, Starten.

Einstellungs-Module (cinnamon-settings <modul>) gelten wie bei Windows nicht
als Apps: Sie erscheinen nur in der Suche unter „Einstellungen“.
"""
import glob
import json
import os
import time
import unicodedata

from gi.repository import Gdk, Gio, GLib

from .config import get_config

# Reihenfolge = Vorrang, wenn eine App mehrere Kategorien hat
CATEGORIES = [
    ("games", "Spiele", {"Game"}),
    ("dev", "Entwicklung", {"Development", "IDE"}),
    ("office", "Produktivität", {"Office", "Calendar", "ContactManagement",
                                 "Spreadsheet", "WordProcessor", "Presentation"}),
    ("internet", "Internet", {"Network", "WebBrowser", "Email", "Chat",
                              "InstantMessaging", "FileTransfer"}),
    ("creative", "Kreativität", {"Graphics", "2DGraphics", "3DGraphics",
                                 "RasterGraphics", "VectorGraphics", "Photography"}),
    ("media", "Unterhaltung", {"AudioVideo", "Audio", "Video", "Player", "Music"}),
    ("edu", "Bildung", {"Education", "Science"}),
    ("tools", "Dienstprogramme und Tools", {"Utility", "Accessories", "TextEditor"}),
    ("system", "System", {"System", "Settings", "Monitor", "PackageManager",
                          "HardwareSettings", "Security"}),
]
CATEGORY_LABEL = {k: label for k, label, _ in CATEGORIES}
CATEGORY_LABEL["other"] = "Andere"
MIN_CATEGORY = 3          # kleinere Kategorien landen unter „Andere“


def fold(text):
    """Vergleichsform: Gross/klein egal, ß = ss, Akzente/Umlaute ignoriert."""
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def letter_of(name):
    f = fold(name)[:1].upper()
    return f if "A" <= f <= "Z" else "#"


class AppEntry:
    __slots__ = ("id", "info", "name", "key", "gicon", "cats", "category",
                 "keywords", "is_setting", "letter", "added", "description",
                 "generic", "exe")

    def __init__(self, info):
        self.info = info
        self.id = info.get_id() or ""
        self.name = info.get_display_name() or info.get_name() or self.id
        self.key = fold(self.name)
        self.gicon = info.get_icon()
        self.cats = {c for c in (info.get_categories() or "").split(";") if c}
        self.description = info.get_description() or ""
        self.generic = info.get_generic_name() or ""
        self.keywords = " ".join(fold(k) for k in (info.get_keywords() or []))
        self.exe = os.path.basename(info.get_executable() or "")
        cmd = (info.get_commandline() or "").split()
        self.is_setting = bool(
            info.has_key("X-Cinnamon-Settings-Panel")
            or (self.exe == "cinnamon-settings" and len(cmd) > 1))
        self.category = "other"
        for key, _label, members in CATEGORIES:
            if self.cats & members:
                self.category = key
                break
        self.letter = letter_of(self.name)
        self.added = 0.0
        fn = info.get_filename()
        if fn:
            try:
                self.added = os.stat(fn).st_mtime
            except OSError:
                pass

    def actions(self):
        """Zusatzaktionen der .desktop-Datei (z. B. „Neues Fenster“)."""
        out = []
        for a in self.info.list_actions() or []:
            out.append((a, self.info.get_action_name(a) or a))
        return out


def normalize_id(app_id):
    # Cinnamon-Favoriten tragen bei Flatpaks ein Suffix „:flatpak“
    return app_id.split(":", 1)[0] if app_id else app_id


class AppIndex:
    def __init__(self):
        self.entries = []          # sichtbare Apps (ohne Einstellungs-Module)
        self.settings = []         # Einstellungs-Module
        self.by_id = {}
        self._listeners = []
        self._pending = 0
        self.load()
        self._monitor = Gio.AppInfoMonitor.get()
        self._monitor.connect("changed", self._on_changed)

    def connect_changed(self, cb):
        self._listeners.append(cb)

    def _on_changed(self, *_a):
        # .desktop-Dateien kommen bei Installationen oft schubweise
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(800, self._reload)

    def _reload(self):
        self._pending = 0
        self.load()
        for cb in self._listeners:
            cb()
        return False

    def load(self):
        entries, settings, by_id = [], [], {}
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo) or not info.should_show():
                continue
            e = AppEntry(info)
            if not e.id or e.id in by_id:
                continue
            by_id[e.id] = e
            (settings if e.is_setting else entries).append(e)
        entries.sort(key=lambda e: e.key)
        settings.sort(key=lambda e: e.key)
        self.entries, self.settings, self.by_id = entries, settings, by_id

    def get(self, app_id):
        app_id = normalize_id(app_id)
        e = self.by_id.get(app_id)
        if e is None and app_id:
            try:
                info = Gio.DesktopAppInfo.new(app_id)
            except TypeError:
                info = None
            if info is not None:
                e = AppEntry(info)
                self.by_id[app_id] = e
        return e

    # --------------------------------------------------------------- Kategorien
    def categories(self):
        """[(key, label, [entries nach Nutzung])] – kleine Gruppen unter „Andere“."""
        cfg = get_config()
        groups = {}
        for e in self.entries:
            groups.setdefault(e.category, []).append(e)
        other = groups.pop("other", [])
        out = []
        for key, label, _m in CATEGORIES:
            items = groups.get(key)
            if not items:
                continue
            if len(items) < MIN_CATEGORY:
                other.extend(items)
                continue
            out.append((key, label, items))
        if other:
            out.append(("other", CATEGORY_LABEL["other"], other))
        result = []
        for key, label, items in out:
            items = sorted(items, key=lambda e: (-cfg.score(e.id), e.key))
            result.append((key, label, items))
        return result

    def recently_added(self, days=7, limit=3):
        cutoff = time.time() - days * 86400
        new = [e for e in self.entries if e.added > cutoff]
        new.sort(key=lambda e: -e.added)
        return new[:limit]

    # -------------------------------------------------------------------- Suche
    def search(self, query, settings=False):
        q = fold(query).strip()
        if not q:
            return []
        cfg = get_config()
        out = []
        for e in (self.settings if settings else self.entries):
            s = _match_score(q, e)
            if s:
                out.append((s + min(cfg.score(e.id), 20), e))
        out.sort(key=lambda t: (-t[0], t[1].key))
        return out

    # ------------------------------------------------------------------ Starten
    def launch(self, entry, timestamp=0, action=None):
        display = Gdk.Display.get_default()
        ctx = display.get_app_launch_context() if display else None
        if ctx is not None and timestamp:
            ctx.set_timestamp(timestamp)
        try:
            if action:
                entry.info.launch_action(action, ctx)
            else:
                entry.info.launch([], ctx)
        except GLib.Error as e:
            print("Startklar: Start fehlgeschlagen:", entry.id, e.message)
            return False
        get_config().note_launch(entry.id)
        return True


def _match_score(q, e):
    name = e.key
    if name == q:
        return 120
    if name.startswith(q):
        return 100
    words = name.replace("-", " ").replace("_", " ").split()
    if any(w.startswith(q) for w in words):
        return 85
    initials = "".join(w[0] for w in words if w)
    if len(q) >= 2 and initials.startswith(q):
        return 70
    if q in name:
        return 60
    gen = fold(e.generic)
    if gen.startswith(q) or any(w.startswith(q) for w in gen.split()):
        return 55
    if q in e.keywords or q in fold(e.exe):
        return 45
    if len(q) >= 3 and (q in gen or q in fold(e.description)):
        return 25
    return 0


# ---------------------------------------------------------------- Vorgaben
def _gsettings_list(schema, key):
    src = Gio.SettingsSchemaSource.get_default()
    if src is None or src.lookup(schema, True) is None:
        return []
    try:
        value = Gio.Settings.new(schema).get_value(key).unpack()
    except Exception:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [v for v in value if isinstance(v, str)]


def _taskbar_pins():
    out = []
    base = os.path.expanduser("~/.config/cinnamon/spices/grouped-window-list@cinnamon.org")
    for fn in sorted(glob.glob(os.path.join(base, "*.json"))):
        try:
            with open(fn, encoding="utf-8") as f:
                out.extend(json.load(f).get("pinned-apps", {}).get("value", []))
        except (OSError, ValueError, AttributeError):
            pass
    return out


def default_pinned(index):
    """Erste Belegung von „Angeheftet“: Cinnamon-Favoriten, Taskleiste und
    die üblichen Standardprogramme."""
    ids = []

    def add(app_id):
        e = index.get(app_id) if app_id else None
        if e is not None and not e.is_setting and e.id not in ids:
            ids.append(e.id)

    for i in _gsettings_list("org.cinnamon", "favorite-apps"):
        add(i)
    for i in _taskbar_pins():
        add(i)
    for mime in ("x-scheme-handler/https", "inode/directory", "text/plain",
                 "application/vnd.oasis.opendocument.text", "image/jpeg"):
        info = Gio.AppInfo.get_default_for_type(mime, False)
        if info is not None:
            add(info.get_id())
    term = _gsettings_list("org.cinnamon.desktop.default-applications.terminal", "exec")
    for e in index.entries:
        if term and e.exe == term[0]:
            add(e.id)
            break
    for i in ("cinnamon-settings.desktop", "mintinstall.desktop", "mintupdate.desktop",
              "org.gnome.Calculator.desktop", "gnome-calculator.desktop",
              "gnome-system-monitor.desktop", "org.gnome.SystemMonitor.desktop",
              "libreoffice-writer.desktop", "libreoffice-calc.desktop",
              "thunderbird.desktop", "org.gnome.Screenshot.desktop",
              "gnome-screenshot.desktop", "schnipsel.desktop"):
        add(i)
    return ids[:24]
