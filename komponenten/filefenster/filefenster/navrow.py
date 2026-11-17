"""Adresszeile unter den Tabs wie im Windows-11-Explorer (22H2+):

    [←][→][↑][⟳]  [Ort-Icon › Brotkrume › Brotkrume › ……… ⌄]  [Suchfeld 🔍]

- Brotkrumen: Klick → navigieren, Chevron → Menü der Unterordner,
  erster Chevron → Start/Dieser PC/Spezialordner/Laufwerke,
  zu lange Pfade → vorne ausblenden, «-Knopf mit Menü der ausgeblendeten.
- Klick auf freie Fläche (oder Strg+L/Alt+D/F4 über focus_address) →
  Bearbeitungsmodus mit Vervollständigung der Unterordner.
- Suchfeld: nach 400 ms Tipp-Pause host.start_search(text).

Der Host (das Fenster) muss bieten: go_back, go_forward, go_up, refresh,
navigate, start_search, stop_search, location, config. Optional:
open_in_new_tab, open_in_new_window, focus_view (Fokus zurück an die Liste).
"""
import os
import re

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import dnd, icons, places, theme
from .menu import WinMenu

HISTORY_MAX = 15
SEARCH_DELAY_MS = 400
MENU_MAX_ITEMS = 1000

# Ortsarten, die als Ordner mit Glyphe gezeigt werden (wie Windows)
_FOLDER_KINDS = {"desktop", "downloads", "documents", "pictures", "music", "videos", "user"}

_SUBFOLDER_ATTRS = ",".join([
    "standard::name", "standard::display-name", "standard::type",
    "standard::is-hidden", "standard::is-backup", "standard::target-uri",
])


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def natural_key(text):
    """Sortierschlüssel wie Windows (StrCmpLogicalW): Zahlen numerisch."""
    # ungerade Stellen = Treffer von \d+ (nicht isdigit(): „²“ oder „①“ liessen int() scheitern)
    return [int(t) if i % 2 else t.casefold() for i, t in enumerate(re.split(r"(\d+)", text))]


def loc_pixbuf(loc, size=16):
    """Icon eines Ortes (Adressleiste, Menüs)."""
    try:
        kind = places.loc_icon_kind(loc)
    except Exception:
        kind = "folder"
    if kind in _FOLDER_KINDS:
        return icons.folder_pixbuf(size, kind)
    return icons.place_pixbuf(kind, size)


def loc_to_text(loc):
    """Text im Bearbeitungsmodus: lokaler Pfad, "Start", "Dieser PC" oder URI."""
    if not loc:
        return ""
    if loc == places.HOME:
        return "Start"
    if loc == places.THISPC:
        return "Dieser PC"
    if loc == places.GALLERY:
        return "Katalog"
    try:
        gfile = Gio.File.new_for_uri(loc)
        if gfile.get_uri_scheme() == "file":
            return gfile.get_path() or loc
        if gfile.get_uri_scheme() == "smb":      # wie Windows: \\\\server\\freigabe
            _s, host, parts = places.remote_parts(loc)
            if host:
                return "\\\\" + "\\".join([host] + parts)
        # Parse-Name zeigt Sonderzeichen unmaskiert (smb://server/Freigabe Ä)
        return gfile.get_parse_name() or loc
    except Exception:
        return loc


def _safe_crumbs(loc):
    if not loc:
        return []
    try:
        crumbs = places.breadcrumbs(loc)
    except Exception:
        crumbs = []
    if not crumbs:
        try:
            crumbs = [(places.display_name(loc), loc)]
        except Exception:
            crumbs = [(loc, loc)]
    return crumbs


def _same_loc(a, b):
    if a is None or b is None:
        return False
    if a == b:
        return True
    if places.is_virtual(a) or places.is_virtual(b):
        return False
    try:
        return Gio.File.new_for_uri(a).equal(Gio.File.new_for_uri(b))
    except Exception:
        return False


