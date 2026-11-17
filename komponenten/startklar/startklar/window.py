"""Das Startmenü-Fenster: Lage über der Taskleiste, Acryl-Hintergrund,
Einblenden, Tastatur, Schliessen bei Fokusverlust."""
import math
import time

import cairo
from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk

from . import files, icons, session, taskbar, theme, ui
from .config import get_config, metrics
from .searchpage import SearchPage
from .startpage import StartPage

MAX_HEIGHT = 900
MIN_HEIGHT = 560
GAP = 12                  # Abstand zur Taskleiste
SHADOW = 28               # transparenter Rand für den Schatten
RADIUS = 8
ANIM_MS = 190
ANIM_SHIFT = 26


class MenuWindow(Gtk.Window):
    def __init__(self, app, index, file_index):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.app = app
        self.index = index
        self.file_index = file_index
        cfg = get_config()
        if cfg.get("pinned") is None:
            from .apps import default_pinned
            cfg.set("pinned", default_pinned(index))
        self.pinned = list(cfg.get("pinned"))
        self._recent = None
        self._backdrop = None
        self._offset = 0.0
        self._anim = 0
        self._hidden_at = 0.0
        self._hidden_by_focus = False
        self._focus_check = 0
        self._body = (0, 0, metrics()['width'], MAX_HEIGHT)
        self.drag_id = None
        self._dragging = False
        self.update_info = None       # (Version, Release) wenn ein Update bereitliegt

        self.set_title("Start")
        self.set_wmclass("startklar", "Startklar")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()
        ui.add_class(self, "sk")
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.composited = bool(visual is not None and screen.is_composited())
        if self.composited:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.margin = SHADOW if self.composited else 0

        self.overlay = Gtk.Overlay()
        self.overlay.set_margin_start(self.margin)
        self.overlay.set_margin_end(self.margin)
        self.overlay.set_margin_top(self.margin)
        self.overlay.set_margin_bottom(self.margin)
        self.add(self.overlay)
        self.layer = ui.Layer(self.overlay)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.add(body)

        self.entry = Gtk.Entry()
        ui.add_class(self.entry, "search")
        self.entry.set_icon_from_pixbuf(Gtk.EntryIconPosition.PRIMARY,
                                        icons._fluent_pixbuf("search_20_regular", 16,
                                                             theme.P["text2"], 1))
        self.entry.connect("changed", self._on_query)
        self.entry.connect("activate", self._on_entry_activate)
        # Windows zeigt den Hinweis auch im fokussierten, leeren Feld
        self.placeholder = ui.label("Nach Apps, Einstellungen und Dokumenten suchen", "dim")
        ui.add_class(self.placeholder, "placeholder")
        self.placeholder.set_margin_start(44)
        self.placeholder.set_halign(Gtk.Align.START)
        self.placeholder.set_valign(Gtk.Align.CENTER)
        search_ov = Gtk.Overlay()
        search_ov.add(self.entry)
        search_ov.add_overlay(self.placeholder)
        search_ov.set_overlay_pass_through(self.placeholder, True)
        search_ov.set_margin_start(32)
        search_ov.set_margin_end(32)
        search_ov.set_margin_top(28)
        search_ov.set_margin_bottom(20)
        body.pack_start(search_ov, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(110)
        self.start = StartPage(self)
        self.search = SearchPage(self)
        self.stack.add_named(self.start, "start")
        self.stack.add_named(self.search, "search")
        body.pack_start(self.stack, True, True, 0)

        self.footer = self._build_footer()
        body.pack_start(self.footer, False, False, 0)

        self.connect("key-press-event", self._on_key)
        self.connect("notify::is-active", self._on_active)
        self.connect("delete-event", lambda *_a: (self.hide_menu(), True)[1])
        self.connect("realize", lambda *_a: self._apply_input_shape())
        self.connect("size-allocate", lambda *_a: self._apply_input_shape())
        index.connect_changed(lambda: self.start.mark_dirty("pinned", "all", "recommended"))
        Gtk.RecentManager.get_default().connect("changed", lambda *_a: self._recent_changed())
        self.overlay.show_all()
        self.stack.set_visible_child_name("start")

    # ------------------------------------------------------------ Fusszeile
    def _build_footer(self):
        bar = Gtk.Box(spacing=4)
        ui.add_class(bar, "footer")
        bar.set_size_request(-1, 64)
        inner = Gtk.Box(spacing=4)
        inner.set_margin_start(40)
        inner.set_margin_end(40)
        bar.pack_start(inner, True, True, 0)

        user = Gtk.Button()
        user.set_relief(Gtk.ReliefStyle.NONE)
        ub = Gtk.Box(spacing=12)
        ub.set_margin_start(10)
        ub.set_margin_end(12)
        ub.pack_start(icons.avatar(32), False, False, 0)
        ub.pack_start(ui.label(session.user_name()), False, False, 0)
        user.add(ub)
        user.set_size_request(-1, 44)
        user.set_valign(Gtk.Align.CENTER)
        user.connect("clicked", self._user_menu)
        inner.pack_start(user, False, False, 0)

        self.power_btn = ui.icon_button("power_20_regular", "Ein/Aus", self._power_menu)
        # Punkt am Ein/Aus-Knopf, wenn ein Update bereitliegt (wie bei Windows)
        img = self.power_btn.get_child()
        self.power_btn.remove(img)
        ov = Gtk.Overlay()
        ov.add(img)
        self.update_dot = Gtk.Box()
        ui.add_class(self.update_dot, "dot")
        self.update_dot.set_size_request(6, 6)
        self.update_dot.set_halign(Gtk.Align.END)
        self.update_dot.set_valign(Gtk.Align.START)
        self.update_dot.set_no_show_all(True)
        ov.add_overlay(self.update_dot)
        self.power_btn.add(ov)
        inner.pack_end(self.power_btn, False, False, 0)
        for kind in reversed(get_config().get("folders")):
            if kind in session.FOLDERS:
                text, icon, _d = session.FOLDERS[kind]
                inner.pack_end(ui.icon_button(icon, text,
                                              lambda _b, k=kind: self._open_folder(k)),
                               False, False, 0)
        return bar

    def reconfigure(self):
        """Nach einer Änderung in den Einstellungen: alles neu aufbauen."""
        if self.get_visible():
            self.hide_menu()
        theme.apply()
        body = self.footer.get_parent()
        self.footer.destroy()
        self.footer = self._build_footer()
        body.pack_start(self.footer, False, False, 0)
        self.footer.show_all()
        self.set_update(self.update_info)
        self.entry.set_icon_from_pixbuf(Gtk.EntryIconPosition.PRIMARY,
                                        icons._fluent_pixbuf("search_20_regular", 16,
                                                             theme.P["text2"], 1))
        self.start.mark_dirty()
        self.search.reset()

    def open_settings(self):
        self.hide_menu()
        self.app.open_settings()

    def _open_folder(self, kind):
        ts = Gtk.get_current_event_time()
        self.hide_menu()
        session.open_folder(kind, ts)

    def set_update(self, version):
        self.update_info = version
        self.update_dot.set_visible(bool(version))
        self.power_btn.set_tooltip_text(f"Ein/Aus – Startklar {version} verfügbar" if version
                                        else "Ein/Aus")

    def _power_menu(self, btn):
        items = []
        if self.update_info:
            items += [("arrow_download_20_regular", f"Startklar auf {self.update_info} aktualisieren",
                       self._do(lambda: self.app.activate_action("install-update", None))),
                      None]
        items += [("weather_moon_20_regular", "Energie sparen", self._do(session.suspend)),
                 ("power_20_regular", "Herunterfahren", self._do(session.shutdown)),
                 ("arrow_clockwise_20_regular", "Neu starten", self._do(session.reboot))]
        ui.popup_menu(self.layer, items, self.layer.anchor_of(btn), "above", 220)

    def _user_menu(self, btn):
        items = [("person_20_regular", "Kontoeinstellungen ändern", self._do(session.account_settings)),
                 None,
                 ("lock_closed_20_regular", "Sperren", self._do(session.lock)),
                 ("sign_out_20_regular", "Abmelden", self._do(session.logout))]
        a = self.layer.anchor_of(btn)
        ui.popup_menu(self.layer, items, (a[0], a[1], 0, a[3]), "above", 240)

    def _do(self, fn):
        def run():
            self.hide_menu()
            fn()
        return run

    # ------------------------------------------------------------ Aktionen
    def launch_entry(self, entry, action=None):
        ts = Gtk.get_current_event_time()
        self.hide_menu()
        self.index.launch(entry, ts, action)
        self.start.mark_dirty("all")

    def open_file(self, path):
        ts = Gtk.get_current_event_time()
        self.hide_menu()
        session.open_path(path, ts)

    def reveal(self, path):
        ts = Gtk.get_current_event_time()
        self.hide_menu()
        session.show_in_folder(path, ts)

    def uninstall(self, entry):
        self.hide_menu()
        session.uninstall(entry)

    def pin(self, app_id):
        if app_id not in self.pinned:
            self.pinned.append(app_id)
            self._save_pins()

    def unpin(self, app_id):
        if app_id in self.pinned:
            self.pinned.remove(app_id)
            self._save_pins()

    def move_front(self, app_id):
        if app_id in self.pinned:
            self.pinned.remove(app_id)
            self.pinned.insert(0, app_id)
            self._save_pins()

    def set_pinned(self, ids):
        seen = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        self.pinned = seen
        self._save_pins()
        return False

    def drag_started(self, app_id):
        self.drag_id = app_id
        self._dragging = True

    def drag_finished(self):
        self.drag_id = None
        self._dragging = False
        self.start._stop_scroll()
        # Während des Ziehens hält GTK die Tastatur – danach Fokus prüfen
        GLib.timeout_add(400, lambda: (self._on_active(), False)[1])

    def _save_pins(self):
        get_config().set("pinned", list(self.pinned))
        self.start.mark_dirty("pinned")
        self.start.refresh()
        if self.stack.get_visible_child_name() == "search":
            self.search.select(self.search.selected)

    def recent(self):
        if self._recent is None:
            self._recent = files.recent_files(60)
        return self._recent

    def _recent_changed(self):
        self._recent = None
        self.start.mark_dirty("recommended")

    def app_menu(self, widget, x, y, entry, pinned=False):
        items = []
        if entry.id in self.pinned:
            items.append(("pin_off_20_regular", "Vom Start lösen", lambda: self.unpin(entry.id)))
            if pinned and self.pinned and self.pinned[0] != entry.id:
                items.append((None, "Nach vorne verschieben", lambda: self.move_front(entry.id)))
        else:
            items.append(("pin_20_regular", "An Start anheften", lambda: self.pin(entry.id)))
        if taskbar.available() and not entry.is_setting:
            if taskbar.is_pinned(entry.id):
                items.append(("pin_off_20_regular", "Von Taskleiste lösen",
                              lambda: taskbar.set_pinned(entry.id, False)))
            else:
                items.append(("taskbar_pin_20_regular", "An Taskleiste anheften",
                              lambda: taskbar.set_pinned(entry.id, True)))
        acts = entry.actions()
        if acts:
            items.append(None)
            for aid, name in acts:
                items.append((None, name, lambda a=aid: self.launch_entry(entry, a)))
        if session.can_uninstall():
            items.append(None)
            items.append(("delete_20_regular", "Deinstallieren", lambda: self.uninstall(entry)))
        ui.popup_menu(self.layer, items, self.layer.anchor_of(widget, x, y))

    def file_menu(self, widget, x, y, path, uri):
        items = [("open_20_regular", "Öffnen", lambda: self.open_file(path)),
                 ("folder_open_20_regular", "Dateispeicherort öffnen", lambda: self.reveal(path)),
                 ("copy_20_regular", "Pfad kopieren", lambda: session.copy_text(path)),
                 None,
                 ("dismiss_16_regular", "Aus Liste entfernen", lambda: self._forget(uri))]
        ui.popup_menu(self.layer, items, self.layer.anchor_of(widget, x, y))

    def _forget(self, uri):
        files.forget_recent(uri)
        self._recent = None
        self.start.mark_dirty("recommended")
        self.start.refresh()

    # ------------------------------------------------------------ Suche
    def _on_query(self, entry):
        text = entry.get_text()
        self.placeholder.set_visible(not text)
        if text.strip():
            if self.stack.get_visible_child_name() != "search":
                self.stack.set_visible_child_name("search")
                self.footer.hide()
            self.search.update(text)
        else:
            if self.stack.get_visible_child_name() != "start":
                self.stack.set_visible_child_name("start")
                self.footer.show()
                self.search.reset()

    def _on_entry_activate(self, _e):
        if self.stack.get_visible_child_name() == "search":
            self.search.activate()

    # ------------------------------------------------------------ Tastatur
    def _on_key(self, _w, ev):
        key = Gdk.keyval_name(ev.keyval) or ""
        mods = ev.state & Gtk.accelerator_get_default_mod_mask()
        if key == "Escape":
            if self.layer.is_open:
                self.layer.close()
            elif self.entry.get_text():
                self.entry.set_text("")
                self.entry.grab_focus()
            else:
                self.hide_menu()
            return True
        if self.layer.is_open:
            return False
        searching = self.stack.get_visible_child_name() == "search"
        if searching and key in ("Up", "Down") and not mods:
            self.search.move(-1 if key == "Up" else 1)
            return True
        if searching and key in ("Page_Up", "Page_Down"):
            self.search.move(-5 if key == "Page_Up" else 5)
            return True
        focus = self.get_focus()
        if not searching and focus is self.entry and key == "Down":
            return self.start.first_focus()
        # Tippen irgendwo → ins Suchfeld (wie Windows)
        if focus is not self.entry and not (mods & (Gdk.ModifierType.CONTROL_MASK
                                                     | Gdk.ModifierType.MOD1_MASK)):
            ch = Gdk.keyval_to_unicode(ev.keyval)
            if ch and chr(ch).isprintable() and not chr(ch).isspace():
                self.entry.grab_focus_without_selecting()
                self.entry.set_position(-1)
                return False
            if key == "BackSpace":
                self.entry.grab_focus_without_selecting()
                self.entry.set_position(-1)
                return False
        return False

    # ------------------------------------------------------------ Zeigen
    def _geometry(self, geom):
        """Liefert (x, y, Höhe) des sichtbaren Menüs in Bildschirmkoordinaten."""
        display = Gdk.Display.get_default()
        mon = panel = None
        side, zone = "bottom", "center"
        button = None
        if geom:
            try:
                mon = [int(v) for v in geom["monitor"]][:4]
                panel = [int(v) for v in geom.get("panel", [])][:4] or None
                side = geom.get("side", "bottom")
                zone = geom.get("zone", "center")
                button = [int(v) for v in geom.get("button", [])][:4] or None
            except (KeyError, TypeError, ValueError):
                mon = None
        if mon is None or len(mon) != 4:
            seat = display.get_default_seat()
            _s, px, py = seat.get_pointer().get_position()
            m = display.get_monitor_at_point(px, py)
            g = m.get_geometry()
            mon = [g.x, g.y, g.width, g.height]
            panel = [g.x, g.y + g.height - 40, g.width, 40]
            side, zone = "bottom", "center"
        mx, my, mw, mh = mon
        if panel is None or len(panel) != 4:
            panel = [mx, my + mh - 40, mw, 40]
        px, py, pw, ph = panel
        horizontal = side in ("bottom", "top")
        avail = mh - (ph if horizontal else 0) - 2 * GAP
        h = int(max(MIN_HEIGHT, min(MAX_HEIGHT, avail * 0.8)))
        h = min(h, avail)
        w = metrics()["width"]
        if horizontal:
            if zone == "center" or button is None:
                x = mx + (mw - w) // 2
            elif zone == "right":
                x = button[0] + button[2] - w
            else:
                x = mx + GAP
            y = py - GAP - h if side == "bottom" else py + ph + GAP
        else:
            x = px + pw + GAP if side == "left" else px - GAP - w
            y = my + mh - GAP - h if button is None else button[1] + button[3] - h
        x = max(mx + GAP, min(x, mx + mw - w - GAP))
        y = max(my + GAP, min(y, my + mh - h - GAP))
        return x, y, w, h

    def toggle(self, geom=None):
        if self.get_visible():
            self.hide_menu()
            return
        if self._hidden_by_focus and time.monotonic() - self._hidden_at < 0.35:
            # Der Klick auf den Startknopf hat das Menü eben erst geschlossen
            return
        self.show_menu(geom)

    def show_menu(self, geom=None, text=None):
        theme_changed = theme.apply()
        if theme_changed:
            self.start.mark_dirty()
        x, y, w, h = self._geometry(geom)
        m = self.margin
        self._body = (x, y, w, h)
        self.set_size_request(w + 2 * m, h + 2 * m)
        self.resize(w + 2 * m, h + 2 * m)
        self.move(x - m, y - m)
        self._backdrop = self._capture(x, y, w, h) if (
            self.composited and get_config().get("acrylic")) else None

        self._recent = None
        self.layer.close()
        self.entry.set_text(text or "")
        self.stack.set_visible_child_name("search" if text else "start")
        self.footer.set_visible(not text)
        self.start.reset()
        self.start.refresh()
        self.file_index.refresh()

        self._offset = ANIM_SHIFT if self.composited else 0
        if self.composited:
            self.set_opacity(0.0)
        self.show()
        ts = GdkX11.x11_get_server_time(self.get_window())
        self.present_with_time(ts)
        self.get_window().focus(ts)
        self.entry.grab_focus_without_selecting()
        self.entry.set_position(-1)
        if self.composited:
            self._start_anim()
        self.app.emit_visibility(True)

    def hide_menu(self, by_focus=False):
        if not self.get_visible():
            return
        if self._anim:
            self.remove_tick_callback(self._anim)
            self._anim = 0
        self.layer.close()
        self.hide()
        self._hidden_at = time.monotonic()
        self._hidden_by_focus = by_focus
        self._backdrop = None
        self.app.emit_visibility(False)

    def _start_anim(self):
        if self._anim:
            self.remove_tick_callback(self._anim)
        clock_start = [None]

        def tick(_w, clock):
            now = clock.get_frame_time()
            if clock_start[0] is None:
                clock_start[0] = now
            t = min(1.0, (now - clock_start[0]) / (ANIM_MS * 1000))
            ease = 1 - (1 - t) ** 3
            self._offset = ANIM_SHIFT * (1 - ease)
            self.set_opacity(min(1.0, 0.15 + ease * 1.2))
            self.queue_draw()
            if t >= 1.0:
                self._anim = 0
                self._offset = 0
                self.set_opacity(1.0)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        self._anim = self.add_tick_callback(tick)

    def _on_active(self, *_a):
        if self.is_active() or not self.get_visible() or self._dragging:
            return
        if self._focus_check:
            GLib.source_remove(self._focus_check)

        def check():
            self._focus_check = 0
            if self.get_visible() and not self.is_active() and not self._dragging:
                self.hide_menu(by_focus=True)
            return False
        self._focus_check = GLib.timeout_add(60, check)

    # ------------------------------------------------------------ Zeichnen
    def _capture(self, x, y, w, h):
        """Bildschirm hinter dem Menü weichzeichnen (Acryl)."""
        try:
            root = Gdk.get_default_root_window()
            sw, sh = root.get_width(), root.get_height()
            cx, cy = max(0, x), max(0, y)
            cw, ch = min(w, sw - cx), min(h, sh - cy)
            if cw <= 0 or ch <= 0:
                return None
            pb = Gdk.pixbuf_get_from_window(root, cx, cy, cw, ch)
            if pb is None:
                return None
            interp = GdkPixbuf.InterpType.BILINEAR
            small = pb.scale_simple(max(1, cw // 20), max(1, ch // 20), interp)
            mid = small.scale_simple(max(1, cw // 5), max(1, ch // 5), interp)
            return mid.scale_simple(w, h, interp)
        except Exception as e:      # Acryl ist Zierde – nie daran scheitern
            print("Startklar: Hintergrund nicht lesbar:", e)
            return None

    def _apply_input_shape(self):
        gw = self.get_window()
        if gw is None or not self.composited:
            return
        a = self.get_allocation()
        m = self.margin
        region = cairo.Region(cairo.RectangleInt(m, m, max(1, a.width - 2 * m),
                                                 max(1, a.height - 2 * m)))
        self.input_shape_combine_region(region)

    def do_draw(self, cr):
        a = self.get_allocation()
        m = self.margin
        bw, bh = a.width - 2 * m, a.height - 2 * m
        p = theme.P
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.translate(0, self._offset)
        if self.composited:
            # weicher Schatten
            for i in range(m, 0, -2):
                f = 1 - i / m
                cr.set_source_rgba(0, 0, 0, 0.022 * f * f * (2.2 if theme.dark else 1.0))
                _rounded(cr, m - i, m - i + 6, bw + 2 * i, bh + 2 * i, RADIUS + i)
                cr.fill()
        _rounded(cr, m, m, bw, bh, RADIUS)
        cr.clip()
        if self._backdrop is not None:
            Gdk.cairo_set_source_pixbuf(cr, self._backdrop, m, m)
            cr.paint()
            r, g, b = p["tint"]
            cr.set_source_rgba(r, g, b, p["tint_alpha"])
            cr.paint()
        else:
            cr.set_source_rgb(*p["solid"])
            cr.paint()
        cr.reset_clip()
        cr.set_line_width(1)
        cr.set_source_rgba(*p["border"])
        _rounded(cr, m + 0.5, m + 0.5, bw - 1, bh - 1, RADIUS)
        cr.stroke()
        cr.restore()
        cr.save()
        cr.translate(0, self._offset)
        Gtk.Window.do_draw(self, cr)
        cr.restore()
        return False


def _rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
