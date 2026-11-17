"""Detailbereich rechts (Schalter "Details" in der Befehlsleiste) wie im
Windows-11-Explorer: grosses Icon bzw. Vorschaubild, Name, Typ, darunter
"Details" mit Eigenschaften (Bezeichnung grau links, Wert rechts).

    pane = DetailsPane(host)
    pane.show_items(location, gfiles)   # gfiles leer → Infos zum aktuellen Ort

Alles, was dauern kann (Dateiinfos, Bildvorschau, Elemente zählen), läuft in
einem Hintergrund-Thread; veraltete Ergebnisse werden verworfen.
"""
import hashlib
import os
import threading

from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango

from . import cloudstatus, icons, places
from .mica import too_many_pixels
from .propsdialog import stack_pixbuf

PREVIEW_MAX = 200          # Vorschau höchstens so breit/hoch
ICON_SIZE = 96             # Icon, wenn es keine Vorschau gibt
MAX_PREVIEW_BYTES = 80 * 1024 * 1024
MAX_MULTI = 5000           # bei Mehrfachauswahl höchstens so viele Dateien abfragen

_ATTRS = places.FILE_ATTRS + ",standard::allocated-size"

PLACEHOLDER = "Wählen Sie eine Datei aus, um Details anzuzeigen."

_thumb_factory = None
_thumb_factory_tried = False
_thumb_lock = threading.Lock()


def _thumbnail_factory():
    """Cinnamon-Thumbnailer (Videos, PDFs …), falls vorhanden – sonst None."""
    global _thumb_factory, _thumb_factory_tried
    if _thumb_factory_tried:
        return _thumb_factory
    _thumb_factory_tried = True
    try:
        import gi
        gi.require_version("CinnamonDesktop", "3.0")
        from gi.repository import CinnamonDesktop
        _thumb_factory = CinnamonDesktop.DesktopThumbnailFactory.new(
            CinnamonDesktop.DesktopThumbnailSize.LARGE)
    except (ImportError, ValueError, AttributeError, TypeError):
        _thumb_factory = None
    return _thumb_factory


def _cached_thumbnail(uri):
    md5 = hashlib.md5(uri.encode()).hexdigest() + ".png"
    base = os.path.join(GLib.get_user_cache_dir(), "thumbnails")
    for sub in ("xx-large", "x-large", "large", "normal"):
        p = os.path.join(base, sub, md5)
        if os.path.exists(p):
            return p
    return None


def _scaled_file(path, target):
    try:
        _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
        if w and h and (w > target or h > target):
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, target, target, True)
        return GdkPixbuf.Pixbuf.new_from_file(path)
    except (GLib.Error, TypeError):
        return None


def _load_image(gfile, target, cancel):
    """Bild skaliert laden (nur so gross wie nötig). → (pixbuf, (w, h))"""
    loader = GdkPixbuf.PixbufLoader()
    dims = {}

    def on_size(ld, w, h):
        dims["wh"] = (w, h)
        fmt = ld.get_format()
        if too_many_pixels(fmt.get_name() if fmt else "", w, h):
            dims["big"] = True       # Dekompressionsbombe o. Ä.: 0×0 = gar nicht dekodieren
            ld.set_size(0, 0)
            return
        if w > target or h > target:
            s = min(target / w, target / h)
            ld.set_size(max(1, int(w * s)), max(1, int(h * s)))
    loader.connect("size-prepared", on_size)
    try:
        stream = gfile.read(None)
    except GLib.Error:
        return None, None
    ok = True
    try:
        while True:
            if cancel.is_set() or dims.get("big"):
                ok = False
                break
            data = stream.read_bytes(65536 if dims else 4096, None)   # erst nur den Kopf
            if data.get_size() == 0:
                break
            loader.write_bytes(data)
    except GLib.Error:
        ok = False
    finally:
        try:
            stream.close(None)
        except GLib.Error:
            pass
        try:
            loader.close()
        except GLib.Error:
            ok = ok and loader.get_pixbuf() is not None
    if not ok:
        return None, dims.get("wh")
    pb = loader.get_pixbuf()
    if pb is not None:
        pb = pb.apply_embedded_orientation() or pb
    return pb, dims.get("wh")


