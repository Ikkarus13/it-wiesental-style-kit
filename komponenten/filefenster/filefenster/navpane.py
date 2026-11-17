"""Navigationsbereich (links) wie im Windows-11-Explorer (22H2/23H2).

Einträge: Start, Katalog, OneDrive-Ordner, Trenner, angeheftete Ordner (mit
Pin), Trenner, Dieser PC (Laufwerke), Netzwerk.

Technik: Gtk.TreeView mit TreeStore, ohne Kopfzeile. Statt der GTK-Expander
gibt es eigene Chevrons in einer Zelle links, die immer sichtbar sind (Windows
zeigt sie nur beim Überfahren – auf Wunsch des Nutzers geändert). Unterordner werden erst
beim Aufklappen asynchron geladen (Platzhalterkind); Ordner ohne Unterordner
verlieren ihren Chevron (Prüfung im Hintergrund). Hover und Auswahl werden als
abgerundete Flächen selbst gezeichnet (RowHighlighter), die GTK-Zeilen-
hintergründe sind per CSS transparent.

Ausserdem gemeinsame Hilfen, die auch Start (homeview) und Dieser PC
(thispcview) benutzen: natural_key, norm_loc, list_subdirs, location_menu,
copy_as_path, copy_files_to_clipboard, trash_files, eject_mount,
RowHighlighter, rgba.
"""
import os
import re

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

from . import dnd, icons, places, theme
from .host import pinned_uris
from .menu import WinMenu

ROW_H = 32          # Zeilenhöhe
INDENT = 16         # Einrückung je Ebene
CHEV_W = 20         # Breite der Chevron-Zelle
ICON_W = 26         # Breite der Icon-Zelle (16px Icon + Abstand)
PIN_W = 30          # Breite der Pin-Zelle rechts

# Spalten des TreeStore
C_KIND, C_LOC, C_LABEL, C_ICON, C_PIN, C_LOADED, C_KEY, C_DATA = range(8)

# Arten, die Unterordner haben können
_EXPANDABLE = ("onedrive", "pinned", "thispc", "drive", "folder")


# ---------------------------------------------------------------------------
# Allgemeine Hilfen (auch für homeview/thispcview)
# ---------------------------------------------------------------------------

def rgba(color):
    c = Gdk.RGBA()
    c.parse(color)
    return c


def natural_key(name):
    """Natürliche Sortierung wie Windows (Ordner 2 vor Ordner 10)."""
    parts = re.split(r"(\d+)", name.casefold())
    return [int(p) if i % 2 else p for i, p in enumerate(parts)]


def norm_loc(loc):
    """Ort vergleichbar machen (file-URIs als normalisierter Pfad)."""
    if not loc:
        return ""
    if places.is_virtual(loc):
        return loc
    if loc.startswith("file:"):
        path = Gio.File.new_for_uri(loc).get_path()
        if path:
            return "file:" + os.path.normpath(path)
    return loc.rstrip("/") or loc


def _is_cancelled(exc):
    return isinstance(exc, GLib.Error) and exc.matches(Gio.io_error_quark(),
                                                       Gio.IOErrorEnum.CANCELLED)


_DIR_ATTRS = ("standard::name,standard::display-name,standard::type,"
              "standard::is-hidden,standard::is-backup")


def list_subdirs(gfile, show_hidden, cancellable, callback, first_only=False):
    """Unterordner asynchron auflisten (blockiert nie, wichtig für FUSE/OneDrive).

    callback(dirs, ok): dirs = Liste von (Anzeigename, URI);
    ok = True (fertig), False (Fehler), None (abgebrochen).
    first_only: nach dem ersten gefundenen Ordner aufhören (Chevron-Prüfung).
    """
    found = []

    def finish(enum, ok):
        if enum is not None:
            try:
                enum.close_async(GLib.PRIORITY_LOW, None, None)
            except Exception:
                pass
        callback(found, ok)

    def on_next(enum, res, *_):
        try:
            infos = enum.next_files_finish(res)
        except GLib.Error as exc:
            finish(enum, None if _is_cancelled(exc) else False)
            return
        if not infos:
            finish(enum, True)
            return
        for info in infos:
            if info.get_file_type() != Gio.FileType.DIRECTORY:
                continue
            if not show_hidden and (info.get_attribute_boolean("standard::is-hidden") or info.get_attribute_boolean("standard::is-backup")):
                continue
            found.append((info.get_display_name(), enum.get_child(info).get_uri()))
            if first_only:
                finish(enum, True)
                return
        enum.next_files_async(64, GLib.PRIORITY_LOW, cancellable, on_next)

    def on_enum(src, res, *_):
        try:
            enum = src.enumerate_children_finish(res)
        except GLib.Error as exc:
            callback(found, None if _is_cancelled(exc) else False)
            return
        enum.next_files_async(64, GLib.PRIORITY_LOW, cancellable, on_next)

    gfile.enumerate_children_async(_DIR_ATTRS, Gio.FileQueryInfoFlags.NONE,
                                   GLib.PRIORITY_LOW, cancellable, on_enum)


def copy_as_path(gfiles):
    """Wie Windows "Als Pfad kopieren": Pfade in Anführungszeichen, je Zeile einer."""
    lines = []
    for g in gfiles:
        p = g.get_path() or g.get_uri()
        try:
            p.encode("utf-8")
        except UnicodeEncodeError:          # ungültiges UTF-8 im Namen
            p = g.get_parse_name()
        lines.append(f'"{p}"')
    clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    clip.set_text("\n".join(lines), -1)
    clip.store()


# Zwischenablage für Dateien (Nemo/Nautilus-kompatibel). Gtk.Clipboard.set_with_data
# ist in PyGObject nicht verfügbar, daher über einen unsichtbaren Selektions-Besitzer.
_CLIP_TARGETS = ["x-special/gnome-copied-files", "text/uri-list", "UTF8_STRING",
                 "text/plain;charset=utf-8", "text/plain", "STRING", "TEXT"]
