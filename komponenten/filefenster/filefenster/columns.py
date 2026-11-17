"""Spalten der Detailansicht wie im Windows-Explorer: Rechtsklick auf den
Spaltenkopf blendet Spalten ein/aus, „Weitere…“ öffnet „Details auswählen“.

    OPTIONAL   [(key, Titel, Breite)] – Name ist immer da
    MENU_KEYS  Spalten direkt im Kopf-Menü (Rest unter „Weitere…“)
    text(key, gfile, info), sort_value(key, gfile, info)
"""
import datetime
import os

from gi.repository import Gdk, Gio, Gtk

from . import metadata, places

OPTIONAL = [
    ("mtime", "Änderungsdatum", 150),
    ("type", "Typ", 150),
    ("size", "Grösse", 90),
    ("ctime", "Erstelldatum", 150),
    ("atime", "Letzter Zugriff", 150),
    ("dims", "Abmessungen", 110),
    ("length", "Länge", 80),
    ("authors", "Autoren", 160),
    ("title", "Titel", 200),
    ("ext", "Dateierweiterung", 110),
    ("owner", "Besitzer", 110),
]
TITLES = {k: t for k, t, _w in OPTIONAL}
EXTRA = [k for k, _t, _w in OPTIONAL if k not in ("mtime", "type", "size")]
MENU_KEYS = ["mtime", "type", "size", "ctime", "atime", "dims", "length", "authors", "title"]
DEFAULT_VISIBLE = ["mtime", "type", "size"]


def _time(info, attr):
    if info is None or not info.has_attribute(attr):
        return 0
    return info.get_attribute_uint64(attr)


_btime_cache = {}


_libc = None


def _birthtime(gfile, info):
    """Erstelldatum: GIO kennt es oft nicht – dann direkt per statx (Linux)."""
    if info is not None and info.has_attribute("time::created"):
        return info.get_attribute_uint64("time::created")
    path = gfile.get_path() if gfile is not None else None
    if not path:
        return 0
    key = (path, _time(info, "time::modified"))
    if key in _btime_cache:
        return _btime_cache[key]
    sec = 0
    try:
        import ctypes
        global _libc
        if _libc is None:
            _libc = ctypes.CDLL(None, use_errno=True)
        buf = ctypes.create_string_buffer(256)
        # statx(AT_FDCWD, path, AT_SYMLINK_NOFOLLOW, STATX_BTIME, buf)
        if _libc.statx(-100, os.fsencode(path), 0x100, 0x800, buf) == 0 \
                and int.from_bytes(buf.raw[0:4], "little") & 0x800:
            sec = int.from_bytes(buf.raw[80:88], "little", signed=True)   # stx_btime.tv_sec
    except Exception:       # kein statx, ungültige Namen …
        sec = 0
    if len(_btime_cache) > 20000:
        _btime_cache.clear()
    _btime_cache[key] = sec
    return sec


def _date(ts):
    if not ts:
        return ""
    try:    # unsinnige Zeitstempel dürfen die Zelle nicht abstürzen lassen
        return datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except (ValueError, OverflowError, OSError):
        return ""


def _meta(gfile, info):
    if gfile is None or info is None:
        return {}
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        return {}
    return metadata.get(gfile.get_path(), _time(info, "time::modified"),
                        places.content_type(info)) or {}


def _ext(info):
    if info is None or info.get_file_type() == Gio.FileType.DIRECTORY:
        return ""
    name = info.get_display_name() or ""
    i = name.rfind(".")
    return name[i:].lower() if 0 < i < len(name) - 1 else ""


def text(key, gfile, info):
    if info is None:
        return ""
    if key == "ctime":
        return _date(_birthtime(gfile, info))
    if key == "atime":
        return _date(_time(info, "time::access"))
    if key == "ext":
        return _ext(info)
    if key == "owner":
        return info.get_attribute_string("owner::user") or ""
    m = _meta(gfile, info)
    if key == "dims" and m.get("dims"):
        w, h = m["dims"]
        return f"{w} x {h}"
    if key == "length" and m.get("length"):
        s = int(round(m["length"]))
        return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"
    if key in ("authors", "title"):
        return m.get(key, "")
    return ""


def sort_value(key, gfile, info):
    """Wert zum Sortieren (Zahl oder kleingeschriebener Text)."""
    if key == "ctime":
        return _birthtime(gfile, info)
    if key == "atime":
        return _time(info, "time::access")
    if key == "dims":
        d = _meta(gfile, info).get("dims") or (0, 0)
        return d[0] * d[1]
    if key == "length":
        return _meta(gfile, info).get("length") or 0
    return text(key, gfile, info).casefold()


def visible(config):
    cols = config.get("columns")
    if not isinstance(cols, list):
        return list(DEFAULT_VISIBLE)
    return [k for k in cols if k in TITLES]


class ChooseDialog(Gtk.Window):
    """„Details auswählen“: alle Spalten zum Ein-/Ausblenden."""

    def __init__(self, parent, config):
        super().__init__(title="Details auswählen")
        self.config = config
        for c in ("filefenster", "win-dialog"):
            self.get_style_context().add_class(c)
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
            self.set_modal(True)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        from .propsdialog import _fluent_button
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(24)
        box.pack_start(Gtk.Label(label="Wählen Sie die Details aus, die für die Elemente in "
                                       "diesem Ordner angezeigt werden sollen.", xalign=0),
                       False, False, 4)
        box.pack_start(Gtk.Label(label="Details:", xalign=0), False, False, 4)
        cur = visible(config)
        self.checks = {}
        name = Gtk.CheckButton(label="Name")
        name.set_active(True)
        name.set_sensitive(False)
        box.pack_start(name, False, False, 0)
        for key, title, _w in OPTIONAL:
            cb = Gtk.CheckButton(label=title)
            cb.set_active(key in cur)
            box.pack_start(cb, False, False, 0)
            self.checks[key] = cb
        area.pack_start(box, True, True, 0)
        footer = Gtk.Box()
        footer.get_style_context().add_class("win-footer")
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        for side in ("top", "bottom", "start", "end"):
            getattr(btns, f"set_margin_{side}")(16 if side in ("top", "bottom") else 24)
        ok = _fluent_button("OK", accent=True)
        ok.connect("clicked", lambda *_: self._ok())
        cancel = _fluent_button("Abbrechen")
        cancel.connect("clicked", lambda *_: self.destroy())
        btns.pack_start(ok, False, False, 0)
        btns.pack_start(cancel, False, False, 0)
        footer.pack_start(btns, True, True, 0)
        area.pack_end(footer, False, False, 0)
        self.add(area)
        self.connect("key-press-event", lambda _w, ev: (self.destroy(), True)[1]
                     if ev.keyval == Gdk.KEY_Escape else False)
        self.show_all()

    def _ok(self):
        cur = visible(self.config)
        keep = [k for k in cur if self.checks[k].get_active()]
        new = [k for k, _t, _w in OPTIONAL if self.checks[k].get_active() and k not in keep]
        self.config.set("columns", keep + new)
        self.destroy()