def _thumbnail(gfile, info, target):
    uri = gfile.get_uri()
    path = _cached_thumbnail(uri)
    if path is None and info.has_attribute("thumbnail::path"):
        path = info.get_attribute_byte_string("thumbnail::path")
    if path:
        return _scaled_file(path, target)
    # noch keins vorhanden → erzeugen lassen (wie Nemo), nur lokal und nicht zu gross
    factory = _thumbnail_factory()
    ct = places.content_type(info)
    mtime = info.get_attribute_uint64("time::modified") if info.has_attribute("time::modified") else 0
    if (factory is None or not ct or gfile.get_path() is None
            or info.get_size() > 512 * 1024 * 1024):
        return None
    try:
        with _thumb_lock:
            from .previewpane import factory_thumbnail      # abgeschottet
            pb = factory_thumbnail(factory, uri, gfile.get_path(), ct, mtime)
            if pb is None:
                return None
    except (GLib.Error, TypeError, AttributeError):
        return None
    if pb.get_width() > target or pb.get_height() > target:
        s = min(target / pb.get_width(), target / pb.get_height())
        pb = pb.scale_simple(max(1, int(pb.get_width() * s)), max(1, int(pb.get_height() * s)),
                             GdkPixbuf.InterpType.BILINEAR)
    return pb


def _count_children(gfile, show_hidden, cancel):
    n = 0
    try:
        en = gfile.enumerate_children("standard::name,standard::is-hidden,standard::is-backup",
                                      Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return None
    try:
        while True:
            if cancel.is_set():
                return None
            i = en.next_file(None)
            if i is None:
                break
            if show_hidden or not (i.get_attribute_boolean("standard::is-hidden") or i.get_attribute_boolean("standard::is-backup")):
                n += 1
    except GLib.Error:
        return None
    finally:
        try:
            en.close(None)
        except GLib.Error:
            pass
    return n


def _is_image(info):
    ct = places.content_type(info)
    return ct.startswith("image/") or Gio.content_type_is_a(ct, "image/*")


def _local_path(gfile):
    return gfile.get_path() if gfile.get_uri_scheme() == "file" else None


def _parent_text(gfile, info):
    if info.has_attribute("trash::orig-path"):
        orig = info.get_attribute_byte_string("trash::orig-path")
        if orig:
            return places.safe_text(os.path.dirname(orig))
    parent = gfile.get_parent()
    if parent is None:
        return ""
    if parent.get_uri().startswith("trash:"):
        return "Papierkorb"
    return parent.get_parse_name()


def _time(info, key):
    attr = f"time::{key}"
    return info.get_attribute_uint64(attr) if info.has_attribute(attr) else 0


def _no_hyphens(label):
    """Pango trennt beim Umbruch mitten im Wort mit "-" – Windows nicht."""
    try:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_insert_hyphens_new(False))
        label.set_attributes(attrs)
    except Exception:
        pass


