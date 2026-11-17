"""Eigenschaften-Dialog wie im Windows-Explorer und Meldungsdialoge im
Windows-Stil.

    show_properties(parent_window, gfiles) -> Gtk.Window
    message(parent, text, kind="error", title="FileFenster", buttons=None, default=0) -> Index

Registerkarte "Allgemein": Icon + Name, Dateityp, Öffnen mit, Ort, Grösse
(mit Bytes in Klammern), Grösse auf Datenträger, Inhalt (Ordner, rekursiv im
Thread, abbrechbar), Erstellt/Geändert/Letzter Zugriff, Attribute
(Schreibgeschützt = Schreibrecht des Besitzers, Versteckt = Punkt am
Namensanfang). Registerkarte "Berechtigungen": Besitzer, Gruppe, rwx.
OK / Abbrechen / Übernehmen (Übernehmen erst nach einer Änderung aktiv).
"""
import grp
import os
import pwd
import threading

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import icons, places

_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"]

_ATTRS = ",".join([
    "standard::*", "time::*", "unix::mode", "unix::uid", "unix::gid", "unix::device",
    "owner::*", "access::*", "trash::*",
])

_PERM_ROWS = [("u", "Besitzer"), ("g", "Gruppe"), ("o", "Andere")]
_PERM_COLS = [("r", "Lesen"), ("w", "Schreiben"), ("x", "Ausführen")]
_BITS = {
    ("u", "r"): 0o400, ("u", "w"): 0o200, ("u", "x"): 0o100,
    ("g", "r"): 0o040, ("g", "w"): 0o020, ("g", "x"): 0o010,
    ("o", "r"): 0o004, ("o", "w"): 0o002, ("o", "x"): 0o001,
}
OWNER_WRITE = 0o200


# ---------------------------------------------------------------------------
# Formatierung
# ---------------------------------------------------------------------------
def format_long_date(ts):
    """Sonntag, 8. November 2026, 14:32:10 (wie der Windows-Eigenschaftendialog)."""
    if not ts:
        return ""
    try:
        dt = GLib.DateTime.new_from_unix_local(int(ts))
    except (TypeError, ValueError, OverflowError):
        return ""
    if dt is None:
        return ""
    return (f"{_WEEKDAYS[dt.get_day_of_week() - 1]}, {dt.get_day_of_month()}. "
            f"{_MONTHS[dt.get_month() - 1]} {dt.get_year()}, {dt.format('%H:%M:%S')}")


def format_bytes(n):
    return f"{int(n):,}".replace(",", ".")


def size_with_bytes(n):
    """1,23 MB (1.290.123 Bytes)"""
    return f"{places.format_size(n)} ({format_bytes(n)} Bytes)"


def _time_of(info, key):
    if info is None or not info.has_attribute(f"time::{key}"):
        return None
    return info.get_attribute_uint64(f"time::{key}")


# ---------------------------------------------------------------------------
# Meldungen im Windows-Stil
# ---------------------------------------------------------------------------
def _svg_pixbuf(svg, size):
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf()