_clip_owner = None
_clip_data = None


def _on_clip_get(_widget, sel, info, _time):
    if not _clip_data:
        return
    op, uris, paths = _clip_data
    if info == 0:
        payload = "\n".join([op] + uris).encode()
        sel.set(Gdk.Atom.intern(_CLIP_TARGETS[0], False), 8, payload)
    elif info == 1:
        sel.set_uris(uris)
    else:
        sel.set_text("\n".join(paths), -1)


def copy_files_to_clipboard(gfiles, cut=False, host=None):
    """Dateien in die Zwischenablage (Kopieren/Ausschneiden).
    Hat der Host eine eigene Methode copy_files(gfiles, cut=False), wird sie benutzt."""
    fn = getattr(host, "copy_files", None) if host is not None else None
    if callable(fn):
        fn(gfiles, cut=cut) if cut else fn(gfiles)
        return
    global _clip_owner, _clip_data
    if _clip_owner is None:
        _clip_owner = Gtk.Invisible()
        _clip_owner.connect("selection-get", _on_clip_get)
        for i, target in enumerate(_CLIP_TARGETS):
            Gtk.selection_add_target(_clip_owner, Gdk.SELECTION_CLIPBOARD,
                                     Gdk.Atom.intern(target, False), i)
    _clip_data = ("cut" if cut else "copy", [g.get_uri() for g in gfiles],
                  [g.get_path() or g.get_uri() for g in gfiles])
    Gtk.selection_owner_set(_clip_owner, Gdk.SELECTION_CLIPBOARD, Gtk.get_current_event_time())


def _error_dialog(host, title, text):
    win = getattr(host, "window", None)
    dlg = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.ERROR,
                            buttons=Gtk.ButtonsType.OK, text=title)
    dlg.format_secondary_text(text)
    dlg.connect("response", lambda d, *_: d.destroy())
    dlg.show()


def trash_files(host, gfiles, on_done=None):
    """In den Papierkorb verschieben (Windows "Löschen").
    Hat der Host delete_files(gfiles), wird diese benutzt (Rückgängig usw.)."""
    fn = getattr(host, "delete_files", None)
    if callable(fn):
        fn(gfiles)
        if on_done:
            GLib.timeout_add(600, lambda: (on_done(), False)[1])
        return

    def done(src, res, *_):
        try:
            src.trash_finish(res)
        except GLib.Error as exc:
            _error_dialog(host, "Das Element konnte nicht gelöscht werden.", exc.message)
        if on_done:
            on_done()

    for g in gfiles:
        g.trash_async(GLib.PRIORITY_DEFAULT, None, done)


def eject_mount(host, mount, on_done=None):
    op = Gtk.MountOperation.new(getattr(host, "window", None))

    def done(m, res, *_):
        try:
            if m.can_eject():
                m.eject_with_operation_finish(res)
            else:
                m.unmount_with_operation_finish(res)
        except GLib.Error as exc:
            if not _is_cancelled(exc):
                _error_dialog(host, "Das Gerät konnte nicht ausgeworfen werden.", exc.message)
        if on_done:
            on_done()

    if mount.can_eject():
        mount.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, done)
    else:
        mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, op, None, done)


def _is_protected(loc):
    """Orte, die man im Kontextmenü nicht löschen kann (Spezialordner, Laufwerke)."""
    path = places.local_path(loc) if not places.is_virtual(loc) else None
    if not path:
        return True
    norm = os.path.normpath(path)
    if norm == "/" or norm == os.path.normpath(places.home_dir()):
        return True
    if places.loc_icon_kind(loc) != "folder":
        return True
    if os.path.ismount(norm):
        return True
    return False


def location_menu(host, loc, *, expanded=None, on_toggle=None, open_default=False,
                  mount=None, on_changed=None, deletable=None, pin_option=True,
                  icon_row=True):
    """Kontextmenü für einen Ordner/Ort (Navigationsbereich, Start-Kacheln, Laufwerke).

    expanded/on_toggle: Eintrag Erweitern/Reduzieren (nur Navigationsbereich).
    open_default: fetten Eintrag "Öffnen" oben (Kacheln).
    on_changed: nach Löschen/Auswerfen aufgerufen (z. B. zum Neuladen).
    icon_row: Icon-Reihe (Kopieren/Löschen) oben – nicht bei Laufwerken.
    """
    m = WinMenu()
    virtual = places.is_virtual(loc)
    gfile = None if virtual else Gio.File.new_for_uri(loc)
    path = gfile.get_path() if gfile is not None else None
    is_network_root = loc.rstrip("/") in ("network:", "network:/")

    if icon_row and gfile is not None and not is_network_root:
        row = [("copy", "Kopieren",
                lambda: copy_files_to_clipboard([gfile], host=host), True)]
        if deletable is None:
            deletable = not _is_protected(loc)
        if deletable:
            row.append(("delete", "Löschen",
                        lambda: trash_files(host, [gfile], on_changed), True))
        m.icon_row(row)

    if open_default:
        m.item("Öffnen", lambda: host.navigate(loc), icon="folder_open", bold=True)
    if on_toggle is not None:
        m.item("Reduzieren" if expanded else "Erweitern", on_toggle)
    m.item("In neuem Tab öffnen", lambda: host.open_in_new_tab(loc), icon="tab_add")
    m.item("In neuem Fenster öffnen", lambda: host.open_in_new_window(loc), icon="window_new")
    if pin_option and gfile is not None and not is_network_root:
        if host.is_pinned(loc):
            m.item("Vom Schnellzugriff lösen", lambda: host.unpin(loc), icon="pin_off")
        else:
            m.item("An Schnellzugriff anheften", lambda: host.pin(loc), icon="pin")
    from . import network
    if places.is_remote(loc) and not is_network_root:
        if mount is not None or network.is_saved(loc):     # Netzlaufwerk: „Trennen“
            m.item("Trennen", lambda: network.disconnect(host, loc, mount, on_changed),
                   icon="arrow_eject")
    elif mount is not None and (mount.can_eject() or mount.can_unmount()):
        m.item("Auswerfen", lambda: eject_mount(host, mount, on_changed), icon="arrow_eject")
    if loc == places.THISPC or is_network_root:
        win = getattr(host, "win", host)
        m.item("Netzlaufwerk verbinden…", lambda: network.connect_dialog(win, host), icon="link")
    if path:
        m.item("Als Pfad kopieren", lambda: copy_as_path([gfile]), icon="copy_select")
    if gfile is not None and not is_network_root:
        m.item("Eigenschaften", lambda: host.show_properties([gfile]), icon="wrench")
    if path:
        m.separator()
        m.item("In Terminal öffnen", lambda: host.open_terminal(path), icon="window_console")
    return m