def list_subfolders_async(loc, show_hidden, callback, include_hidden_flag=False):
    """Unterordner von loc asynchron auflisten.
    callback(liste) mit dicts {name, uri, hidden} (natürlich sortiert) oder None bei Fehler."""
    try:
        gfile = Gio.File.new_for_uri(loc)
    except Exception:
        callback(None)
        return
    results = []

    def on_next(enum, res):
        try:
            infos = enum.next_files_finish(res)
        except GLib.Error:
            infos = None
        if infos:
            for info in infos:
                ftype = info.get_file_type()
                if ftype not in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE,
                                 Gio.FileType.SHORTCUT):
                    continue
                hidden = info.get_attribute_boolean("standard::is-hidden") or info.get_attribute_boolean("standard::is-backup")
                if hidden and not show_hidden and not include_hidden_flag:
                    continue
                uri = None
                if ftype != Gio.FileType.DIRECTORY:
                    uri = info.get_attribute_string("standard::target-uri")
                if not uri:
                    uri = enum.get_child(info).get_uri()
                results.append({"name": info.get_display_name() or info.get_name(),
                                "uri": uri, "hidden": hidden})
            if len(results) < 20000:
                enum.next_files_async(256, GLib.PRIORITY_DEFAULT, None, on_next)
                return
        enum.close_async(GLib.PRIORITY_DEFAULT, None, None)
        results.sort(key=lambda d: natural_key(d["name"]))
        callback(results)

    def on_enum(f, res):
        try:
            enum = f.enumerate_children_finish(res)
        except GLib.Error:
            callback(None)
            return
        enum.next_files_async(256, GLib.PRIORITY_DEFAULT, None, on_next)

    gfile.enumerate_children_async(_SUBFOLDER_ATTRS, Gio.FileQueryInfoFlags.NONE,
                                   GLib.PRIORITY_DEFAULT, None, on_enum)


def _set_rect(x, y, w, h):
    r = Gdk.Rectangle()
    r.x, r.y, r.width, r.height = int(x), int(y), max(1, int(w)), max(1, int(h))
    return r


# ---------------------------------------------------------------------------
# Brotkrumen-Leiste mit Überlauf
# ---------------------------------------------------------------------------
class _CrumbStrip(Gtk.Fixed):
    """Ordnet [Wurzel-Chevron | «] [Krume][›] [Krume][›] … an.
    Passt nicht alles hinein, werden vorne Krumen ausgeblendet und statt des
    Wurzel-Chevrons der «-Knopf gezeigt (wie GtkPathBar: set_child_visible).
    (Gtk.Fixed als Basis, weil es kein eigenes CSS-Gadget allokieren muss.)"""

    def __init__(self):
        super().__init__()
        self.root_chev = None
        self.overflow = None
        self.pairs = []
        self.first_visible = 0

    def set_items(self, root_chev, overflow, pairs):
        for child in self.get_children():
            child.destroy()
        self.root_chev, self.overflow, self.pairs = root_chev, overflow, pairs
        self.first_visible = 0
        widgets = [root_chev, overflow] + [w for pair in pairs for w in pair]
        for w in widgets:
            w.set_child_visible(w is not overflow)
            self.put(w, 0, 0)
            w.show_all()
        self.queue_resize()

    @staticmethod
    def _nat(w):
        return w.get_preferred_width()[1]

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_get_preferred_width(self):
        if not self.pairs:
            return (0, 0)
        nat = self._nat(self.root_chev) + sum(self._nat(c) + self._nat(ch) for c, ch in self.pairs)
        last, last_ch = self.pairs[-1]
        mn = self._nat(self.overflow) + min(last.get_preferred_width()[0], 40) + self._nat(last_ch)
        return (mn, max(mn, nat))

    def do_get_preferred_height(self):
        h = 0
        for w in self.get_children():
            h = max(h, w.get_preferred_height()[1])
        return (h, h)

    def do_get_preferred_width_for_height(self, _height):
        return self.do_get_preferred_width()

    def do_get_preferred_height_for_width(self, _width):
        return self.do_get_preferred_height()

    def do_size_allocate(self, alloc):
        self.set_allocation(alloc)
        if not self.pairs:
            return
        avail = alloc.width
        widths = [(self._nat(c), self._nat(ch)) for c, ch in self.pairs]
        n = len(widths)
        root_w = self._nat(self.root_chev)
        if root_w + sum(a + b for a, b in widths) <= avail:
            first = 0
        else:
            used = self._nat(self.overflow) + sum(widths[-1])
            first = n - 1
            for i in range(n - 2, -1, -1):
                if used + sum(widths[i]) > avail:
                    break
                used += sum(widths[i])
                first = i
            if first == 0:  # passt doch alles (nur mit « gerechnet)
                first = 0
        self.first_visible = first

        x = alloc.x
        right = alloc.x + avail

        def place(w, width):
            nonlocal x
            w.set_child_visible(True)
            w.size_allocate(_set_rect(x, alloc.y, width, alloc.height))
            x += width

        if first == 0:
            self.overflow.set_child_visible(False)
            place(self.root_chev, root_w)
        else:
            self.root_chev.set_child_visible(False)
            place(self.overflow, self._nat(self.overflow))
        for i, (crumb, chev) in enumerate(self.pairs):
            if i < first:
                crumb.set_child_visible(False)
                chev.set_child_visible(False)
                continue
            cw, chw = widths[i]
            if i == n - 1:
                room = right - x - chw
                cw = max(crumb.get_preferred_width()[0], min(cw, room))
            place(crumb, cw)
            place(chev, chw)


