"""Startseite: „Angeheftet“, „Empfohlen“ und „Alle“ (Kategorie/Raster/Liste)."""
import os

from gi.repository import Gdk, GLib, Gtk

from . import files, icons, session, ui
from .apps import CATEGORY_LABEL
from .config import get_config, metrics

VIEW_LABELS = {"category": "Kategorie", "grid": "Raster", "list": "Liste"}


class StartPage(Gtk.ScrolledWindow):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.m = metrics()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.set_margin_start(32)
        self.content.set_margin_end(32)
        self.content.set_margin_top(8)
        self.content.set_margin_bottom(24)
        self.add(self.content)
        self.pinned_expanded = False
        self.recommended_expanded = False
        self.letter_heads = {}
        self.dirty = {"pinned", "recommended", "all"}

        # Angeheftet
        self.pinned_head = ui.section_header("Angeheftet", "Alle anzeigen",
                                             self._toggle_pinned)
        self.pinned_head.set_margin_bottom(8)
        self.pinned_head.set_margin_start(4)
        self.content.pack_start(self.pinned_head, False, False, 0)
        self.pinned_grid = self._grid()
        self.content.pack_start(self.pinned_grid, False, False, 0)
        # Ziehen: Reihenfolge ändern bzw. Apps aus „Alle“ anheften
        self._tiles = {}
        self._order = None
        self._restore = 0
        self._placeholder = None
        actions = Gdk.DragAction.MOVE | Gdk.DragAction.COPY
        self.pinned_grid.drag_dest_set(0, ui.dnd_targets(), actions)
        self.pinned_grid.connect("drag-motion", self._pin_motion)
        self.pinned_grid.connect("drag-leave", self._pin_leave)
        self.pinned_grid.connect("drag-drop", self._pin_drop)
        self._autoscroll = 0
        self._scroll_dir = 0
        self.drag_dest_set(0, ui.dnd_targets(), actions)
        self.connect("drag-motion", self._scroll_motion)
        self.connect("drag-leave", lambda *_a: self._stop_scroll())
        self.connect("button-press-event", self._background_press)


        # Empfohlen
        self.rec_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.rec_box.set_margin_top(24)
        self.rec_head = ui.section_header("Empfohlen", "Mehr", self._toggle_recommended)
        self.rec_head.set_margin_bottom(8)
        self.rec_head.set_margin_start(4)
        self.rec_box.pack_start(self.rec_head, False, False, 0)
        self.rec_grid = Gtk.Grid(column_homogeneous=True, column_spacing=8, row_spacing=4)
        self.rec_box.pack_start(self.rec_grid, False, False, 0)
        self.rec_empty = ui.label("Zuletzt geöffnete Dateien und neue Apps werden hier angezeigt.",
                                  "sub", ellipsize=False)
        self.rec_empty.set_margin_start(4)
        self.rec_empty.set_no_show_all(True)
        self.rec_box.pack_start(self.rec_empty, False, False, 0)
        self.rec_box.set_no_show_all(True)
        self.content.pack_start(self.rec_box, False, False, 0)

        # Alle
        self.all_head = ui.section_header("Alle", "Ansicht: " + VIEW_LABELS[get_config().get("view")],
                                          self._view_menu, chevron="chevron_down_12_regular")
        self.all_head.set_margin_top(28)
        self.all_head.set_margin_bottom(10)
        self.all_head.set_margin_start(4)
        self.content.pack_start(self.all_head, False, False, 0)
        self.all_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.pack_start(self.all_box, False, False, 0)

    def _background_press(self, _w, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 3:
            ui.popup_menu(self.win.layer, [("settings_20_regular", "Starteinstellungen",
                                            self.win.open_settings)],
                          self.win.layer.anchor_of(self, ev.x, ev.y), "point", 200)
            return True
        return False

    def _grid(self):
        g = Gtk.Grid(column_homogeneous=True, row_spacing=4, column_spacing=0)
        return g

    # ------------------------------------------------------------ Aktualisieren
    def mark_dirty(self, *parts):
        self.dirty.update(parts or ("pinned", "recommended", "all"))

    def refresh(self):
        m = metrics()
        if m != self.m:
            self.m = m
            self.dirty.update(("pinned", "all"))
        show_all = get_config().get("show_all")
        self.all_head.set_visible(show_all)
        self.all_box.set_visible(show_all)
        if not show_all:
            self.dirty.discard("all")
        if "pinned" in self.dirty:
            self._build_pinned()
        if "recommended" in self.dirty or True:   # Zeitangaben ändern sich ständig
            self._build_recommended()
        if "all" in self.dirty:
            self._build_all()
        self.dirty.clear()

    def reset(self):
        self.pinned_expanded = False
        self.recommended_expanded = False
        self.get_vadjustment().set_value(0)

    def first_focus(self):
        kids = self.pinned_grid.get_children()
        if kids:
            # Grid liefert Kinder rückwärts
            kids[-1].grab_focus()
            return True
        return False

    # ------------------------------------------------------------ Angeheftet
    def _build_pinned(self):
        for c in self.pinned_grid.get_children():
            c.destroy()
        self._order = None
        self._placeholder = None
        self._tiles = {}
        entries = [e for e in (self.win.index.get(i) for i in self.win.pinned) if e]
        limit = len(entries) if self.pinned_expanded else self.m['cols'] * self.m['pin_rows']
        for n, e in enumerate(entries[:limit]):
            btn = ui.tile(e, self.win.launch_entry, self.m['tile_w'], self.m['tile_h'], self.m['icon'])
            ui.on_context(btn, lambda w, x, y, e=e: self.win.app_menu(w, x, y, e, pinned=True))
            ui.drag_source(btn, e, self.win, move=True)
            self._tiles[e.id] = btn
            self.pinned_grid.attach(btn, n % self.m['cols'], n // self.m['cols'], 1, 1)
        # leere Spalten auffüllen, damit Kacheln nicht breiter werden
        for n in range(len(entries[:limit]), self.m['cols']):
            filler = Gtk.Box()
            self.pinned_grid.attach(filler, n, 0, 1, 1)
        btn = self.pinned_head.button
        btn.set_visible(len(entries) > self.m['cols'] * self.m['pin_rows'])
        btn.text_label.set_text("Weniger anzeigen" if self.pinned_expanded else "Alle anzeigen")
        btn.chevron.set_from_pixbuf(icons._fluent_pixbuf(
            "chevron_up_12_regular" if self.pinned_expanded else "chevron_right_12_regular",
            12, icons.theme.P["icon"], 1))
        if not entries:
            hint = ui.label("Apps mit Rechtsklick → „An Start anheften“ hier ablegen.", "sub")
            hint.set_margin_start(4)
            self.pinned_grid.attach(hint, 0, 0, self.m['cols'], 1)
        self.pinned_grid.show_all()

    def _toggle_pinned(self, _b):
        self.pinned_expanded = not self.pinned_expanded
        self._build_pinned()

    # ------------------------------------------------------------ Ziehen
    def _visible_ids(self):
        ids = [i for i in self.win.pinned if self.win.index.get(i)]
        return ids if self.pinned_expanded else ids[:self.m['cols'] * self.m['pin_rows']]

    def _index_at(self, x, y, count):
        alloc = self.pinned_grid.get_allocation()
        cw = max(1, alloc.width / self.m['cols'])
        kids = list(self._tiles.values())
        rh = (kids[0].get_allocated_height() if kids else 84) + self.pinned_grid.get_row_spacing()
        col = max(0, min(self.m['cols'] - 1, int(x // cw)))
        row = max(0, int(y // max(1, rh)))
        return max(0, min(count, row * self.m['cols'] + col))

    def _pin_motion(self, _grid, ctx, x, y, time_):
        drag_id = self.win.drag_id
        if not drag_id:
            Gdk.drag_status(ctx, 0, time_)
            return False
        if self._restore:
            GLib.source_remove(self._restore)
            self._restore = 0
        self._stop_scroll()
        base = self._visible_ids()
        rest = [i for i in base if i != drag_id]
        order = list(rest)
        order.insert(self._index_at(x, y, len(rest)), drag_id)
        if order != self._order:
            self._order = order
            self._live_layout(order, drag_id)
        Gdk.drag_status(ctx, Gdk.DragAction.MOVE if drag_id in self.win.pinned
                        else Gdk.DragAction.COPY, time_)
        return True

    def _live_layout(self, order, drag_id):
        """Kacheln rücken schon beim Ziehen zur Seite (wie Windows)."""
        grid = self.pinned_grid
        if drag_id not in self._tiles and self._placeholder is None:
            self._placeholder = Gtk.Box()
            self._placeholder.set_size_request(self.m['tile_w'], self.m['tile_h'])
            grid.attach(self._placeholder, 0, 0, 1, 1)
            self._placeholder.show()
        for n, app_id in enumerate(order):
            w = self._tiles.get(app_id) or self._placeholder
            if w is None:
                continue
            grid.child_set_property(w, "left-attach", n % self.m['cols'])
            grid.child_set_property(w, "top-attach", n // self.m['cols'])

    def _pin_leave(self, _grid, _ctx, _time):
        # kommt auch unmittelbar vor drag-drop – deshalb verzögert zurücksetzen
        if self._restore:
            GLib.source_remove(self._restore)
        self._restore = GLib.timeout_add(80, self._restore_layout)

    def _restore_layout(self):
        self._restore = 0
        if self._order is not None:
            self._build_pinned()
        return False

    def _pin_drop(self, _grid, ctx, _x, _y, time_):
        if self._restore:
            GLib.source_remove(self._restore)
            self._restore = 0
        drag_id, order = self.win.drag_id, self._order
        ok = bool(drag_id and order)
        if ok:
            full = order + [i for i in self.win.pinned if i not in order]
            GLib.idle_add(self.win.set_pinned, full)
        Gtk.drag_finish(ctx, ok, False, time_)
        return True

    def _scroll_motion(self, _w, ctx, _x, y, time_):
        h = self.get_allocated_height()
        self._scroll_dir = -1 if y < 70 else (1 if y > h - 70 else 0)
        if self._scroll_dir and not self._autoscroll:
            self._autoscroll = GLib.timeout_add(30, self._scroll_step)
        Gdk.drag_status(ctx, 0, time_)
        return True

    def _scroll_step(self):
        if not self._scroll_dir:
            self._autoscroll = 0
            return False
        adj = self.get_vadjustment()
        adj.set_value(max(0, min(adj.get_upper() - adj.get_page_size(),
                                 adj.get_value() + self._scroll_dir * 14)))
        return True

    def _stop_scroll(self):
        self._scroll_dir = 0
        if self._autoscroll:
            GLib.source_remove(self._autoscroll)
            self._autoscroll = 0

    # ------------------------------------------------------------ Empfohlen
    def _build_recommended(self):
        for c in self.rec_grid.get_children():
            c.destroy()
        if not get_config().get("show_recommended"):
            self.rec_box.hide()
            return
        items = []
        cfg = get_config()
        if cfg.get("show_recent_apps"):
            for e in self.win.index.recently_added():
                items.append(("app", e))
        if cfg.get("show_recent_files"):
            for f in files.recent_files(self.m['rec_more']):
                items.append(("file", f))
        limit = self.m['rec_more'] if self.recommended_expanded else self.m['rec']
        for n, (kind, obj) in enumerate(items[:limit]):
            if kind == "app":
                w = ui.row(icons.app_image(obj.gicon, 32), obj.name, "Kürzlich hinzugefügt", 56)
                w.connect("clicked", lambda _b, e=obj: self.win.launch_entry(e))
                ui.on_context(w, lambda wd, x, y, e=obj: self.win.app_menu(wd, x, y, e))
            else:
                w = ui.row(icons.app_image(icons.file_gicon(obj.path, obj.mime, obj.is_dir), 32),
                           obj.name, files.relative_time(obj.time), 56)
                w.set_tooltip_text(_short(obj.path))
                w.connect("clicked", lambda _b, f=obj: self.win.open_file(f.path))
                ui.on_context(w, lambda wd, x, y, f=obj: self.win.file_menu(wd, x, y, f.path, f.uri))
            self.rec_grid.attach(w, n % 2, n // 2, 1, 1)
        if len(items) == 1:
            self.rec_grid.attach(Gtk.Box(), 1, 0, 1, 1)
        self.rec_head.button.set_visible(len(items) > self.m['rec'])
        self.rec_head.button.text_label.set_text("Weniger" if self.recommended_expanded else "Mehr")
        self.rec_empty.set_visible(not items)
        self.rec_box.show()
        self.rec_head.show_all()
        self.rec_head.button.set_visible(len(items) > self.m['rec'])
        self.rec_grid.show_all()

    def _toggle_recommended(self, _b):
        self.recommended_expanded = not self.recommended_expanded
        self._build_recommended()

    # ------------------------------------------------------------ Alle
    def _view_menu(self, btn):
        view = get_config().get("view")
        items = []
        for key in ("category", "grid", "list"):
            items.append(("checkmark_16_regular" if key == view else None, VIEW_LABELS[key],
                          lambda k=key: self.set_view(k)))
        ui.popup_menu(self.win.layer, items, self.win.layer.anchor_of(btn), "below", 160)

    def set_view(self, view):
        get_config().set("view", view)
        self.all_head.button.text_label.set_text("Ansicht: " + VIEW_LABELS[view])
        self._build_all()

    def _build_all(self):
        for c in self.all_box.get_children():
            c.destroy()
        self.letter_heads = {}
        view = get_config().get("view")
        if view == "category":
            self._build_categories()
        elif view == "grid":
            self._build_letters(grid=True)
        else:
            self._build_letters(grid=False)
        self.all_box.show_all()

    def _build_categories(self):
        grid = Gtk.Grid(column_homogeneous=True, column_spacing=16, row_spacing=16)
        for n, (key, label, items) in enumerate(self.win.index.categories()):
            grid.attach(self._category_tile(label, items), n % self.m['cat_cols'], n // self.m['cat_cols'], 1, 1)
        self.all_box.pack_start(grid, False, False, 0)

    def _category_tile(self, title, items):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame = Gtk.Box()
        ui.add_class(frame, "cattile")
        cells = Gtk.Grid(column_homogeneous=True, row_homogeneous=True,
                         column_spacing=2, row_spacing=2)
        big = items[:3] if len(items) > 4 else items[:4]
        for n, e in enumerate(big):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_size_request(*self._cell())
            b.add(icons.app_image(e.gicon, self.m['icon']))
            b.set_tooltip_text(e.name)
            b.connect("clicked", lambda _b, e=e: self.win.launch_entry(e))
            ui.on_context(b, lambda w, x, y, e=e: self.win.app_menu(w, x, y, e))
            ui.drag_source(b, e, self.win)
            cells.attach(b, n % 2, n // 2, 1, 1)
        if len(items) > 4:
            more = Gtk.Button()
            more.set_relief(Gtk.ReliefStyle.NONE)
            more.set_size_request(*self._cell())
            mini = Gtk.Grid(column_homogeneous=True, row_homogeneous=True,
                            column_spacing=4, row_spacing=4)
            mini.set_halign(Gtk.Align.CENTER)
            mini.set_valign(Gtk.Align.CENTER)
            for n, e in enumerate(items[3:7]):
                mini.attach(icons.app_image(e.gicon, max(12, self.m['icon'] // 2)), n % 2, n // 2, 1, 1)
            more.add(mini)
            more.set_tooltip_text(f"{title} – alle {len(items)} Apps")
            more.connect("clicked", lambda _b: self.open_folder(title, items))
            cells.attach(more, 1, 1, 1, 1)
        frame.pack_start(cells, True, True, 0)
        outer.pack_start(frame, False, False, 0)
        name = Gtk.Button()
        name.set_relief(Gtk.ReliefStyle.NONE)
        name.add(ui.label(title, xalign=0.5))
        name.connect("clicked", lambda _b: self.open_folder(title, items))
        outer.pack_start(name, False, False, 0)
        return outer

    def _cell(self):
        f = self.m['icon'] / 32
        return int(72 * f), int(60 * f)

    def open_folder(self, title, items):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        ui.add_class(box, "folder")
        head = ui.label(title, "big")
        box.pack_start(head, False, False, 0)
        cols = 5
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=4)
        for n, e in enumerate(items):
            t = ui.tile(e, self.win.launch_entry, self.m['tile_w'], self.m['tile_h'], self.m['icon'])
            ui.on_context(t, lambda w, x, y, e=e: self.win.app_menu(w, x, y, e))
            grid.attach(t, n % cols, n // cols, 1, 1)
        for n in range(len(items), cols):
            grid.attach(Gtk.Box(), n, 0, 1, 1)
        rows = (len(items) + cols - 1) // cols
        if rows > 4:
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_size_request(-1, 4 * (self.m['tile_h'] + 4))
            sw.add(grid)
            box.pack_start(sw, True, True, 0)
        else:
            box.pack_start(grid, False, False, 0)
        box.set_size_request(cols * (self.m['tile_w'] + 4) + 48, -1)
        self.win.layer.open(box, center=True, dim=True)

    def _build_letters(self, grid):
        groups = {}
        for e in self.win.index.entries:
            groups.setdefault(e.letter, []).append(e)
        order = sorted(groups, key=lambda L: (L != "#", L))
        for L in order:
            head = Gtk.Button()
            head.set_relief(Gtk.ReliefStyle.NONE)
            head.add(ui.label(L, "letter", xalign=0.5))
            head.set_halign(Gtk.Align.START)
            head.connect("clicked", lambda _b: self.letter_picker(set(groups)))
            head.set_margin_top(6)
            self.letter_heads[L] = head
            self.all_box.pack_start(head, False, False, 0)
            if grid:
                g = self._grid()
                for n, e in enumerate(groups[L]):
                    t = ui.tile(e, self.win.launch_entry, self.m['tile_w'], self.m['tile_h'], self.m['icon'])
                    ui.on_context(t, lambda w, x, y, e=e: self.win.app_menu(w, x, y, e))
                    ui.drag_source(t, e, self.win)
                    g.attach(t, n % self.m['cols'], n // self.m['cols'], 1, 1)
                for n in range(len(groups[L]), self.m['cols']):
                    g.attach(Gtk.Box(), n, 0, 1, 1)
                self.all_box.pack_start(g, False, False, 0)
            else:
                for e in groups[L]:
                    r = ui.row(icons.app_image(e.gicon, self.m['list_icon']), e.name, None,
                               self.m['list_icon'] + 14, "name13")
                    r.connect("clicked", lambda _b, e=e: self.win.launch_entry(e))
                    ui.on_context(r, lambda w, x, y, e=e: self.win.app_menu(w, x, y, e))
                    ui.drag_source(r, e, self.win)
                    r.entry = e
                    self.all_box.pack_start(r, False, False, 0)

    def letter_picker(self, available):
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True,
                        column_spacing=4, row_spacing=4)
        ui.add_class(grid, "folder")
        letters = ["#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]
        for n, L in enumerate(letters):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_size_request(52, 52)
            b.add(ui.label(L, "big", xalign=0.5))
            b.set_sensitive(L in available)
            b.connect("clicked", lambda _b, L=L: self._jump(L))
            grid.attach(b, n % 7, n // 7, 1, 1)
        self.win.layer.open(grid, center=True, dim=True)

    def _jump(self, letter):
        self.win.layer.close()
        head = self.letter_heads.get(letter)
        if head is None:
            return
        res = head.translate_coordinates(self.content, 0, 0)
        if res:
            adj = self.get_vadjustment()
            GLib.idle_add(lambda: adj.set_value(min(res[1], adj.get_upper() - adj.get_page_size())))


def _short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + "/") else path