class RowHighlighter:
    """Zeichnet Hover und Auswahl einer Gtk.TreeView als abgerundete Flächen.

    Die TreeView muss per CSS transparente Zeilen haben; gezeichnet wird vor
    dem Standard-Handler in das bin_window (also unter den Zellen).
    """

    def __init__(self, tree, hover="hover", selected="selected",
                 selected_hover="selected_hover", margin_x=4, radius=4,
                 background=None, skip=None, extra_draw=None):
        self.tree = tree
        self.keys = (hover, selected, selected_hover)
        self.margin_x = margin_x
        self.radius = radius
        self.background = background
        self.skip = skip            # skip(model, iter) -> True: Zeile nie hervorheben
        self.extra_draw = extra_draw  # extra_draw(cr): zusätzlich unter den Zellen zeichnen
        self.hover_path = None
        self.extra_path = None      # z. B. Zeile mit offenem Kontextmenü
        tree.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                        | Gdk.EventMask.ENTER_NOTIFY_MASK)
        tree.connect("draw", self._on_draw)
        tree.connect("motion-notify-event", self._on_motion)
        tree.connect("leave-notify-event", self._on_leave)
        tree.connect("scroll-event", self._on_scroll)
        tree.get_selection().connect("changed", lambda *_: tree.queue_draw())

    def set_hover(self, path):
        if path is not None and self.skip is not None:
            model = self.tree.get_model()
            try:
                if self.skip(model, model.get_iter(path)):
                    path = None
            except ValueError:
                path = None
        old = self.hover_path
        if (old is None) != (path is None) or (old is not None and old.compare(path) != 0):
            self.hover_path = path
            self.tree.queue_draw()

    def _on_motion(self, tree, ev):
        if ev.window != tree.get_bin_window():
            self.set_hover(None)
            return False
        hit = tree.get_path_at_pos(int(ev.x), int(ev.y))
        self.set_hover(hit[0] if hit else None)
        return False

    def _on_leave(self, tree, ev):
        if ev.detail != Gdk.NotifyType.INFERIOR:
            self.set_hover(None)
        return False

    def _on_scroll(self, tree, ev):
        # nach dem Scrollen Hover neu bestimmen
        def update():
            seat = tree.get_display().get_default_seat()
            win = tree.get_bin_window()
            if win is None or seat is None:
                return False
            _w, x, y, _m = win.get_device_position(seat.get_pointer())
            hit = tree.get_path_at_pos(x, y) if x >= 0 and y >= 0 else None
            self.set_hover(hit[0] if hit else None)
            return False
        GLib.idle_add(update)
        return False

    def _row_rect(self, path):
        col = self.tree.get_column(0)
        rect = self.tree.get_background_area(path, col)
        _x, y = self.tree.convert_bin_window_to_widget_coords(0, rect.y)
        x = self.margin_x
        w = self.tree.get_allocated_width() - 2 * self.margin_x
        return x, y, w, rect.height

    def _rounded(self, cr, x, y, w, h, r):
        r = min(r, h / 2, w / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.14159)
        cr.arc(x + r, y + r, r, 3.14159, 4.71239)
        cr.close_path()

    def _on_draw(self, tree, cr):
        if self.background and Gtk.cairo_should_draw_window(cr, tree.get_window()):
            Gdk.cairo_set_source_rgba(cr, rgba(theme.P[self.background]))
            cr.paint()
        binwin = tree.get_bin_window()
        if binwin is None or not Gtk.cairo_should_draw_window(cr, binwin):
            return False
        if self.background:
            Gdk.cairo_set_source_rgba(cr, rgba(theme.P[self.background]))
            cr.paint()
        hover_key, sel_key, sel_hover_key = self.keys
        _model, selected = tree.get_selection().get_selected_rows()
        sel_set = {p.to_string() for p in selected}
        hover = self.hover_path.to_string() if self.hover_path is not None else None
        extra = self.extra_path.to_string() if self.extra_path is not None else None
        todo = []
        if hover is not None and hover not in sel_set:
            todo.append((self.hover_path, hover_key))
        if extra is not None and extra not in sel_set and extra != hover:
            todo.append((self.extra_path, hover_key))
        for p in selected:
            todo.append((p, sel_hover_key if p.to_string() == hover else sel_key))
        for path, key in todo:
            x, y, w, h = self._row_rect(path)
            if h <= 0:
                continue
            Gdk.cairo_set_source_rgba(cr, rgba(theme.P[key]))
            self._rounded(cr, x, y, w, h, self.radius)
            cr.fill()
        if self.extra_draw is not None:
            self.extra_draw(cr)
        # Fokusrahmen nur nach Tastaturbedienung (wie Windows)
        if tree.has_visible_focus():
            cur = tree.get_cursor()[0]
            if cur is not None:
                x, y, w, h = self._row_rect(cur)
                if h > 0:
                    c = rgba(theme.P["text"])
                    c.alpha = 0.85
                    Gdk.cairo_set_source_rgba(cr, c)
                    cr.set_line_width(1.5)
                    self._rounded(cr, x + 0.75, y + 0.75, w - 1.5, h - 1.5, self.radius)
                    cr.stroke()
        return False