# ---------------------------------------------------------------------------
# NavRow
# ---------------------------------------------------------------------------
class NavRow(Gtk.Box):
    def __init__(self, host):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.host = host
        self._loc = None
        self._crumbs = []
        self._menu = None           # offenes Menü am Leben halten
        self._menu_gen = 0
        self._dialog_open = False
        self._search_timer = 0
        self._search_block = False
        # Fenster zu, während eine Eingabe im Suchfeld noch wartet: Suche nicht mehr starten
        self.connect("destroy", lambda *_: self._cancel_search_timer())
        self._edit_block = False
        self._compl_dir = None
        self._compl_gen = 0

        self.get_style_context().add_class("navrow")
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(8)
        self.set_margin_end(8)

        # -- Navigationsknöpfe -------------------------------------------
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.btn_back = self._nav_button("arrow_left", "Zurück (Alt+Nach-links)",
                                         lambda: self.host.go_back())
        self.btn_forward = self._nav_button("arrow_right", "Vorwärts (Alt+Nach-rechts)",
                                            lambda: self.host.go_forward())
        self.btn_up = self._nav_button("arrow_up", "Nach oben (Alt+Nach-oben)",
                                       lambda: self.host.go_up())
        self.btn_refresh = self._nav_button("arrow_clockwise", "Aktualisieren (F5)",
                                            lambda: self.host.refresh())
        for b in (self.btn_back, self.btn_forward, self.btn_up, self.btn_refresh):
            nav.pack_start(b, False, False, 0)
        self.pack_start(nav, False, False, 0)

        # -- Adressleiste -------------------------------------------------
        self._build_address()
        self.pack_start(self.addr_events, True, True, 0)
        self.addr_events.set_margin_start(8)
        self.addr_events.set_margin_end(8)

        # -- Suchfeld -----------------------------------------------------
        self._build_search()
        self.pack_start(self.search, False, False, 0)

        self.set_nav_state(False, False, False)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _nav_button(self, icon, tip, callback):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_focus_on_click(False)
        b.get_style_context().add_class("nav-btn")
        b.add(icons.fluent(icon, 16))
        b.set_tooltip_text(tip)
        b.connect("clicked", lambda *_: callback())
        return b

    def _build_address(self):
        self.addr_events = Gtk.EventBox()
        self.addr_events.set_visible_window(False)
        self.addr_events.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                    | Gdk.EventMask.ENTER_NOTIFY_MASK
                                    | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.addr_events.connect("button-press-event", self._on_addr_press)
        self.addr_events.connect("enter-notify-event", self._on_addr_enter)
        self.addr_events.connect("leave-notify-event", self._on_addr_leave)

        self.addr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.addr.get_style_context().add_class("address-bar")
        self.addr_events.add(self.addr)

        self.loc_icon = Gtk.Image()
        self.loc_icon.get_style_context().add_class("addr-icon")
        self.loc_icon.set_valign(Gtk.Align.CENTER)
        self.addr.pack_start(self.loc_icon, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_homogeneous(False)
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.strip = _CrumbStrip()
        self.stack.add_named(self.strip, "crumbs")

        self.entry = Gtk.Entry()
        self.entry.get_style_context().add_class("address-entry")
        self.entry.set_width_chars(4)
        self.entry.set_has_frame(False)
        self.stack.add_named(self.entry, "edit")
        self.addr.pack_start(self.stack, True, True, 0)

        self.hist_btn = Gtk.Button()
        self.hist_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.hist_btn.set_focus_on_click(False)
        self.hist_btn.set_can_focus(False)
        self.hist_btn.get_style_context().add_class("addr-dropdown")
        self._hist_img = icons.fluent("chevron_down", 12)
        self.hist_btn.add(self._hist_img)
        self.hist_btn.set_tooltip_text("Vorherige Speicherorte")
        self.hist_btn.connect("clicked", self._on_history_clicked)
        self.addr.pack_end(self.hist_btn, False, False, 0)

        # Vervollständigung (Unterordner, während getippt wird)
        self._compl_store = Gtk.ListStore(str, bool)   # Text, versteckt
        before = set(Gtk.Window.list_toplevels())
        self._completion = Gtk.EntryCompletion()
        popups = set(Gtk.Window.list_toplevels()) - before
        for win in popups:   # Popup-Fenster für unser CSS markieren
            ctx = win.get_style_context()
            ctx.add_class("filefenster")
            ctx.add_class("address-completion")
        self._completion.set_model(self._compl_store)
        self._completion.set_text_column(0)
        self._completion.set_minimum_key_length(1)
        self._completion.set_popup_completion(True)
        self._completion.set_inline_completion(False)
        self._completion.set_popup_single_match(True)
        self._completion.set_match_func(self._completion_match, None)
        for cell in self._completion.get_cells():
            cell.set_property("ypad", 5)
            cell.set_property("xpad", 6)
        self.entry.set_completion(self._completion)

        # nach set_completion verbinden: Esc schliesst erst das Popup
        self.entry.connect("key-press-event", self._on_entry_key)
        self.entry.connect("activate", self._on_entry_activate)
        self.entry.connect("changed", self._on_entry_changed)
        self.entry.connect("focus-out-event", self._on_entry_focus_out)

    def _build_search(self):
        self.search = Gtk.Entry()
        self.search.get_style_context().add_class("search-entry")
        self.search.set_size_request(240, -1)
        self.search.set_width_chars(8)
        self.search.set_placeholder_text("Durchsuchen")
        self._pb_search = icons.fluent_pixbuf("search", 16, theme.P["text2"])
        self._pb_clear = icons.fluent_pixbuf("dismiss", 12, theme.P["text2"])
        self._search_icon_is_clear = None
        self._update_search_icon()
        self.search.connect("changed", self._on_search_changed)
        self.search.connect("icon-press", self._on_search_icon)
        self.search.connect("key-press-event", self._on_search_key)
        self.search.connect("activate", self._on_search_activate)

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------
    def set_location(self, loc):
        self._loc = loc
        if self.is_editing():
            self._leave_edit(refocus=False)
        self.loc_icon.set_from_pixbuf(loc_pixbuf(loc, 16) if loc else None)
        self._rebuild_crumbs()
        try:
            name = places.display_name(loc) if loc else ""
        except Exception:
            name = ""
        self.search.set_placeholder_text(f"{name} durchsuchen" if name else "Durchsuchen")
        up = None
        try:
            up = places.parent(loc) if loc else None
        except Exception:
            pass
        if up:
            try:
                self.btn_up.set_tooltip_text(
                    f"Nach oben zu „{places.display_name(up)}“ (Alt+Nach-oben)")
            except Exception:
                self.btn_up.set_tooltip_text("Nach oben (Alt+Nach-oben)")
        else:
            self.btn_up.set_tooltip_text("Nach oben (Alt+Nach-oben)")
        if name:
            self.btn_refresh.set_tooltip_text(f"„{name}“ aktualisieren (F5)")

    def set_nav_state(self, can_back, can_forward, can_up):
        self.btn_back.set_sensitive(bool(can_back))
        self.btn_forward.set_sensitive(bool(can_forward))
        self.btn_up.set_sensitive(bool(can_up))

    def focus_address(self):
        self._enter_edit()

    def focus_search(self):
        self.search.grab_focus()
        self.search.select_region(0, -1)

    def clear_search(self):
        self.set_search_text("")

    def set_search_text(self, text):
        self._cancel_search_timer()
        self._search_block = True
        try:
            self.search.set_text(text or "")
        finally:
            self._search_block = False
        self._update_search_icon()

    def is_editing(self):
        return self.stack.get_visible_child_name() == "edit"

    # ------------------------------------------------------------------
    # Brotkrumen
    # ------------------------------------------------------------------
    def _rebuild_crumbs(self):
        self._crumbs = _safe_crumbs(self._loc)
        root = self._chevron_button(self._show_root_menu)
        root.get_style_context().add_class("root-chevron")
        overflow = Gtk.Button()
        overflow.set_relief(Gtk.ReliefStyle.NONE)
        overflow.set_focus_on_click(False)
        overflow.set_can_focus(False)
        overflow.get_style_context().add_class("crumb-overflow")
        overflow.add(icons.fluent("chevron_double_left", 12))
        overflow.connect("clicked", self._show_overflow_menu)
        pairs = []
        for i, (label, loc) in enumerate(self._crumbs):
            nxt = self._crumbs[i + 1][1] if i + 1 < len(self._crumbs) else None
            crumb = self._crumb_button(label, loc)
            chev = self._chevron_button(
                lambda btn, l=loc, n=nxt: self._show_children_menu(btn, l, n))
            pairs.append((crumb, chev))
        self.strip.set_items(root, overflow, pairs)

    def _crumb_button(self, label, loc):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_focus_on_click(False)
        b.set_can_focus(False)
        b.get_style_context().add_class("crumb")
        lab = Gtk.Label(label=label)
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        lab.set_single_line_mode(True)
        b.add(lab)
        b.connect("clicked", lambda *_: self._navigate(loc))
        b.connect("button-press-event", self._on_crumb_press, loc)
        if dnd.droppable(loc):
            dest = Gio.File.new_for_uri(loc)

            def mark(hit, b=b):
                if hit is not None:
                    b.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
                else:
                    b.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            b._drop = dnd.DropTarget(b, lambda _x, _y: dnd.Hit(dest, label), highlight=mark)
        return b

    def _chevron_button(self, callback):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_focus_on_click(False)
        b.set_can_focus(False)
        b.get_style_context().add_class("crumb-chevron")
        img = Gtk.Image.new_from_pixbuf(icons.fluent_pixbuf("chevron_right", 12, theme.P["text2"]))
        b.add(img)
        b._img = img
        b.connect("clicked", lambda btn: callback(btn))
        return b

    @staticmethod
    def _set_chevron_open(btn, is_open):
        img = getattr(btn, "_img", None)
        if img is not None:
            name = "chevron_down" if is_open else "chevron_right"
            img.set_from_pixbuf(icons.fluent_pixbuf(name, 12, theme.P["text2"]))

    def _navigate(self, loc):
        if self.is_editing():
            self._leave_edit(refocus=False)
        self.host.navigate(loc)

    def _on_crumb_press(self, _btn, event, loc):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if event.button == 2 and hasattr(self.host, "open_in_new_tab"):
            self.host.open_in_new_tab(loc)
            return True
        if event.button == 3:
            self._crumb_context_menu(event, loc)
            return True
        return False

    # -- Menüs ----------------------------------------------------------
    def _popup(self, menu, widget, chevron=None, width=None):
        old = self._menu
        if old is not None and old is not menu:
            old.menu.popdown()
        self._menu = menu
        if chevron is not None:
            self._set_chevron_open(chevron, True)

        def on_deactivate(*_):
            if chevron is not None:
                self._set_chevron_open(chevron, False)

            def drop():
                if self._menu is menu:
                    self._menu = None
                try:
                    menu.menu.detach()
                except Exception:
                    pass
                menu.menu.destroy()
                return False
            GLib.idle_add(drop)
        menu.menu.connect("deactivate", on_deactivate)
        if width:
            menu.menu.set_size_request(width, -1)
        menu.popup_below(widget)

    def _add_loc_items(self, menu, entries, bold_loc=None):
        """entries: Liste (label, loc, pixbuf) oder None für Trenner."""
        last_sep = True
        bolded = False
        for e in entries:
            if e is None:
                if not last_sep:
                    menu.separator()
                    last_sep = True
                continue
            label, loc, pb = e
            bold = not bolded and _same_loc(loc, bold_loc)
            bolded = bolded or bold
            menu.item(label, lambda l=loc: self._navigate(l), icon=pb, bold=bold)
            last_sep = False

    def _root_entries(self):
        out = [("Start", places.HOME, loc_pixbuf(places.HOME)),
               ("Katalog", places.GALLERY, loc_pixbuf(places.GALLERY)),
               ("Dieser PC", places.THISPC, loc_pixbuf(places.THISPC)), None]
        for d in places.special_dirs():
            out.append((d["label"], d["uri"], icons.folder_pixbuf(16, d["key"])))
        for d in places.cloud_dirs():
            out.append((d["label"], d["uri"], icons.place_pixbuf(d["key"], 16)))
        home = places.home_dir()
        out.append((places.user_display_name(), places.uri_for_path(home),
                    icons.folder_pixbuf(16, "user")))
        out.append(None)
        try:
            for d in places.drives():
                out.append((d["label"], d["uri"], icons.place_pixbuf(d["kind"], 16)))
        except Exception:
            pass
        out.append(None)
        out.append(("Netzwerk", "network:///", icons.place_pixbuf("network", 16)))
        out.append(("Papierkorb", "trash:///", icons.place_pixbuf("trash", 16)))
        return out

    def _thispc_entries(self):
        out = []
        for d in places.special_dirs():
            out.append((d["label"], d["uri"], icons.folder_pixbuf(16, d["key"])))
        out.append(None)
        try:
            for d in places.drives():
                out.append((d["label"], d["uri"], icons.place_pixbuf(d["kind"], 16)))
        except Exception:
            pass
        return out

    def _home_entries(self):
        from .host import pinned_uris
        out = []
        for uri in pinned_uris(self.host.config):
            try:
                out.append((places.display_name(uri), uri, loc_pixbuf(uri)))
            except Exception:
                continue
        return out

    def _show_root_menu(self, btn):
        m = WinMenu()
        first = self._crumbs[0][1] if self._crumbs else None
        self._add_loc_items(m, self._root_entries(), bold_loc=first)
        self._popup(m, btn, chevron=btn)

    def _show_overflow_menu(self, btn):
        m = WinMenu()
        hidden = self._crumbs[:self.strip.first_visible]
        entries = [(label, loc, loc_pixbuf(loc)) for label, loc in reversed(hidden)]
        entries.append(None)
        entries.extend(self._root_entries())
        self._add_loc_items(m, entries)
        self._popup(m, btn)

    def _show_children_menu(self, btn, loc, next_loc):
        if loc == places.HOME:
            entries = self._home_entries()
            m = WinMenu()
            if entries:
                self._add_loc_items(m, entries, bold_loc=next_loc)
            else:
                m.item("Keine Elemente", sensitive=False)
            self._popup(m, btn, chevron=btn)
            return
        if loc == places.THISPC:
            m = WinMenu()
            self._add_loc_items(m, self._thispc_entries(), bold_loc=next_loc)
            self._popup(m, btn, chevron=btn)
            return
        if loc == places.GALLERY:        # Ordner der Sammlung
            from . import gallery
            m = WinMenu()
            entries = [(gallery.folder_label(p), places.uri_for_path(p),
                        loc_pixbuf(places.uri_for_path(p))) for p in gallery.active_roots(self.host.config)]
            if entries:
                self._add_loc_items(m, entries)
            else:
                m.item("Keine Elemente", sensitive=False)
            self._popup(m, btn, chevron=btn)
            return
        self._menu_gen += 1
        gen = self._menu_gen
        show_hidden = bool(self.host.config.get("show_hidden", False))
        self._set_chevron_open(btn, True)

        def ready(items):
            if gen != self._menu_gen or btn.get_parent() is None or not btn.get_mapped():
                self._set_chevron_open(btn, False)
                return
            m = WinMenu()
            if not items:
                m.item("Keine Elemente" if items is not None else "Nicht verfügbar",
                       sensitive=False)
            else:
                for d in items[:MENU_MAX_ITEMS]:
                    path = None
                    if d["uri"].startswith("file://"):
                        path = Gio.File.new_for_uri(d["uri"]).get_path()
                    pb = icons.folder_pixbuf(16, places.special_key_for_path(path))
                    m.item(d["name"], lambda u=d["uri"]: self._navigate(u), icon=pb,
                           bold=_same_loc(d["uri"], next_loc))
            self._popup(m, btn, chevron=btn)

        list_subfolders_async(loc, show_hidden, ready)

    def _on_history_clicked(self, _btn):
        hist = list(self.host.config.get("address_history", []) or [])
        m = WinMenu()
        if not hist:
            m.item("Kein Verlauf", sensitive=False)
        for loc in hist[:HISTORY_MAX]:
            m.item(loc_to_text(loc), lambda l=loc: self._navigate(l), icon=loc_pixbuf(loc))
        self._set_open_dropdown(True)
        self._popup(m, self.addr_events, width=self.addr_events.get_allocated_width())
        self._menu.menu.connect("deactivate", lambda *_: self._set_open_dropdown(False))

    def _set_open_dropdown(self, is_open):
        self._hist_img.set_from_pixbuf(
            icons.fluent_pixbuf("chevron_up" if is_open else "chevron_down", 12))

    # -- Kontextmenüs ---------------------------------------------------
    def _copy_text(self, text):
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.set_text(text, -1)
        cb.store()

    def _crumb_context_menu(self, event, loc):
        m = WinMenu()
        if hasattr(self.host, "open_in_new_tab"):
            m.item("In neuem Tab öffnen", lambda: self.host.open_in_new_tab(loc), icon="tab_add")
        if hasattr(self.host, "open_in_new_window"):
            m.item("In neuem Fenster öffnen", lambda: self.host.open_in_new_window(loc),
                   icon="window_new")
        m.separator()
        m.item("Adresse als Text kopieren", lambda: self._copy_text(loc_to_text(loc)), icon="copy")
        m.item("Adresse bearbeiten", self._enter_edit, icon="edit")
        self._menu = m
        m.popup_at_pointer(event, attach_to=self)

    def _addr_context_menu(self, event):
        m = WinMenu()
        m.item("Adresse als Text kopieren", lambda: self._copy_text(loc_to_text(self._loc)),
               icon="copy")
        m.item("Adresse bearbeiten", self._enter_edit, icon="edit")
        m.separator()
        m.item("Verlauf löschen", lambda: self.host.config.set("address_history", []),
               icon="delete",
               sensitive=bool(self.host.config.get("address_history", [])))
        self._menu = m
        m.popup_at_pointer(event, attach_to=self)

    # -- Adressleiste: Maus ----------------------------------------------
    def _on_addr_press(self, _w, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if event.button == 1:
            if not self.is_editing():
                self._enter_edit()
            return True
        if event.button == 3 and not self.is_editing():
            self._addr_context_menu(event)
            return True
        return False

    def _on_addr_enter(self, *_):
        self.addr.get_style_context().add_class("hover")
        return False

    def _on_addr_leave(self, _w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.addr.get_style_context().remove_class("hover")
        return False

    # ------------------------------------------------------------------
    # Bearbeitungsmodus
    # ------------------------------------------------------------------
    def _enter_edit(self):
        if not self.is_editing():
            self._edit_block = True
            try:
                self._entry_shown = places.safe_text(loc_to_text(self._loc))
                self.entry.set_text(self._entry_shown)
            finally:
                self._edit_block = False
            self._compl_dir = None
            self.stack.set_visible_child_name("edit")
            self.addr.get_style_context().add_class("editing")
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def _leave_edit(self, refocus=True):
        if not self.is_editing():
            return
        self._compl_gen += 1
        had_focus = self.entry.has_focus()
        self.stack.set_visible_child_name("crumbs")
        self.addr.get_style_context().remove_class("editing")
        if refocus and had_focus:
            focus_view = getattr(self.host, "focus_view", None)
            if callable(focus_view):
                focus_view()
            else:
                top = self.get_toplevel()
                if isinstance(top, Gtk.Window):
                    top.set_focus(None)

    def _on_entry_key(self, _entry, event):
        if event.keyval == Gdk.KEY_Escape:
            self._leave_edit()
            return True
        return False

    def _on_entry_focus_out(self, *_):
        if self._dialog_open:
            return False
        GLib.idle_add(self._check_focus_lost)
        return False

    def _check_focus_lost(self):
        if not self.is_editing() or self._dialog_open or self.entry.has_focus():
            return False
        top = self.get_toplevel()
        if isinstance(top, Gtk.Window) and not top.is_active():
            # Fenster nur in den Hintergrund geraten → Bearbeitung bleibt
            return False
        self._leave_edit(refocus=False)
        return False

    def _resolve(self, text):
        t = text.strip()
        if not t:
            return None
        low = t.lower()
        expanded = os.path.expanduser(t)
        special = low in ("start", "home:", "schnellzugriff", "dieser pc", "thispc:", "computer",
                          "papierkorb", "trash", "trash:", "trash:///", "netzwerk", "network",
                          "network:///") or low in places.GALLERY_WORDS
        if not special and "://" not in t and not os.path.isabs(expanded):
            # relativer Pfad nur innerhalb eines lokalen Ordners sinnvoll
            if not (self._loc and places.local_path(self._loc)):
                return None
        try:
            return places.resolve_input(t, self._loc)
        except Exception:
            return None

    def _on_entry_activate(self, _entry):
        text = self.entry.get_text()
        if not text.strip():
            self._leave_edit()
            return
        # unverändert (bereinigt angezeigter Ort mit Sonderzeichen): den echten Ort nehmen
        loc = self._loc if text == getattr(self, "_entry_shown", None) else self._resolve(text)
        if loc is None:
            self._not_found(text.strip())
            return
        if places.is_virtual(loc):
            self._accept(loc)
            return
        try:
            gfile = Gio.File.new_for_uri(loc)
        except Exception:
            self._not_found(text.strip())
            return

        def done(f, res):
            try:
                info = f.query_info_finish(res)
            except GLib.Error as exc:
                if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_MOUNTED):
                    self._accept(loc)   # Einhängen übernimmt die Ordneransicht
                else:
                    self._not_found(text.strip())
                return
            if info.get_file_type() in (Gio.FileType.REGULAR, Gio.FileType.SPECIAL):
                # wie Windows: Datei in der Adressleiste → mit Standardprogramm öffnen
                from .host import launch_file
                self._remember(loc)
                launch_file(f, self.get_toplevel())
                self._leave_edit()
                return
            self._accept(loc)

        gfile.query_info_async("standard::type", Gio.FileQueryInfoFlags.NONE,
                               GLib.PRIORITY_DEFAULT, None, done)

    def _accept(self, loc):
        self._remember(loc)
        self._leave_edit()
        self.host.navigate(loc)

    def _remember(self, loc):
        loc = places.without_password(loc)      # kein Passwort in settings.json
        hist = [h for h in (self.host.config.get("address_history", []) or [])
                if not _same_loc(h, loc)]
        hist.insert(0, loc)
        self.host.config.set("address_history", hist[:HISTORY_MAX])

    def _not_found(self, text):
        from .propsdialog import message
        self._dialog_open = True
        try:
            message(self.get_toplevel(),
                    f'FileFenster findet "{text}" nicht. Überprüfen Sie die Schreibweise, '
                    'und wiederholen Sie den Vorgang.', kind="error")
        finally:
            self._dialog_open = False
        if self.is_editing():
            self.entry.grab_focus()
            self.entry.select_region(0, -1)

    # -- Vervollständigung ------------------------------------------------
    def _completion_match(self, _completion, key, it, _data):
        row = self._compl_store[it]
        text = row[0]
        if not text:
            return False
        typed = self.entry.get_text()
        if row[1]:   # versteckt: nur bei show_hidden oder wenn "." getippt wurde
            last = typed.rsplit("/", 1)[-1]
            if not last.startswith(".") and not self.host.config.get("show_hidden", False):
                return False
        return text.casefold().startswith(typed.casefold()) and text != typed

    def _on_entry_changed(self, _entry):
        if self._edit_block or not self.is_editing():
            return
        text = self.entry.get_text()
        if "/" not in text or "://" in text:
            if self._compl_dir is not None:
                self._compl_dir = None
                self._compl_store.clear()
            return
        dirpart = text[:text.rfind("/") + 1]
        if dirpart == self._compl_dir:
            return
        self._compl_dir = dirpart
        self._compl_gen += 1
        gen = self._compl_gen
        self._compl_store.clear()
        expanded = os.path.expanduser(dirpart)
        if not os.path.isabs(expanded):
            base = places.local_path(self._loc) if self._loc else None
            if not base:
                return
            expanded = os.path.join(base, expanded)
        if not os.path.isdir(expanded):
            return

        def ready(items):
            if gen != self._compl_gen or not self.is_editing():
                return
            self._compl_store.clear()
            for d in items or []:
                self._compl_store.append([dirpart + d["name"], d["hidden"]])
            if self.entry.has_focus():
                self._completion.complete()

        list_subfolders_async(Gio.File.new_for_path(expanded).get_uri(), True, ready,
                              include_hidden_flag=True)

    # ------------------------------------------------------------------
    # Suche
    # ------------------------------------------------------------------
    def _update_search_icon(self):
        has_text = bool(self.search.get_text())
        if has_text == self._search_icon_is_clear:
            return
        self._search_icon_is_clear = has_text
        pos = Gtk.EntryIconPosition.SECONDARY
        self.search.set_icon_from_pixbuf(pos, self._pb_clear if has_text else self._pb_search)
        self.search.set_icon_activatable(pos, True)
        self.search.set_icon_tooltip_text(pos, "Suchbegriff löschen" if has_text else None)

    def _cancel_search_timer(self):
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0

    def _on_search_changed(self, _entry):
        self._update_search_icon()
        if self._search_block:
            return
        self._cancel_search_timer()
        if not self.search.get_text().strip():
            self.host.stop_search()
            return
        self._search_timer = GLib.timeout_add(SEARCH_DELAY_MS, self._fire_search)

    def _fire_search(self):
        self._search_timer = 0
        text = self.search.get_text().strip()
        if text:
            self.host.start_search(text)
        return False

    def _on_search_activate(self, _entry):
        self._cancel_search_timer()
        self._fire_search()

    def _on_search_icon(self, _entry, pos, _event):
        if pos != Gtk.EntryIconPosition.SECONDARY:
            return
        if self.search.get_text():
            self.search.set_text("")
        self.search.grab_focus()

    def _on_search_key(self, _entry, event):
        if event.keyval == Gdk.KEY_Escape and self.search.get_text():
            self.search.set_text("")
            return True
        return False