def status_icon(kind, size=32):
    """Fehler (rot ✕), Warnung (gelbes Dreieck), Info/Frage (blauer Kreis)."""
    if kind == "error":
        body = ('<circle cx="16" cy="16" r="15" fill="#c42b1c"/>'
                '<path d="M11 11L21 21M21 11L11 21" stroke="#fff" stroke-width="2.2" '
                'stroke-linecap="round"/>')
    elif kind == "warning":
        body = ('<path d="M14.2 3.6a2.1 2.1 0 0 1 3.6 0l12.3 22.3A2.1 2.1 0 0 1 28.3 29H3.7'
                'a2.1 2.1 0 0 1-1.8-3.1Z" fill="#fce100"/>'
                '<path d="M16 11v8.5" stroke="#000" stroke-width="2.4" stroke-linecap="round"/>'
                '<circle cx="16" cy="24" r="1.6" fill="#000"/>')
    else:
        mark = "?" if kind == "question" else "i"
        body = ('<circle cx="16" cy="16" r="15" fill="#005fb8"/>'
                f'<text x="16" y="22.5" font-family="Selawik,sans-serif" font-size="18" '
                f'font-weight="bold" text-anchor="middle" fill="#fff">{mark}</text>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{body}</svg>'
    return _svg_pixbuf(svg, size)


def _fluent_button(label, accent=False):
    b = Gtk.Button(label=label)
    b.set_use_underline(False)
    ctx = b.get_style_context()
    ctx.add_class("win-btn")
    if accent:
        ctx.add_class("accent")
    return b


def message(parent, text, kind="error", title="FileFenster", buttons=None, default=0):
    """Modaler Meldungsdialog. buttons: Texte (Standard ["OK"]); gibt den Index
    des gedrückten Knopfs zurück, -1 bei Schliessen/Esc."""
    # Bewusst Gtk.Window statt Gtk.Dialog: dessen CSS-Knoten heisst "dialog",
    # und alle Regeln mit "window.filefenster" würden nicht greifen.
    buttons = buttons or ["OK"]
    parent = parent if isinstance(parent, Gtk.Window) else None
    dlg = Gtk.Window(title=title)
    if parent is not None:
        dlg.set_transient_for(parent)
        dlg.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
    else:
        dlg.set_position(Gtk.WindowPosition.CENTER)
    dlg.set_modal(True)
    dlg.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    dlg.set_resizable(False)
    dlg.set_skip_taskbar_hint(True)
    ctx = dlg.get_style_context()
    ctx.add_class("filefenster")
    ctx.add_class("win-dialog")
    result = {"r": -1}
    loop = GLib.MainLoop()

    def finish(idx):
        result["r"] = idx
        dlg.destroy()

    def on_key(_w, event):
        if event.keyval == Gdk.KEY_Escape:
            finish(-1)
            return True
        return False
    dlg.connect("key-press-event", on_key)
    dlg.connect("destroy", lambda *_: loop.quit())

    area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    dlg.add(area)
    body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    body.get_style_context().add_class("win-body")
    for side in ("top", "bottom", "start", "end"):
        getattr(body, f"set_margin_{side}")(24 if side != "bottom" else 26)
    img = Gtk.Image.new_from_pixbuf(status_icon(kind, 32))
    img.set_valign(Gtk.Align.START)
    body.pack_start(img, False, False, 0)
    lab = Gtk.Label(label=text, xalign=0)
    lab.set_line_wrap(True)
    lab.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    lab.set_max_width_chars(56)
    lab.set_valign(Gtk.Align.CENTER)
    body.pack_start(lab, True, True, 0)
    area.pack_start(body, True, True, 0)

    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    footer.get_style_context().add_class("win-footer")
    inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    inner.set_halign(Gtk.Align.END)
    for side in ("top", "bottom", "start", "end"):
        getattr(inner, f"set_margin_{side}")(16 if side in ("top", "bottom") else 24)
    footer.pack_start(inner, True, True, 0)
    default_btn = None
    for i, t in enumerate(buttons):
        b = _fluent_button(t, accent=(i == default))
        b.connect("clicked", lambda _b, idx=i: finish(idx))
        inner.pack_start(b, False, False, 0)
        if i == default:
            default_btn = b
    area.pack_end(footer, False, False, 0)

    dlg.show_all()
    if default_btn is not None:
        default_btn.set_can_default(True)
        dlg.set_default(default_btn)
        default_btn.grab_focus()
    dlg.present()
    loop.run()
    return result["r"]


# ---------------------------------------------------------------------------
# Rekursives Zählen (Thread)
# ---------------------------------------------------------------------------
class _Counter(threading.Thread):
    """Zählt Dateien/Ordner und summiert Grössen rekursiv. Andere Dateisysteme
    unterhalb eines Ordners werden nicht betreten (wie du -x)."""

    def __init__(self, roots, count_roots, cancel):
        super().__init__(daemon=True)
        self.roots = roots          # Liste (Gio.File, Gio.FileInfo)
        self.count_roots = count_roots
        self.cancel = cancel
        self.files = 0
        self.dirs = 0
        self.size = 0
        self.alloc = 0
        self.done = False

    def run(self):
        try:
            for gfile, info in self.roots:
                if self.cancel.is_set():
                    return
                is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
                if not is_dir:
                    self.files += 1
                    self.size += info.get_size()
                    self.alloc += info.get_attribute_uint64("standard::allocated-size")
                    continue
                if self.count_roots:
                    self.dirs += 1
                path = gfile.get_path()
                if path and gfile.get_uri_scheme() == "file":
                    self._walk_local(path)
                else:
                    self._walk_gio(gfile)
        finally:
            self.done = True

    def _walk_local(self, root):
        try:
            root_dev = os.lstat(root).st_dev
        except OSError:
            return
        stack = [root]
        while stack:
            if self.cancel.is_set():
                return
            p = stack.pop()
            try:
                it = os.scandir(p)
            except OSError:
                continue
            with it:
                for e in it:
                    if self.cancel.is_set():
                        return
                    try:
                        st = e.stat(follow_symlinks=False)
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    self.alloc += st.st_blocks * 512
                    if is_dir:
                        self.dirs += 1
                        if st.st_dev == root_dev:
                            stack.append(e.path)
                    else:
                        self.files += 1
                        self.size += st.st_size

    def _walk_gio(self, root):
        attrs = "standard::name,standard::type,standard::size,standard::allocated-size"
        stack = [root]
        while stack:
            if self.cancel.is_set():
                return
            d = stack.pop()
            try:
                en = d.enumerate_children(attrs, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
            except GLib.Error:
                continue
            try:
                while True:
                    if self.cancel.is_set():
                        return
                    info = en.next_file(None)
                    if info is None:
                        break
                    self.alloc += info.get_attribute_uint64("standard::allocated-size")
                    if info.get_file_type() == Gio.FileType.DIRECTORY:
                        self.dirs += 1
                        stack.append(en.get_child(info))
                    else:
                        self.files += 1
                        self.size += info.get_size()
            except GLib.Error:
                pass
            finally:
                try:
                    en.close(None)
                except GLib.Error:
                    pass


def _count_text(files, dirs):
    f = "1 Datei" if files == 1 else f"{format_bytes(files)} Dateien"
    d = "1 Ordner" if dirs == 1 else f"{format_bytes(dirs)} Ordner"
    return f"{f}, {d}"


def stack_pixbuf(pb, size):
    """Mehrere Elemente: Icon dreifach versetzt gestapelt."""
    inner = max(8, int(size * 0.78))
    off = (size - inner) // 2
    src = pb.scale_simple(inner, inner, GdkPixbuf.InterpType.BILINEAR) if pb else None
    dest = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    dest.fill(0)
    if src is None:
        return dest
    for i, alpha in ((2, 110), (1, 180), (0, 255)):
        x = off * i
        y = off * (2 - i)
        src.composite(dest, x, y, inner, inner, x, y, 1, 1, GdkPixbuf.InterpType.BILINEAR, alpha)
    return dest


def _icon_for(gfile, info, size):
    path = gfile.get_path() if gfile.get_uri_scheme() == "file" else None
    try:
        return icons.file_pixbuf(info, size, path)
    except Exception:
        return icons.gicon_pixbuf(None, size)


# ---------------------------------------------------------------------------
# Eigenschaften-Dialog
# ---------------------------------------------------------------------------
class PropertiesDialog(Gtk.Window):
    def __init__(self, parent, gfiles, infos):
        super().__init__(title="Eigenschaften")
        self.gfiles = list(gfiles)
        self.infos = list(infos)
        self._cancel = threading.Event()
        self._counter = None
        self._count_timer = 0
        self._block = False
        self._desired = {}          # bit -> bool (nur vom Nutzer geänderte Bits)
        self._hidden_touched = False
        self._hidden_value = False
        self._val = {}              # Werte-Labels, die laufend aktualisiert werden

        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
            self.set_destroy_with_parent(True)
            self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_resizable(False)
        self.set_default_size(400, -1)
        ctx = self.get_style_context()
        ctx.add_class("filefenster")
        ctx.add_class("props-dialog")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for side in ("top", "start", "end"):
            getattr(outer, f"set_margin_{side}")(12)
        outer.set_margin_bottom(14)
        self.add(outer)

        self.notebook = Gtk.Notebook()
        self.notebook.set_show_border(False)
        outer.pack_start(self.notebook, True, True, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        btns.set_margin_top(14)
        self.btn_ok = _fluent_button("OK", accent=True)
        self.btn_cancel = _fluent_button("Abbrechen")
        self.btn_apply = _fluent_button("Übernehmen")
        for b in (self.btn_ok, self.btn_cancel, self.btn_apply):
            btns.pack_start(b, False, False, 0)
        outer.pack_start(btns, False, False, 0)
        self.btn_ok.connect("clicked", self._on_ok)
        self.btn_cancel.connect("clicked", lambda *_: self.destroy())
        self.btn_apply.connect("clicked", lambda *_: self._apply())
        self.btn_ok.set_can_default(True)
        self.set_default(self.btn_ok)

        self.connect("key-press-event", self._on_key)
        self.connect("destroy", self._on_destroy)

        self._build_pages()
        self._start_counter()

    # ------------------------------------------------------------------
    @property
    def single(self):
        return len(self.gfiles) == 1

    def _is_dir(self, i):
        return self.infos[i].get_file_type() == Gio.FileType.DIRECTORY

    def _modes(self):
        out = []
        for info in self.infos:
            # Verknüpfung: Linux kennt keine Rechte am Link – chmod träfe das ZIEL
            # (und setzte dort die 777 des Links, z. B. „Schreibgeschützt“ → 577)
            if info.has_attribute("standard::type") and \
                    info.get_file_type() == Gio.FileType.SYMBOLIC_LINK:
                out.append(None)
            elif info.has_attribute("unix::mode"):
                out.append(info.get_attribute_uint32("unix::mode") & 0o7777)
            else:
                out.append(None)
        return out

    def _names(self):
        return [info.get_display_name() or info.get_name() for info in self.infos]

    def _update_title(self):
        names = self._names()
        if self.single:
            self.set_title(f"Eigenschaften von {names[0]}")
        else:
            self.set_title(f"{names[0]}, … Eigenschaften")

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _build_pages(self, page=0):
        for child in self.notebook.get_children():
            self.notebook.remove(child)
        self._val = {}
        self._update_title()
        self.notebook.append_page(self._general_page(), Gtk.Label(label="Allgemein"))
        self.notebook.append_page(self._perm_page(), Gtk.Label(label="Berechtigungen"))
        self.notebook.show_all()
        self.notebook.set_current_page(page)
        self._update_dirty()
        self._update_counter_labels()

    @staticmethod
    def _page_box():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("props-page")
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(16)
        return box

    @staticmethod
    def _key(text):
        lab = Gtk.Label(label=text, xalign=0)
        lab.get_style_context().add_class("props-key")
        lab.set_valign(Gtk.Align.CENTER)
        return lab

    @staticmethod
    def _value(text, selectable=False, ellipsize=True):
        lab = Gtk.Label(label=text or "", xalign=0)
        lab.get_style_context().add_class("props-value")
        lab.set_hexpand(True)
        if ellipsize:
            lab.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lab.set_max_width_chars(34)
            lab.set_width_chars(10)
        if selectable:
            lab.set_selectable(True)
            lab.set_can_focus(False)
        if text:
            lab.set_tooltip_text(text if len(text) > 34 else None)
        return lab

    class _Rows:
        def __init__(self, grid):
            self.grid = grid
            self.r = 0

        def add(self, key, widget):
            self.grid.attach(PropertiesDialog._key(key), 0, self.r, 1, 1)
            self.grid.attach(widget, 1, self.r, 1, 1)
            self.r += 1
            return widget

        def full(self, widget):
            self.grid.attach(widget, 0, self.r, 2, 1)
            self.r += 1

        def sep(self):
            s = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            s.get_style_context().add_class("props-sep")
            s.set_margin_top(4)
            s.set_margin_bottom(4)
            self.full(s)

    def _general_page(self):
        page = self._page_box()
        grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        page.pack_start(grid, False, False, 0)
        rows = self._Rows(grid)
        names = self._names()

        # -- Kopf: Icon + Name ------------------------------------------
        if self.single:
            pb = _icon_for(self.gfiles[0], self.infos[0], 32)
        else:
            pb = stack_pixbuf(_icon_for(self.gfiles[0], self.infos[0], 32), 32)
        img = Gtk.Image.new_from_pixbuf(pb)
        img.set_halign(Gtk.Align.START)
        grid.attach(img, 0, rows.r, 1, 1)
        if self.single:
            self.name_entry = Gtk.Entry()
            self.name_entry.set_text(names[0])
            self.name_entry.set_hexpand(True)
            self.name_entry.set_activates_default(True)
            self.name_entry.get_style_context().add_class("props-name")
            self.name_entry.connect("changed", self._on_name_changed)
            grid.attach(self.name_entry, 1, rows.r, 1, 1)
        else:
            self.name_entry = None
            nfiles = sum(1 for i in range(len(self.infos)) if not self._is_dir(i))
            ndirs = len(self.infos) - nfiles
            grid.attach(self._value(_count_text(nfiles, ndirs)), 1, rows.r, 1, 1)
        rows.r += 1
        rows.sep()

        # -- Typ / Öffnen mit -------------------------------------------
        if self.single:
            info = self.infos[0]
            if self._is_dir(0):
                rows.add("Typ:", self._value("Dateiordner"))
            else:
                desc = places.type_description(info)
                ext = os.path.splitext(names[0])[1]   # ".bashrc" → keine Endung
                if ext:
                    desc = f"{desc} ({ext})"
                rows.add("Dateityp:", self._value(desc))
                ct = places.content_type(info)
                if ct and info.get_file_type() == Gio.FileType.REGULAR:
                    rows.add("Öffnen mit:", self._open_with_box(ct))
        else:
            types = {("dir" if self._is_dir(i) else places.type_description(self.infos[i]))
                     for i in range(len(self.infos))}
            if len(types) == 1:
                t = types.pop()
                rows.add("Typ:", self._value(f"Alle vom Typ {'Dateiordner' if t == 'dir' else t}"))
            else:
                rows.add("Typ:", self._value("Mehrere Typen"))
        rows.sep()

        # -- Ort / Grösse / Inhalt ----------------------------------------
        parents = {(g.get_parent().get_uri() if g.get_parent() else "") for g in self.gfiles}
        if len(parents) == 1:
            parent_uri = parents.pop()
            ptext = (Gio.File.new_for_uri(parent_uri).get_parse_name() if parent_uri else "")
            if parent_uri.startswith("trash:"):
                ptext = "Papierkorb"
            rows.add("Ort:", self._value(ptext if self.single else f"Alle in {ptext}",
                                         selectable=True))
        else:
            rows.add("Ort:", self._value("Verschiedene Ordner"))
        if self.single and self._is_dir(0) is False:
            info = self.infos[0]
            rows.add("Grösse:", self._value(size_with_bytes(info.get_size())))
            rows.add("Grösse auf Datenträger:", self._value(size_with_bytes(
                info.get_attribute_uint64("standard::allocated-size"))))
        else:
            self._val["size"] = rows.add("Grösse:", self._value("Wird berechnet …"))
            self._val["alloc"] = rows.add("Grösse auf Datenträger:", self._value(""))
            if any(self._is_dir(i) for i in range(len(self.infos))):
                self._val["content"] = rows.add("Inhalt:", self._value(""))
        rows.sep()

        # -- Zeiten ------------------------------------------------------
        if self.single:
            info = self.infos[0]
            created = _time_of(info, "created")
            modified = _time_of(info, "modified")
            access = _time_of(info, "access")
            if created:
                rows.add("Erstellt:", self._value(format_long_date(created)))
            if modified and (not self._is_dir(0) or not created):
                rows.add("Geändert:", self._value(format_long_date(modified)))
            if access and not self._is_dir(0):
                rows.add("Letzter Zugriff:", self._value(format_long_date(access)))
            if created or modified or access:
                rows.sep()

        # -- Attribute ---------------------------------------------------
        attr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.chk_readonly = Gtk.CheckButton(label="Schreibgeschützt")
        self.chk_hidden = Gtk.CheckButton(label="Versteckt")
        attr.pack_start(self.chk_readonly, False, False, 0)
        attr.pack_start(self.chk_hidden, False, False, 0)
        rows.add("Attribute:", attr)
        self._init_attr_checks()
        return page

    def _open_with_box(self, ct):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        app = Gio.AppInfo.get_default_for_type(ct, False)
        self._app_img = Gtk.Image()
        self._app_label = Gtk.Label(xalign=0)
        self._app_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._app_label.set_hexpand(True)
        self._set_app(app)
        box.pack_start(self._app_img, False, False, 0)
        box.pack_start(self._app_label, True, True, 0)
        change = _fluent_button("Ändern…")
        change.get_style_context().add_class("small")
        change.connect("clicked", lambda *_: self._change_app(ct))
        box.pack_end(change, False, False, 0)
        return box

    def _set_app(self, app):
        if app is not None:
            self._app_img.set_from_pixbuf(icons.gicon_pixbuf(app.get_icon(), 16))
            self._app_label.set_text(app.get_display_name() or app.get_name())
        else:
            self._app_img.clear()
            self._app_label.set_text("Keine Anwendung zugeordnet")

    def _change_app(self, ct):
        dlg = Gtk.AppChooserDialog.new_for_content_type(self, Gtk.DialogFlags.MODAL, ct)
        dlg.set_title("Öffnen mit")
        if dlg.run() == Gtk.ResponseType.OK:
            app = dlg.get_app_info()
            if app is not None:
                try:
                    app.set_as_default_for_type(ct)
                except GLib.Error as exc:
                    print(f"[filefenster] Standardprogramm nicht gesetzt: {exc.message}")
                self._set_app(app)
        dlg.destroy()

    def _perm_page(self):
        page = self._page_box()
        grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        page.pack_start(grid, False, False, 0)
        rows = self._Rows(grid)

        def owner_name(info, kind):
            name = info.get_attribute_string(f"owner::{kind}")
            if name:
                return name
            attr = "unix::uid" if kind == "user" else "unix::gid"
            if not info.has_attribute(attr):    # Archiv, Handy, manche Freigaben: unbekannt
                return ""
            try:
                if kind == "user":
                    return pwd.getpwuid(info.get_attribute_uint32("unix::uid")).pw_name
                return grp.getgrgid(info.get_attribute_uint32("unix::gid")).gr_name
            except (KeyError, TypeError):
                return ""
        users = {owner_name(i, "user") for i in self.infos}
        groups = {owner_name(i, "group") for i in self.infos}
        rows.add("Besitzer:", self._value(users.pop() if len(users) == 1 else "Verschiedene"))
        rows.add("Gruppe:", self._value(groups.pop() if len(groups) == 1 else "Verschiedene"))
        rows.sep()

        modes = self._modes()
        self._perm_checks = {}
        self.lbl_mode = None
        if all(m is None for m in modes):
            note = Gtk.Label(label="Für diesen Speicherort nicht verfügbar.", xalign=0)
            note.get_style_context().add_class("props-note")
            rows.full(note)
            return page

        table = Gtk.Grid(column_spacing=22, row_spacing=10)
        table.attach(Gtk.Label(label=""), 0, 0, 1, 1)
        for c, (_w, title) in enumerate(_PERM_COLS):
            h = Gtk.Label(label=title, xalign=0)
            h.get_style_context().add_class("props-colhead")
            table.attach(h, c + 1, 0, 1, 1)
        for r, (who, title) in enumerate(_PERM_ROWS):
            table.attach(self._key(title), 0, r + 1, 1, 1)
            for c, (what, _t) in enumerate(_PERM_COLS):
                chk = Gtk.CheckButton()
                bit = _BITS[(who, what)]
                self._set_check_state(chk, bit, modes)
                chk.connect("toggled", self._on_perm_toggled, bit)
                self._perm_checks[bit] = chk
                table.attach(chk, c + 1, r + 1, 1, 1)
        rows.full(table)
        rows.sep()
        self.lbl_mode = rows.add("Als Zahl:", self._value(""))
        if any(self._is_dir(i) for i in range(len(self.infos))):
            hint = Gtk.Label(label="Bei Ordnern erlaubt „Ausführen“ das Öffnen des Ordners.",
                             xalign=0)
            hint.set_line_wrap(True)
            hint.set_max_width_chars(40)
            hint.get_style_context().add_class("props-note")
            rows.full(hint)
        self._update_mode_label()
        return page

    # ------------------------------------------------------------------
    # Zustände der Kästchen
    # ------------------------------------------------------------------
    def _set_check_state(self, chk, bit, modes, invert=False):
        vals = [bool(m & bit) != invert for m in modes if m is not None]
        self._block = True
        try:
            if bit in self._desired:
                chk.set_inconsistent(False)
                chk.set_active(self._desired[bit] != invert)
            elif not vals:
                chk.set_sensitive(False)
            elif all(vals):
                chk.set_inconsistent(False)
                chk.set_active(True)
            elif not any(vals):
                chk.set_inconsistent(False)
                chk.set_active(False)
            else:
                chk.set_active(False)
                chk.set_inconsistent(True)
        finally:
            self._block = False

    def _init_attr_checks(self):
        modes = self._modes()
        if all(m is None for m in modes):
            # kein Unix-Modus (z. B. Netzwerk): nur anzeigen
            self._block = True
            self.chk_readonly.set_active(not self.infos[0].get_attribute_boolean("access::can-write"))
            self._block = False
            self.chk_readonly.set_sensitive(False)
        else:
            self._set_check_state(self.chk_readonly, OWNER_WRITE, modes, invert=True)
        names = self._names()
        hidden = [n.startswith(".") for n in names]
        self._block = True
        try:
            if self._hidden_touched:
                self.chk_hidden.set_active(self._hidden_value)
            elif all(hidden):
                self.chk_hidden.set_active(True)
            elif any(hidden):
                self.chk_hidden.set_inconsistent(True)
        finally:
            self._block = False
        self.chk_readonly.connect("toggled", self._on_readonly_toggled)
        self.chk_hidden.connect("toggled", self._on_hidden_toggled)
        can_rename = all(i.get_attribute_boolean("access::can-rename")
                         if i.has_attribute("access::can-rename") else True for i in self.infos)
        self.chk_hidden.set_sensitive(can_rename)
        if self.name_entry is not None:
            self.name_entry.set_sensitive(can_rename)

    def _on_perm_toggled(self, chk, bit):
        if self._block:
            return
        chk.set_inconsistent(False)
        self._desired[bit] = chk.get_active()
        if bit == OWNER_WRITE:
            self._block = True
            self.chk_readonly.set_inconsistent(False)
            self.chk_readonly.set_active(not chk.get_active())
            self._block = False
        self._update_mode_label()
        self._update_dirty()

    def _on_readonly_toggled(self, chk):
        if self._block:
            return
        chk.set_inconsistent(False)
        self._desired[OWNER_WRITE] = not chk.get_active()
        w = self._perm_checks.get(OWNER_WRITE)
        if w is not None:
            self._block = True
            w.set_inconsistent(False)
            w.set_active(not chk.get_active())
            self._block = False
        self._update_mode_label()
        self._update_dirty()

    def _on_hidden_toggled(self, chk):
        if self._block:
            return
        chk.set_inconsistent(False)
        if self.single and self.name_entry is not None:
            text = self.name_entry.get_text()
            text = ("." + text.lstrip(".")) if chk.get_active() else text.lstrip(".")
            self._block = True
            self.name_entry.set_text(text)
            self._block = False
        else:
            self._hidden_touched = True
            self._hidden_value = chk.get_active()
        self._update_dirty()

    def _on_name_changed(self, entry):
        if self._block:
            return
        self._block = True
        self.chk_hidden.set_active(entry.get_text().startswith("."))
        self._block = False
        self._update_dirty()

    def _update_mode_label(self):
        if self.lbl_mode is None:
            return
        modes = [m for m in self._modes() if m is not None]
        if not modes:
            return
        planned = {self._new_mode(m) for m in modes}
        if len(planned) == 1:
            m = planned.pop()
            sym = "".join(ch if m & bit else "-" for bit, ch in zip(
                (0o400, 0o200, 0o100, 0o040, 0o020, 0o010, 0o004, 0o002, 0o001), "rwxrwxrwx"))
            self.lbl_mode.set_text(f"{m & 0o777:03o}  ({sym})")
        else:
            self.lbl_mode.set_text("Verschieden")

    def _new_mode(self, mode):
        m = mode
        for bit, val in self._desired.items():
            m = (m | bit) if val else (m & ~bit)
        return m

    # ------------------------------------------------------------------
    # Änderungen planen / übernehmen
    # ------------------------------------------------------------------
    def _plan(self):
        plan = []
        modes = self._modes()
        names = self._names()
        for i in range(len(self.gfiles)):
            new_mode = None
            if modes[i] is not None and self._desired:
                m = self._new_mode(modes[i])
                if m != modes[i]:
                    new_mode = m
            if self.single and self.name_entry is not None:
                target = self.name_entry.get_text()
            else:
                target = names[i]
                if self._hidden_touched:
                    target = ("." + target.lstrip(".")) if self._hidden_value else target.lstrip(".")
            new_name = target if target != names[i] else None
            if new_mode is not None or new_name is not None:
                plan.append((i, new_mode, new_name))
        return plan

    def _update_dirty(self):
        self.btn_apply.set_sensitive(bool(self._plan()))

    def _validate(self, plan):
        for i, _mode, new_name in plan:
            if new_name is None:
                continue
            if not new_name.strip() or new_name.lstrip(".") == "":
                message(self, "Sie müssen einen Dateinamen eingeben.", kind="error")
                return False
            if "/" in new_name or "\0" in new_name:
                message(self, "Ein Dateiname darf keines der folgenden Zeichen enthalten:\n/",
                        kind="error")
                return False
            if self.single and not self._is_dir(i):
                old = self._names()[i]
                old_ext = os.path.splitext(old.lstrip("."))[1].lower()
                new_ext = os.path.splitext(new_name.lstrip("."))[1].lower()
                if old_ext != new_ext:
                    ans = message(self, "Wenn Sie die Dateinamenerweiterung ändern, wird die "
                                        "Datei möglicherweise unbrauchbar.\n\n"
                                        "Möchten Sie sie wirklich ändern?",
                                  kind="warning", title="Umbenennen",
                                  buttons=["Ja", "Nein"], default=0)
                    if ans != 0:
                        return False
        return True

    def _apply(self):
        plan = self._plan()
        if not plan:
            return True
        if not self._validate(plan):
            return False
        errors = []
        for i, new_mode, new_name in plan:
            g = self.gfiles[i]
            label = self._names()[i]
            if new_mode is not None:
                try:
                    g.set_attribute_uint32("unix::mode", new_mode,
                                           Gio.FileQueryInfoFlags.NONE, None)
                except GLib.Error as exc:
                    errors.append(f"{label}: {exc.message}")
            if new_name is not None:
                try:
                    self.gfiles[i] = g.set_display_name(new_name, None)
                except GLib.Error as exc:
                    if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                        errors.append(f'Es ist bereits eine Datei mit dem Namen "{new_name}" '
                                      'vorhanden.')
                    else:
                        errors.append(f"{label}: {exc.message}")
        self._reload()
        if errors:
            message(self, "\n".join(errors), kind="error")
            return False
        return True

    def _reload(self):
        infos = []
        for g, old in zip(self.gfiles, self.infos):
            try:
                infos.append(g.query_info(_ATTRS, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None))
            except GLib.Error:
                infos.append(old)
        self.infos = infos
        self._desired = {}
        self._hidden_touched = False
        self._build_pages(self.notebook.get_current_page())

    def _on_ok(self, *_):
        if self._apply():
            self.destroy()

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    # ------------------------------------------------------------------
    # Grösse/Inhalt im Hintergrund
    # ------------------------------------------------------------------
    def _start_counter(self):
        if self.single and not self._is_dir(0):
            return
        roots = list(zip(self.gfiles, self.infos))
        self._counter = _Counter(roots, count_roots=not self.single, cancel=self._cancel)
        self._counter.start()
        self._count_timer = GLib.timeout_add(200, self._tick)

    def _tick(self):
        self._update_counter_labels()
        if self._counter is None or self._counter.done:
            self._count_timer = 0
            return False
        return True

    def _update_counter_labels(self):
        c = self._counter
        if c is None:
            return
        if "size" in self._val:
            self._val["size"].set_text(size_with_bytes(c.size))
        if "alloc" in self._val:
            self._val["alloc"].set_text(size_with_bytes(c.alloc))
        if "content" in self._val:
            self._val["content"].set_text(_count_text(c.files, c.dirs))

    def _on_destroy(self, *_):
        self._cancel.set()
        if self._count_timer:
            GLib.source_remove(self._count_timer)
            self._count_timer = 0


def show_properties(parent_window, gfiles):
    """Eigenschaften-Dialog für eine Liste von Gio.File öffnen (nicht modal)."""
    gfiles = [g for g in (gfiles or []) if g is not None]
    good, infos, failed = [], [], []
    for g in gfiles:
        try:
            infos.append(g.query_info(_ATTRS, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None))
            good.append(g)
        except GLib.Error as exc:
            failed.append(exc.message)
    if not good:
        if failed:
            message(parent_window, failed[0], kind="error")
        return None
    dlg = PropertiesDialog(parent_window, good, infos)
    dlg.show_all()
    dlg.present()
    if dlg.name_entry is not None:
        dlg.name_entry.grab_focus()
        name = dlg.name_entry.get_text()
        stem = os.path.splitext(name)[0] if not dlg._is_dir(0) else name
        dlg.name_entry.select_region(0, len(stem) if stem and not name.startswith(".") else -1)
    else:
        dlg.btn_ok.grab_focus()
    return dlg
