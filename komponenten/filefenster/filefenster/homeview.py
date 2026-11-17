"""Ansicht "Start" wie im Windows-11-Explorer (23H2).

Abschnitte (jeweils mit Chevron ein-/ausklappbar, Zustand in config
'home_collapsed'):
    Schnellzugriff     Kacheln der angehefteten und (Ordneroption „Häufig
                       verwendete Ordner anzeigen“) häufig besuchten Ordner
    Favoriten          nur Hinweistext (Favoriten gibt es unter Linux nicht)
    Zuletzt verwendet  Liste aus Gtk.RecentManager (existierende lokale Dateien),
                       abschaltbar (Ordneroption „Zuletzt verwendete Dateien anzeigen“)

_Section wird auch von thispcview benutzt.
"""
import os
import threading

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

from . import host as hostmod
from . import folderoptions, icons, places, theme
from .host import pinned_uris
from .menu import WinMenu
from .navpane import RowHighlighter, copy_as_path, copy_files_to_clipboard, location_menu, rgba

TILE_W = 240
TILE_H = 64
RECENT_MAX = 25

_FOLDER_SPECIALS = ("desktop", "downloads", "documents", "pictures", "music", "videos",
                    "onedrive", "user")


class _Section(Gtk.Box):
    """Abschnitt mit aufklappbarer Überschrift (Chevron + Titel) wie bei Windows."""

    def __init__(self, section_id, title, body, config=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.section_id = section_id
        self.config = config
        self.get_style_context().add_class("home-section")

        self.header = Gtk.Button()
        self.header.get_style_context().add_class("section-header")
        self.header.set_halign(Gtk.Align.START)
        self.header.set_can_focus(True)
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.chev = Gtk.Image()
        self.chev.get_style_context().add_class("section-chevron")
        hb.pack_start(self.chev, False, False, 0)
        self.label = Gtk.Label(label=title, xalign=0)
        self.label.get_style_context().add_class("section-title")
        hb.pack_start(self.label, False, False, 0)
        self.header.add(hb)
        self.header.connect("clicked", lambda *_: self.set_expanded(not self.expanded))
        self.pack_start(self.header, False, False, 0)

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(120)
        self.revealer.add(body)
        self.pack_start(self.revealer, False, False, 0)

        collapsed = set(config.get("home_collapsed", []) or []) if config else set()
        self.expanded = section_id not in collapsed
        self.revealer.set_reveal_child(self.expanded)
        self._update_chevron()

    def _update_chevron(self):
        name = "chevron_down" if self.expanded else "chevron_right"
        self.chev.set_from_pixbuf(icons.fluent_pixbuf(name, 12, theme.P["text"]))

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self.revealer.set_reveal_child(self.expanded)
        self._update_chevron()
        if self.config is not None:
            collapsed = set(self.config.get("home_collapsed", []) or [])
            if self.expanded:
                collapsed.discard(self.section_id)
            else:
                collapsed.add(self.section_id)
            self.config.set("home_collapsed", sorted(collapsed))


def make_flowbox():
    flow = Gtk.FlowBox()
    flow.get_style_context().add_class("tiles")
    flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
    flow.set_activate_on_single_click(False)
    flow.set_homogeneous(True)
    flow.set_min_children_per_line(1)
    flow.set_max_children_per_line(12)
    flow.set_column_spacing(4)
    flow.set_row_spacing(4)
    # START + passende max_children_per_line (fit_flowboxes): Kacheln behalten
    # ihre feste Breite und stehen linksbündig wie bei Windows.
    flow.set_halign(Gtk.Align.START)
    flow.set_valign(Gtk.Align.START)
    return flow


def fit_flowboxes(scrolled, flows, tile_w, spacing=4, chrome=40):
    """Spaltenzahl der FlowBoxen an die Breite anpassen (feste Kachelbreite).
    chrome = horizontale Ränder/Innenabstände um die FlowBox."""
    state = {"n": 0}

    def apply(n):
        for flow in flows:
            if flow.get_max_children_per_line() != n:
                flow.set_max_children_per_line(n)
        return False

    def on_alloc(_w, alloc):
        avail = alloc.width - chrome
        n = max(1, (avail + spacing) // (tile_w + spacing))
        if n != state["n"]:
            state["n"] = n
            GLib.idle_add(apply, n)

    scrolled.connect("size-allocate", on_alloc)


def tile_subtitle(uri):
    """Zweite Zeile einer Schnellzugriff-Kachel wie bei Windows:
    Spezialordner -> "Dieser PC" (bzw. das OneDrive, in dem sie liegen),
    sonst der Name des übergeordneten Ordners."""
    if places.is_virtual(uri):
        return ""
    path = places.local_path(uri)
    if path is None:
        return "Netzwerk"
    norm = os.path.normpath(path)
    onedrive = None
    for d in places.cloud_dirs():
        od = os.path.normpath(d["path"])
        if norm == od:
            return "Dieser PC"
        if norm.startswith(od + os.sep):
            onedrive = d["label"]
    kind = places.loc_icon_kind(uri)
    if kind not in ("folder", "drive", "drive-system"):
        return onedrive or "Dieser PC"
    if norm == "/":
        return "Dieser PC"
    parent = os.path.dirname(norm)
    return places.display_name(places.uri_for_path(parent))


def tile_pixbuf(uri, size=48):
    kind = places.loc_icon_kind(uri)
    if kind == "folder":
        return icons.folder_pixbuf(size)
    if kind in _FOLDER_SPECIALS:
        return icons.folder_pixbuf(size, kind)
    return icons.place_pixbuf(kind, size)


class _FolderTile(Gtk.FlowBoxChild):
    def __init__(self, uri, pinned=True):
        super().__init__()
        self.uri = uri
        self.pinned = pinned
        self.get_style_context().add_class("tile")
        self.set_size_request(TILE_W, TILE_H)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        img = Gtk.Image.new_from_pixbuf(tile_pixbuf(uri, 48))
        img.get_style_context().add_class("tile-icon")
        img.set_valign(Gtk.Align.CENTER)
        box.pack_start(img, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_valign(Gtk.Align.CENTER)
        name = places.display_name(uri) if (places.is_virtual(uri) or uri.startswith("file:")) \
            else (Gio.File.new_for_uri(uri).get_basename() or uri)
        self.name = name
        lab = Gtk.Label(label=name, xalign=0)
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        lab.get_style_context().add_class("tile-name")
        text.pack_start(lab, False, False, 0)
        line2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sub = Gtk.Label(label=tile_subtitle(uri), xalign=0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.get_style_context().add_class("tile-sub")
        line2.pack_start(sub, True, True, 0)
        if pinned:      # häufig verwendete Ordner ohne Nadel (wie Windows)
            pin = Gtk.Image.new_from_pixbuf(icons.fluent_pixbuf("pin", 16, theme.P["text3"]))
            pin.get_style_context().add_class("tile-pin")
            line2.pack_end(pin, False, False, 0)
        text.pack_start(line2, False, False, 0)
        box.pack_start(text, True, True, 0)
        self.add(box)
        path = places.local_path(uri) if not places.is_virtual(uri) else None
        self.set_tooltip_text(path or name)


class HomeView(Gtk.ScrolledWindow):
    """Start: Schnellzugriff, Favoriten, Zuletzt verwendet."""

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.config = host.config
        ctx = self.get_style_context()
        ctx.add_class("content-area")
        ctx.add_class("home-view")
        # waagrecht nur scrollen, wenn die Liste "Zuletzt verwendet" nicht passt
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self._destroyed = False
        self._tile_press = False
        self._busy = False          # Auswahl wird programmgesteuert geändert
        self._recent_gen = 0
        self._recent_timer = 0
        self._tiles_timer = 0
        self._frequent = []         # häufig verwendete Ordner (geprüft, dass es sie gibt)
        self._freq_gen = 0

        events = Gtk.EventBox()
        events.get_style_context().add_class("home-bg")
        events.connect("button-press-event", self._on_bg_press)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.get_style_context().add_class("home-body")
        events.add(body)
        self.add(events)

        # Schnellzugriff
        self.flow = make_flowbox()
        self.flow.connect("child-activated", self._on_tile_activated)
        self.flow.connect("selected-children-changed", self._on_tiles_selection)
        self.flow.connect("button-press-event", self._on_flow_press)
        self.sec_quick = _Section("quick", "Schnellzugriff", self.flow, self.config)
        body.pack_start(self.sec_quick, False, False, 0)
        fit_flowboxes(self, [self.flow], TILE_W)

        # Favoriten
        fav = Gtk.Label(label="Nachdem Sie einige Dateien als Favoriten markiert haben, "
                              "werden sie hier angezeigt.", xalign=0)
        fav.set_line_wrap(True)
        fav.get_style_context().add_class("home-hint")
        self.sec_fav = _Section("favorites", "Favoriten", fav, self.config)
        body.pack_start(self.sec_fav, False, False, 0)

        # Zuletzt verwendet
        self.recent_store = Gtk.ListStore(str, str, GdkPixbuf.Pixbuf, str, str, GObject.TYPE_INT64)
        self.recent = self._make_recent_tree()
        self.recent_empty = Gtk.Label(
            label="Nachdem Sie einige Dateien geöffnet haben, zeigen wir die neuesten hier an.",
            xalign=0)
        self.recent_empty.set_line_wrap(True)
        self.recent_empty.get_style_context().add_class("home-hint")
        self.recent_empty.set_no_show_all(True)
        rbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rbox.pack_start(self.recent, False, False, 0)
        rbox.pack_start(self.recent_empty, False, False, 0)
        self.sec_recent = _Section("recent", "Zuletzt verwendet", rbox, self.config)
        body.pack_start(self.sec_recent, False, False, 0)
        # Ordneroption „Zuletzt verwendete Dateien anzeigen“: Sichtbarkeit selbst steuern
        self.sec_recent.show_all()
        self.sec_recent.set_no_show_all(True)
        self.sec_recent.set_visible(bool(self.config.get("show_recent", True)))

        self.config.connect_changed(self._on_config_changed)
        self._rm = Gtk.RecentManager.get_default()
        self._rm_id = self._rm.connect("changed", lambda *_: self._schedule_recent())
        self.connect("destroy", self._on_destroy)

        self._apply_single_click()
        self._fill_tiles()
        self._load_recent()
        self._load_frequent()

    # -- Aufbau --------------------------------------------------------------
    def _make_recent_tree(self):
        tree = Gtk.TreeView(model=self.recent_store)
        tree.get_style_context().add_class("recent-list")
        tree.set_headers_visible(True)
        tree.set_enable_search(False)
        tree.set_activate_on_single_click(False)
        tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        tree.set_rubber_banding(False)
        text2 = rgba(theme.P["text2"])

        col = Gtk.TreeViewColumn(title="Name")
        col.set_resizable(True)
        col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        col.set_fixed_width(320)
        rp = Gtk.CellRendererPixbuf()
        rp.set_fixed_size(28, 30)
        rp.set_alignment(0.5, 0.5)
        col.pack_start(rp, False)
        col.add_attribute(rp, "pixbuf", 2)
        rt = Gtk.CellRendererText()
        rt.set_property("ellipsize", Pango.EllipsizeMode.END)
        rt.set_fixed_size(-1, 30)
        rt.set_padding(4, 0)
        col.pack_start(rt, True)
        col.add_attribute(rt, "text", 1)
        tree.append_column(col)

        for title, idx, width, expand in (("Zuletzt geöffnet", 3, 150, False),
                                          ("Speicherort", 4, 200, True)):
            c = Gtk.TreeViewColumn(title=title)
            c.set_resizable(True)
            c.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            c.set_fixed_width(width)
            c.set_expand(expand)
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.END)
            r.set_property("foreground-rgba", text2)
            r.set_padding(8, 0)
            c.pack_start(r, True)
            c.add_attribute(r, "text", idx)
            tree.append_column(c)

        self.recent_hl = RowHighlighter(tree, hover="hover", selected="selected",
                                        selected_hover="selected_hover", margin_x=0, radius=4)
        tree.connect("row-activated", self._on_recent_activated)
        tree.connect("button-press-event", self._on_recent_press)
        tree.connect("key-press-event", self._on_recent_key)
        tree.get_selection().connect("changed", self._on_recent_selection)
        return tree

    def _fill_tiles(self):
        keep = self._selected_tile_uri()
        for child in self.flow.get_children():
            self.flow.remove(child)
        pinned = pinned_uris(self.config)
        uris = list(pinned)
        if self.config.get("show_frequent", True):
            uris += [u for u in self._frequent if u not in pinned]
        for uri in uris:
            tile = _FolderTile(uri, pinned=uri in pinned)
            self.flow.add(tile)
            if uri == keep:
                self._busy = True
                self.flow.select_child(tile)
                self._busy = False
        self.flow.show_all()

    def _selected_tile_uri(self):
        sel = self.flow.get_selected_children()
        return sel[0].uri if sel else None

    # -- Zuletzt verwendet ---------------------------------------------------
    def _schedule_recent(self, delay=500):
        if self._destroyed:
            return
        if self._recent_timer:
            GLib.source_remove(self._recent_timer)

        def run():
            self._recent_timer = 0
            if not self._destroyed:
                self._load_recent()
            return False

        self._recent_timer = GLib.timeout_add(delay, run)

    def _load_frequent(self):
        """Häufig verwendete Ordner im Hintergrund prüfen (gibt es sie noch?)."""
        self._freq_gen += 1
        gen = self._freq_gen
        cands = []
        if self.config.get("show_frequent", True):
            cands = folderoptions.frequent_folders(self.config, exclude=set(pinned_uris(self.config)))

        def work():
            out = []
            for uri in cands:
                try:
                    if os.path.isdir(GLib.filename_from_uri(uri)[0]):
                        out.append(uri)
                except GLib.Error:
                    pass
            GLib.idle_add(self._set_frequent, gen, out)

        threading.Thread(target=work, daemon=True).start()

    def _set_frequent(self, gen, uris):
        if gen != self._freq_gen or self._destroyed:
            return False
        if uris != self._frequent:
            self._frequent = uris
            self._fill_tiles()
            self.host.state_changed()
        return False

    def _load_recent(self):
        """Liste im Hintergrund prüfen (Existenz-Test kann auf FUSE/OneDrive hängen)."""
        self._recent_gen += 1
        gen = self._recent_gen
        if not self.config.get("show_recent", True):
            # Ordneroption aus: nichts anzeigen (und nicht als Element zählen)
            if len(self.recent_store):
                self.recent_store.clear()
                self.host.state_changed()
            return
        raw = []
        for info in self._rm.get_items():
            uri = info.get_uri()
            if not uri.startswith("file://"):
                continue
            ts = max(info.get_modified() or 0, info.get_visited() or 0, info.get_added() or 0)
            raw.append((uri, ts, info.get_mime_type() or ""))
        raw.sort(key=lambda r: r[1], reverse=True)

        def work():
            out = []
            for uri, ts, mime in raw:
                try:
                    path = GLib.filename_from_uri(uri)[0]
                except GLib.Error:
                    continue
                if os.path.isfile(path):
                    out.append((uri, path, ts, mime))
                    if len(out) >= RECENT_MAX:
                        break
            GLib.idle_add(self._fill_recent, gen, out)

        threading.Thread(target=work, daemon=True).start()

    def _fill_recent(self, gen, rows):
        if gen != self._recent_gen or self._destroyed:
            return False
        sel_uris = {self.recent_store[p][0] for p in self.recent.get_selection().get_selected_rows()[1]}
        self._busy = True
        self.recent_store.clear()
        home = os.path.normpath(places.home_dir())
        show_ext = self.config.get("show_extensions", True)
        for uri, path, ts, mime in rows:
            name = os.path.basename(path)
            if not show_ext:
                stem, ext = os.path.splitext(name)
                if stem and ext:
                    name = stem
            folder = os.path.dirname(path)
            if folder == home or folder.startswith(home + os.sep):
                folder = "~" + folder[len(home):]
            gicon = Gio.content_type_get_icon(mime) if mime else None
            pb = icons.gicon_pixbuf(gicon, 16)
            it = self.recent_store.append([uri, places.safe_text(name), pb, places.format_date(ts),
                                           places.safe_text(folder), int(ts)])
            if uri in sel_uris:
                self.recent.get_selection().select_iter(it)
        self._busy = False
        has = len(rows) > 0
        self.recent.set_visible(has)
        self.recent_empty.set_visible(not has)
        self.host.state_changed()
        return False

    def _recent_selected_files(self):
        _m, paths = self.recent.get_selection().get_selected_rows()
        return [Gio.File.new_for_uri(self.recent_store[p][0]) for p in paths]

    def _launch(self, gfiles):
        fn = getattr(self.host, "launch_file", None)
        for g in gfiles:
            if callable(fn):
                fn(g)
            else:
                hostmod.launch_file(g, getattr(self.host, "window", None))

    def _on_recent_activated(self, tree, path, _col):
        self._launch([Gio.File.new_for_uri(self.recent_store[path][0])])

    def _on_recent_press(self, tree, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS or ev.window != tree.get_bin_window():
            return False
        hit = tree.get_path_at_pos(int(ev.x), int(ev.y))
        sel = tree.get_selection()
        if hit is None:
            if ev.button in (1, 3):
                sel.unselect_all()
            return ev.button == 3
        if ev.button == 3:
            path = hit[0]
            if not sel.path_is_selected(path):
                tree.set_cursor(path, None, False)
            self._recent_menu().popup_at_pointer(ev, attach_to=tree)
            return True
        return False

    def _on_recent_key(self, tree, ev):
        if ev.keyval == Gdk.KEY_Menu or (ev.keyval == Gdk.KEY_F10 and
                                         ev.state & Gdk.ModifierType.SHIFT_MASK):
            if self._recent_selected_files():
                self._recent_menu().popup_at_pointer(None, attach_to=tree)
            return True
        return False

    def _recent_menu(self):
        files = self._recent_selected_files()
        m = WinMenu()
        m.icon_row([("copy", "Kopieren", lambda: copy_files_to_clipboard(files, host=self.host),
                     bool(files))])
        m.item("Öffnen", lambda: self._launch(files), icon="open", bold=True)
        parent = files[0].get_parent() if len(files) == 1 else None
        m.item("Dateispeicherort öffnen",
               lambda: self.host.navigate(parent.get_uri()) if parent else None,
               icon="folder_open", sensitive=parent is not None)
        m.item('Aus "Zuletzt verwendet" entfernen', lambda: self._forget(files), icon="dismiss")
        m.item("Als Pfad kopieren", lambda: copy_as_path(files), icon="copy_select")
        m.item("Eigenschaften", lambda: self.host.show_properties(files), icon="wrench")
        return m

    def _forget(self, files):
        for g in files:
            try:
                self._rm.remove_item(g.get_uri())
            except GLib.Error:
                pass
        self._schedule_recent(50)

    def _on_recent_selection(self, sel):
        if self._busy:
            return
        if sel.count_selected_rows() > 0 and self.flow.get_selected_children():
            self._busy = True
            self.flow.unselect_all()
            self._busy = False
        self.host.state_changed()

    # -- Kacheln -------------------------------------------------------------
    def _on_tile_activated(self, _flow, child):
        folderoptions.open_folder(self.host, child.uri)

    def _on_tiles_selection(self, flow):
        if self._busy:
            return
        if flow.get_selected_children() and self.recent.get_selection().count_selected_rows():
            self._busy = True
            self.recent.get_selection().unselect_all()
            self._busy = False
        self.host.state_changed()

    def _on_flow_press(self, flow, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        child = flow.get_child_at_pos(int(ev.x), int(ev.y))
        if child is None:
            if ev.button in (1, 3):
                flow.unselect_all()
            return ev.button == 3
        # Hintergrund-Klick (EventBox) soll diese Auswahl nicht wieder aufheben
        self._tile_press = True
        GLib.idle_add(self._reset_tile_press)
        if ev.button == 1:
            # wie Windows schon beim Drücken auswählen (FlowBox wählt erst beim Loslassen)
            if not child.is_selected():
                flow.select_child(child)
            return False
        if ev.button == 2:
            self.host.open_in_new_tab(child.uri)
            return True
        if ev.button == 3:
            flow.select_child(child)
            self.tile_menu(child).popup_at_pointer(ev, attach_to=flow)
            return True
        return False

    def tile_menu(self, child):
        return location_menu(self.host, child.uri, open_default=True, deletable=False)

    def _reset_tile_press(self):
        self._tile_press = False
        return False

    def _on_bg_press(self, _w, ev):
        if self._tile_press:
            return False
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button in (1, 3):
            self.clear_selection()
        return False

    def clear_selection(self):
        self.flow.unselect_all()
        self.recent.get_selection().unselect_all()

    # -- API -----------------------------------------------------------------
    def refresh(self):
        self._fill_tiles()
        self._load_recent()
        self._load_frequent()

    def get_selected(self):
        sel = self.flow.get_selected_children()
        if sel:
            uri = sel[0].uri
            return [] if places.is_virtual(uri) else [Gio.File.new_for_uri(uri)]
        return self._recent_selected_files()

    def item_count(self):
        return len(self.flow.get_children()) + len(self.recent_store)

    # -- Signale -------------------------------------------------------------
    def _on_config_changed(self, key, value):
        if self._destroyed:
            return
        if key == "pinned":
            if self._tiles_timer:
                GLib.source_remove(self._tiles_timer)

            def run():
                self._tiles_timer = 0
                if not self._destroyed:
                    self._fill_tiles()
                    self.host.state_changed()
                return False

            self._tiles_timer = GLib.timeout_add(50, run)
            self._load_frequent()
        elif key == "show_extensions":
            self._schedule_recent(50)
        elif key == "show_recent":
            self.sec_recent.set_visible(bool(value))
            self._schedule_recent(50)
        elif key == "show_frequent" or (key == "folder_visits" and not value):
            self._load_frequent()       # Verlauf gelöscht oder Option umgeschaltet
        elif key == "single_click":
            self._apply_single_click()

    def _apply_single_click(self):
        """Ordneroption „Öffnen durch einfachen Klick“ auch auf der Startseite."""
        single = bool(self.config.get("single_click", False))
        self.flow.set_activate_on_single_click(single)
        self.recent.set_activate_on_single_click(single)

    def _on_destroy(self, *_):
        self._destroyed = True
        try:
            self._rm.disconnect(self._rm_id)
        except Exception:
            pass
        listeners = getattr(self.config, "_listeners", None)
        if listeners is not None and self._on_config_changed in listeners:
            listeners.remove(self._on_config_changed)
