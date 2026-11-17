"""Dateiliste (Inhaltsbereich eines Tabs) im Stil des Windows-11-Explorers.

Ansichten: Details (Gtk.TreeView), Extra grosse/Grosse/Mittelgrosse/Kleine
Symbole, Liste (spaltenweise wie Windows), Kacheln, Inhalt (Gtk.IconView mit
eigenem Text-Renderer für zeilenbegrenzte Namen).

Datenhaltung:
    self._entries  key(URI) -> (Gio.File, Gio.FileInfo)   alle Einträge
    self._rows     sichtbare Schlüssel in Anzeigereihenfolge
    self._skeys    Sortierschlüssel parallel zu _rows (für bisect)
    self.store     Gtk.ListStore in Anzeigereihenfolge (in der Listenansicht
                   spaltenweise umsortiert, mit leeren Platzhaltern)

Die öffentliche API ist im Klassen-Docstring von FolderView beschrieben.
"""
import bisect
import collections
import locale
import math
import os
import re
import subprocess
import threading

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango, PangoCairo  # noqa: E402

from . import archive, cloudstatus, columns, diskimage, dnd, fileops, icons, metadata, places, theme  # noqa: E402
from . import host as host_mod  # noqa: E402
from .menu import WinMenu  # noqa: E402
from .search import SearchJob  # noqa: E402
from . import folderoptions  # noqa: E402

COL_KEY, COL_NAME, COL_ICON, COL_INFO = range(4)
_COLS = [COL_KEY, COL_NAME, COL_ICON, COL_INFO]

VIEW_MODES = ["xl", "large", "medium", "small", "list", "details", "tiles", "content"]
VIEW_LABELS = {
    "xl": "Extra grosse Symbole",
    "large": "Grosse Symbole",
    "medium": "Mittelgrosse Symbole",
    "small": "Kleine Symbole",
    "list": "Liste",
    "details": "Details",
    "tiles": "Kacheln",
    "content": "Inhalt",
}
VIEW_ICONS = {
    "xl": "square",
    "large": "grid",
    "medium": "grid_dots",
    "small": "grid_kanban",
    "list": "apps_list",
    "details": "list",
    "tiles": "apps_list_detail",
    "content": "content_view",
}
ICON_SIZES = {"xl": 256, "large": 96, "medium": 48, "small": 16, "list": 16,
              "details": 16, "tiles": 48, "content": 32}
ZOOM_ORDER = ["details", "small", "medium", "large", "xl"]
SORT_COLUMNS = [("name", "Name"), ("mtime", "Änderungsdatum"), ("type", "Typ"), ("size", "Grösse")]
# erste Sortierrichtung beim Klick auf einen neuen Spaltenkopf (wie Windows)
SORT_DEFAULT_ASC = {"name": True, "mtime": False, "type": True, "size": False,
                    "ctime": False, "atime": False, "dims": False, "length": False}
# Sortierbar per Klick auf den Spaltenkopf (auch die zusätzlichen Spalten)
SORTABLE = dict(SORT_COLUMNS + [(k, t) for k, t, _w in columns.OPTIONAL if k not in ("mtime", "type", "size")])

# Gruppieren nach (wie Windows): Spalte → Gruppen; Datum ohne Angabe = neueste zuerst
GROUP_DEFAULT_ASC = {"name": True, "mtime": False, "type": True, "size": False}
DATE_GROUPS = ["Heute", "Gestern", "Anfang dieser Woche", "Letzte Woche", "Anfang dieses Monats",
               "Letzter Monat", "Anfang dieses Jahres", "Letztes Jahr", "Vor langer Zeit"]
SIZE_GROUPS = [("Leer (0 KB)", 0), ("Winzig (0 – 16 KB)", 16 << 10), ("Klein (16 KB – 1 MB)", 1 << 20),
               ("Mittel (1 – 128 MB)", 128 << 20), ("Gross (128 MB – 1 GB)", 1 << 30),
               ("Riesig (1 – 4 GB)", 4 << 30), ("Gigantisch (> 4 GB)", None)]
NAME_GROUPS = [("0 – 9", "0123456789"), ("A – H", "ABCDEFGH"), ("I – P", "IJKLMNOP"),
               ("Q – Z", "QRSTUVWXYZ"), ("Sonstige", "")]

# Ordneroptionen: Kontrollkästchen zur Auswahl, Auswahl durch Zeigen
CHECK_SIZE = 16
CHECK_CELL_W = 24          # Breite der Kästchen-Zelle vor dem Symbol (Details)
HOVER_SELECT_MS = 500      # „Auswahl durch Zeigen“ nach so langem Verweilen


def _date_group(ts, today):
    """Index in DATE_GROUPS (0 = Heute … 8 = Vor langer Zeit)."""
    import datetime
    if not ts:
        return 8
    try:
        d = datetime.date.fromtimestamp(ts)
    except (ValueError, OverflowError, OSError):     # unsinniger Zeitstempel
        return 8
    if d >= today:
        return 0
    if (today - d).days == 1:
        return 1
    week = today - datetime.timedelta(days=today.weekday())   # Woche ab Montag
    if d >= week:
        return 2
    if d >= week - datetime.timedelta(days=7):
        return 3
    month = today.replace(day=1)
    if d >= month:
        return 4
    if d >= (month - datetime.timedelta(days=1)).replace(day=1):
        return 5
    if d.year == today.year:
        return 6
    return 7 if d.year == today.year - 1 else 8


def group_of(column, info, today):
    """→ (Gruppenname, Ordnungswert); aufsteigend = kleiner Wert zuerst."""
    if column == "mtime":
        i = _date_group(mtime_of(info), today)
        return DATE_GROUPS[i], 8 - i
    if column == "size":
        if is_dir(info):
            return "Nicht angegeben", -1
        size = info.get_size()
        for i, (label, limit) in enumerate(SIZE_GROUPS):
            if limit is None or size <= limit:
                return label, i
    if column == "type":
        t = type_text(info)
        return t, t.casefold()
    import unicodedata
    name = info.get_display_name() or info.get_name() or ""
    ch = unicodedata.normalize("NFKD", name[:1]).upper()[:1]
    for i, (label, chars) in enumerate(NAME_GROUPS):
        if not chars or (ch and ch in chars):
            return label, i
    return NAME_GROUPS[-1][0], len(NAME_GROUPS) - 1


LOAD_BLOCK = 500
FLUSH_INTERVAL_US = 400_000
MONITOR_DELAY_MS = 250
TYPEAHEAD_US = 1_000_000
FADE_ALPHA = 120


# ---------------------------------------------------------------------------
# Sortierung und Formatierung
# ---------------------------------------------------------------------------
_num_rx = re.compile(r"(\d+)")


def _xfrm(s):
    try:
        return locale.strxfrm(s)
    except Exception:
        return s


def natural_key(name):
    """Natürliche Sortierung wie Windows: "Datei2" vor "Datei10", Gross/Klein egal."""
    out = []
    for i, part in enumerate(_num_rx.split(name.casefold())):
        if not part:
            continue
        if i % 2:
            out.append((0, int(part), ""))
        else:
            out.append((1, 0, _xfrm(part)))
    return tuple(out)


class _Rev:
    """Umgekehrte Ordnung für absteigende Sortierung (bisect braucht nur <)."""
    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k

    def __lt__(self, other):
        return other.k < self.k

    def __eq__(self, other):
        return self.k == other.k


def is_dir(info):
    if info is None:
        return False
    t = info.get_file_type()
    if t in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE):
        return True
    if t == Gio.FileType.SHORTCUT:     # Netzwerk-Ansicht: Server/Freigaben wie Ordner
        target = info.get_attribute_string("standard::target-uri") or ""
        return places.is_remote(target)
    return False


def mtime_of(info):
    if info is None or not info.has_attribute("time::modified"):
        return 0
    return info.get_attribute_uint64("time::modified")


_type_cache = {}


def type_text(info):
    if info is None:
        return ""
    if is_dir(info):
        return "Dateiordner"
    ct = places.content_type(info)
    t = _type_cache.get(ct)
    if t is None:
        t = places.type_description(info) or "Datei"
        _type_cache[ct] = t
    return t


_date_cache = {}


def date_text(info):
    ts = mtime_of(info)
    s = _date_cache.get(ts)
    if s is None:
        if len(_date_cache) > 20000:
            _date_cache.clear()
        s = _date_cache[ts] = places.format_date(ts)
    return s


def size_kb_text(info):
    if info is None or is_dir(info):
        return ""
    return places.format_size_kb(info.get_size())


def is_hidden_info(info):
    name = info.get_name() or ""
    return info.get_attribute_boolean("standard::is-hidden") or info.get_attribute_boolean("standard::is-backup") or name.startswith(".")


def _rgba(color):
    c = Gdk.RGBA()
    c.parse(color)
    return c


_check_cache = {}


def check_pixbuf(checked):
    """Kontrollkästchen im Fluent-Stil (Ordneroption „Kontrollkästchen zur
    Auswahl von Elementen verwenden“)."""
    key = (checked, theme.P["accent"], theme.P["accent_text"], theme.P["text2"], theme.P["control_bg"])
    pb = _check_cache.get(key)
    if pb is None:
        import cairo
        s = CHECK_SIZE
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, s, s)
        cr = cairo.Context(surf)
        x = y = 0.5
        w = h = s - 1
        r = 3.5
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        if checked:
            Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["accent"]))
            cr.fill()
            Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["accent_text"]))
            cr.set_line_width(1.5)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.move_to(4.2, 8.3)
            cr.line_to(6.8, 10.9)
            cr.line_to(11.8, 5.4)
            cr.stroke()
        else:
            Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["control_bg"]))
            cr.fill_preserve()
            Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["text2"]))
            cr.set_line_width(1)
            cr.stroke()
        surf.flush()
        pb = Gdk.pixbuf_get_from_surface(surf, 0, 0, s, s)
        _check_cache[key] = pb
    return pb


_fade_cache = {}


def faded(pb):
    """Halbtransparente Kopie (ausgeschnittene/versteckte Elemente)."""
    if pb is None:
        return None
    out = _fade_cache.get(pb)
    if out is None:
        if len(_fade_cache) > 3000:
            _fade_cache.clear()
        w, h = pb.get_width(), pb.get_height()
        out = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
        out.fill(0)
        pb.composite(out, 0, 0, w, h, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, FADE_ALPHA)
        _fade_cache[pb] = out
    return out


_link_cache = {}