class DetailsPane(Gtk.ScrolledWindow):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._gen = 0
        self._timer = 0
        self._cancel = None
        self._dims_row = None
        self._count_row = None

        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self.get_style_context().add_class("details-pane")

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vhomogeneous(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(18)
        self.preview = Gtk.Image()
        self.preview.get_style_context().add_class("dp-preview")
        self.preview.set_halign(Gtk.Align.CENTER)
        self.preview.set_margin_top(8)
        self.preview.set_margin_bottom(20)
        content.pack_start(self.preview, False, False, 0)

        self.name = Gtk.Label(xalign=0)
        self.name.get_style_context().add_class("dp-name")
        self.name.set_line_wrap(True)
        self.name.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.name.set_max_width_chars(24)
        _no_hyphens(self.name)
        content.pack_start(self.name, False, False, 0)

        self.kind = Gtk.Label(xalign=0)
        self.kind.get_style_context().add_class("dp-type")
        self.kind.set_line_wrap(True)
        self.kind.set_max_width_chars(24)
        self.kind.set_margin_top(2)
        content.pack_start(self.kind, False, False, 0)

        self.heading = Gtk.Label(label="Details", xalign=0)
        self.heading.get_style_context().add_class("dp-heading")
        self.heading.set_margin_top(24)
        self.heading.set_margin_bottom(12)
        content.pack_start(self.heading, False, False, 0)

        self.grid = Gtk.Grid(column_spacing=16, row_spacing=10)
        content.pack_start(self.grid, False, False, 0)
        self.stack.add_named(content, "content")

        ph = Gtk.Label(label=PLACEHOLDER)
        ph.get_style_context().add_class("dp-placeholder")
        ph.set_line_wrap(True)
        ph.set_justify(Gtk.Justification.CENTER)
        ph.set_max_width_chars(26)
        ph.set_valign(Gtk.Align.CENTER)
        ph.set_halign(Gtk.Align.CENTER)
        ph.set_vexpand(True)
        ph.set_margin_start(20)
        ph.set_margin_end(20)
        self.stack.add_named(ph, "placeholder")

        self.add(self.stack)
        child = self.get_child()
        if isinstance(child, Gtk.Viewport):
            child.set_shadow_type(Gtk.ShadowType.NONE)
        self.stack.show_all()
        self.stack.set_visible_child_name("placeholder")
        self.connect("destroy", lambda *_: self._cancel and self._cancel.set())

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------
    def show_items(self, location, gfiles):
        self._gen += 1
        if self._cancel is not None:
            self._cancel.set()
            self._cancel = None
        if self._timer:
            GLib.source_remove(self._timer)
        gfiles = [g for g in (gfiles or []) if g is not None]
        # kurz entprellen: beim Durchlaufen mit Pfeiltasten nicht jede Datei laden
        self._timer = GLib.timeout_add(40, self._start, self._gen, location, gfiles)

    # ------------------------------------------------------------------
    def _preview_target(self):
        w = self.get_allocated_width()
        if w <= 1:
            return PREVIEW_MAX
        return max(64, min(PREVIEW_MAX, w - 40))

    def _show_placeholder(self):
        self.stack.set_visible_child_name("placeholder")

    def _start(self, gen, location, gfiles):
        self._timer = 0
        if gen != self._gen:
            return False
        if not gfiles:
            if not location:
                self._show_placeholder()
                return False
            if places.is_virtual(location):          # Start, Katalog, Dieser PC
                self._show_virtual(location)
                return False
            try:
                gfiles = [Gio.File.new_for_uri(location)]
            except Exception:
                self._show_placeholder()
                return False
            mode = "location"
        else:
            mode = "single" if len(gfiles) == 1 else "multi"
        cancel = threading.Event()
        self._cancel = cancel
        config = getattr(self.host, "config", None)
        show_hidden = bool(config.get("show_hidden", False)) if config is not None else False
        t = threading.Thread(target=self._worker, daemon=True,
                             args=(gen, cancel, mode, gfiles, show_hidden, self._preview_target()))
        t.start()
        return False

    def _worker(self, gen, cancel, mode, gfiles, show_hidden, target):
        infos = []
        for g in gfiles[:MAX_MULTI]:
            if cancel.is_set():
                return
            try:
                infos.append((g, g.query_info(_ATTRS, Gio.FileQueryInfoFlags.NONE, None)))
            except GLib.Error:
                continue
        if cancel.is_set():
            return
        if not infos:
            GLib.idle_add(self._apply_placeholder, gen)
            return
        GLib.idle_add(self._apply_info, gen, mode, infos, len(gfiles))
        if mode == "multi":
            return
        g, info = infos[0]
        extra = {}
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            extra["count"] = _count_children(g, show_hidden, cancel)
        elif info.get_size() <= MAX_PREVIEW_BYTES:
            pb = None
            if _is_image(info):
                pb, wh = _load_image(g, target, cancel)
                extra["dims"] = wh
            if pb is None and not cancel.is_set():
                pb = _thumbnail(g, info, target)
            extra["preview"] = pb
        if not cancel.is_set():
            GLib.idle_add(self._apply_extra, gen, extra)

    # ------------------------------------------------------------------
    # UI füllen (Hauptthread)
    # ------------------------------------------------------------------
    def _apply_placeholder(self, gen):
        if gen == self._gen:
            self._show_placeholder()
        return False

    def _clear(self):
        for child in self.grid.get_children():
            child.destroy()
        self._rows = 0
        self._dims_row = None
        self._count_row = None

    def _add_row(self, key, value):
        k = Gtk.Label(label=key, xalign=0)
        k.get_style_context().add_class("dp-key")
        k.set_valign(Gtk.Align.START)
        v = Gtk.Label(label=value or "", xalign=0)
        v.get_style_context().add_class("dp-value")
        v.set_line_wrap(True)
        v.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        v.set_max_width_chars(24)
        _no_hyphens(v)
        v.set_hexpand(True)
        v.set_valign(Gtk.Align.START)
        self.grid.attach(k, 0, self._rows, 1, 1)
        self.grid.attach(v, 1, self._rows, 1, 1)
        k.show()
        v.show()
        self._rows += 1
        return v

    def _set_header(self, pixbuf, name, kind):
        self.preview.set_from_pixbuf(pixbuf)
        self.name.set_text(name or "")
        self.kind.set_text(kind or "")
        self.kind.set_visible(bool(kind))

    def _show_virtual(self, loc):
        self._clear()
        kind = places.loc_icon_kind(loc)
        self._set_header(icons.place_pixbuf(kind, ICON_SIZE), places.display_name(loc), "")
        self.heading.hide()
        self.stack.set_visible_child_name("content")

    def _icon(self, gfile, info, size):
        path = _local_path(gfile)
        try:
            if info.get_file_type() == Gio.FileType.DIRECTORY:
                return icons.folder_pixbuf(size, places.special_key_for_path(path))
            return icons.file_pixbuf(info, size, path)
        except Exception:
            return icons.gicon_pixbuf(None, size)

    def _apply_info(self, gen, mode, infos, total):
        if gen != self._gen:
            return False
        self._clear()
        if mode == "multi":
            self._fill_multi(infos, total)
        else:
            self._fill_single(infos[0][0], infos[0][1])
        self.heading.set_visible(self._rows > 0)
        self.stack.set_visible_child_name("content")
        return False

    def _fill_single(self, gfile, info):
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        if is_dir:
            try:
                name = places.display_name(gfile.get_uri())
            except Exception:
                name = info.get_display_name()
        else:
            name = info.get_display_name()
        self._set_header(self._icon(gfile, info, ICON_SIZE), name, places.type_description(info))

        if is_dir:
            self._count_row = self._add_row("Inhalt", "Wird gezählt …")
        else:
            self._add_row("Grösse", places.format_size(info.get_size()))
            if _is_image(info):
                self._dims_row = self._add_row("Abmessungen", "…")
        cst = cloudstatus.state(gfile.get_path(), is_dir) if gfile.get_path() else None
        if cst:
            self._add_row("Verfügbarkeitsstatus", cloudstatus.label(cst))
        mod = _time(info, "modified")
        if mod:
            self._add_row("Änderungsdatum", places.format_date(mod))
        created = _time(info, "created")
        if created:
            self._add_row("Erstellungsdatum", places.format_date(created))
        access = _time(info, "access")
        if access and not is_dir:
            self._add_row("Letzter Zugriff", places.format_date(access))
        if info.has_attribute("trash::deletion-date"):
            raw = info.get_attribute_as_string("trash::deletion-date") or ""
            dt = GLib.DateTime.new_from_iso8601(raw, GLib.TimeZone.new_local()) if raw else None
            if dt is not None:
                self._add_row("Gelöscht am", places.format_date(dt.to_unix()))
            self._add_row("Ursprünglicher Ort", _parent_text(gfile, info))
        else:
            self._add_row("Speicherort", _parent_text(gfile, info))
        if info.get_attribute_boolean("standard::is-symlink"):
            target = info.get_symlink_target()
            if target:
                self._add_row("Verknüpfungsziel", places.safe_text(target))

    def _fill_multi(self, infos, total):
        first_g, first_i = infos[0]
        self._set_header(stack_pixbuf(self._icon(first_g, first_i, ICON_SIZE), ICON_SIZE),
                         f"{total} Elemente ausgewählt", "")
        files = [i for _g, i in infos if i.get_file_type() != Gio.FileType.DIRECTORY]
        if files:
            self._add_row("Grösse", places.format_size(sum(i.get_size() for i in files)))
        mtimes = [_time(i, "modified") for _g, i in infos if _time(i, "modified")]
        if mtimes:
            lo, hi = min(mtimes), max(mtimes)
            text = places.format_date(lo)
            if places.format_date(hi) != text:
                text = f"{text} – {places.format_date(hi)}"
            self._add_row("Änderungsdatum", text)
        parents = {(_parent_text(g, i)) for g, i in infos}
        if len(parents) == 1:
            self._add_row("Speicherort", parents.pop())
        types = {places.type_description(i) for _g, i in infos}
        self.kind.set_text(f"Alle vom Typ {types.pop()}" if len(types) == 1 else "Mehrere Typen")
        self.kind.show()

    def _apply_extra(self, gen, extra):
        if gen != self._gen:
            return False
        pb = extra.get("preview")
        if pb is not None:
            self.preview.set_from_pixbuf(pb)
        if self._dims_row is not None:
            wh = extra.get("dims")
            if wh:
                self._dims_row.set_text(f"{wh[0]} x {wh[1]}")
            else:
                self._remove_row_of(self._dims_row)
                self._dims_row = None
        if self._count_row is not None and "count" in extra:
            n = extra["count"]
            if n is None:
                self._count_row.set_text("Nicht verfügbar")
            else:
                self._count_row.set_text("1 Element" if n == 1 else f"{n:,} Elemente".replace(",", "."))
        return False

    def _remove_row_of(self, value_label):
        top = self.grid.child_get_property(value_label, "top-attach")
        self.grid.remove_row(top)
        self._rows -= 1