# ---------------------------------------------------------------------------
# Navigationsbereich
# ---------------------------------------------------------------------------

class NavPane(Gtk.ScrolledWindow):
    """Navigationsbereich links. host = Fenster (wirkt auf den aktuellen Tab)."""

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.config = host.config
        self.get_style_context().add_class("nav-pane")
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.NONE)

        self._pane_hover = False
        self._sel_loc = None
        self._gen = 0
        self._cancel = Gio.Cancellable()
        self._loading = set()
        self._pending_expand = set()
        self._check_queue = []
        self._checks_running = 0
        self._refresh_id = 0
        self._kbd_nav = False
        self._kbd_timer = 0
        self._destroyed = False

        self.store = Gtk.TreeStore(str, str, str, GdkPixbuf.Pixbuf, bool, bool, str,
                                   GObject.TYPE_PYOBJECT)
        tree = self.tree = Gtk.TreeView(model=self.store)
        tree.get_style_context().add_class("nav-tree")
        tree.set_headers_visible(False)
        tree.set_show_expanders(False)
        tree.set_level_indentation(INDENT)
        tree.set_enable_search(False)
        tree.set_enable_tree_lines(False)
        tree.set_activate_on_single_click(False)
        tree.set_margin_top(4)
        tree.set_row_separator_func(lambda m, it, *_: m[it][C_KIND] == "sep")
        tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        col = Gtk.TreeViewColumn()
        col.set_expand(True)
        col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)

        self.r_chev = Gtk.CellRendererPixbuf()
        self.r_chev.set_fixed_size(CHEV_W, ROW_H)
        self.r_chev.set_alignment(0.5, 0.5)
        col.pack_start(self.r_chev, False)
        col.set_cell_data_func(self.r_chev, self._chev_data)

        r_icon = Gtk.CellRendererPixbuf()
        r_icon.set_fixed_size(ICON_W, ROW_H)
        r_icon.set_alignment(0.5, 0.5)
        col.pack_start(r_icon, False)
        col.add_attribute(r_icon, "pixbuf", C_ICON)

        r_text = Gtk.CellRendererText()
        r_text.set_property("ellipsize", Pango.EllipsizeMode.END)
        r_text.set_padding(6, 0)
        r_text.set_fixed_size(-1, ROW_H)
        r_text.set_alignment(0, 0.5)
        col.pack_start(r_text, True)
        col.add_attribute(r_text, "text", C_LABEL)

        self.r_pin = Gtk.CellRendererPixbuf()
        self.r_pin.set_fixed_size(PIN_W, ROW_H)
        self.r_pin.set_alignment(0.5, 0.5)
        col.pack_end(self.r_pin, False)
        col.set_cell_data_func(self.r_pin, self._pin_data)
        tree.append_column(col)
        self.column = col

        self.highlighter = RowHighlighter(
            tree, hover="nav_hover", selected="nav_selected", selected_hover="nav_selected",
            margin_x=4, radius=4, background="pane_bg",
            skip=lambda m, it: m[it][C_KIND] in ("sep", "dummy"),
            extra_draw=self._draw_separators)
        self._chev_hover = None     # Pfad, dessen Chevron unter der Maus ist
        dnd.DropTarget(tree, self._drop_hit, highlight=self._drop_highlight,
                       autoscroll=self._drop_scroll)

        tree.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        tree.connect("motion-notify-event", self._on_motion)
        tree.connect("button-press-event", self._on_button_press)
        tree.connect("key-press-event", self._on_key_press)
        tree.connect("cursor-changed", self._on_cursor_changed)
        tree.connect("test-expand-row", self._on_test_expand)
        tree.connect("row-expanded", self._on_row_expanded)
        tree.connect("row-collapsed", self._on_row_collapsed)
        self._monitors = {}         # key -> Gio.FileMonitor aufgeklappter Ordner
        self._reload_timers = {}    # key -> GLib-Quelle (entprellt)
        self._reloading = set()
        tree.connect("enter-notify-event", self._on_enter)
        tree.connect("leave-notify-event", self._on_leave)
        self.add(tree)

        self._load_pixbufs()

        # Pins/versteckte Elemente/Laufwerke beobachten
        self.config.connect_changed(self._on_config_changed)
        self._vm = Gio.VolumeMonitor.get()
        self._vm_ids = [self._vm.connect(sig, self._on_mounts_changed)
                        for sig in ("mount-added", "mount-removed",
                                    "volume-added", "volume-removed")]
        self.connect("destroy", self._on_destroy)

        self._build()

    # -- Aufbau --------------------------------------------------------------
    def _load_pixbufs(self):
        P = theme.P
        self._pb_right = icons.fluent_pixbuf("chevron_right", 12, P["text2"])
        self._pb_down = icons.fluent_pixbuf("chevron_down", 12, P["text2"])
        self._pb_right_hi = icons.fluent_pixbuf("chevron_right", 12, P["text"])
        self._pb_down_hi = icons.fluent_pixbuf("chevron_down", 12, P["text"])
        self._pb_pin = icons.fluent_pixbuf("pin", 16, P["text3"])

    def _draw_separators(self, cr):
        """Trennlinien selbst zeichnen (mit Einzug links/rechts wie bei Windows)."""
        width = self.tree.get_allocated_width()
        color = rgba(theme.P["border"])
        it = self.store.get_iter_first()
        while it is not None:
            if self.store[it][C_KIND] == "sep":
                rect = self.tree.get_background_area(self.store.get_path(it), self.column)
                _x, y = self.tree.convert_bin_window_to_widget_coords(0, rect.y)
                ymid = y + rect.height // 2
                Gdk.cairo_set_source_rgba(cr, color)
                cr.rectangle(8, ymid, width - 16, 1)
                cr.fill()
            it = self.store.iter_next(it)

    def _row(self, parent, kind, loc, label, pixbuf, key, pinned=False,
             expandable=False, data=None):
        it = self.store.append(parent, [kind, loc, label, pixbuf, pinned,
                                        not expandable, key, data])
        if expandable:
            self.store.append(it, ["dummy", "", "", None, False, True, key + ">~", None])
        return it

    def _sep(self, key):
        self.store.append(None, ["sep", "", "", None, False, True, key, None])

    @staticmethod
    def _label_for(uri):
        if places.is_virtual(uri) or uri.startswith("file:"):
            return places.display_name(uri)
        # Nicht-lokale Orte ohne blockierende Abfrage benennen
        base = Gio.File.new_for_uri(uri).get_basename() or uri
        return places.safe_text(GLib.uri_unescape_string(base, None) or base)

    def _build(self):
        P = icons.place_pixbuf
        self._row(None, "home", places.HOME, "Start", P("home", 16), "home")
        # Katalog (Windows 11 23H2) direkt unter „Start“
        self._row(None, "gallery", places.GALLERY, "Katalog", P("gallery", 16), "gallery")
        tops = []

        def cloud(dirs):
            for d in dirs:
                tops.append(self._row(None, "onedrive", d["uri"], d["label"], P(d["key"], 16),
                                      "od:" + d["uri"], expandable=True))
        cloud(places.onedrive_dirs())
        self._sep("sep1")
        for uri in pinned_uris(self.config):
            if places.is_virtual(uri):
                kind = places.loc_icon_kind(uri)
                self._row(None, "pinned-virtual", uri, places.display_name(uri),
                          P(kind, 16), "pin:" + uri, pinned=True)
                continue
            kind = places.loc_icon_kind(uri)
            it = self._row(None, "pinned", uri, self._label_for(uri), P(kind, 16),
                           "pin:" + uri, pinned=True, expandable=True)
            tops.append(it)
        self._sep("sep2")
        # Teams-/SharePoint-Organisationsordner wie unter Windows direkt über „Dieser PC“
        cloud(places.org_dirs())
        self._row(None, "thispc", places.THISPC, "Dieser PC", P("thispc", 16), "thispc",
                  expandable=True)
        # USB-Sticks usw. wie unter Windows zusätzlich als eigene Einträge
        for d in places.drives():
            if d["kind"] == "usb":
                mounted = not d["uri"].startswith("volume:")
                it = self._row(None, "drive", d["uri"], d["label"], P("usb", 16),
                               "usb:" + d["uri"], expandable=mounted, data=d.get("mount"))
                if mounted:
                    tops.append(it)
        self._row(None, "network", "network:///", "Netzwerk", P("network", 16), "net")
        for it in tops:
            self._queue_check(it)
        # gemerkte aufgeklappte Einträge wiederherstellen
        it = self.store.get_iter_first()
        while it is not None:
            if self.store[it][C_KEY] in self._pending_expand:
                self._pending_expand.discard(self.store[it][C_KEY])
                self._expand(self.store.get_path(it))
            it = self.store.iter_next(it)

    def refresh(self):
        """Komplett neu aufbauen (Pins, Laufwerke, Unterordner); Aufklappzustand bleibt."""
        if self._refresh_id:
            GLib.source_remove(self._refresh_id)
            self._refresh_id = 0
        self._reloading.clear()             # alte Ladevorgänge sind ab jetzt ungültig
        expanded = set()

        def collect(model, path, it):
            if self.tree.row_expanded(path):
                expanded.add(model[it][C_KEY])
            return False

        self.store.foreach(collect)
        vadj = self.get_vadjustment()
        scroll = vadj.get_value()

        self._cancel.cancel()
        self._cancel = Gio.Cancellable()
        self._gen += 1
        self._loading.clear()
        self._check_queue.clear()
        self._checks_running = 0
        self._pending_expand = expanded
        self.highlighter.hover_path = None
        self.highlighter.extra_path = None
        self._stop_monitors()
        self._reloading.add("*")
        self.store.clear()
        self._reloading.discard("*")
        self._build()
        self._reselect()
        GLib.idle_add(lambda: (vadj.set_value(min(scroll, vadj.get_upper() - vadj.get_page_size())), False)[1])

    # -- Live-Aktualisierung aufgeklappter Ordner ----------------------------
    def _on_row_expanded(self, _tree, it, _path):
        row = self.store[it]
        key, loc = row[C_KEY], row[C_LOC]
        if key not in self._monitors and loc.startswith("file:"):
            try:
                mon = Gio.File.new_for_uri(loc).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error:
                mon = None
            if mon is not None:
                mon.set_rate_limit(500)
                mon.connect("changed", self._on_dir_changed, key)
                self._monitors[key] = mon
        self._reselect()

    def _on_row_collapsed(self, _tree, it, _path):
        if "*" in self._reloading:
            return
        key = self.store[it][C_KEY]
        if key not in self._reloading:
            # Überwachung dieses Ordners und aller Unterordner beenden
            for k in [k for k in self._monitors if k == key or k.startswith(key + ">")]:
                self._monitors.pop(k).cancel()
        self._reselect()

    def _stop_monitors(self):
        for mon in self._monitors.values():
            mon.cancel()
        self._monitors.clear()
        for src in self._reload_timers.values():
            GLib.source_remove(src)
        self._reload_timers.clear()

    def _on_dir_changed(self, _mon, _file, _other, event, key):
        if event not in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED,
                         Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.MOVED_OUT,
                         Gio.FileMonitorEvent.RENAMED):
            return
        if key in self._reload_timers:
            GLib.source_remove(self._reload_timers[key])
        self._reload_timers[key] = GLib.timeout_add(300, self._reload_key, key)

    def _find_key(self, key):
        found = []

        def visit(model, path, it):
            if model[it][C_KEY] == key:
                found.append(it.copy())
                return True
            return False

        self.store.foreach(visit)
        return found[0] if found else None

    def _reload_key(self, key):
        """Unterordner eines aufgeklappten Ordners neu laden (Aufklappzustand bleibt)."""
        self._reload_timers.pop(key, None)
        if self._destroyed:
            return False
        it = self._find_key(key)
        if it is None or not self.tree.row_expanded(self.store.get_path(it)):
            return False

        def collect(model, path, child):
            k = model[child][C_KEY]
            if k.startswith(key + ">") and self.tree.row_expanded(path):
                self._pending_expand.add(k)
            return False

        self.store.foreach(collect)
        self._reloading.add(key)
        self.store[it][C_LOADED] = False
        self._load_children(it, expand=True)
        return False

    def _schedule_refresh(self, delay=250):
        if self._destroyed:
            return
        if self._refresh_id:
            GLib.source_remove(self._refresh_id)

        def run():
            self._refresh_id = 0
            if not self._destroyed:
                self.refresh()
            return False

        self._refresh_id = GLib.timeout_add(delay, run)

    def _on_config_changed(self, key, _value):
        if self._destroyed:
            return
        if key in ("pinned", "show_hidden"):
            self._schedule_refresh(50)

    def _on_mounts_changed(self, *_):
        self._schedule_refresh(400)

    def _on_destroy(self, *_):
        self._destroyed = True
        for attr in ("_kbd_timer", "_refresh_id"):
            if getattr(self, attr, 0):
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, 0)
        self._cancel.cancel()
        self._stop_monitors()
        for hid in self._vm_ids:
            self._vm.disconnect(hid)
        self._vm_ids = []
        listeners = getattr(self.config, "_listeners", None)
        if listeners is not None and self._on_config_changed in listeners:
            listeners.remove(self._on_config_changed)

    # -- Laden der Unterordner ----------------------------------------------
    def _show_hidden(self):
        return bool(self.config.get("show_hidden", False))

    def _queue_check(self, it):
        """Im Hintergrund prüfen, ob der Ordner Unterordner hat (sonst Chevron weg)."""
        ref = Gtk.TreeRowReference.new(self.store, self.store.get_path(it))
        self._check_queue.append(ref)
        self._pump_checks()

    def _pump_checks(self):
        while self._checks_running < 3 and self._check_queue:
            ref = self._check_queue.pop(0)
            if not ref.valid():
                continue
            it = self.store.get_iter(ref.get_path())
            row = self.store[it]
            if row[C_LOADED] or row[C_KEY] in self._loading:
                continue
            self._checks_running += 1
            gen = self._gen
            list_subdirs(Gio.File.new_for_uri(row[C_LOC]), self._show_hidden(), self._cancel,
                         lambda dirs, ok, ref=ref, gen=gen: self._on_checked(ref, gen, dirs, ok),
                         first_only=True)

    def _on_checked(self, ref, gen, dirs, ok):
        if gen != self._gen or self._destroyed:
            return
        self._checks_running -= 1
        if ok is not None and not dirs and ref.valid():
            it = self.store.get_iter(ref.get_path())
            if not self.store[it][C_LOADED] and self.store[it][C_KEY] not in self._loading:
                self._clear_children(it)
                self.store[it][C_LOADED] = True
        self._pump_checks()

    def _clear_children(self, it):
        while True:
            child = self.store.iter_children(it)
            if child is None:
                break
            self.store.remove(child)

    def _load_children(self, it, expand):
        row = self.store[it]
        kind, loc, key = row[C_KIND], row[C_LOC], row[C_KEY]
        if key in self._loading:
            if expand:
                self._pending_expand.add(key)
            return
        if kind == "thispc":
            drives = []
            for d in places.drives():
                drives.append((d["label"], d["uri"], d["kind"], d.get("mount")))
            self._fill_children(it, drives, expand, drives_mode=True)
            return
        self._loading.add(key)
        if expand:
            self._pending_expand.add(key)
        ref = Gtk.TreeRowReference.new(self.store, self.store.get_path(it))
        gen = self._gen
        list_subdirs(Gio.File.new_for_uri(loc), self._show_hidden(), self._cancel,
                     lambda dirs, ok: self._on_loaded(ref, gen, key, dirs, ok))

    def _on_loaded(self, ref, gen, key, dirs, ok):
        if gen != self._gen or self._destroyed:
            return
        self._loading.discard(key)
        if ok is None or not ref.valid():
            self._reloading.discard(key)    # sonst bleibt der Schlüssel für immer „im Neuladen“
            return
        it = self.store.get_iter(ref.get_path())
        expand = key in self._pending_expand
        self._pending_expand.discard(key)
        entries = []
        for name, uri in dirs:
            label = places.display_name(uri) if uri.startswith("file:") else places.safe_text(name)
            kind = places.loc_icon_kind(uri)
            entries.append((label, uri, kind, None))
        self._fill_children(it, entries, expand)

    def _fill_children(self, it, entries, expand, drives_mode=False):
        self._clear_children(it)
        parent_key = self.store[it][C_KEY]
        if not drives_mode:
            entries.sort(key=lambda e: natural_key(e[0]))
        new = []
        for label, uri, kind, mount in entries:
            if drives_mode:
                ckey = "drv:" + uri
                child = self._row(it, "drive", uri, label, icons.place_pixbuf(kind, 16),
                                  ckey, expandable=not uri.startswith("volume:"), data=mount)
            else:
                ckey = parent_key + ">" + label
                child = self._row(it, "folder", uri, label, icons.place_pixbuf(kind, 16),
                                  ckey, expandable=True)
            new.append(child)
        self.store[it][C_LOADED] = True
        path = self.store.get_path(it)
        if expand and new:
            self.tree.expand_row(path, False)
        self._reloading.discard(parent_key)
        for child in new:
            ckey = self.store[child][C_KEY]
            if ckey in self._pending_expand:
                self._pending_expand.discard(ckey)
                self._expand(self.store.get_path(child))
            else:
                self._queue_check(child)
        self._reselect()

    def _on_test_expand(self, tree, it, path):
        if not self.store[it][C_LOADED]:
            self._load_children(it, expand=True)
            return True     # erst nach dem Laden aufklappen
        return False

    # -- Drag & Drop: Ordner als Ablageziel, Verweilen klappt auf -------
    def _drop_hit(self, x, y):
        r = self.tree.get_dest_row_at_pos(x, y)
        if not r:
            return None
        path = r[0]
        row = self.store[path]
        loc = row[C_LOC]
        if row[C_KIND] in ("sep", "dummy") or not dnd.droppable(loc):
            return None
        hover = None
        it = self.store.get_iter(path)
        if self._expandable(it) and not self.tree.row_expanded(path):
            hover = (lambda p=path.copy(): self._expand(p))
        return dnd.Hit(Gio.File.new_for_uri(loc), row[C_LABEL], key=path.to_string(),
                       on_hover=hover, data=path.copy())

    def _drop_highlight(self, hit):
        self.highlighter.extra_path = hit.data if hit is not None else None
        self.tree.queue_draw()

    def _drop_scroll(self, x, y):
        h = self.tree.get_allocated_height()
        e = dnd.DropTarget.EDGE
        return self.get_vadjustment(), (-1 if y < e else 1 if y > h - e else 0)

    def _expandable(self, it):
        row = self.store[it]
        if row[C_KIND] not in _EXPANDABLE:
            return False
        return (not row[C_LOADED]) or self.store.iter_has_child(it)

    def _expand(self, path):
        it = self.store.get_iter(path)
        if not self.store[it][C_LOADED]:
            self._load_children(it, expand=True)
        elif self.store.iter_has_child(it):
            self.tree.expand_row(path, False)

    def _toggle(self, path):
        if self.tree.row_expanded(path):
            self.tree.collapse_row(path)
        else:
            self._expand(path)

    def expand_location(self, loc):
        """Eintrag der obersten Ebene (z. B. "thispc:") aufklappen – für Tests/Fenster."""
        target = norm_loc(loc)
        it = self.store.get_iter_first()
        while it is not None:
            if self.store[it][C_KIND] not in ("sep", "dummy") and \
                    norm_loc(self.store[it][C_LOC]) == target:
                self._expand(self.store.get_path(it))
                return True
            it = self.store.iter_next(it)
        return False

    # -- Zellen --------------------------------------------------------------
    def _chev_data(self, _col, cell, model, it, *_):
        pb = None
        # Pfeile immer zeigen (Nutzerwunsch; Windows blendet sie erst beim Überfahren ein)
        if model[it][C_KIND] in _EXPANDABLE and model.iter_has_child(it):
            path = model.get_path(it)
            hot = self._chev_hover is not None and self._chev_hover.compare(path) == 0
            if self.tree.row_expanded(path):
                pb = self._pb_down_hi if hot else self._pb_down
            else:
                pb = self._pb_right_hi if hot else self._pb_right
        cell.set_property("pixbuf", pb)

    def _pin_data(self, _col, cell, model, it, *_):
        cell.set_property("pixbuf", self._pb_pin if model[it][C_PIN] else None)

    # -- Auswahl -------------------------------------------------------------
    def select_location(self, loc, scroll=True):
        """Passenden Eintrag markieren (oberste Ebene, dann aufgeklappte Kinder),
        sonst Auswahl leeren. Löst KEIN navigate aus."""
        self._sel_loc = loc
        target = norm_loc(loc) if loc else None
        store = self.store
        # Passt der angeklickte Eintrag schon, bleibt er markiert – ein Laufwerk
        # steht z. B. unter „Dieser PC“ und zusätzlich oben (wie Windows)
        _m, cur = self.tree.get_selection().get_selected()
        if target and cur is not None and norm_loc(store[cur][C_LOC]) == target:
            return
        found = None
        if target:
            tops = []
            it = store.get_iter_first()
            while it is not None:
                tops.append(it)
                it = store.iter_next(it)
            for it in tops:
                if store[it][C_KIND] not in ("sep", "dummy") and norm_loc(store[it][C_LOC]) == target:
                    found = it
                    break
            queue = [it for it in tops if self.tree.row_expanded(store.get_path(it))]
            while found is None and queue:
                parent = queue.pop(0)
                child = store.iter_children(parent)
                while child is not None:
                    if store[child][C_KIND] != "dummy":
                        if norm_loc(store[child][C_LOC]) == target:
                            found = child
                            break
                        if self.tree.row_expanded(store.get_path(child)):
                            queue.append(child)
                    child = store.iter_next(child)
        sel = self.tree.get_selection()
        if found is None:
            sel.unselect_all()
            return
        path = store.get_path(found)
        sel.select_path(path)
        if scroll:
            rng = self.tree.get_visible_range()
            if rng is None or path.compare(rng[0]) < 0 or path.compare(rng[1]) > 0:
                self.tree.scroll_to_cell(path, None, False, 0, 0)

    def _reselect(self):
        if self._sel_loc is not None:
            self.select_location(self._sel_loc, scroll=False)

    # -- Maus & Tastatur -----------------------------------------------------
    def _on_enter(self, _tree, _ev):
        if not self._pane_hover:
            self._pane_hover = True
            self.tree.queue_draw()
        return False

    def _on_leave(self, _tree, ev):
        if ev.detail == Gdk.NotifyType.INFERIOR:
            return False
        if self._pane_hover or self._chev_hover is not None:
            self._pane_hover = False
            self._chev_hover = None
            self.tree.queue_draw()
        return False

    def _on_motion(self, tree, ev):
        if not self._pane_hover:
            self._pane_hover = True
            tree.queue_draw()
        hot = None
        if ev.window == tree.get_bin_window():
            hit = tree.get_path_at_pos(int(ev.x), int(ev.y))
            if hit is not None and self._on_chevron(hit[0], ev.x):
                hot = hit[0]
        old = self._chev_hover
        if (old is None) != (hot is None) or (old is not None and old.compare(hot) != 0):
            self._chev_hover = hot
            tree.queue_draw()
        return False

    def _on_chevron(self, path, x):
        area = self.tree.get_cell_area(path, self.column)
        return area.x - 4 <= x < area.x + CHEV_W + 2

    def _navigate_row(self, it):
        loc = self.store[it][C_LOC]
        if not loc:
            return
        self._sel_loc = loc
        self.tree.get_selection().select_iter(it)
        self.host.navigate(loc)

    def _on_button_press(self, tree, ev):
        if ev.window != tree.get_bin_window():
            return False
        hit = tree.get_path_at_pos(int(ev.x), int(ev.y))
        if hit is None:
            return ev.button in (1, 2)
        path = hit[0]
        it = self.store.get_iter(path)
        if self.store[it][C_KIND] in ("sep", "dummy"):
            return True
        loc = self.store[it][C_LOC]
        if ev.button == 1:
            tree.grab_focus()
            on_chev = self._on_chevron(path, ev.x) and self._expandable(it)
            if ev.type == Gdk.EventType.BUTTON_PRESS:
                if on_chev:
                    self._toggle(path)
                    return True
                tree.set_cursor(path, None, False)
                self._navigate_row(it)
                return True
            if ev.type == Gdk.EventType._2BUTTON_PRESS:
                if not on_chev and self._expandable(it):
                    self._toggle(path)
                return True
            return True
        if ev.button == 2 and ev.type == Gdk.EventType.BUTTON_PRESS:
            self.host.open_in_new_tab(loc)
            return True
        if ev.button == 3 and ev.type == Gdk.EventType.BUTTON_PRESS:
            self._popup_menu(path, ev)
            return True
        return False

    def _popup_menu(self, path, event=None):
        menu = self.build_menu(path)
        if menu is None:
            return
        self.highlighter.extra_path = path
        self.tree.queue_draw()

        def clear(*_):
            self.highlighter.extra_path = None
            self.tree.queue_draw()

        menu.menu.connect("deactivate", clear)
        if event is not None:
            menu.popup_at_pointer(event, attach_to=self.tree)
        else:
            rect = self.tree.get_cell_area(path, self.column)
            x, y = self.tree.convert_bin_window_to_widget_coords(rect.x, rect.y)
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = x + 24, y, 1, rect.height
            menu.popup_at_rect(self.tree.get_window(), r, attach_to=self.tree)

    def build_menu(self, path):
        """Kontextmenü (WinMenu) für die Zeile path oder None."""
        it = self.store.get_iter(path)
        row = self.store[it]
        kind, loc = row[C_KIND], row[C_LOC]
        if kind in ("sep", "dummy"):
            return None
        expandable = self._expandable(it)
        toggle = (lambda p=path.copy(): self._toggle(p)) if expandable else None
        expanded = self.tree.row_expanded(path)
        if kind in ("home", "gallery", "network", "thispc", "pinned-virtual"):
            m = WinMenu()
            if toggle:
                m.item("Reduzieren" if expanded else "Erweitern", toggle)
            m.item("In neuem Tab öffnen", lambda: self.host.open_in_new_tab(loc), icon="tab_add")
            m.item("In neuem Fenster öffnen", lambda: self.host.open_in_new_window(loc),
                   icon="window_new")
            if kind == "pinned-virtual":
                m.item("Vom Schnellzugriff lösen", lambda: self.host.unpin(loc), icon="pin_off")
            return m
        deletable = None
        if kind in ("onedrive", "drive"):
            deletable = False
        return location_menu(self.host, loc, expanded=expanded, on_toggle=toggle,
                             mount=row[C_DATA] if kind == "drive" else None,
                             on_changed=lambda: self._schedule_refresh(300),
                             deletable=deletable, icon_row=kind != "drive")

    def _on_key_press(self, tree, ev):
        key = ev.keyval
        cur = tree.get_cursor()[0]
        if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End,
                   Gdk.KEY_Page_Up, Gdk.KEY_Page_Down, Gdk.KEY_KP_Up, Gdk.KEY_KP_Down):
            self._kbd_nav = True
            return False
        if cur is None:
            return False
        it = self.store.get_iter(cur)
        if key in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            if tree.row_expanded(cur):
                child = self.store.iter_children(it)
                if child is not None:
                    self._kbd_nav = True
                    tree.set_cursor(self.store.get_path(child), None, False)
            elif self._expandable(it):
                self._expand(cur)
            return True
        if key in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            if tree.row_expanded(cur):
                tree.collapse_row(cur)
            elif cur.get_depth() > 1:
                parent = cur.copy()
                parent.up()
                self._kbd_nav = True
                tree.set_cursor(parent, None, False)
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._navigate_row(it)
            return True
        if key == Gdk.KEY_Menu or (key == Gdk.KEY_F10 and ev.state & Gdk.ModifierType.SHIFT_MASK):
            self._popup_menu(cur)
            return True
        return False

    def _on_cursor_changed(self, tree):
        if not self._kbd_nav:
            return
        self._kbd_nav = False
        # Windows navigiert beim Blättern mit den Pfeiltasten nach kurzer Pause
        if self._kbd_timer:
            GLib.source_remove(self._kbd_timer)

        def go():
            self._kbd_timer = 0
            if self._destroyed:
                return False
            cur = tree.get_cursor()[0]
            if cur is not None:
                it = self.store.get_iter(cur)
                if self.store[it][C_KIND] not in ("sep", "dummy"):
                    self._navigate_row(it)
            return False

        self._kbd_timer = GLib.timeout_add(450, go)