def with_link_overlay(pb, size):
    """Verknüpfungspfeil unten links (symbolische Links) wie Windows."""
    if pb is None:
        return pb
    key = (pb, size)
    out = _link_cache.get(key)
    if out is not None:
        return out
    if len(_link_cache) > 2000:
        _link_cache.clear()
    s = 9 if size <= 16 else max(12, min(48, size // 3))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
           '<rect x="0.5" y="0.5" width="15" height="15" rx="2" fill="#ffffff" stroke="#9a9a9a"/>'
           '<path d="M5 11.5V9a3 3 0 0 1 3-3h3.2" fill="none" stroke="#1a5fb4" stroke-width="1.6"/>'
           '<path d="M9.2 3.6L11.8 6L9.2 8.4" fill="none" stroke="#1a5fb4" stroke-width="1.6"/></svg>')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(s, s)
    loader.write(svg.encode())
    loader.close()
    arrow = loader.get_pixbuf()
    out = pb.copy()
    y = out.get_height() - s
    if y >= 0 and s <= out.get_width():
        arrow.composite(out, 0, y, s, s, 0, y, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
    _link_cache[key] = out
    return out


# ---------------------------------------------------------------------------
# Vorschaubilder (Thread)
# ---------------------------------------------------------------------------
_PIXBUF_MIMES = None


def _pixbuf_mimes():
    global _PIXBUF_MIMES
    if _PIXBUF_MIMES is None:
        _PIXBUF_MIMES = {m for f in GdkPixbuf.Pixbuf.get_formats() for m in f.get_mime_types()}
    return _PIXBUF_MIMES


_THUMBNAILER_MIMES = None


def _thumbnailer_mimes():
    """MIME-Typen, für die das System einen Thumbnailer hat (Videos, PDF, Office …)."""
    global _THUMBNAILER_MIMES
    if _THUMBNAILER_MIMES is None:
        mimes = set()
        dirs = [os.path.join(d, "thumbnailers") for d in
                [GLib.get_user_data_dir()] + list(GLib.get_system_data_dirs())]
        for d in dirs:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                if not n.endswith(".thumbnailer"):
                    continue
                try:
                    with open(os.path.join(d, n), encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if line.startswith("MimeType="):
                                mimes.update(m.strip() for m in line[9:].split(";") if m.strip())
                except OSError:
                    pass
        _THUMBNAILER_MIMES = mimes
    return _THUMBNAILER_MIMES


def thumbnailable(mime):
    return mime in _pixbuf_mimes() or mime in _thumbnailer_mimes()


_factory = None
_factory_lock = threading.Lock()


def _thumb_factory():
    """Thumbnailer des Systems (Cinnamon) für Videos, PDFs, Office … oder None."""
    global _factory
    with _factory_lock:
        if _factory is None:
            try:
                gi.require_version("CinnamonDesktop", "3.0")
                from gi.repository import CinnamonDesktop
                _factory = CinnamonDesktop.DesktopThumbnailFactory.new(
                    CinnamonDesktop.DesktopThumbnailSize.LARGE)
            except Exception:
                _factory = False
        return _factory or None


def _same_file(a, b):
    """Dieselbe Datei (z. B. „A.txt“ und „a.txt“ auf FAT/NTFS/SMB)?"""
    try:
        flags = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
        ia = a.query_info("id::file", flags, None).get_attribute_string("id::file")
        ib = b.query_info("id::file", flags, None).get_attribute_string("id::file")
    except GLib.Error:
        return False
    return bool(ia) and ia == ib


def _load_scaled(path, size):
    """Bild höchstens size×size, kleine Bilder nicht vergrössern."""
    fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
    if fmt is None:
        return None
    from .mica import MAX_SVG_BYTES, too_many_pixels
    if too_many_pixels(fmt.get_name(), w, h) or (
            fmt.get_name() == "svg" and os.path.getsize(path) > MAX_SVG_BYTES):
        return None          # Dekompressionsbombe o. Ä. – nie im eigenen Prozess dekodieren
    if w and h and w <= size and h <= size:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
    else:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    if pb is not None:
        pb = pb.apply_embedded_orientation() or pb
    return pb


def _fit(pb, size):
    w, h = pb.get_width(), pb.get_height()
    if w <= size and h <= size:
        return pb
    f = min(size / w, size / h)
    return pb.scale_simple(max(1, int(w * f)), max(1, int(h * f)), GdkPixbuf.InterpType.BILINEAR)


def _make_thumbnail(job):
    size = job["size"]
    tp = job.get("thumb")
    path = job.get("path")
    mime = job.get("mime") or ""
    is_image = mime in _pixbuf_mimes()
    if tp:
        try:
            pb = _load_scaled(tp, size)
            if pb is not None and (not is_image or max(pb.get_width(), pb.get_height()) >= min(size, 128)
                                   or size <= 128):
                return pb
        except GLib.Error:
            pass
    if is_image and path and job.get("filesize", 0) < 120 * 1024 * 1024:
        try:
            return _load_scaled(path, size)
        except GLib.Error:
            return None
    if path and not job.get("failed") and not is_image:
        fac = _thumb_factory()
        if fac is None:
            return None
        uri, mt = job["uri"], job["mtime"]
        try:
            from .previewpane import factory_thumbnail      # abgeschottet
            pb = factory_thumbnail(fac, uri, path, mime, mt)
            return _fit(pb, size) if pb is not None else None
        except Exception:
            return None
    return None


class _ThumbLoader:
    """Ein Arbeitsthread; Ergebnisse per GLib.idle_add an deliver(ckey, pixbuf)."""

    def __init__(self, deliver):
        self._deliver = deliver
        self._cv = threading.Condition()
        self._queue = collections.deque()
        self._prio = collections.deque()
        self._queued = set()
        self._gen = 0
        self._stop = False
        self._thread = None
        self.cache = collections.OrderedDict()
        self.failed = set()

    def cached(self, ckey):
        with self._cv:
            pb = self.cache.get(ckey)
            if pb is not None:
                self.cache.move_to_end(ckey)
            return pb

    def request(self, ckey, job, front=False):
        with self._cv:
            if ckey in self.cache or ckey in self.failed:
                return
            if front:
                self._prio.append((self._gen, ckey, job))
            elif ckey in self._queued:
                return
            else:
                self._queue.append((self._gen, ckey, job))
            self._queued.add(ckey)
            self._cv.notify()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def reset(self):
        with self._cv:
            self._gen += 1
            self._queue.clear()
            self._prio.clear()
            self._queued.clear()

    def stop(self):
        with self._cv:
            self._stop = True
            self._queue.clear()
            self._prio.clear()
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while not self._queue and not self._prio and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                gen, ckey, job = (self._prio.pop() if self._prio else self._queue.popleft())
                self._queued.discard(ckey)
                if gen != self._gen or ckey in self.cache or ckey in self.failed:
                    continue
            try:
                pb = _make_thumbnail(job)
            except Exception:
                pb = None
            with self._cv:
                if gen != self._gen:
                    continue
                if pb is None:
                    self.failed.add(ckey)
                    continue
                self.cache[ckey] = pb
                while len(self.cache) > 1500:
                    self.cache.popitem(last=False)
            GLib.idle_add(self._deliver, ckey, pb)


# ---------------------------------------------------------------------------
# Text-Renderer für die Symbolansichten
# ---------------------------------------------------------------------------
def _no_hyphens():
    """Pango trennt beim Umbruch mitten im Wort mit "-" – Windows nicht."""
    try:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_insert_hyphens_new(False))
        return attrs
    except Exception:
        return None


_NO_HYPHENS = _no_hyphens()


class LabelCell(Gtk.CellRendererText):
    """Name (max. N Zeilen, Auslassungspunkte) plus graue Nebenzeilen.

    kind: 'name' (nur Name), 'tiles' (Name/Typ/Grösse), 'content' (Name/Typ),
          'content_right' (Änderungsdatum/Grösse).
    Feste Höhe je Modus (wie das gleichmässige Raster in Windows) – das hält
    das Layout auch bei vielen tausend Einträgen schnell.
    Bearbeitbar (Umbenennen) über mode=EDITABLE; Signal "edited"(path, text)
    (geerbt von CellRendererText, dessen C-Eigenschaft "text" das Layout
    tausender Einträge spürbar schneller macht als eine Python-Eigenschaft).
    """
    info = GObject.Property(type=Gio.FileInfo)

    def __init__(self):
        super().__init__()
        self.kind = "name"
        self.lines = 1
        self.nat_width = 60
        self._lh = 0
        self._entry = None
        self._finished = True

    # -- Masse ------------------------------------------------------------
    def reset_metrics(self):
        self._lh = 0

    def line_height(self, widget):
        if not self._lh:
            layout = widget.create_pango_layout("Ägj")
            self._lh = layout.get_pixel_size()[1]
        return self._lh

    def n_lines(self):
        return {"name": self.lines, "tiles": 3, "content": 2, "content_right": 2}.get(self.kind, 1)

    def _height(self, widget):
        return self.n_lines() * self.line_height(widget) + 2 * self.props.ypad

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_get_preferred_width(self, widget):
        w = self.nat_width + 2 * self.props.xpad
        return w, w

    def do_get_preferred_height(self, widget):
        h = self._height(widget)
        return h, h

    def do_get_preferred_height_for_width(self, widget, width):
        h = self._height(widget)
        return h, h

    def do_get_preferred_width_for_height(self, widget, height):
        return self.do_get_preferred_width(widget)

    # -- Inhalt ----------------------------------------------------------
    def _content(self):
        """[(text, art, max_zeilen)]; art: 'p' Hauptfarbe, 's' grau, 'm' Markup."""
        info = self.props.info
        text = self.props.text
        if self.kind == "name":
            return [(text, "p", self.lines)]
        if info is None:
            return [(text, "p", 1)]
        if self.kind == "tiles":
            out = [(text, "p", 1), (type_text(info), "s", 1)]
            if not is_dir(info):
                out.append((places.format_size(info.get_size()), "s", 1))
            return out
        if self.kind == "content":
            return [(text, "p", 1), (type_text(info), "s", 1)]
        # content_right
        t2 = GLib.markup_escape_text(theme.P["text2"])
        out = [(f'<span foreground="{t2}">Änderungsdatum:</span> '
                + GLib.markup_escape_text(date_text(info)), "m", 1)]
        if not is_dir(info):
            out.append((f'<span foreground="{t2}">Grösse:</span> '
                        + GLib.markup_escape_text(places.format_size(info.get_size())), "m", 1))
        return out

    def do_render(self, cr, widget, background_area, cell_area, flags):
        content = self._content()
        if not content or not any(c[0] for c in content):
            return
        xpad, ypad = self.props.xpad, self.props.ypad
        x = cell_area.x + xpad
        w = max(1, cell_area.width - 2 * xpad)
        lh = self.line_height(widget)
        xalign = self.props.xalign
        align = (Pango.Alignment.CENTER if xalign == 0.5 else
                 Pango.Alignment.RIGHT if xalign > 0.5 else Pango.Alignment.LEFT)
        layouts = []
        total = 0
        for text, art, maxl in content:
            layout = widget.create_pango_layout(None)
            if art == "m":
                layout.set_markup(text, -1)
            else:
                layout.set_text(text, -1)
            layout.set_width(w * Pango.SCALE)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            if _NO_HYPHENS is not None and art != "m":
                layout.set_attributes(_NO_HYPHENS)
            if maxl > 1:
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
                layout.set_height(-maxl)
            layout.set_alignment(align)
            h = max(lh, layout.get_pixel_size()[1]) if text else lh
            layouts.append((layout, art, h))
            total += h
        avail = cell_area.height - 2 * ypad
        y = cell_area.y + ypad + max(0, avail - total) * self.props.yalign
        ctx = widget.get_style_context()
        sec = _rgba(theme.P["text2"])
        for layout, art, h in layouts:
            if art == "s":
                cr.save()
                Gdk.cairo_set_source_rgba(cr, sec)
                cr.move_to(x, y)
                PangoCairo.show_layout(cr, layout)
                cr.restore()
            else:
                Gtk.render_layout(ctx, cr, x, y, layout)
            y += h

    # -- Umbenennen ------------------------------------------------------
    def do_start_editing(self, event, widget, path, background_area, cell_area, flags):
        entry = Gtk.Entry()
        entry.get_style_context().add_class("rename-entry")
        entry.set_text(self.props.text)
        entry.set_has_frame(True)
        if self.props.xalign == 0.5:
            entry.set_alignment(0.5)
        entry.connect("editing-done", self._on_editing_done, path)
        entry.connect("focus-out-event", self._on_focus_out)
        entry.show()
        self._entry = entry
        self._finished = False
        return entry

    def _on_editing_done(self, entry, path):
        if self._finished:
            return
        self._finished = True
        canceled = entry.get_property("editing-canceled")
        self.stop_editing(canceled)
        self._entry = None
        if not canceled:
            self.emit("edited", path, entry.get_text())

    def _on_focus_out(self, entry, _event):
        if not self._finished:
            entry.editing_done()
            entry.remove_widget()
        return False


# ---------------------------------------------------------------------------
# Dateiliste
# ---------------------------------------------------------------------------
class FolderView(Gtk.Box):
    """Inhaltsbereich mit allen Windows-Ansichten.

    Öffentliche API (vom Fenster benutzt):
        uri, view_mode, sort_column, sort_ascending
        load(uri), reload(), is_loading()
        set_view_mode(mode), set_sort(column, ascending=None)
        set_show_hidden(b), set_show_extensions(b), set_compact(b)
        set_item_checkboxes(b), set_single_click(b), reapply_folder_settings()
        get_selected(), get_selected_infos(), item_count(), selected_size()
        select_all(), select_none(), invert_selection()
        begin_rename(), cut(), copy(), paste(), can_paste()
        delete(permanent=False), new_folder(), new_file(template_path=None)
        copy_path(), open_selected(), zoom(delta), focus_view()
        start_search(root_uri, query), stop_search(), is_searching()
        build_view_submenu(), build_sort_menu(), build_new_menu()
    """

    def __init__(self, host):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_style_context().add_class("content-area")
        self.host = host
        cfg = host.config
        self._show_hidden = bool(cfg.get("show_hidden", False))
        self._show_ext = bool(cfg.get("show_extensions", True))
        self._compact = bool(cfg.get("compact", False))
        # Ordneroptionen: Kontrollkästchen, Öffnen durch einfachen Klick
        self._checks = bool(cfg.get("item_checkboxes", False))
        self._single = bool(cfg.get("single_click", False))
        self._hover_key, self._hover_on_name, self._hover_win = None, False, None
        self._hover_timer = 0
        self._click_open = None
        self._opened_at = 0

        self.uri = None
        self._gfile = None
        self.view_mode = cfg.get("view_mode", "details") or "details"
        if self.view_mode not in VIEW_MODES:
            self.view_mode = "details"
        self.sort_column = "name"
        self.sort_ascending = True

        self._entries = {}
        self._rows = []
        self._skeys = []
        self._index_cache = None
        self._gen = 0
        self._cancellable = None
        self._loading = False
        self._error = None
        self._last_flush = 0
        self._shown_once = False
        self._select_after_load = None
        self._monitor = None
        self._pending_events = []
        self._monitor_timer = 0
        self._renaming = None
        self._rename_escape = False
        self._rename_entry = None
        self._rename_stem = 0
        self._pending_icons = {}
        self._pending_rebuild = False
        self._search_job = None
        self._searching = False
        self._state_idle = 0
        self._ta_buf = ""
        self._ta_time = 0
        self._cut = frozenset(fileops.cut_uris())
        self._list_rows = 1
        self._list_cols = 1
        self._list_geom_idle = 0
        self._content_width = 0
        self._scroll_idle = 0
        self._zoom_acc = 0.0

        self.store = Gtk.ListStore(str, str, GdkPixbuf.Pixbuf, Gio.FileInfo)
        self._thumbs = _ThumbLoader(self._on_thumb_ready)

        self._build_tree()
        self._build_icons()
        self._drop_path = None
        self._meta_idle = 0
        metadata.connect(self._on_meta)
        self.host.config.connect_changed(self._on_columns_config)
        self.group_by, self.group_ascending = None, True
        self._groups, self._group_heads, self._store_pos = None, {}, None
        self._collapsed, self._group_cols, self._group_cols_idle = set(), 1, 0
        for view in (self.tree, self.iv):
            view.connect_after("draw", self._draw_groups)
        self._cloud, self._cloud_folder = {}, False
        cloudstatus.connect(self._on_cloud_changed)
        self._rpress = None
        self._lpress = None          # Drücken auf Mehrfachauswahl (Ziehen oder Klick)
        self._swallowed_at = 0
        self._press_path = None
        self._drag_button = 1
        for view in (self.tree, self.iv):
            self._setup_dnd(view)
            view.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
            view.connect("motion-notify-event", self._on_hover_motion)
            view.connect("leave-notify-event", self._on_hover_leave)
        self.iv.connect_after("draw", self._draw_iv_checks)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.add_named(self.tree_sw, "tree")
        self.stack.add_named(self.icon_sw, "icons")

        overlay = Gtk.Overlay()
        overlay.add(self.stack)
        self.empty_label = Gtk.Label()
        self.empty_label.get_style_context().add_class("empty-label")
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.empty_label.set_valign(Gtk.Align.START)
        self.empty_label.set_justify(Gtk.Justification.CENTER)
        self.empty_label.set_line_wrap(True)
        self.empty_label.set_no_show_all(True)
        overlay.add_overlay(self.empty_label)
        overlay.set_overlay_pass_through(self.empty_label, True)
        self.pack_start(overlay, True, True, 0)

        fileops.clipboard().connect_changed(self._on_clipboard_changed)
        self.connect("destroy", self._on_destroy)
        self._apply_mode()
        self.show_all()

    # ======================================================================
    # Aufbau
    # ======================================================================
    def _build_tree(self):
        tv = Gtk.TreeView()
        tv.get_style_context().add_class("file-list")
        tv.set_enable_search(False)
        tv.set_search_column(-1)
        tv.set_headers_visible(True)
        tv.set_headers_clickable(True)
        tv.set_rubber_banding(True)
        tv.set_activate_on_single_click(False)
        tv.set_show_expanders(False)
        tv.set_level_indentation(0)
        tv.set_has_tooltip(True)
        tv.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree = tv
        self._headers = {}

        # Name (Symbol + Text)
        col = Gtk.TreeViewColumn()
        self._setup_column(col, "name", "Name", 280, 100)
        # Kontrollkästchen zur Auswahl (Ordneroptionen), nur wenn eingeschaltet
        chk = Gtk.CellRendererPixbuf()
        chk.set_fixed_size(CHECK_CELL_W, -1)
        chk.set_alignment(0.5, 0.5)
        chk.set_visible(self._checks)
        col.pack_start(chk, False)
        pix = Gtk.CellRendererPixbuf()
        pix.set_alignment(0.5, 0.5)
        col.pack_start(pix, False)
        col.add_attribute(pix, "pixbuf", COL_ICON)
        txt = Gtk.CellRendererText()
        txt.set_property("ellipsize", Pango.EllipsizeMode.END)
        txt.set_padding(4, 0)
        col.pack_start(txt, True)
        col.add_attribute(txt, "text", COL_NAME)
        txt.connect("edited", self._on_rename_edited)
        txt.connect("editing-started", self._on_editing_started)
        txt.connect("editing-canceled", self._on_rename_canceled)
        tv.append_column(col)
        self.col_name, self.name_pix, self.name_cell = col, pix, txt
        col.set_cell_data_func(pix, self._name_pix_data)
        col.set_cell_data_func(txt, self._name_text_data)
        col.set_cell_data_func(chk, self._name_check_data)
        self.name_check = chk
        tv.get_selection().set_select_function(
            lambda _s, model, path, selected: selected or bool(model[path][COL_KEY]))

        # OneDrive-Status (nur in OneDrive-Ordnern sichtbar, wie Windows)
        c = Gtk.TreeViewColumn()
        self._setup_column(c, None, "Status", 64, 48)
        r = Gtk.CellRendererPixbuf()
        r.set_alignment(0.0, 0.5)
        r.set_padding(10, 0)
        c.pack_start(r, False)
        c.set_cell_data_func(r, self._cell_status)
        c.set_visible(False)
        tv.append_column(c)
        self.col_status = c

        def side(key, title, width, func, xalign=0.0, xpad=8):
            c = Gtk.TreeViewColumn()
            self._setup_column(c, key, title, width, 40)
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.END)
            r.set_property("foreground-rgba", _rgba(theme.P["text2"]))
            r.set_alignment(xalign, 0.5)
            r.set_padding(xpad, 0)
            c.pack_start(r, True)
            c.set_cell_data_func(r, func)
            tv.append_column(c)
            return c

        # Nur im Papierkorb (wie Windows): direkt hinter dem Namen
        self.col_orig = side(None, "Ursprünglicher Ort", 260, self._cell_orig)
        self.col_deleted = side(None, "Löschdatum", 140, self._cell_deleted)
        self.col_orig.set_visible(False)
        self.col_deleted.set_visible(False)
        self.col_date = side("mtime", "Änderungsdatum", 150, self._cell_date)
        self.col_type = side("type", "Typ", 150, self._cell_type)
        self.col_size = side("size", "Grösse", 90, self._cell_size, xalign=1.0, xpad=12)
        # weitere Spalten (Rechtsklick auf den Spaltenkopf, wie Windows)
        self._opt_cols = {"mtime": self.col_date, "type": self.col_type, "size": self.col_size}
        for key, title, width in columns.OPTIONAL:
            if key not in self._opt_cols:
                self._opt_cols[key] = side(key, title, width, self._extra_cell(key),
                                           xalign=1.0 if key == "length" else 0.0)
        self.col_folder = side(None, "Ordnerpfad", 320, self._cell_folder)
        self.col_folder.set_visible(False)
        # Füllspalte rechts (nimmt den Restplatz, Spalten bleiben fest wie in Windows)
        filler = Gtk.TreeViewColumn()
        filler.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        filler.set_fixed_width(16)
        filler.set_min_width(16)
        filler.set_expand(True)
        filler.set_clickable(False)
        filler.set_widget(Gtk.Label())
        tv.append_column(filler)
        self.col_filler = filler
        for c in tv.get_columns():
            btn = c.get_button()
            if btn is not None:
                btn.connect("button-press-event", self._on_header_press, c)
        tv.set_fixed_height_mode(True)

        tv.connect("button-press-event", self._on_button_press)
        tv.connect("key-press-event", self._on_key_press)
        tv.connect("scroll-event", self._on_scroll)
        tv.connect("row-activated", self._on_row_activated)
        tv.connect("popup-menu", self._on_popup_menu)
        tv.connect("query-tooltip", self._on_query_tooltip)
        tv.connect("state-flags-changed", self._strip_prelight)
        tv.get_selection().connect("changed", self._on_selection_changed)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(tv)
        sw.get_vadjustment().connect("value-changed", self._on_scrolled)
        self.tree_sw = sw

    def _setup_column(self, col, key, title, width, min_width):
        col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        col.set_fixed_width(width)
        col.set_min_width(min_width)
        col.set_resizable(True)
        col.set_clickable(True)
        ov = Gtk.Overlay()
        ov.set_size_request(-1, 31)  # volle Kopfhöhe: Pfeil oben, Text mittig (wie Windows)
        lab = Gtk.Label(label=title, xalign=0)
        lab.get_style_context().add_class("column-title")
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        lab.set_valign(Gtk.Align.CENTER)
        ov.add(lab)
        arrow = Gtk.Image()
        arrow.get_style_context().add_class("sort-arrow")
        arrow.set_halign(Gtk.Align.CENTER)
        arrow.set_valign(Gtk.Align.START)
        ov.add_overlay(arrow)
        ov.set_overlay_pass_through(arrow, True)
        ov.show_all()
        col.set_widget(ov)

        # GTK gibt dem Kopf-Widget nur seine natürliche Breite; damit der Pfeil
        # über der Spaltenmitte sitzt (wie Windows), Breite mitführen.
        def follow_width(c, _p, w=ov):
            if c.get_fixed_width() < 0:     # ohne feste Breite: sonst Rückkopplung (schrumpft)
                return
            w.set_size_request(max(1, min(c.get_width(), c.get_fixed_width()) - 16), 31)
        col.connect("notify::width", follow_width)

        # Doppelklick auf die Spaltengrenze: GTK setzt die feste Breite auf -1 –
        # dann wie Windows an den Inhalt anpassen (und wieder fest machen)
        def fixed_lost(c, _p):
            if c.get_fixed_width() < 0:
                GLib.idle_add(lambda: (self._autosize(c), False)[1])
        col.connect("notify::fixed-width", fixed_lost)
        if key:
            self._headers[key] = arrow
            col.connect("clicked", lambda _c, k=key: self.set_sort(k))
        else:
            col.set_clickable(False)

    def _build_icons(self):
        iv = Gtk.IconView()
        iv.get_style_context().add_class("file-icons")
        iv.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        iv.set_activate_on_single_click(False)
        iv.set_has_tooltip(True)
        self.iv_pix = Gtk.CellRendererPixbuf()
        self.iv_label = LabelCell()
        self.iv_right = LabelCell()
        self.iv_right.kind = "content_right"
        iv.pack_start(self.iv_pix, False)
        iv.add_attribute(self.iv_pix, "pixbuf", COL_ICON)
        iv.set_cell_data_func(self.iv_pix, lambda _l, cell, model, it, *_d: cell.set_property(
            "visible", not self._group_heads or bool(model.get_value(it, COL_KEY))))
        iv.pack_start(self.iv_label, True)
        iv.add_attribute(self.iv_label, "text", COL_NAME)
        iv.pack_start(self.iv_right, False)
        self._label_has_info = False
        self.iv_label.connect("edited", self._on_rename_edited)
        self.iv_label.connect("editing-started", self._on_editing_started)
        self.iv_label.connect("editing-canceled", self._on_rename_canceled)
        self.iv = iv

        iv.connect("button-press-event", self._on_button_press)
        iv.connect("key-press-event", self._on_key_press)
        iv.connect("scroll-event", self._on_scroll)
        iv.connect("motion-notify-event", self._on_iv_motion)
        iv.connect("item-activated", self._on_item_activated)
        iv.connect("popup-menu", self._on_popup_menu)
        iv.connect("query-tooltip", self._on_query_tooltip)
        iv.connect("state-flags-changed", self._strip_prelight)
        iv.connect("selection-changed", self._on_selection_changed)
        iv.connect_after("draw", self._on_iv_draw)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(iv)
        sw.connect("size-allocate", self._on_icon_sw_allocate)
        sw.get_vadjustment().connect("value-changed", self._on_scrolled)
        sw.get_hadjustment().connect("value-changed", self._on_scrolled)
        self.icon_sw = sw

    @staticmethod
    def _strip_prelight(widget, _old):
        # Hover nur für Zeilen/Elemente, nie für die ganze Fläche
        # (GTK3 setzt :hover sonst auch auf den Hintergrund des Widgets).
        if widget.get_state_flags() & Gtk.StateFlags.PRELIGHT:
            widget.unset_state_flags(Gtk.StateFlags.PRELIGHT)

    # ======================================================================
    # Ansichtsmodus
    # ======================================================================
    def _is_tree(self):
        return self.view_mode == "details"

    def _view(self):
        return self.tree if self._is_tree() else self.iv

    def _icon_size(self):
        return ICON_SIZES.get(self.view_mode, 16)

    def _geom(self):
        c = self._compact
        m = self.view_mode
        V, H = Gtk.Orientation.VERTICAL, Gtk.Orientation.HORIZONTAL
        if m == "xl":
            return dict(orient=V, item_w=264, lines=2, pad=4, rsp=2 if c else 8, csp=4, margin=10, spacing=4)
        if m == "large":
            return dict(orient=V, item_w=108, lines=2, pad=4, rsp=2 if c else 6, csp=4, margin=10, spacing=4)
        if m == "medium":
            return dict(orient=V, item_w=80, lines=2, pad=3, rsp=1 if c else 4, csp=2, margin=10, spacing=3)
        if m == "small":
            return dict(orient=H, item_w=232, h=18 if c else 22, pad=1, rsp=0, csp=6, margin=10, spacing=6)
        if m == "list":
            return dict(orient=H, item_w=self._list_item_width(), h=18 if c else 22, pad=1, rsp=0,
                        csp=6, margin=10, spacing=6)
        if m == "tiles":
            return dict(orient=H, item_w=264, h=52 if c else 56, pad=4, rsp=2 if c else 6, csp=6,
                        margin=10, spacing=8)
        # content
        return dict(orient=H, item_w=max(200, self._content_width), h=40 if c else 48, pad=4, rsp=0,
                    csp=0, margin=8, spacing=10)

    def _apply_mode(self):
        """Widgets passend zum aktuellen Modus einstellen (ohne Neuaufbau)."""
        if self._is_tree():
            self.iv.set_model(None)
            row_h = 22 if self._compact else 30
            self.name_pix.set_fixed_size(30, row_h)
            self.col_folder.set_visible(self._searching)
            self.col_orig.set_visible(self.in_trash() and not self._searching)
            self.col_deleted.set_visible(self.in_trash() and not self._searching)
            self.col_status.set_visible(self._cloud_folder)
            self._apply_columns()
            self.stack.set_visible_child_name("tree")
            self.empty_label.set_margin_top(32 + 14)
            self._update_sort_arrows()
            return
        self.tree.set_model(None)
        g = self._geom()
        iv = self.iv
        size = self._icon_size()
        iv.set_item_orientation(g["orient"])
        iv.set_margin(g["margin"])
        iv.set_item_padding(g["pad"])
        iv.set_row_spacing(g["rsp"])
        iv.set_column_spacing(g["csp"])
        iv.set_spacing(g["spacing"])
        # IconView reicht "spacing" nicht an seine Zellbox weiter
        iv.props.cell_area.set_spacing(g["spacing"])
        iv.set_item_width(g["item_w"])
        lab, right = self.iv_label, self.iv_right
        lab.reset_metrics()
        right.reset_metrics()
        right.set_visible(self.view_mode == "content")
        # Dateiinfo nur in Kacheln/Inhalt an die Textzelle (spart viele Aufrufe)
        need_info = self.view_mode in ("tiles", "content")
        if need_info != self._label_has_info:
            iv.clear_attributes(lab)
            iv.add_attribute(lab, "text", COL_NAME)
            iv.clear_attributes(right)
            if need_info:
                iv.add_attribute(lab, "info", COL_INFO)
                iv.add_attribute(right, "info", COL_INFO)
            self._label_has_info = need_info
        if g["orient"] == Gtk.Orientation.VERTICAL:
            self.iv_pix.set_fixed_size(size, size)
            self.iv_pix.set_alignment(0.5, 1.0)
            lab.kind, lab.lines = "name", g["lines"]
            lab.set_alignment(0.5, 0.0)
            lab.set_padding(2, 0)
            lab.nat_width = g["item_w"] - 2 * g["pad"]
        else:
            self.iv_pix.set_fixed_size(size, g["h"])
            self.iv_pix.set_alignment(0.5, 0.5)
            lab.kind = {"tiles": "tiles", "content": "content"}.get(self.view_mode, "name")
            lab.lines = 1
            lab.set_alignment(0.0, 0.5)
            lab.set_padding(0, 0)
            lab.nat_width = 40
            right.set_alignment(0.0, 0.5)
            right.nat_width = 230
        if self.view_mode == "list":
            self.icon_sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.EXTERNAL)
        elif self.view_mode == "content":
            # Elementbreite folgt der Fensterbreite – keine Mindestbreite erzwingen
            self.icon_sw.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
            iv.set_columns(1)
        else:
            self.icon_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            iv.set_columns(1 if self.view_mode == "content" else -1)
        self.stack.set_visible_child_name("icons")
        self.empty_label.set_margin_top(16)

    def _list_item_width(self):
        """Spaltenbreite der Listenansicht nach dem längsten Namen (wie Windows)."""
        layout = self.iv.create_pango_layout(None)
        longest = 0
        # beim Laden eines neuen Ordners sind _rows noch die alten Schlüssel
        names = sorted((self._display_name(self._entries[k][1]) for k in self._rows if k in self._entries),
                       key=len, reverse=True)[:200]
        for n in names:
            layout.set_text(n, -1)
            longest = max(longest, layout.get_pixel_size()[0])
        return int(max(120, min(420, 16 + 6 + longest + 12)))

    def _list_geometry(self):
        g = self._geom()
        alloc = self.icon_sw.get_allocated_height()
        if alloc <= 1:
            alloc = 400
        row_h = g["h"] + 2 * g["pad"] + g["rsp"]
        avail = alloc - 2 * g["margin"] - 14  # Platz für die waagrechte Bildlaufleiste
        return max(1, int((avail + g["rsp"]) // row_h))

    def set_view_mode(self, mode):
        if mode not in VIEW_MODES:
            return
        if mode == self.view_mode:
            return
        sel = self._selected_keys()
        cursor = self._cursor_key()
        self._cancel_rename()
        self.view_mode = mode
        if self.uri and not self._searching:
            fv = dict(self.host.config.get("folder_views") or {})
            fv[self.uri] = mode
            self.host.config.set("folder_views", fv)
        self._thumbs.reset()
        self._apply_mode()
        self._rebuild(select=sel, cursor=cursor)
        self._schedule_state_changed()

    def set_compact(self, b):
        b = bool(b)
        if b == self._compact:
            return
        self._compact = b
        sel, cursor = self._selected_keys(), self._cursor_key()
        self._apply_mode()
        self._rebuild(select=sel, cursor=cursor)

    def set_show_hidden(self, b):
        b = bool(b)
        if b == self._show_hidden:
            return
        self._show_hidden = b
        if self._searching:
            return
        self._rebuild(keep_selection=True)

    def set_show_extensions(self, b):
        b = bool(b)
        if b == self._show_ext:
            return
        self._show_ext = b
        self._rebuild(keep_selection=True, keep_scroll=True)

    def set_item_checkboxes(self, b):
        """Ordneroption „Kontrollkästchen zur Auswahl von Elementen verwenden“."""
        b = bool(b)
        if b == self._checks:
            return
        self._checks = b
        self.name_check.set_visible(b)
        self.col_name.queue_resize()
        self.tree.queue_draw()
        self.iv.queue_draw()

    def set_single_click(self, b):
        """Ordneroption „Öffnen durch einfachen Klick (Auswahl durch Zeigen)“."""
        self._single = bool(b)
        self._click_open = None
        self._set_hover(None, False, None)
        self._view().queue_draw()

    def reapply_folder_settings(self):
        """Ansicht, Sortierung und Gruppierung neu aus der Konfiguration lesen
        (Ordneroptionen „Für Ordner übernehmen“ / „Ordner zurücksetzen“)."""
        if self.uri is None or self._searching:
            return
        sel, cursor = self._selected_keys(), self._cursor_key()
        self._cancel_rename()
        uri = self.uri
        mode = self._mode_for_uri(uri)
        srt = (self.host.config.get("folder_sort") or {}).get(uri)
        if isinstance(srt, (list, tuple)) and len(srt) == 2 and srt[0] in dict(SORT_COLUMNS):
            self.sort_column, self.sort_ascending = srt[0], bool(srt[1])
        else:
            self.sort_column, self.sort_ascending = "name", True
        grp = (self.host.config.get("folder_group") or {}).get(uri)
        if grp is None and places.special_key_for_path(places.local_path(uri)) == "downloads":
            grp = ["mtime", False]
        if isinstance(grp, (list, tuple)) and len(grp) == 2 and grp[0] in dict(SORT_COLUMNS):
            self.group_by, self.group_ascending = grp[0], bool(grp[1])
        else:
            self.group_by, self.group_ascending = None, True
        self._collapsed = set()
        if mode != self.view_mode:
            self.view_mode = mode
            self._thumbs.reset()
        self._apply_mode()
        self._update_sort_arrows()
        self._rebuild(select=sel, cursor=cursor)
        self._schedule_state_changed()

    def zoom(self, delta):
        cur = self.view_mode
        if cur not in ZOOM_ORDER:
            cur = {"list": "small", "tiles": "medium", "content": "medium"}.get(cur, "details")
        i = ZOOM_ORDER.index(cur) + (1 if delta > 0 else -1)
        i = max(0, min(len(ZOOM_ORDER) - 1, i))
        self.set_view_mode(ZOOM_ORDER[i])

    # ======================================================================
    # Sortierung
    # ======================================================================
    def set_sort(self, column, ascending=None):
        if column not in SORTABLE:
            return
        if ascending is None:
            if column == self.sort_column:
                ascending = not self.sort_ascending
            else:
                ascending = SORT_DEFAULT_ASC.get(column, True)
        self.sort_column = column
        self.sort_ascending = bool(ascending)
        if self.uri and not self._searching:
            fs = dict(self.host.config.get("folder_sort") or {})
            fs[self.uri] = [column, self.sort_ascending]
            self.host.config.set("folder_sort", fs)
        self._update_sort_arrows()
        self._rebuild(keep_selection=True)
        self._schedule_state_changed()

    # -- Weitere Spalten (columns.py, metadata.py) ----------------------
    def _extra_cell(self, key):
        def func(_col, cell, model, it, _data):
            gf, info = self._entries.get(model.get_value(it, COL_KEY), (None, None))
            cell.set_property("text", columns.text(key, gf, info) if gf is not None else "")
        return func

    def _apply_columns(self):
        vis = set(columns.visible(self.host.config))
        for key, col in self._opt_cols.items():
            col.set_visible(key in vis)

    def _on_columns_config(self, key, _value):
        if key == "columns" and self.get_realized():
            self._apply_columns()

    def _toggle_column(self, key):
        vis = columns.visible(self.host.config)
        vis = [k for k in vis if k != key] if key in vis else vis + [key]
        self.host.config.set("columns", vis)
        self._apply_columns()

    def _on_meta(self, _path):
        # nachgeladene Abmessungen/Länge/Autoren/Titel: neu zeichnen (gebündelt)
        if not self._meta_idle and self._is_tree():
            def draw():
                self._meta_idle = 0
                self.tree.queue_draw()
                return False
            self._meta_idle = GLib.timeout_add(150, draw)

    def _on_header_press(self, _btn, ev, col):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.button != 3:
            return False
        vis = columns.visible(self.host.config)
        m = WinMenu()
        m.item("Spaltenbreite anpassen", self._later(self._autosize, col),
               sensitive=col is not self.col_filler)
        m.item("Grösse aller Spalten anpassen", self._later(self._autosize_all))
        m.separator()
        m.item("Name", check=True, sensitive=False)
        for key in columns.MENU_KEYS:
            m.item(columns.TITLES[key], self._later(self._toggle_column, key), check=key in vis)
        m.separator()
        m.item("Weitere…", self._later(columns.ChooseDialog, self.get_toplevel(), self.host.config))
        m.popup_at_pointer(ev, attach_to=self.tree)
        return True

    def _autosize(self, col):
        """Breite nach dem längsten Eintrag (wie Doppelklick auf die Spaltengrenze)."""
        model = self.tree.get_model()
        if col is self.col_filler:
            return
        if model is None:           # nichts zu messen: wenigstens wieder feste Breite
            col.set_fixed_width(max(col.get_min_width(), col.get_width()))
            return
        w = 0
        layout = self.tree.create_pango_layout("")
        cells = col.get_cells()
        it, n = model.get_iter_first(), 0
        while it is not None and n < 3000:
            col.cell_set_cell_data(model, it, False, False)
            cw = 0
            for r in cells:      # angezeigten Text messen (gekürzte Zellen melden sonst zu wenig)
                if isinstance(r, Gtk.CellRendererText):
                    layout.set_text(r.get_property("text") or "", -1)
                    cw += layout.get_pixel_size()[0] + 2 * r.get_property("xpad") + 6
                else:
                    cw += r.get_preferred_width(self.tree)[1]
            w = max(w, cw)
            it, n = model.iter_next(it), n + 1
        head = col.get_widget()
        lab = head.get_child() if isinstance(head, Gtk.Overlay) else None
        if isinstance(lab, Gtk.Label):
            w = max(w, lab.get_layout().get_pixel_size()[0] + 24)
        col.set_fixed_width(max(col.get_min_width(), min(900, w + 12)))

    def _autosize_all(self):
        for col in self.tree.get_columns():
            if col.get_visible() and col is not self.col_filler:
                self._autosize(col)

    # -- Gruppieren nach -------------------------------------------------
    def _grouping(self):
        return self.group_by is not None and self.view_mode != "list"

    def set_group(self, column, ascending=None):
        if column is not None and column not in dict(SORT_COLUMNS):
            return
        if ascending is None:
            ascending = GROUP_DEFAULT_ASC.get(column, True)
        self.group_by, self.group_ascending = column, bool(ascending)
        if self.uri and not self._searching:
            fg = dict(self.host.config.get("folder_group") or {})
            fg[self.uri] = [column, self.group_ascending]
            self.host.config.set("folder_group", fg)
        self._collapsed = set()
        self._rebuild(keep_selection=True)
        self._schedule_state_changed()

    def _fill_groups(self, ins):
        """Kopfzeile je Gruppe; in Symbolansichten auf eigener Zeile (Platzhalter)."""
        icon = not self._is_tree()
        ncols = max(1, self._group_cols) if icon else 1
        pos, heads, n = {}, {}, 0

        def pad():
            nonlocal n
            while icon and n % ncols:
                ins(-1, [COL_KEY, COL_NAME], ["", ""])
                n += 1
        for label, keys in self._groups:
            pad()
            heads[n] = (label, len(keys))
            ins(-1, [COL_KEY, COL_NAME], ["", "" if icon else f"{label} ({len(keys)})"])
            n += 1
            pad()
            if label in self._collapsed:
                continue
            for k in keys:
                if k not in self._entries:
                    continue
                ins(-1, _COLS, self._row_values(k))
                pos[k] = n
                n += 1
        self._store_pos, self._group_heads = pos, heads
        if icon and not self._group_cols_idle:
            self._group_cols_idle = GLib.idle_add(self._check_group_cols)

    def _check_group_cols(self):
        """Spaltenzahl der Symbolansicht bestimmen; bei Änderung neu einteilen."""
        self._group_cols_idle = 0
        if self._groups is None or self._is_tree() or not self._store_pos:
            return False
        first = min(self._store_pos.values())
        ok, rect = self.iv.get_cell_rect(Gtk.TreePath.new_from_indices([first]), None)
        if not ok or rect.width <= 0:
            return False
        sp = self.iv.get_column_spacing()
        width = self.iv.get_allocated_width() - 2 * self.iv.get_margin()
        ncols = max(1, (width + sp) // (rect.width + sp))
        if ncols != self._group_cols:
            self._group_cols = ncols
            self._rebuild(keep_selection=True, keep_scroll=True)
        return False

    def _head_at(self, widget, event):
        """Index der Gruppen-Kopfzeile unter dem Mauszeiger oder None."""
        x, y = int(event.x), int(event.y)
        if widget is self.tree:
            if event.window != self.tree.get_bin_window():
                return None
            hit = self.tree.get_path_at_pos(x, y)
            n = hit[0].get_indices()[0] if hit else None
            return n if n in self._group_heads else None
        for n in self._group_heads:
            ok, rect = self.iv.get_cell_rect(Gtk.TreePath.new_from_indices([n]), None)
            if ok and rect.y <= y < rect.y + rect.height:
                return n
        return None

    def _toggle_group(self, n):
        label = self._group_heads[n][0]
        self._collapsed ^= {label}
        self._rebuild(keep_selection=True, keep_scroll=True)

    def _head_of(self, model, it):
        if not self._group_heads or model.get_value(it, COL_KEY):
            return None
        return self._group_heads.get(model.get_path(it).get_indices()[0])

    def _name_pix_data(self, _col, cell, model, it, _d):
        head = self._head_of(model, it)
        if head is not None:
            name = "chevron_right" if head[0] in self._collapsed else "chevron_down"
            cell.set_property("pixbuf", icons.fluent_pixbuf(name, 12, theme.P["text2"]))

    def _name_text_data(self, _col, cell, model, it, _d):
        under = False
        if self._head_of(model, it) is not None:
            cell.set_property("foreground-rgba", _rgba(theme.P["accent"]))
        else:
            cell.set_property("foreground-set", False)
            # Öffnen durch einfachen Klick: Name unter dem Zeiger unterstreichen (Windows)
            under = self._single and self._hover_on_name and self._hover_key is not None \
                and model.get_value(it, COL_KEY) == self._hover_key
        cell.set_property("underline", Pango.Underline.SINGLE if under else Pango.Underline.NONE)

    def _name_check_data(self, _col, cell, model, it, _d):
        """Kästchen nur bei ausgewählten Zeilen und unter dem Zeiger (wie Windows)."""
        key = model.get_value(it, COL_KEY) if self._checks else None
        pb = None
        if key:
            if self.tree.get_selection().iter_is_selected(it):
                pb = check_pixbuf(True)
            elif key == self._hover_key:
                pb = check_pixbuf(False)
        cell.set_property("pixbuf", pb)

    def _draw_groups(self, view, cr):
        """Kopfzeilen: Linie hinter dem Namen (Details), in Symbolansichten
        Pfeil, Name und Linie über die ganze Zeile – wie Windows."""
        if not self._group_heads or view is not self._view() or view.get_model() is None:
            return False
        acc, div = _rgba(theme.P["accent"]), _rgba(theme.P["divider"])
        width = view.get_allocated_width()
        for n, (label, count) in self._group_heads.items():
            path = Gtk.TreePath.new_from_indices([n])
            text = f"{label} ({count})"
            layout = view.create_pango_layout(text)
            tw, th = layout.get_pixel_size()
            if view is self.tree:
                rect = self.tree.get_background_area(path, None)
                x0, y = self.tree.convert_bin_window_to_widget_coords(
                    self.col_name.get_x_offset(), rect.y)
                h = rect.height
                if y + h < 0 or y > self.tree.get_allocated_height():
                    continue
                lx = x0 + (CHECK_CELL_W if self._checks else 0) + 30 + 4 + tw + 12
            else:
                ok, rect = self.iv.get_cell_rect(path, None)
                if not ok:
                    continue
                y, h = rect.y, rect.height
                if y + h < 0 or y > self.iv.get_allocated_height():
                    continue
                name = "chevron_right" if label in self._collapsed else "chevron_down"
                Gdk.cairo_set_source_pixbuf(cr, icons.fluent_pixbuf(name, 12, theme.P["text2"]),
                                            rect.x + 6, y + (h - 12) / 2)
                cr.paint()
                Gdk.cairo_set_source_rgba(cr, acc)
                cr.move_to(rect.x + 24, y + (h - th) / 2)
                PangoCairo.show_layout(cr, layout)
                lx = rect.x + 24 + tw + 12
            if lx < width - 24:
                Gdk.cairo_set_source_rgba(cr, div)
                cr.rectangle(lx, int(y + h / 2), width - 16 - lx, 1)
                cr.fill()
        return False

    def _update_sort_arrows(self):
        for key, img in self._headers.items():
            if key == self.sort_column:
                name = "chevron_up" if self.sort_ascending else "chevron_down"
                img.set_from_pixbuf(icons.fluent_pixbuf(name, 10, theme.P["text2"]))
            else:
                img.clear()

    def _natkey(self, info):
        return natural_key(info.get_display_name() or info.get_name() or "")

    def _sort_key(self, key):
        _gf, info = self._entries[key]
        d = not is_dir(info)
        nk = self._natkey(info)
        c = self.sort_column
        if c == "mtime":
            k = (d, mtime_of(info), nk, key)
        elif c == "type":
            k = (d, _xfrm(type_text(info).casefold()), nk, key)
        elif c == "size":
            k = (d, 0 if not d else info.get_size(), nk, key)
        elif c in SORTABLE and c != "name":
            k = (d, columns.sort_value(c, _gf, info), nk, key)
        else:
            k = (d, nk, key)
        return k if self.sort_ascending else _Rev(k)

    # ======================================================================
    # Laden
    # ======================================================================
    def load(self, uri):
        prev = self.uri
        self._stop_search_job()
        self._cancel_rename()
        self._cancel_load()
        self._drop_monitor()
        self._searching = False
        self.uri = uri
        self._gfile = Gio.File.new_for_uri(uri)
        archive.track(self, uri)
        self._cloud = {}
        self._cloud_folder = cloudstatus.in_cloud(self._gfile.get_path())
        self._error = None
        self._entries = {}
        self._pending_events = []
        self._pending_icons = {}
        self._thumbs.reset()
        self._shown_once = False
        # Ansicht und Sortierung dieses Ordners
        self.view_mode = self._mode_for_uri(uri)
        srt = (self.host.config.get("folder_sort") or {}).get(uri)
        if isinstance(srt, (list, tuple)) and len(srt) == 2 and srt[0] in SORTABLE:
            self.sort_column, self.sort_ascending = srt[0], bool(srt[1])
        else:
            self.sort_column, self.sort_ascending = "name", True
        grp = (self.host.config.get("folder_group") or {}).get(uri)
        if grp is None and places.special_key_for_path(places.local_path(uri)) == "downloads":
            grp = ["mtime", False]
        if isinstance(grp, (list, tuple)) and len(grp) == 2 and grp[0] in dict(SORT_COLUMNS):
            self.group_by, self.group_ascending = grp[0], bool(grp[1])
        else:
            self.group_by, self.group_ascending = None, True
        self._collapsed = set()
        # beim Hochnavigieren den Ordner auswählen, aus dem man kommt (wie Windows)
        self._select_after_load = None
        if prev and prev != uri:
            pf = Gio.File.new_for_uri(prev)
            if pf.has_prefix(self._gfile):
                child = pf
                while child.get_parent() is not None and not child.get_parent().equal(self._gfile):
                    child = child.get_parent()
                self._select_after_load = [child.get_uri()]
            arc = places.archive_parts(prev)
            if arc and not uri.startswith("archive:") and arc[0].get_parent() is not None \
                    and arc[0].get_parent().equal(self._gfile):
                self._select_after_load = [arc[0].get_uri()]   # aus dem ZIP nach oben
        self._apply_mode()
        self._rows, self._skeys, self._index_cache = [], [], None
        self._groups = None
        self._fill_store()
        self._loading = True
        self._update_empty()
        self._setup_monitor()
        self._start_enumerate()
        self._schedule_state_changed()

    def reload(self):
        if self._searching:
            return
        if self.uri is None:
            return
        sel, cursor = self._selected_keys(), self._cursor_key()
        self._cancel_load()
        self._loading = True
        self._error = None              # F5 nach behobenem Fehler: alte Meldung weg
        self._mount_tried = False       # … und Einhängen (Passwort) wieder anbieten
        self._reload_keep = (sel, cursor, self._scroll_pos())
        self._start_enumerate(reload=True)
        self._schedule_state_changed()

    def is_loading(self):
        return self._loading

    def _mode_for_uri(self, uri):
        m = (self.host.config.get("folder_views") or {}).get(uri)
        if m in VIEW_MODES:
            return m
        key = places.special_key_for_path(places.local_path(uri)) if uri else None
        if key in ("pictures", "videos"):
            return "large"
        m = self.host.config.get("view_mode", "details")
        return m if m in VIEW_MODES else "details"

    def _cancel_load(self):
        self._gen += 1
        if self._cancellable is not None:
            self._cancellable.cancel()
        self._cancellable = None
        self._loading = False

    def _start_enumerate(self, reload=False):
        self._gen += 1
        gen = self._gen
        self._cancellable = Gio.Cancellable()
        self._last_flush = GLib.get_monotonic_time()
        self._is_reload = reload
        # beim Neuladen bleibt die alte Liste gültig, bis alles da ist
        self._incoming = {} if reload else self._entries
        self._gfile.enumerate_children_async(
            places.FILE_ATTRS, Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT,
            self._cancellable, self._on_enum_ready, gen)

    def _on_enum_ready(self, src, res, gen):
        try:
            en = src.enumerate_children_finish(res)
        except GLib.Error as exc:
            if gen == self._gen:
                self._load_failed(exc, gen)
            return
        if gen != self._gen:
            en.close_async(GLib.PRIORITY_DEFAULT, None, None, None)
            return
        en.next_files_async(LOAD_BLOCK, GLib.PRIORITY_DEFAULT, self._cancellable, self._on_next_files, gen)

    def _on_next_files(self, en, res, gen):
        try:
            infos = en.next_files_finish(res)
        except GLib.Error as exc:
            if gen == self._gen:
                self._load_failed(exc, gen)
            return
        if gen != self._gen:
            en.close_async(GLib.PRIORITY_DEFAULT, None, None, None)
            return
        if infos:
            folder = self._gfile
            target = self._incoming
            for info in infos:
                if not fileops.valid_name(info.get_name()):
                    continue        # „..“ oder „a/b“ von einem fremden Server
                gf = folder.get_child(info.get_name())
                target[gf.get_uri()] = (gf, info)
            now = GLib.get_monotonic_time()
            if not self._is_reload and (not self._shown_once or now - self._last_flush > FLUSH_INTERVAL_US):
                self._shown_once = True
                self._last_flush = now
                self._rebuild(keep_selection=True, keep_scroll=True)
            en.next_files_async(LOAD_BLOCK, GLib.PRIORITY_DEFAULT, self._cancellable,
                                self._on_next_files, gen)
            return
        en.close_async(GLib.PRIORITY_DEFAULT, None, None, None)
        self._loading = False
        if self._is_reload:
            self._entries = self._incoming
            sel, cursor, scroll = getattr(self, "_reload_keep", ([], None, None))
            self._rebuild(select=sel, cursor=cursor, scroll=scroll)
        elif self._select_after_load:
            sel = self._select_after_load
            self._select_after_load = None
            self._rebuild(select=sel, cursor=sel[0])
        else:
            self._rebuild(keep_selection=True, keep_scroll=True)
        self._schedule_state_changed()

    def _load_failed(self, exc, gen):
        if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
            return
        if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_MOUNTED) and not getattr(self, "_mount_tried", False):
            self._mount_tried = True
            op = Gtk.MountOperation.new(self.get_toplevel() if self.get_toplevel().is_toplevel() else None)
            self._gfile.mount_enclosing_volume(Gio.MountMountFlags.NONE, op, self._cancellable,
                                               self._on_mounted, gen)
            return
        self._loading = False
        name = places.display_name(self.uri) if self.uri else ""
        if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.PERMISSION_DENIED):
            self._error = f"Auf „{name}“ kann nicht zugegriffen werden.\n\nZugriff verweigert."
        elif exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
            self._error = f"„{name}“ ist nicht verfügbar.\n\nDer Ordner wurde nicht gefunden."
        else:
            self._error = f"Auf „{name}“ kann nicht zugegriffen werden.\n\n{exc.message}"
        self._rebuild(keep_selection=False)
        self._schedule_state_changed()

    def _on_mounted(self, src, res, gen):
        try:
            src.mount_enclosing_volume_finish(res)
        except GLib.Error as exc:
            if gen == self._gen:
                self._mount_tried = True
                self._load_failed(exc, gen)
            return
        if gen == self._gen:
            self._start_enumerate(reload=self._is_reload if hasattr(self, "_is_reload") else False)
            self._setup_monitor()

    # ======================================================================
    # Ordnerüberwachung
    # ======================================================================
    def _setup_monitor(self):
        self._drop_monitor()
        self._mount_tried = False
        try:
            mon = self._gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            return
        mon.connect("changed", self._on_monitor_changed)
        self._monitor = mon

    def _drop_monitor(self):
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._monitor_timer:
            GLib.source_remove(self._monitor_timer)
            self._monitor_timer = 0
        self._pending_events = []

    def _on_monitor_changed(self, mon, f, other, event):
        if mon is not self._monitor:
            return
        self._pending_events.append((event, f, other))
        if not self._monitor_timer:
            self._monitor_timer = GLib.timeout_add(MONITOR_DELAY_MS, self._flush_monitor)

    def _flush_monitor(self):
        self._monitor_timer = 0
        if not self._pending_events:
            return False
        if self._renaming or self._loading:
            # während Umbenennen/Laden nicht neu aufbauen – später nachholen
            self._monitor_timer = GLib.timeout_add(MONITOR_DELAY_MS, self._flush_monitor)
            return False
        events, self._pending_events = self._pending_events, []
        E = Gio.FileMonitorEvent
        folder = self._gfile
        removed, touched = set(), {}
        gone = False

        def inside(g):
            p = g.get_parent() if g is not None else None
            return p is not None and p.equal(folder)

        for ev, f, other in events:
            if f is not None and f.equal(folder):
                if ev in (E.DELETED, E.MOVED_OUT, E.UNMOUNTED, E.PRE_UNMOUNT):
                    gone = True
                continue
            if ev in (E.CREATED, E.CHANGED, E.CHANGES_DONE_HINT, E.ATTRIBUTE_CHANGED, E.MOVED_IN):
                if inside(f):
                    touched[f.get_uri()] = f
            elif ev in (E.DELETED, E.MOVED_OUT):
                if inside(f):
                    removed.add(f.get_uri())
                    touched.pop(f.get_uri(), None)
            elif ev == E.RENAMED:
                if inside(f):
                    removed.add(f.get_uri())
                    touched.pop(f.get_uri(), None)
                if other is not None and inside(other):
                    touched[other.get_uri()] = other
                    removed.discard(other.get_uri())
        if gone:
            self.host.go_up()
            return False
        if len(touched) > 400:
            self.reload()
            return False

        def apply(updated, failed):
            if self._renaming or self._loading:     # Antwort kam erst jetzt – später nachholen
                self._pending_events.extend((E.DELETED, Gio.File.new_for_uri(u), None)
                                            for u in removed | failed)
                self._pending_events.extend((E.CHANGED, f, None) for f, _i in updated.values())
                if not self._monitor_timer:
                    self._monitor_timer = GLib.timeout_add(MONITOR_DELAY_MS, self._flush_monitor)
                return
            self._apply_entry_changes(removed | failed, updated)
        self._query_infos(list(touched.values()), apply)
        return False

    def _slow_location(self):
        """Alles ausser lokalen Ordnern (Netzwerk, Archiv, Handy über MTP, Kamera …):
        Abfragen können hängen → nie im Hauptthread."""
        return not (self.uri or "").startswith(("file:", "trash:", "recent:"))

    def _query_infos(self, gfiles, done):
        """Infos holen → done({uri: (gfile, info)}, {fehlgeschlagene uris}). Lokal
        sofort; an Netzwerk-/Archivorten in einem Hilfsthread (ein hängender Server
        soll das Fenster nicht einfrieren) – dann nur, wenn der Ordner noch derselbe ist.
        Gibt True zurück, wenn done schon aufgerufen wurde."""
        def query_all():
            res, failed = {}, set()
            for gf in gfiles:
                try:
                    info = gf.query_info(places.FILE_ATTRS, Gio.FileQueryInfoFlags.NONE, None)
                except GLib.Error:
                    try:
                        info = gf.query_info(places.FILE_ATTRS,
                                             Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
                    except GLib.Error:
                        failed.add(gf.get_uri())
                        continue
                res[gf.get_uri()] = (gf, info)
            return res, failed

        if not self._slow_location():
            done(*query_all())
            return True
        folder = self._gfile

        def deliver(res, failed):
            if self._gfile is folder and not getattr(self, "_destroyed", False):
                done(res, failed)
            return False
        threading.Thread(target=lambda: GLib.idle_add(deliver, *query_all()),
                         name="filefenster-info", daemon=True).start()
        return False

    # ======================================================================
    # Zeilen
    # ======================================================================
    def _display_name(self, info):
        name = places.safe_text(info.get_display_name() or info.get_name() or "")
        if not self._show_ext and not is_dir(info):
            return fileops.split_ext(name)[0]
        return name

    def _visible(self, info):
        return self._searching or self._show_hidden or not is_hidden_info(info)

    def _icon_for(self, key, gf, info, size=None):
        size = size or self._icon_size()
        pb = None
        if is_dir(info):
            path = gf.get_path()
            pb = icons.folder_pixbuf(size, places.special_key_for_path(path) if path else None)
        else:
            if size >= 32:
                pb = self._thumb_for(key, gf, info, size)
            if pb is None:
                pb = icons.gicon_pixbuf(info.get_icon(), size)
        if info.get_attribute_boolean("standard::is-symlink") and pb is not None and not self._is_library_link(gf):
            pb = with_link_overlay(pb, size)
        if self._cloud_folder and not self._is_tree():
            pb = cloudstatus.with_overlay(pb, self._cloud_state(key), size)
        if key in self._cut or (not self._searching and is_hidden_info(info)):
            pb = faded(pb)
        return pb

    # -- OneDrive-Status (cloudstatus.py) ------------------------------
    def _cloud_state(self, key):
        if key in self._cloud:
            return self._cloud[key]
        gf, info = self._entries.get(key, (None, None))
        st = None
        if gf is not None and gf.get_path():
            st = cloudstatus.state(gf.get_path(), is_dir(info))
        self._cloud[key] = st
        return st

    def _cell_status(self, _col, cell, model, it, _data):
        st = self._cloud_state(model[it][COL_KEY]) if self._cloud_folder else None
        cell.set_property("pixbuf", cloudstatus.pixbuf(st, 16) if st else None)

    def _on_cloud_changed(self, paths):
        if not self._cloud_folder or self._gfile is None:
            return
        folder = self._gfile.get_path()
        keys = []
        for p in paths:
            if p == folder:
                keys = list(self._cloud)
                break
            uri = Gio.File.new_for_path(p).get_uri()
            if uri in self._entries:
                keys.append(uri)
        if not keys:
            return
        for k in keys:
            self._cloud.pop(k, None)
            if not self._is_tree() and k in self._entries:
                gf, info = self._entries[k]
                self._set_row_icon(k, self._icon_for(k, gf, info))
        self._view().queue_draw()

    @staticmethod
    def _is_library_link(gf):
        """Teams-/SharePoint-Bibliotheken sind Links im Organisationsordner;
        Windows zeigt dort keinen Verknüpfungspfeil."""
        path = gf.get_path()
        return bool(path) and places.special_key_for_path(os.path.dirname(path)) == "org"

    def _thumb_for(self, key, gf, info, size):
        mime = places.content_type(info)
        tp = None
        if info.has_attribute("thumbnail::path"):
            tp = info.get_attribute_byte_string("thumbnail::path")
        path = gf.get_path()
        if not tp and (not path or not thumbnailable(mime)):
            return None
        if not tp and mime not in _pixbuf_mimes() and size < 48:
            return None
        mt = mtime_of(info)
        ckey = (key, size, mt)
        pb = self._thumbs.cached(ckey)
        if pb is not None:
            return pb
        if ckey in self._thumbs.failed:
            return None
        failed = info.has_attribute("thumbnail::failed") and info.get_attribute_boolean("thumbnail::failed")
        self._thumbs.request(ckey, {"size": size, "thumb": tp, "path": path, "mime": mime,
                                    "uri": key, "mtime": mt, "failed": failed,
                                    "filesize": info.get_size()})
        return None

    def _on_thumb_ready(self, ckey, pb):
        key, size, mt = ckey
        if size != self._icon_size():
            return False
        ent = self._entries.get(key)
        if ent is None or mtime_of(ent[1]) != mt:
            return False
        self._set_row_icon(key, self._icon_for(key, ent[0], ent[1]))
        return False

    def _set_row_icon(self, key, pb):
        if self._renaming:
            self._pending_icons[key] = pb
            return
        path = self._store_path(key)
        if path is None:
            return
        it = self.store.get_iter(path)
        self.store.set_value(it, COL_ICON, pb)

    def _row_values(self, key):
        gf, info = self._entries[key]
        return [key, self._display_name(info), self._icon_for(key, gf, info), info]

    def _index(self):
        if self._index_cache is None:
            self._index_cache = {k: i for i, k in enumerate(self._rows)}
        return self._index_cache

    def _store_path(self, key):
        if self._store_pos is not None:
            n = self._store_pos.get(key)
            return Gtk.TreePath.new_from_indices([n]) if n is not None else None
        i = self._index().get(key)
        if i is None:
            return None
        if self.view_mode == "list":
            c, r = divmod(i, self._list_rows)
            return Gtk.TreePath.new_from_indices([r * self._list_cols + c])
        return Gtk.TreePath.new_from_indices([i])

    def _key_at(self, path):
        if path is None:
            return None
        try:
            it = self.store.get_iter(path)
        except ValueError:
            return None
        return self.store.get_value(it, COL_KEY) or None

    def _rebuild(self, keep_selection=False, keep_scroll=False, select=None, cursor=None, scroll=None):
        """Sortiert alle sichtbaren Einträge neu und füllt das Modell."""
        if self._renaming:
            self._pending_rebuild = True
            return
        if select is None and keep_selection:
            select = self._selected_keys()
            cursor = self._cursor_key()
        if scroll is None and keep_scroll:
            scroll = self._scroll_pos()
        keys = [k for k, (_g, info) in self._entries.items() if self._visible(info)]
        sk = {k: self._sort_key(k) for k in keys}
        keys.sort(key=sk.__getitem__)
        self._groups = None
        if self._grouping():
            import datetime
            today = datetime.date.today()
            groups = {}
            for k in keys:                      # Reihenfolge in der Gruppe = Sortierung
                label, val = group_of(self.group_by, self._entries[k][1], today)
                groups.setdefault(label, (val, []))[1].append(k)
            order = sorted(groups.items(), key=lambda g: g[1][0], reverse=not self.group_ascending)
            self._groups = [(label, ks) for label, (_v, ks) in order]
            keys = [k for _l, ks in self._groups for k in ks]
        self._rows = keys
        self._skeys = [sk[k] for k in keys]
        self._index_cache = None
        if self.view_mode == "list":
            self._apply_mode()  # Spaltenbreite nach längstem Namen
        self._fill_store()
        if select:
            self._select_keys([k for k in select if k in self._index()],
                              cursor if cursor in self._index() else None,
                              scroll=scroll is None)
        if scroll:
            self._restore_scroll(scroll)
        self._update_empty()
        self._schedule_state_changed()
        self._schedule_visible_thumbs()

    def _fill_store(self):
        view = self._view()
        view.set_model(None)
        self.store.clear()
        ins = self.store.insert_with_valuesv
        if self.view_mode == "list":
            n = len(self._rows)
            nrows = self._list_geometry()
            ncols = max(1, math.ceil(n / nrows)) if n else 1
            self._list_rows, self._list_cols = nrows, ncols
            order = [None] * (nrows * ncols)
            for i in range(n):
                c, r = divmod(i, nrows)
                order[r * ncols + c] = i
            for i in order:
                if i is None:
                    # Platzhalter (leere Zelle am Ende der letzten Spalte);
                    # Objektspalten bleiben NULL (None hier wäre ein Absturz)
                    ins(-1, [COL_KEY, COL_NAME], ["", ""])
                else:
                    ins(-1, _COLS, self._row_values(self._rows[i]))
            self.iv.set_columns(ncols)
        elif self._groups is not None:
            self._fill_groups(ins)
        else:
            for k in self._rows:
                ins(-1, _COLS, self._row_values(k))
        if self._groups is None:
            self._store_pos, self._group_heads = None, {}
        view.set_model(self.store)

    def _insert_row(self, key):
        sk = self._sort_key(key)
        i = bisect.bisect_left(self._skeys, sk)
        self._rows.insert(i, key)
        self._skeys.insert(i, sk)
        self._index_cache = None
        self.store.insert_with_valuesv(i, _COLS, self._row_values(key))

    def _remove_rows(self, keys):
        idx = self._index()
        positions = sorted((idx[k] for k in keys if k in idx), reverse=True)
        for i in positions:
            del self._rows[i]
            del self._skeys[i]
            it = self.store.iter_nth_child(None, i)
            if it is not None:
                self.store.remove(it)
        if positions:
            self._index_cache = None

    def _apply_entry_changes(self, removed, updated):
        """removed: set(keys), updated: {key: (gfile, info)} – inkrementell ins Modell."""
        if self._renaming:
            # später nachholen (Modelländerungen würden das Umbenennen abbrechen)
            for k in removed:
                self._entries.pop(k, None)
            self._entries.update(updated)
            self._pending_rebuild = True
            return
        sel = set(self._selected_keys())
        cursor = self._cursor_key()
        for k in removed:
            self._entries.pop(k, None)
        old_infos = {k: self._entries[k][1] for k in updated if k in self._entries}
        self._entries.update(updated)
        if self.view_mode == "list" or self._grouping() or len(removed) + len(updated) > 300:
            self._rebuild(select=[k for k in sel if k in self._entries], cursor=cursor,
                          scroll=self._scroll_pos())
            return
        idx = self._index()
        to_remove = {k for k in removed if k in idx}
        to_insert = []
        for k, (gf, info) in updated.items():
            vis = self._visible(info)
            if k in idx:
                old = old_infos.get(k)
                i = idx[k]
                if vis and old is not None and self._skeys[i] == self._sort_key(k):
                    # gleiche Position: nur Werte aktualisieren
                    it = self.store.iter_nth_child(None, i)
                    self.store.set(it, _COLS, self._row_values(k))
                    continue
                to_remove.add(k)
            if vis:
                to_insert.append(k)
        self._remove_rows(to_remove)
        for k in to_insert:
            self._insert_row(k)
        reselect = [k for k in sel if k in self._index() and (k in to_insert)]
        if reselect:
            paths = [self._store_path(k) for k in reselect]
            for p in paths:
                if p is not None:
                    self._view_select_path(p)
        self._update_empty()
        self._schedule_state_changed()

    def _upsert_files(self, gfiles, then=None, force=False):
        """Neue/geänderte Dateien sofort übernehmen (ohne auf den Monitor zu warten).
        then(keys) danach – an Netzwerkorten erst, wenn die Antwort da ist. Gibt die
        Schlüssel zurück, wenn sie sofort feststehen (lokal), sonst []."""
        if self._searching and not force:   # Suchergebnisse: nichts Unpassendes untermischen
            return []
        mine = list(gfiles) if force else [
            gf for gf in gfiles if self._gfile is not None and gf.get_parent() is not None
            and gf.get_parent().equal(self._gfile)]
        keys = []

        def done(updated, _failed):
            if updated:
                self._apply_entry_changes(set(), updated)
            keys.extend(updated)
            if then is not None and updated:
                then(list(updated))
        if mine:
            self._query_infos(mine, done)
        return keys

    def _update_empty(self):
        lab = self.empty_label
        if self._error:
            lab.set_text(self._error)
            lab.show()
        elif not self._rows and not self._loading:
            lab.set_text("Keine Elemente entsprechen Ihren Suchkriterien." if self._searching
                         else "Dieser Ordner ist leer.")
            lab.show()
        else:
            lab.hide()

    # ======================================================================
    # Auswahl
    # ======================================================================
    def _sel_paths(self):
        if self._is_tree():
            if self.tree.get_model() is None:
                return []
            _m, paths = self.tree.get_selection().get_selected_rows()
            return paths
        if self.iv.get_model() is None:
            return []
        return self.iv.get_selected_items()

    def _selected_keys(self):
        keys = [k for k in (self._key_at(p) for p in self._sel_paths()) if k]
        idx = self._index()
        keys.sort(key=lambda k: idx.get(k, 0))
        return keys

    def _cursor_key(self):
        view = self._view()
        if view.get_model() is None:
            return None
        if self._is_tree():
            path, _col = self.tree.get_cursor()
        else:
            ok, path, _cell = self.iv.get_cursor()
            if not ok:
                path = None
        return self._key_at(path)

    def _view_select_path(self, path):
        if self._is_tree():
            self.tree.get_selection().select_path(path)
        else:
            self.iv.select_path(path)

    def _select_keys(self, keys, cursor_key=None, scroll=True):
        view = self._view()
        if view.get_model() is None:
            return
        paths = [p for p in (self._store_path(k) for k in keys) if p is not None]
        cpath = self._store_path(cursor_key) if cursor_key else None
        if self._is_tree():
            sel = self.tree.get_selection()
            sel.unselect_all()
            if cpath is not None:
                self.tree.set_cursor(cpath, None, False)
                if cursor_key not in keys:
                    sel.unselect_path(cpath)
            for p in paths:
                sel.select_path(p)
            target = cpath or (paths[0] if paths else None)
            if scroll and target is not None:
                self.tree.scroll_to_cell(target, None, False, 0, 0)
        else:
            self.iv.unselect_all()
            for p in paths:
                self.iv.select_path(p)
            if cpath is not None:
                self.iv.set_cursor(cpath, None, False)
            target = cpath or (paths[0] if paths else None)
            if scroll and target is not None:
                self.iv.scroll_to_path(target, False, 0, 0)

    def _on_selection_changed(self, *_a):
        if (self.view_mode == "list" or self._group_heads) and self.iv.get_model() is not None:
            # Platzhalter der Listenansicht nie auswählbar
            for p in self.iv.get_selected_items():
                if not self._key_at(p):
                    self.iv.unselect_path(p)
        self._schedule_state_changed()

    def _schedule_state_changed(self):
        if not self._state_idle:
            self._state_idle = GLib.idle_add(self._emit_state_changed)

    def _emit_state_changed(self):
        self._state_idle = 0
        try:
            self.host.state_changed()
        except Exception as exc:
            print(f"[filefenster] state_changed: {exc}")
        return False

    def get_selected(self):
        return [self._entries[k][0] for k in self._selected_keys() if k in self._entries]

    def get_selected_infos(self):
        return [self._entries[k][1] for k in self._selected_keys() if k in self._entries]

    def item_count(self):
        return len(self._rows)

    def selected_size(self):
        total = 0
        for info in self.get_selected_infos():
            if is_dir(info):
                return None
            total += info.get_size()
        return total

    def reveal(self, uris):
        """Elemente auswählen, auch wenn der Ordner noch lädt („Im Ordner anzeigen“)."""
        uris = list(uris)
        if not uris:
            return
        if self._loading:
            self._select_after_load = uris
        else:
            self._rebuild(select=uris, cursor=uris[0])

    def select_all(self):
        if self._is_tree():
            self.tree.get_selection().select_all()
        else:
            self.iv.select_all()
            self._on_selection_changed()

    def select_none(self):
        if self._is_tree():
            self.tree.get_selection().unselect_all()
        else:
            self.iv.unselect_all()

    def invert_selection(self):
        sel = set(self._selected_keys())
        self._select_keys([k for k in self._rows if k not in sel], scroll=False)

    def focus_view(self):
        self._view().grab_focus()

    # ======================================================================
    # Maus und Tastatur
    # ======================================================================
    def _path_at_event(self, widget, event):
        """Pfad unter dem Mauszeiger oder None (leere Fläche, Platzhalter, Füllspalte)."""
        x, y = int(event.x), int(event.y)
        if widget is self.tree:
            if event.window != self.tree.get_bin_window():
                return None
            hit = self.tree.get_path_at_pos(x, y)
            if not hit:
                return None
            path, col, _cx, _cy = hit
            if col is self.col_filler:
                return None
            return path
        path = self.iv.get_path_at_pos(x, y)
        if path is None or not self._key_at(path):
            return None
        return path

    def _path_selected(self, path):
        if self._is_tree():
            return self.tree.get_selection().path_is_selected(path)
        return self.iv.path_is_selected(path)

    # -- Ordneroptionen: Kontrollkästchen, Einfachklick, Auswahl durch Zeigen --
    @staticmethod
    def _dbl_time():
        settings = Gtk.Settings.get_default()
        return settings.get_property("gtk-double-click-time") if settings else 400

    def _on_name(self, widget, event):
        """Zeiger auf dem Namen (Details) bzw. auf dem Element (Symbole)?"""
        if widget is not self.tree:
            return True
        hit = self.tree.get_path_at_pos(int(event.x), int(event.y))
        return bool(hit) and hit[1] is self.col_name

    def _iv_check_pos(self, rect):
        """Lage des Kästchens in einem Symbol (Widget-Koordinaten): oben links,
        bei kleinen Symbolen über dem Symbol."""
        if self._icon_size() >= 32:
            return rect.x + 4, rect.y + 4
        return rect.x + 2, rect.y + (rect.height - CHECK_SIZE) // 2

    def _check_hit(self, widget, event, path):
        """Klick auf das Kontrollkästchen eines Elements?"""
        if not self._checks or path is None or widget is not self._view():
            return False
        if widget is self.tree:
            if event.window != self.tree.get_bin_window():
                return False
            hit = self.tree.get_path_at_pos(int(event.x), int(event.y))
            return bool(hit) and hit[1] is self.col_name and hit[2] < CHECK_CELL_W
        ok, rect = self.iv.get_cell_rect(path, None)
        if not ok:
            return False
        x, y = self._iv_check_pos(rect)
        bx, by = self.iv.convert_widget_to_bin_window_coords(x, y)
        return bx - 3 <= event.x <= bx + CHECK_SIZE + 3 and by - 3 <= event.y <= by + CHECK_SIZE + 3

    def _toggle_path(self, path):
        """Ein Element an-/abwählen, die übrige Auswahl bleibt (Kontrollkästchen)."""
        was = self._path_selected(path)
        if self._is_tree():
            sel = self.tree.get_selection()
            keep = [p for p in sel.get_selected_rows()[1] if p.compare(path) != 0]
            self.tree.set_cursor(path, None, False)
            sel.unselect_all()
            for p in keep:
                sel.select_path(p)
            if not was:
                sel.select_path(path)
        else:
            keep = [p for p in self.iv.get_selected_items() if p.compare(path) != 0]
            self.iv.set_cursor(path, None, False)
            self.iv.unselect_all()
            for p in keep:
                self.iv.select_path(p)
            if not was:
                self.iv.select_path(path)
        self._view().queue_draw()

    def _draw_iv_checks(self, iv, cr):
        if not self._checks or self._is_tree() or iv.get_model() is None:
            return False
        paths = list(iv.get_selected_items())
        hp = self._store_path(self._hover_key) if self._hover_key else None
        if hp is not None and not iv.path_is_selected(hp):
            paths.append(hp)
        for p in paths:
            if not self._key_at(p):
                continue
            ok, rect = iv.get_cell_rect(p, None)
            if not ok:
                continue
            x, y = self._iv_check_pos(rect)
            Gdk.cairo_set_source_pixbuf(cr, check_pixbuf(iv.path_is_selected(p)), x, y)
            cr.paint()
        return False

    def _on_hover_motion(self, widget, event):
        if not (self._checks or self._single) or widget is not self._view():
            return False
        key, on_name = None, False
        if not event.state & (Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON3_MASK):
            path = self._path_at_event(widget, event)
            key = self._key_at(path) if path is not None else None
            on_name = key is not None and self._on_name(widget, event)
        self._set_hover(key, on_name, event.window)
        return False

    def _on_hover_leave(self, widget, event):
        if self._hover_key is not None or self._hover_win is not None:
            self._set_hover(None, False, None)
        return False

    def _set_hover(self, key, on_name, gdk_window):
        if key == self._hover_key and on_name == self._hover_on_name:
            return
        self._hover_key, self._hover_on_name = key, on_name
        self._view().queue_draw()
        if self._hover_timer:
            GLib.source_remove(self._hover_timer)
            self._hover_timer = 0
        # Einfachklick: Hand-Zeiger über Elementen, Auswahl nach kurzem Verweilen
        link = self._single and key is not None and on_name
        if self._hover_win is not None and self._hover_win is not gdk_window:
            self._hover_win.set_cursor(None)
            self._hover_win = None
        if gdk_window is not None:
            gdk_window.set_cursor(Gdk.Cursor.new_from_name(gdk_window.get_display(), "pointer")
                                  if link else None)
            self._hover_win = gdk_window if link else None
        if link:
            self._hover_timer = GLib.timeout_add(HOVER_SELECT_MS, self._hover_select, key)

    def _hover_select(self, key):
        """„Auswahl durch Zeigen“: verweilt der Zeiger auf einem Element, wird es
        ausgewählt (ohne Strg/Umschalt, ohne gedrückte Taste)."""
        self._hover_timer = 0
        if not self._single or self._renaming or key != self._hover_key or not self._hover_on_name:
            return False
        view = self._view()
        gw = view.get_window()
        seat = view.get_display().get_default_seat() if gw is not None else None
        if seat is not None:
            mask = gw.get_device_position(seat.get_pointer())[3]
            if mask & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                       | Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON3_MASK):
                return False
        if key in self._entries and self._selected_keys() != [key]:
            self._select_keys([key], cursor_key=key, scroll=False)
        return False

    def _on_button_press(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS:
            # ein neuer Druck macht alte, nie losgelassene Drücke ungültig (z. B. wenn
            # ein Doppelklick ein Rückfragefenster öffnete und das Loslassen dorthin ging)
            self._lpress = self._rpress = None
        if self._hover_timer:           # geklickt: keine „Auswahl durch Zeigen“ mehr
            GLib.source_remove(self._hover_timer)
            self._hover_timer = 0
        if self._renaming and event.type == Gdk.EventType.BUTTON_PRESS:
            pass  # Klick beendet Umbenennen (über Fokusverlust) und wirkt normal weiter
        if event.button == 8:
            if event.type == Gdk.EventType.BUTTON_PRESS:
                self.host.go_back()
            return True
        if event.button == 9:
            if event.type == Gdk.EventType.BUTTON_PRESS:
                self.host.go_forward()
            return True
        dbl = getattr(Gdk.EventType, "DOUBLE_BUTTON_PRESS", None) or getattr(Gdk.EventType, "_2BUTTON_PRESS")
        if event.button == 1 and event.type != Gdk.EventType.BUTTON_PRESS and (
                self._single or self._check_hit(widget, event, self._path_at_event(widget, event))):
            # Einfachklick-Modus öffnet nie doppelt; Doppelklick aufs Kästchen öffnet nicht
            return True
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS and self._single \
                and self._opened_at and 0 <= event.time - self._opened_at < self._dbl_time():
            return True   # zweiter Klick eines Doppelklicks nach dem Öffnen durch Einfachklick
        if event.type == dbl and event.button == 1 and self._swallowed_at \
                and event.time - self._swallowed_at < 800:
            # Doppelklick, dessen erster Klick eine Mehrfachauswahl aufgelöst hat:
            # GTK hat den ersten Klick nicht gesehen und meldet keinen Doppelklick
            self._swallowed_at = 0
            key = self._key_at(self._path_at_event(widget, event))
            if key:
                self._activate(key)
                return True
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if event.button == 1 and self._group_heads:
            n = self._head_at(widget, event)
            if n is not None:
                self._toggle_group(n)
                return True
        path = self._path_at_event(widget, event)
        if widget is self.tree and event.window != self.tree.get_bin_window():
            return False  # Spaltenköpfe
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        if event.button == 1:
            self._drag_button = 1
            self._press_path = path
            if widget is self.tree and path is not None:
                # Auf dem Namen ziehen = Element ziehen, in den anderen Spalten
                # Auswahlrahmen (wie Windows)
                hit = self.tree.get_path_at_pos(int(event.x), int(event.y))
                self.tree.set_rubber_banding(not (hit and hit[1] is self.col_name))
            if path is not None and self._check_hit(widget, event, path):
                # Kontrollkästchen: nur dieses Element an-/abwählen (wie Strg+Klick)
                self._toggle_path(path)
                widget.grab_focus()
                return True
            self._click_open = None
            if self._single and path is not None and self._on_name(widget, event) \
                    and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
                # Öffnen durch einfachen Klick: beim Loslassen ohne Ziehen
                self._click_open = (widget, event.x, event.y, self._key_at(path))
            if path is not None and self._path_selected(path) and len(self._sel_paths()) > 1 \
                    and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
                # Windows: Drücken auf ein markiertes Element lässt die Auswahl stehen
                # (zum Ziehen); erst Loslassen ohne Ziehen wählt nur dieses Element.
                # GTK würde die Auswahl schon beim Drücken aufheben.
                self._lpress = (widget, event.x, event.y, path.copy())
                widget.grab_focus()
                return True
            if path is None:
                if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
                    self.select_none()
                widget.grab_focus()
                if widget is self.tree:
                    return True  # leere Fläche: nicht die Zeile daneben wählen
            return False
        if event.button == 2:
            if path is not None:
                key = self._key_at(path)
                gf, info = self._entries.get(key, (None, None))
                if gf is not None and is_dir(info):
                    self.host.open_in_new_tab(self._target_uri(gf, info))
            return True
        if event.button == 3:
            widget.grab_focus()
            if path is None:
                self.select_none()
                self._popup_background_menu(event)
            else:
                if not self._path_selected(path):
                    self.select_none()
                    self._view_select_path(path)
                if self._is_tree():
                    # Cursor setzen ohne Auswahl zu verändern
                    sel = self.tree.get_selection()
                    keep = [p for p in sel.get_selected_rows()[1]]
                    self.tree.set_cursor(path, None, False)
                    for p in keep:
                        sel.select_path(p)
                else:
                    keep = self.iv.get_selected_items()
                    self.iv.set_cursor(path, None, False)
                    for p in keep:
                        self.iv.select_path(p)
                # Menü erst beim Loslassen (wie Windows) – Ziehen mit rechts möglich
                self._rpress = (widget, event.x, event.y)
            return True
        return False

    # ======================================================================
    # Drag & Drop (dnd.py)
    # ======================================================================
    def _setup_dnd(self, view):
        view.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, dnd.targets(), dnd.ACTIONS)
        view.connect_after("drag-begin", self._on_drag_begin)
        view.connect("drag-data-get", lambda _w, _c, data, *_: dnd.source_data_get(data))
        # Verschieben macht immer das Ziel – nie Zeilen aus dem Modell löschen lassen
        view.connect("drag-data-delete", lambda w, _c: w.stop_emission_by_name("drag-data-delete"))
        view.connect("drag-end", lambda *_: dnd.source_end())
        view.connect("button-release-event", self._on_button_release)
        view.connect("motion-notify-event", self._on_rdrag_motion)
        view.connect_after("draw", self._draw_drop)
        dnd.DropTarget(view, lambda x, y, v=view: self._drop_hit(v, x, y),
                       highlight=self._drop_highlight,
                       autoscroll=lambda x, y, v=view: self._drop_scroll(v, x, y))

    def _on_drag_begin(self, view, context):
        self._click_open = None     # gezogen statt geklickt: nicht öffnen
        # Strg+Ziehen eines nicht ausgewählten Elements: GTK wählt erst beim
        # Loslassen aus – Windows nimmt es sofort zur Auswahl dazu
        p = self._press_path if self._drag_button == 1 else None
        if p is not None and not self._path_selected(p):
            self._view_select_path(p)
        keys = [k for k in self._selected_keys() if k in self._entries]
        if not keys:
            return
        gf, info = self._entries[keys[0]]
        dnd.source_begin(context, [self._entries[k][0] for k in keys],
                         self._icon_for(keys[0], gf, info, 64), self._drag_button)

    def _on_button_release(self, widget, event):
        if widget is self.tree and event.window != self.tree.get_bin_window():
            return False        # Spaltenköpfe/Spaltengrenzen: Loslassen gehört GTK
        if event.button == 1 and self._click_open is not None:
            co, self._click_open = self._click_open, None
            moved = widget.drag_check_threshold(int(co[1]), int(co[2]), int(event.x), int(event.y))
            mods = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
            if co[0] is widget and co[3] and not moved and not mods:
                self._opened_at = event.time or 1
                GLib.idle_add(lambda k=co[3]: self._activate(k) and False)
        if event.button == 1 and self._lpress is not None:
            path = self._lpress[3]
            self._lpress = None
            self.select_none()
            if self._is_tree():
                self.tree.set_cursor(path, None, False)
            else:
                self.iv.set_cursor(path, None, False)
                self.iv.select_path(path)
            self._swallowed_at = event.time
            return True
        if event.button == 3 and self._rpress is not None:
            self._rpress = None
            self._popup_item_menu(event)
            return True
        return False

    def _on_rdrag_motion(self, widget, event):
        lp = self._lpress
        if lp is not None and lp[0] is widget and event.state & Gdk.ModifierType.BUTTON1_MASK:
            if not widget.drag_check_threshold(int(lp[1]), int(lp[2]), int(event.x), int(event.y)):
                return False
            self._lpress = None
            self._drag_button = 1
            widget.drag_begin_with_coordinates(dnd.target_list(), dnd.ACTIONS, 1, event,
                                               int(lp[1]), int(lp[2]))
            return True
        rp = self._rpress
        if rp is None or rp[0] is not widget or not event.state & Gdk.ModifierType.BUTTON3_MASK:
            return False
        if not widget.drag_check_threshold(int(rp[1]), int(rp[2]), int(event.x), int(event.y)):
            return False
        self._rpress = None
        self._drag_button = 3
        widget.drag_begin_with_coordinates(dnd.target_list(), dnd.ACTIONS, 3, event,
                                           int(rp[1]), int(rp[2]))
        return True

    def _drop_hit(self, view, x, y):
        if self.uri is None:
            return None
        path = None
        if view is self.tree:
            r = self.tree.get_dest_row_at_pos(x, y)
            path = r[0] if r else None
        else:
            r = self.iv.get_dest_item_at_pos(x, y)
            path = next((v for v in (r or ()) if isinstance(v, Gtk.TreePath)), None)
        key = self._key_at(path) if path is not None else None
        if key and key in self._entries:
            gf, info = self._entries[key]
            if is_dir(info):
                dest = Gio.File.new_for_uri(self._target_uri(gf, info))
                return dnd.Hit(dest, self._display_name(info), data=path)
        if self._searching or not dnd.droppable(self.uri):
            return None
        return dnd.Hit(self._gfile, places.display_name(self.uri))

    def _drop_highlight(self, hit):
        self._drop_path = hit.data if hit is not None else None
        self._view().queue_draw()

    def _draw_drop(self, view, cr):
        p = self._drop_path
        if p is None or view is not self._view() or view.get_model() is None:
            return False
        if view is self.tree:
            rect = self.tree.get_background_area(p, None)
            hdr = self.tree.convert_bin_window_to_widget_coords(0, 0)[1]
            _x, y = self.tree.convert_bin_window_to_widget_coords(rect.x, rect.y)
            w = self.tree.get_allocated_width()
            cr.rectangle(0, hdr, w, self.tree.get_allocated_height() - hdr)
            cr.clip()
            dnd.paint_highlight(cr, 0, y, w, rect.height)
        else:
            ok, rect = self.iv.get_cell_rect(p, None)
            if ok:
                dnd.paint_highlight(cr, rect.x, rect.y, rect.width, rect.height)
        return False

    def _drop_scroll(self, view, x, y):
        edge = dnd.DropTarget.EDGE
        if view is self.tree:
            hdr = self.tree.convert_bin_window_to_widget_coords(0, 0)[1]
            h = self.tree.get_allocated_height()
            return self.tree.get_vadjustment(), (-1 if y < hdr + edge else 1 if y > h - edge else 0)
        if self.view_mode == "list":
            w = self.iv.get_allocated_width()
            return self.icon_sw.get_hadjustment(), (-1 if x < edge else 1 if x > w - edge else 0)
        h = self.iv.get_allocated_height()
        return self.icon_sw.get_vadjustment(), (-1 if y < edge else 1 if y > h - edge else 0)

    def _on_iv_motion(self, widget, event):
        # Platzhalter der Listenansicht nicht hervorheben
        if self.view_mode != "list" or event.state & (Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON3_MASK):
            return False
        path = widget.get_path_at_pos(int(event.x), int(event.y))
        if path is not None and not self._key_at(path):
            ev = Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY)
            ev.window = event.window
            ev.send_event = True
            ev.mode = Gdk.CrossingMode.NORMAL
            ev.detail = Gdk.NotifyType.ANCESTOR
            widget.event(ev)
            return True
        return False

    def _on_scroll(self, widget, event):
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        if state & Gdk.ModifierType.CONTROL_MASK:
            # Strg+Mausrad zoomt. Das Fenster fängt das auf einem Elternwidget ab –
            # die ScrolledWindow würde das Ereignis aber vorher verbrauchen, darum
            # hier selbst an die Eltern weiterreichen; nur wenn keiner es will
            # (z. B. im Test-Harness), selbst zoomen.
            w = self.get_parent()
            while w is not None:
                if w.emit("scroll-event", event):
                    return True
                w = w.get_parent()
            d = 0
            if event.direction == Gdk.ScrollDirection.UP:
                d = 1
            elif event.direction == Gdk.ScrollDirection.DOWN:
                d = -1
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                ok, _dx, dy = event.get_scroll_deltas()
                self._zoom_acc -= dy
                if abs(self._zoom_acc) >= 1.0:
                    d = 1 if self._zoom_acc > 0 else -1
                    self._zoom_acc = 0.0
            if d:
                self.zoom(d)
            return True
        if self.view_mode == "list" and widget is self.iv:
            # senkrechtes Mausrad blättert in der Liste waagrecht (wie Windows)
            adj = self.icon_sw.get_hadjustment()
            step = max(40, self.iv.get_item_width() // 2)
            delta = 0
            if event.direction == Gdk.ScrollDirection.UP:
                delta = -step
            elif event.direction == Gdk.ScrollDirection.DOWN:
                delta = step
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                ok, dx, dy = event.get_scroll_deltas()
                delta = (dy if abs(dy) > abs(dx) else dx) * step
            if delta:
                adj.set_value(max(adj.get_lower(), min(adj.get_upper() - adj.get_page_size(),
                                                       adj.get_value() + delta)))
                return True
        return False

    def _on_key_press(self, widget, event):
        if self._renaming:
            return False
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        kv = event.keyval
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
                return False  # Alt+Eingabe etc. macht das Fenster
            self.open_selected()
            return True
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
                    | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.HYPER_MASK
                    | Gdk.ModifierType.META_MASK):
            return False
        cp = Gdk.keyval_to_unicode(kv)
        if cp:
            ch = chr(cp)
            if ch.isprintable() and (ch != " " or self._ta_buf):
                self._typeahead(ch)
                return True
        return False

    def _typeahead(self, ch):
        now = GLib.get_monotonic_time()
        if now - self._ta_time > TYPEAHEAD_US:
            self._ta_buf = ""
        self._ta_time = now
        self._ta_buf += ch.casefold()
        buf = self._ta_buf
        if not self._rows:
            return
        names = [self._display_name(self._entries[k][1]).casefold() for k in self._rows]
        cur = self._cursor_key()
        cur_i = self._index().get(cur, -1) if cur else -1
        n = len(names)
        if len(set(buf)) == 1:
            # eine Taste (auch mehrmals): zum nächsten Eintrag mit diesem Anfangsbuchstaben
            prefix, start = buf[0], cur_i + 1
        else:
            # Wortanfang weitertippen: der aktuelle Eintrag darf weiter passen
            prefix, start = buf, max(cur_i, 0)
        hit = next((i for i in ((start + j) % n for j in range(n)) if names[i].startswith(prefix)), None)
        if hit is None:
            return
        key = self._rows[hit]
        self._select_keys([key], cursor_key=key, scroll=True)

    def _on_row_activated(self, _tv, path, _col):
        if not self._mouse_activation():
            self._activate(self._key_at(path))

    def _on_item_activated(self, _iv, path):
        if not self._mouse_activation():
            self._activate(self._key_at(path))

    def _mouse_activation(self):
        """Doppelklick-Aktivierung durch GTK im Einfachklick-Modus übergehen
        (dort öffnet das Loslassen, siehe _on_button_release)."""
        if not self._single:
            return False
        ev = Gtk.get_current_event()
        return ev is not None and ev.type in (
            Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE,
            getattr(Gdk.EventType, "_2BUTTON_PRESS", None), getattr(Gdk.EventType, "_3BUTTON_PRESS", None))

    def _activate(self, key):
        if not key or key not in self._entries:
            return
        gf, info = self._entries[key]
        if self.in_trash():   # wie open_selected (Windows): Eigenschaften statt Öffnen
            self.host.show_properties([gf])
            return
        state = Gtk.get_current_event_state()
        ctrl = bool(state[0] and state[1] & Gdk.ModifierType.CONTROL_MASK) if isinstance(state, tuple) else False
        # Doppelklick muss dasselbe tun wie Eingabe (open_selected):
        # ZIP/CAB wie einen Ordner öffnen, ISO bereitstellen
        browsable = self._browsable(key)
        if is_dir(info) or browsable:
            uri = places.archive_uri(gf) if browsable else self._target_uri(gf, info)
            if ctrl:
                self.host.open_in_new_tab(uri)
            else:
                folderoptions.open_folder(self.host, uri)   # ggf. eigenes Fenster
        elif diskimage.is_image(gf.get_basename()) and gf.get_path():
            self.mount_image(gf)
        else:
            self._launch(gf, info)

    def _target_uri(self, gf, info):
        if info is not None and info.has_attribute("standard::target-uri"):
            t = info.get_attribute_string("standard::target-uri")
            if t:
                return t
        return gf.get_uri()

    def _launch(self, gf, info=None):
        target = gf
        if info is not None and info.has_attribute("standard::target-uri"):
            t = info.get_attribute_string("standard::target-uri")
            if t:
                target = Gio.File.new_for_uri(t)
        fn = getattr(self.host, "launch_file", None)
        if callable(fn):
            fn(target)
        else:
            host_mod.launch_file(target, self.get_toplevel())

    def open_selected(self):
        keys = self._selected_keys()
        if not keys:
            return
        if self.in_trash():  # wie Windows: Doppelklick zeigt die Eigenschaften
            self.host.show_properties([self._entries[k][0] for k in keys])
            return
        dirs = [k for k in keys if is_dir(self._entries[k][1]) or self._browsable(k)]
        files = [k for k in keys if k not in dirs]
        for k in files:
            gf, info = self._entries[k]
            if diskimage.is_image(gf.get_basename()) and gf.get_path():
                self.mount_image(gf)       # Doppelklick auf ISO = Bereitstellen (Windows)
            else:
                self._launch(gf, info)
        for i, k in enumerate(dirs):
            gf, info = self._entries[k]
            uri = places.archive_uri(gf) if self._browsable(k) else self._target_uri(gf, info)
            if self.host.config.get("open_new_window", False):
                self.host.open_in_new_window(uri)    # Jeden Ordner in einem eigenen Fenster
            elif i == 0 and not files:
                self.host.navigate(uri)
            else:
                self.host.open_in_new_tab(uri)

    # ======================================================================
    # Zeichnen, Tooltips, Grössenänderung
    # ======================================================================
    def _on_iv_draw(self, widget, cr):
        if self.view_mode != "content" or widget.get_model() is None:
            return False
        vr = widget.get_visible_range()
        if not vr or vr[0] is None:
            return False
        if isinstance(vr, tuple) and len(vr) == 3:
            ok, start, end = vr
            if not ok:
                return False
        else:
            start, end = vr
        a, b = start.get_indices()[0], end.get_indices()[0]
        Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["divider"]))
        w = widget.get_allocated_width()
        for i in range(a, b + 1):
            ok, rect = widget.get_cell_rect(Gtk.TreePath.new_from_indices([i]), None)
            if ok:
                y = rect.y + rect.height
                cr.rectangle(rect.x, y, w - 2 * rect.x, 1)
        cr.fill()
        return False

    def _on_icon_sw_allocate(self, sw, alloc):
        if self.view_mode == "content":
            w = alloc.width - 2 * 8 - 2
            if abs(w - self._content_width) > 1:
                self._content_width = w
                if not self._list_geom_idle:
                    self._list_geom_idle = GLib.idle_add(self._relayout_idle)
        elif self.view_mode == "list":
            if self._list_geometry() != self._list_rows and not self._list_geom_idle:
                self._list_geom_idle = GLib.idle_add(self._relayout_idle)
        if self._groups is not None and not self._is_tree() and not self._group_cols_idle:
            self._group_cols_idle = GLib.idle_add(self._check_group_cols)

    def _relayout_idle(self):
        self._list_geom_idle = 0
        if self.view_mode == "content":
            self.iv.set_item_width(max(200, self._content_width))
        elif self.view_mode == "list" and self._list_geometry() != self._list_rows:
            sel, cursor = self._selected_keys(), self._cursor_key()
            self._rebuild(select=sel, cursor=cursor)
        return False

    def _scroll_adj(self):
        if self._is_tree():
            return self.tree_sw.get_vadjustment()
        if self.view_mode == "list":
            return self.icon_sw.get_hadjustment()
        return self.icon_sw.get_vadjustment()

    def _scroll_pos(self):
        return self._scroll_adj().get_value() or None

    def _restore_scroll(self, value):
        adj = self._scroll_adj()
        state = {"h": 0, "t": 0}

        def apply(*_a):
            if adj.get_upper() - adj.get_page_size() >= value - 1:
                adj.set_value(value)
                stop()
            return False

        def stop():
            if state["h"]:
                adj.disconnect(state["h"])
                state["h"] = 0
            if state["t"]:
                GLib.source_remove(state["t"])
                state["t"] = 0
            return False

        state["h"] = adj.connect("changed", apply)
        state["t"] = GLib.timeout_add(800, stop)
        apply()

    def _on_scrolled(self, *_a):
        self._schedule_visible_thumbs()

    def _schedule_visible_thumbs(self):
        if self._scroll_idle:
            GLib.source_remove(self._scroll_idle)
        self._scroll_idle = GLib.timeout_add(120, self._visible_thumbs)

    def _visible_thumbs(self):
        """Vorschaubilder der sichtbaren Elemente zuerst laden."""
        self._scroll_idle = 0
        size = self._icon_size()
        if size < 32 or self._is_tree() or self.iv.get_model() is None:
            return False
        vr = self.iv.get_visible_range()
        if not vr:
            return False
        if len(vr) == 3:
            ok, start, end = vr
            if not ok:
                return False
        else:
            start, end = vr
        if start is None or end is None:
            return False
        a, b = start.get_indices()[0], end.get_indices()[0]
        for i in range(b, a - 1, -1):  # Prioritätsliste wird von hinten abgearbeitet
            key = self._key_at(Gtk.TreePath.new_from_indices([i]))
            ent = self._entries.get(key) if key else None
            if ent is None or is_dir(ent[1]):
                continue
            gf, info = ent
            mt = mtime_of(info)
            ckey = (key, size, mt)
            if self._thumbs.cached(ckey) is not None or ckey in self._thumbs.failed:
                continue
            tp = info.get_attribute_byte_string("thumbnail::path") if info.has_attribute("thumbnail::path") else None
            mime = places.content_type(info)
            if not tp and (not thumbnailable(mime) or (mime not in _pixbuf_mimes() and size < 48)):
                continue
            failed = info.has_attribute("thumbnail::failed") and info.get_attribute_boolean("thumbnail::failed")
            self._thumbs.request(ckey, {"size": size, "thumb": tp, "path": gf.get_path(), "mime": mime,
                                        "uri": key, "mtime": mt, "failed": failed,
                                        "filesize": info.get_size()}, front=True)
        return False

    def _on_query_tooltip(self, widget, x, y, keyboard, tooltip):
        if self._renaming or Gtk.grab_get_current() is not None:
            return False        # offenes Kontextmenü: keine Infotipps (wie Windows)
        res = widget.get_tooltip_context(x, y, keyboard)
        if not res or not res[0]:
            return False
        model, path = res[-3], res[-2]
        if model is None or path is None:
            return False
        key = self._key_at(path)
        if not key or key not in self._entries:
            return False
        if widget is self.tree and not keyboard:
            bx, by = self.tree.convert_widget_to_bin_window_coords(x, y)
            hit = self.tree.get_path_at_pos(bx, by)
            if not hit or hit[1] is not self.col_name:
                return False
        gf, info = self._entries[key]
        lines = []
        if not is_dir(info):
            lines.append(f"Typ: {type_text(info)}")
            lines.append(f"Grösse: {places.format_size(info.get_size())}")
        d = date_text(info)
        if d:
            lines.append(f"Änderungsdatum: {d}")
        if self._searching:
            parent = gf.get_parent()
            if parent is not None:
                lines.append(f"Ordnerpfad: {parent.get_path() or parent.get_uri()}")
        if not lines:
            return False
        tooltip.set_text("\n".join(lines))
        if widget is self.tree:
            self.tree.set_tooltip_row(tooltip, path)
        else:
            self.iv.set_tooltip_item(tooltip, path)
        return True

    # ======================================================================
    # Zellen der Detailansicht
    # ======================================================================
    @staticmethod
    def _cell_date(_col, cell, model, it, _data):
        info = model.get_value(it, COL_INFO)
        cell.set_property("text", date_text(info) if info is not None else "")

    @staticmethod
    def _cell_type(_col, cell, model, it, _data):
        cell.set_property("text", type_text(model.get_value(it, COL_INFO)))

    @staticmethod
    def _cell_size(_col, cell, model, it, _data):
        cell.set_property("text", size_kb_text(model.get_value(it, COL_INFO)))

    @staticmethod
    def _cell_orig(_col, cell, model, it, _data):
        info = model.get_value(it, COL_INFO)
        p = None
        if info is not None and info.has_attribute("trash::orig-path"):
            p = info.get_attribute_byte_string("trash::orig-path")
        cell.set_property("text", places.safe_text(os.path.dirname(p)) if p else "")

    @staticmethod
    def _cell_deleted(_col, cell, model, it, _data):
        info = model.get_value(it, COL_INFO)
        text = ""
        if info is not None and info.has_attribute("trash::deletion-date"):
            raw = info.get_attribute_string("trash::deletion-date") or ""
            try:
                dt = GLib.DateTime.new_from_iso8601(raw, GLib.TimeZone.new_local()) if raw else None
            except (TypeError, ValueError):
                dt = None
            if dt is not None:
                text = dt.format("%d.%m.%Y %H:%M")
        cell.set_property("text", text)

    def _cell_folder(self, _col, cell, model, it, _data):
        key = model.get_value(it, COL_KEY)
        ent = self._entries.get(key)
        text = ""
        if ent is not None:
            parent = ent[0].get_parent()
            if parent is not None:
                text = places.safe_text(parent.get_path() or parent.get_uri())
        cell.set_property("text", text)

    # ======================================================================
    # Umbenennen
    # ======================================================================
    def begin_rename(self):
        if self._renaming or self.in_trash() or self.archive_read_only():
            return
        keys = self._selected_keys()
        if not keys:
            return
        cur = self._cursor_key()
        key = cur if cur in keys else keys[0]
        gf, info = self._entries[key]
        self._select_keys([key], cursor_key=key, scroll=True)
        path = self._store_path(key)
        if path is None:
            return
        shown = self._display_name(info)
        if not is_dir(info) and self._show_ext:
            self._rename_stem = len(fileops.split_ext(shown)[0])
        else:
            self._rename_stem = len(shown)
        self._renaming = key
        self._rename_escape = False
        if self._is_tree():
            self.name_cell.set_property("editable", True)
            self.tree.grab_focus()
            self.tree.set_cursor_on_cell(path, self.col_name, self.name_cell, True)
        else:
            self.iv_label.set_property("mode", Gtk.CellRendererMode.EDITABLE)
            self.iv.grab_focus()
            self.iv.set_cursor(path, self.iv_label, True)
        if self._rename_entry is None:
            # Bearbeitung kam nicht zustande
            self._end_rename()

    def _on_editing_started(self, _cell, editable, _path):
        self._rename_entry = editable
        n = self._rename_stem

        def sel():
            try:
                editable.select_region(0, n)
            except Exception:
                pass
            return False
        GLib.idle_add(sel)
        if isinstance(editable, Gtk.Entry):
            editable.connect("key-press-event", self._on_rename_key)

    def _on_rename_key(self, _entry, event):
        if event.keyval == Gdk.KEY_Escape:
            self._rename_escape = True
        return False

    def _on_rename_canceled(self, _cell):
        entry = self._rename_entry
        key = self._renaming
        escape = self._rename_escape
        self._end_rename()
        # GTK bricht bei Fokusverlust ab – Windows übernimmt dann den Namen.
        if not escape and entry is not None and key is not None:
            text = entry.get_text()
            GLib.idle_add(lambda: self._commit_rename(key, text) and False)

    def _on_rename_edited(self, _cell, _path, new_text):
        key = self._renaming
        self._end_rename()
        if key is not None:
            self._commit_rename(key, new_text)

    def _end_rename(self):
        self._renaming = None
        self._rename_entry = None
        self.name_cell.set_property("editable", False)
        self.iv_label.set_property("mode", Gtk.CellRendererMode.INERT)
        GLib.idle_add(self._after_rename_idle)

    def _cancel_rename(self):
        if not self._renaming:
            return
        self._rename_escape = True
        entry = self._rename_entry
        if entry is not None:
            try:
                entry.set_property("editing-canceled", True)
                entry.editing_done()
                entry.remove_widget()
            except Exception:
                pass
        if self._renaming:
            self._end_rename()

    def _after_rename_idle(self):
        if self._renaming:
            return False
        if self._pending_icons:
            icons_, self._pending_icons = self._pending_icons, {}
            for k, pb in icons_.items():
                self._set_row_icon(k, pb)
        if self._pending_rebuild:
            self._pending_rebuild = False
            self._rebuild(keep_selection=True, keep_scroll=True)
        return False

    def _commit_rename(self, key, text):
        ent = self._entries.get(key)
        if ent is None:
            return
        gf, info = ent
        parent = self.get_toplevel()
        old = info.get_display_name() or info.get_name()
        text = text.strip()
        if not self._show_ext and not is_dir(info):
            new = text + fileops.split_ext(old)[1] if text else ""
        else:
            new = text
        if new == old or new == places.safe_text(old):   # angezeigt wird der bereinigte Name
            return
        if not new:
            fileops.info_dialog(parent, "Umbenennen", "Sie müssen einen Dateinamen eingeben.")
            GLib.idle_add(self._rename_again, key)
            return
        if "/" in new or "\0" in new or new in (".", ".."):
            fileops.info_dialog(parent, "Umbenennen", 'Ein Dateiname darf das Zeichen "/" nicht enthalten '
                                "und nicht nur aus Punkten bestehen.")
            GLib.idle_add(self._rename_again, key)
            return
        if self._show_ext and not is_dir(info):
            old_ext = fileops.split_ext(old)[1]
            new_ext = fileops.split_ext(new)[1]
            if old_ext and old_ext.lower() != new_ext.lower():
                if not fileops.ask_yes_no(
                        parent, "Umbenennen",
                        "Wenn Sie die Dateinamenerweiterung ändern, wird die Datei möglicherweise unbrauchbar.",
                        "Möchten Sie sie wirklich ändern?"):
                    return
        target = gf.get_parent().get_child_for_display_name(new) if gf.get_parent() else None
        if (target is not None and not target.equal(gf) and fileops._exists(target)
                and not _same_file(target, gf)):    # FAT/NTFS/SMB: nur Gross/klein geändert
            fileops.info_dialog(parent, "Umbenennen",
                                f"Es ist bereits ein Element mit dem Namen „{new}“ vorhanden.",
                                "Bitte wählen Sie einen anderen Namen.")
            GLib.idle_add(self._rename_again, key)
            return
        if self.in_archive():       # ZIP-Datei neu schreiben (ohne Rückgängig, wie Windows)
            archive.zip_rename(gf, new, parent=parent, on_done=self._after_zip_edit)
            return
        try:
            new_gf = fileops.rename(gf, new)
        except GLib.Error as exc:
            fileops.show_errors(parent, "Das Element konnte nicht umbenannt werden.", [(old, exc.message)])
            return
        self._apply_entry_changes({key}, {})
        # force: auch in Suchergebnissen (dort liegen Treffer in Unterordnern)
        self._upsert_files([new_gf], force=True, then=lambda added: self._select_keys(
            added, cursor_key=added[0], scroll=True))

    def _rename_again(self, key):
        if key in self._index():
            self._select_keys([key], cursor_key=key)
            self.begin_rename()
        return False

    # ======================================================================
    # Dateioperationen
    # ======================================================================
    def cut(self):
        if self.in_archive():
            return
        files = self.get_selected()
        if files:
            fileops.cut_files(files)

    def copy(self):
        files = self.get_selected()
        if files:
            fileops.copy_files(files)

    def can_paste(self):
        return (fileops.can_paste() and self._gfile is not None and not self._searching
                and not self.in_trash() and not self.archive_read_only())

    def paste(self):
        if self._gfile is None or self._searching or self.archive_read_only():
            return
        if self.in_archive():       # in die geöffnete ZIP-Datei aufnehmen
            dest, top = self._gfile, self.get_toplevel()

            def got(uris, cut, trusted):
                if uris and (trusted or fileops.confirm_foreign(uris, top)):
                    archive.zip_add([Gio.File.new_for_uri(u) for u in uris], dest,
                                    move=cut and trusted, parent=top,
                                    on_done=self._after_zip_edit, from_clipboard=True)
            fileops.clipboard().read_files(got, with_trust=True)
            return
        fileops.paste_into(self._gfile, parent=self.get_toplevel(), on_done=self._after_paste)

    def _after_paste(self, created):
        if not created or self._gfile is None:
            return

        def select(keys):
            self._select_keys(keys, cursor_key=keys[0], scroll=True)
            self.focus_view()
        self._upsert_files(created, then=select)

    # ---------------------------------------------------------------- Papierkorb
    def in_trash(self):
        return bool(self.uri) and self.uri.startswith("trash:")

    def restore_selected_or_all(self):
        files = self.get_selected() or [g for g, _i in self._entries.values()]
        if files:
            fileops.restore(files, parent=self.get_toplevel())

    def empty_trash(self):
        fileops.empty_trash(parent=self.get_toplevel(), on_done=self._after_delete)

    def delete(self, permanent=False):
        if self.archive_read_only():
            return
        files = self.get_selected()
        if not files:
            return
        if self.in_archive():       # kein Papierkorb für ZIP-Inhalte – Windows fragt
            if fileops.confirm_permanent_delete(self.get_toplevel(), files):
                archive.zip_delete(files, parent=self.get_toplevel(), on_done=self._after_zip_edit)
            return
        if self.in_trash():
            permanent = True  # aus dem Papierkorb heisst endgültig
        fileops.delete(files, parent=self.get_toplevel(), permanent=permanent,
                       on_done=self._after_delete)

    def _after_delete(self, deleted):
        keys = {g.get_uri() for g in deleted}
        if keys:
            self._apply_entry_changes(keys, {})

    def new_folder(self):
        if self._gfile is None or self._searching or self.archive_read_only():
            return
        if self.in_archive():       # Ordnereintrag in der ZIP-Datei, danach umbenennen
            target = fileops.numbered_child(self._gfile, "Neuer Ordner", is_dir=True)
            archive.zip_mkdir(target, parent=self.get_toplevel(),
                              on_done=lambda created: self._after_zip_edit(created, rename=True))
            return
        try:
            gf = fileops.new_folder(self._gfile)
        except GLib.Error as exc:
            fileops.show_errors(self.get_toplevel(), "Der Ordner konnte nicht erstellt werden.",
                                [(places.display_name(self.uri), exc.message)])
            return
        self._select_new(gf)

    def new_file(self, template_path=None):
        if self._gfile is None or self._searching or self.in_archive():
            return
        try:
            gf = fileops.new_file(self._gfile, template_path)
        except GLib.Error as exc:
            fileops.show_errors(self.get_toplevel(), "Die Datei konnte nicht erstellt werden.",
                                [(places.display_name(self.uri), exc.message)])
            return
        self._select_new(gf)

    def _select_new(self, gf):
        self._upsert_files([gf], then=self._select_new_keys)

    def _select_new_keys(self, keys):
        self._select_keys(keys, cursor_key=keys[0], scroll=True)

        def go():
            if keys[0] in self._index():
                self._select_keys(keys, cursor_key=keys[0], scroll=True)
                self.begin_rename()
            return False
        GLib.idle_add(go)

    def copy_path(self):
        files = self.get_selected()
        if not files and self._gfile is not None:
            files = [self._gfile]
        fileops.copy_paths(files)

    def _on_clipboard_changed(self):
        new = frozenset(fileops.cut_uris())
        changed = new ^ self._cut
        self._cut = new
        for key in changed:
            ent = self._entries.get(key)
            if ent is not None and key in self._index():
                self._set_row_icon(key, self._icon_for(key, ent[0], ent[1]))
        self._schedule_state_changed()

    # ======================================================================
    # Suche
    # ======================================================================
    def start_search(self, root_uri, query, content=False):
        if not query or not query.strip():
            self.stop_search()
            return
        self._stop_search_job()
        self._cancel_rename()
        self._cancel_load()
        self._drop_monitor()
        if not self._searching:
            self._pre_search_mode = self.view_mode
        self._searching = True
        self._search_root = root_uri
        self._search_query = query
        if self.uri is None:
            self.uri = root_uri
            self._gfile = Gio.File.new_for_uri(root_uri)
        self._error = None
        self._entries = {}
        self._thumbs.reset()
        self.view_mode = "details"
        self._apply_mode()
        self._rows, self._skeys, self._index_cache = [], [], None
        self._groups = None
        self._fill_store()
        self._loading = True
        self._update_empty()
        self._search_job = SearchJob(root_uri, query, self._show_hidden,
                                     self._on_search_batch, self._on_search_done, content=content)
        self._search_job.start()
        self._schedule_state_changed()

    def _on_search_batch(self, batch):
        updated = {}
        for gf, info in batch:
            key = gf.get_uri()
            if key not in self._entries:
                updated[key] = (gf, info)
        if updated:
            if self._renaming:
                self._entries.update(updated)
                self._pending_rebuild = True
            elif self.view_mode == "list" or self._grouping():
                self._apply_entry_changes(set(), updated)
            else:
                sel = self._selected_keys()
                self._entries.update(updated)
                for k in updated:
                    self._insert_row(k)
                if sel:
                    for k in sel:
                        p = self._store_path(k)
                        if p is not None:
                            self._view_select_path(p)
        self._update_empty()
        self._schedule_state_changed()

    def _on_search_done(self, _count, _limited):
        self._loading = False
        self._update_empty()
        self._schedule_state_changed()

    def _stop_search_job(self):
        if self._search_job is not None:
            self._search_job.cancel()
            self._search_job = None

    def stop_search(self):
        if not self._searching:
            return
        self._stop_search_job()
        self._searching = False
        self.col_folder.set_visible(False)
        if self.uri:
            self.load(self.uri)

    def is_searching(self):
        return self._searching

    # ======================================================================
    # Menüs
    # ======================================================================
    def _later(self, fn, *args):
        def run():
            GLib.idle_add(lambda: fn(*args) and False)
        return run

    def build_view_submenu(self):
        m = WinMenu()
        for i, mode in enumerate(VIEW_MODES):
            m.item(VIEW_LABELS[mode], self._later(self.set_view_mode, mode), icon=VIEW_ICONS[mode],
                   accel=f"Strg+Umschalt+{i + 1}", radio=(mode == self.view_mode))
        return m

    def build_sort_menu(self):
        m = WinMenu()
        for key, label in SORT_COLUMNS:
            m.item(label, self._later(self.set_sort, key, self.sort_ascending),
                   radio=(key == self.sort_column))
        m.separator()
        m.item("Aufsteigend", self._later(self.set_sort, self.sort_column, True),
               radio=self.sort_ascending)
        m.item("Absteigend", self._later(self.set_sort, self.sort_column, False),
               radio=not self.sort_ascending)
        m.separator()
        m.item("Gruppieren nach", submenu=self.build_group_menu())
        return m

    def build_group_menu(self):
        m = WinMenu()
        for key, label in SORT_COLUMNS:
            m.item(label, self._later(self.set_group, key), radio=(key == self.group_by))
        m.item("(Ohne)", self._later(self.set_group, None), radio=self.group_by is None)
        m.separator()
        on = self.group_by is not None
        m.item("Aufsteigend", self._later(self.set_group, self.group_by, True),
               radio=on and self.group_ascending, sensitive=on)
        m.item("Absteigend", self._later(self.set_group, self.group_by, False),
               radio=on and not self.group_ascending, sensitive=on)
        return m

    def build_new_menu(self):
        m = WinMenu()
        writable = self._gfile is not None and not self._searching
        m.item("Ordner", self._later(self.new_folder), icon=icons.folder_pixbuf(16),
               sensitive=writable and not self.archive_read_only())
        writable = writable and not self.in_archive()     # in einer ZIP-Datei nur „Ordner“
        m.separator()
        txt_icon = icons.gicon_pixbuf(Gio.content_type_get_icon("text/plain"), 16)
        m.item("Textdokument", self._later(self.new_file), icon=txt_icon, sensitive=writable)
        for label, path in fileops.list_templates():
            ct, _uncertain = Gio.content_type_guess(path, None)
            pb = icons.gicon_pixbuf(Gio.content_type_get_icon(ct), 16) if ct else txt_icon
            m.item(label, self._later(self.new_file, path), icon=pb, sensitive=writable)
        return m

    def _popup(self, menu, event):
        if event is not None and event.type in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
            menu.popup_at_pointer(event, attach_to=self)
            return
        # Tastatur (Menütaste, Umschalt+F10): am ausgewählten Element öffnen
        view = self._view()
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 20, 20, 1, 1
        keys = self._selected_keys()
        path = self._store_path(keys[0]) if keys else None
        if path is not None:
            if self._is_tree():
                area = self.tree.get_cell_area(path, self.col_name)
                wx, wy = self.tree.convert_bin_window_to_widget_coords(area.x, area.y)
                rect.x, rect.y = wx + 24, wy + area.height
            else:
                ok, r = self.iv.get_cell_rect(path, None)
                if ok:
                    rect.x, rect.y = r.x + r.width // 2, r.y + r.height // 2
        win = view.get_window()
        if win is None:
            return
        menu.popup_at_rect(win, rect, attach_to=self)

    def _on_popup_menu(self, _widget):
        if self._selected_keys():
            self._popup_item_menu(None)
        else:
            self._popup_background_menu(None)
        return True

    def _popup_item_menu(self, event):
        keys = self._selected_keys()
        if not keys:
            return
        ents = [self._entries[k] for k in keys]
        single = len(ents) == 1
        gf0, info0 = ents[0]
        dir0 = is_dir(info0)
        all_local_files = all(g.get_path() and not is_dir(i) for g, i in ents)
        files = [g for g, _i in ents]
        ev_copy = event.copy() if event is not None else None
        m = WinMenu()
        if self.in_trash():
            m.icon_row([
                ("cut", "Ausschneiden", self._later(self.cut), True),
                ("delete", "Endgültig löschen", self._later(self.delete), True),
            ])
            m.item("Wiederherstellen", self._later(self.restore_selected_or_all), icon="arrow_undo",
                   bold=True)
            m.item("Eigenschaften", self._later(self.host.show_properties, files), icon="wrench",
                   accel="Alt+Eingabe")
            self._popup(m, event)
            return
        rw = not self.in_archive()     # geöffnetes Archiv: nicht ausschneiden/komprimieren
        zip_rw = not self.archive_read_only()   # lokale ZIP-Datei: umbenennen/löschen
        m.icon_row([
            ("cut", "Ausschneiden", self._later(self.cut), rw),
            ("copy", "Kopieren", self._later(self.copy), True),
            ("rename", "Umbenennen", self._later(self.begin_rename), zip_rw),
            ("share", "Teilen", self._later(self._share_menu, files, ev_copy), all_local_files),
            ("delete", "Löschen", self._later(self.delete), zip_rw),
        ])
        is_img = single and not dir0 and diskimage.is_image(gf0.get_basename()) and gf0.get_path()
        if is_img:
            m.item("Bereitstellen", self._later(self.mount_image, gf0), bold=True)
        else:
            m.item("Öffnen", self._later(self.open_selected), icon="open", accel="Eingabe")
        if single and not dir0:
            m.item("Öffnen mit", icon="apps", submenu=self._open_with_menu(gf0, info0))
        if single and not dir0 and gf0.get_path() and archive.is_archive(gf0.get_basename()):
            m.item("Alle extrahieren…", self._later(archive.extract_dialog, gf0,
                                                    self.get_toplevel(), self.host))
        if single and dir0:
            uri = self._target_uri(gf0, info0)
            m.item("In neuem Tab öffnen", self._later(self.host.open_in_new_tab, uri), icon="tab_add")
            m.item("In neuem Fenster öffnen", self._later(self.host.open_in_new_window, uri), icon="window_new")
            try:
                pinned = self.host.is_pinned(uri)
            except Exception:
                pinned = False
            if pinned:
                m.item("Vom Schnellzugriff lösen", self._later(self.host.unpin, uri), icon="pin_off")
            else:
                m.item("An Schnellzugriff anheften", self._later(self.host.pin, uri), icon="pin")
        self._cloud_menu(m, files, single)
        if rw and all(g.get_path() for g in files):
            m.item("In ZIP-Datei komprimieren", self._later(self.compress, files), icon="folder_zip")
        m.item("Als Pfad kopieren", self._later(self.copy_path), icon="copy_select", accel="Strg+Umschalt+C")
        m.item("Eigenschaften", self._later(self.host.show_properties, files), icon="wrench", accel="Alt+Eingabe")
        if single and dir0 and gf0.get_path():
            m.separator()
            m.item("In Terminal öffnen", self._later(self.host.open_terminal, gf0.get_path()),
                   icon="window_console")
        self._popup(m, event)

    # -- ZIP und Abbilder (archive.py, diskimage.py) ------------------
    def compress(self, files=None):
        files = files or self.get_selected()
        if not files:
            return

        def done(created):
            if created and self._gfile is not None and created[0].get_parent().equal(self._gfile):
                self._select_new(created[0])     # wie Windows: gleich umbenennen
        archive.compress(files, parent=self.get_toplevel(), on_done=done)

    def in_archive(self):
        return bool(self.uri) and self.uri.startswith("archive:")

    def archive_read_only(self):
        """Geöffnetes Archiv, in das man nicht schreiben kann – alles ausser
        einer lokalen ZIP-Datei (TAR/7z/RAR/CAB, ZIP im ZIP; wie Windows)."""
        return self.in_archive() and not archive.writable(self.uri)

    def zip_reload(self):
        """Das Archiv wurde nach dem Schreiben neu eingehängt (archive.remount)."""
        self._mount_tried = False
        self.reload()

    def _after_zip_edit(self, created, rename=False):
        """Nach Einfügen/Umbenennen/Neuer Ordner in der ZIP-Datei die neuen
        Elemente auswählen, sobald die Ansicht neu geladen ist."""
        keys = [g.get_uri() for g in created
                if self._gfile is not None and g.get_parent() is not None
                and g.get_parent().equal(self._gfile)]
        if not keys:
            return
        if self._loading and getattr(self, "_reload_keep", None):
            self._reload_keep = (keys, keys[0], self._reload_keep[2])
        tries = [100]

        def ready():
            tries[0] -= 1
            if self._loading and tries[0] > 0:
                return True
            if not self._loading and keys[0] in self._index():
                self._select_keys(keys, cursor_key=keys[0], scroll=True)
                self.focus_view()
                if rename:
                    self.begin_rename()
            return False
        GLib.timeout_add(50, ready)

    def _browsable(self, key):
        """ZIP/TAR/7z, die sich wie ein Ordner öffnen lassen (Windows: ZIP-komprimierter Ordner)."""
        gf, info = self._entries.get(key, (None, None))
        # auch ein ZIP in einem geöffneten Archiv (gvfs verschachtelt das)
        return (gf is not None and not is_dir(info)
                and gf.get_uri().startswith(("file:", "archive:"))
                and archive.is_archive(gf.get_basename()))

    def mount_image(self, gf):
        def opened(path, by_desktop):
            # Hat der Desktop-Automounter eingehängt, öffnet er das Laufwerk selbst
            # (im Standard-Dateimanager) – sonst gäbe es zwei Fenster
            if not by_desktop:
                self.host.open_in_new_window(places.uri_for_path(path))
        diskimage.mount(gf, self.get_toplevel(), opened)

    def _cloud_menu(self, m, files, single):
        paths = [g.get_path() for g in files if g.get_path()]
        ms = cloudstatus.menu_state(paths) if self._cloud_folder and len(paths) == len(files) else None
        if ms is None:
            return
        all_pinned, any_local = ms
        m.separator()
        m.item("Immer auf diesem Gerät beibehalten",
               self._later(cloudstatus.set_pinned, paths, not all_pinned),
               check=all_pinned)
        if any_local or all_pinned:
            m.item("Speicherplatz freigeben", self._later(cloudstatus.free_up, paths))
        if single:
            m.item("Online anzeigen", self._later(cloudstatus.open_online, paths[0]), icon="globe")
        m.separator()

    def _popup_background_menu(self, event):
        m = WinMenu()
        if self.can_paste():
            m.icon_row([("clipboard_paste", "Einfügen", self._later(self.paste), True)])
        m.item("Anzeigen", icon="grid", submenu=self.build_view_submenu())
        m.item("Sortieren nach", icon="arrow_sort", submenu=self.build_sort_menu())
        m.item("Gruppieren nach", submenu=self.build_group_menu())
        m.separator()
        from . import undo
        top = self.get_toplevel()
        if undo.can_undo():
            m.item(undo.undo_label(), self._later(undo.undo, top), icon="arrow_undo", accel="Strg+Z")
        if undo.can_redo():
            m.item(undo.redo_label(), self._later(undo.redo, top), icon="arrow_redo", accel="Strg+Y")
        if undo.can_undo() or undo.can_redo():
            m.separator()
        if self.in_trash():
            has = bool(self._entries)
            m.item("Papierkorb leeren", self._later(self.empty_trash), icon="delete", sensitive=has)
            m.item("Alle Elemente wiederherstellen", self._later(self.restore_selected_or_all),
                   icon="arrow_undo", sensitive=has)
            self._popup(m, event)
            return
        if self.in_archive():
            arc = places.archive_parts(self.uri)
            m.item("Alle extrahieren…", self._later(archive.extract_dialog, arc[0],
                                                    self.get_toplevel(), self.host),
                   icon="folder_zip")
        m.item("Neu", icon="add_circle", submenu=self.build_new_menu(),
               sensitive=self._gfile is not None and not self._searching
               and not self.archive_read_only())
        m.separator()
        if self._gfile is not None:
            m.item("Eigenschaften", self._later(self.host.show_properties, [self._gfile]),
                   icon="wrench", accel="Alt+Eingabe")
        path = self._gfile.get_path() if self._gfile is not None else None
        if path:
            m.separator()
            m.item("In Terminal öffnen", self._later(self.host.open_terminal, path), icon="window_console")
        self._popup(m, event)

    def _open_with_menu(self, gf, info):
        sub = WinMenu()
        ct = places.content_type(info) or "application/octet-stream"
        apps = []
        default = Gio.AppInfo.get_default_for_type(ct, False)
        if default is not None:
            apps.append(default)
        seen = {default.get_id()} if default is not None else set()
        for app in Gio.AppInfo.get_all_for_type(ct):
            if app.get_id() in seen or not app.should_show():
                continue
            seen.add(app.get_id())
            apps.append(app)
        for app in apps[:12]:
            pb = icons.gicon_pixbuf(app.get_icon(), 16) if app.get_icon() else None
            sub.item(app.get_display_name(), self._later(self._launch_with, app, [gf]), icon=pb)
        if apps:
            sub.separator()
        sub.item("Andere App auswählen", self._later(self._choose_app, gf))
        return sub

    def _launch_with(self, app, gfiles):
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
            app.launch(gfiles, ctx)
        except GLib.Error as exc:
            fileops.show_errors(self.get_toplevel(), "Die App konnte nicht gestartet werden.",
                                [(app.get_display_name(), exc.message)])

    def _choose_app(self, gf):
        top = self.get_toplevel()
        dlg = Gtk.AppChooserDialog.new(top if isinstance(top, Gtk.Window) else None,
                                       Gtk.DialogFlags.MODAL, gf)
        dlg.set_title("Öffnen mit")
        dlg.set_heading("Wählen Sie eine App zum Öffnen dieser Datei aus.")
        resp = dlg.run()
        app = dlg.get_app_info() if resp == Gtk.ResponseType.OK else None
        dlg.destroy()
        if app is not None:
            self._launch_with(app, [gf])

    def _share_menu(self, gfiles, event):
        m = WinMenu()
        m.item("Per E-Mail senden", lambda: self._send_mail(gfiles), icon="mail")
        if event is not None:
            m.popup_at_pointer(event, attach_to=self)
        else:
            self._popup(m, None)

    def _send_mail(self, gfiles):
        cmd = ["xdg-email"]
        for g in gfiles:
            if g.get_path():
                cmd += ["--attach", g.get_path()]
        try:
            subprocess.Popen(cmd, start_new_session=True)
        except OSError as exc:
            fileops.show_errors(self.get_toplevel(), "Das E-Mail-Programm konnte nicht gestartet werden.",
                                [("xdg-email", str(exc))])

    # ======================================================================
    # Aufräumen
    # ======================================================================
    def _on_destroy(self, *_a):
        self._destroyed = True
        if self._hover_timer:
            GLib.source_remove(self._hover_timer)
            self._hover_timer = 0
        archive.untrack(self)
        self.host.config.disconnect_changed(self._on_columns_config)
        fileops.clipboard().disconnect_changed(self._on_clipboard_changed)
        self._stop_search_job()
        self._cancel_load()
        self._drop_monitor()
        self._thumbs.stop()
