"""Aktivitaetscenter – das Flyout, das sich beim Klick auf das Tray-Symbol oeffnet."""

import os
import time

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import errors
from .engine import human_size
from . import marke

CSS = b"""
.odm-flyout { background-color: @theme_base_color; }
.odm-header { padding: 10px 12px; }
.odm-title { font-weight: bold; font-size: 115%; }
.odm-status { padding: 8px 12px; background-color: alpha(@theme_selected_bg_color, 0.10); }
.odm-row { padding: 6px 12px; }
.odm-sub { opacity: 0.65; font-size: 90%; }
.odm-footer { padding: 8px 12px; border-top: 1px solid alpha(@theme_fg_color, 0.12); }
.odm-empty { padding: 40px 20px; opacity: 0.7; }
"""

STATUS_ICONS = {
    "ok": "emblem-odm-pinned",
    "syncing": "emblem-odm-sync",
    "paused": "media-playback-pause-symbolic",
    "error": "emblem-odm-error",
    "warning": "dialog-warning",
    "offline": "network-offline-symbolic",
    "setup": "odm-tray-setup-symbolic",
}

KIND_TEXT = {
    "upload": "Hochgeladen",
    "download": "Heruntergeladen",
    "delete": "Gelöscht",
    "trash": "Gelöscht (OneDrive-Papierkorb)",
    "error": "Fehler",
}


def ago(ts):
    d = time.time() - ts
    if d < 60:
        return "gerade eben"
    if d < 3600:
        return f"vor {int(d // 60)} Min."
    if d < 86400:
        return f"vor {int(d // 3600)} Std."
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def file_icon(name, is_dir=False):
    if is_dir:
        return Gio.ThemedIcon.new("folder")
    ctype, _ = Gio.content_type_guess(name, None)
    return Gio.content_type_get_icon(ctype)


class ActivityWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title=marke.NAME)
        self.app = app
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_default_size(380, 540)
        self.set_icon_name("onedrive-mint")
        self.get_style_context().add_class("odm-flyout")
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", lambda *_: self.hide() or True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame = Gtk.Frame()
        frame.add(outer)
        self.add(frame)

        # Kopfzeile
        header = Gtk.Box(spacing=6)
        header.get_style_context().add_class("odm-header")
        title = Gtk.Label(label=marke.NAME, xalign=0)
        title.get_style_context().add_class("odm-title")
        header.pack_start(Gtk.Image.new_from_icon_name("onedrive-mint", Gtk.IconSize.LARGE_TOOLBAR),
                          False, False, 0)
        header.pack_start(title, True, True, 0)
        for icon, tip, cb in (("folder-symbolic", "Ordner öffnen", self._open_folder),
                              ("web-browser-symbolic", "Online anzeigen", self._open_web),
                              ):
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, c=cb: c())
            header.pack_start(b, False, False, 0)
        self.gear = Gtk.MenuButton()
        self.gear.set_relief(Gtk.ReliefStyle.NONE)
        self.gear.set_tooltip_text("Hilfe und Einstellungen")
        self.gear.add(Gtk.Image.new_from_icon_name("emblem-system-symbolic", Gtk.IconSize.BUTTON))
        header.pack_start(self.gear, False, False, 0)
        outer.pack_start(header, False, False, 0)

        # Statuszeile – bei Problemen anklickbar (oeffnet „Synchronisierungsprobleme“)
        status = Gtk.Box(spacing=8)
        status.get_style_context().add_class("odm-status")
        self.status_icon = Gtk.Image()
        self.status_label = Gtk.Label(xalign=0)
        self.status_label.set_line_wrap(True)
        self.status_more = Gtk.Label()
        self.status_more.set_markup("<b>Anzeigen ›</b>")
        self.status_more.set_no_show_all(True)
        status.pack_start(self.status_icon, False, False, 0)
        status.pack_start(self.status_label, True, True, 0)
        status.pack_start(self.status_more, False, False, 0)
        self.status_click = Gtk.EventBox()
        self.status_click.add(status)
        self.status_click.connect("button-release-event", self._on_status_click)
        outer.pack_start(self.status_click, False, False, 0)
        self._has_problems = False

        # Aktivitaetsliste
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", self._on_row)
        empty = Gtk.Label(label="Keine aktuellen Aktivitäten.\nNeue oder geänderte Dateien "
                                "erscheinen hier.", justify=Gtk.Justification.CENTER)
        empty.get_style_context().add_class("odm-empty")
        empty.show()
        self.listbox.set_placeholder(empty)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.listbox)
        outer.pack_start(sw, True, True, 0)

        # Fusszeile: Speicherplatz pro Konto
        self.footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.footer.get_style_context().add_class("odm-footer")
        outer.pack_start(self.footer, False, False, 0)

        self._timer = None
        self._sig = None

    # ------------------------------------------------------------------

    def _on_status_click(self, *_):
        if self._has_problems:
            self.hide()
            self.app.show_problems()
        return True

    def _on_focus_out(self, *_):
        # Zahnrad-Menue nimmt kurz den Fokus – dann nicht schliessen
        if not self.gear.get_active():
            self._hidden_at = time.time()
            self.hide()
        return False

    def toggle(self):
        # Klick aufs Tray-Symbol bei offenem Flyout: Fokusverlust hat es gerade geschlossen
        if self.get_visible() or time.time() - getattr(self, "_hidden_at", 0) < 0.3:
            self.hide()
        else:
            self.popup()

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.hide()

    def popup(self):
        self._build_gear_menu()
        self.refresh(force=True)
        self._position()
        self.show_all()
        self.present()
        if not self._timer:
            self._timer = GLib.timeout_add(1000, self._tick)

    def _tick(self):
        if not self.get_visible():
            self._timer = None
            return False
        self.refresh()
        return True

    def _position(self):
        display = Gdk.Display.get_default()
        if "Wayland" in type(display).__name__:
            # Wayland erlaubt keine Fensterpositionierung durch das Programm
            self.set_position(Gtk.WindowPosition.CENTER)
            return
        seat = display.get_default_seat()
        _, x, y = seat.get_pointer().get_position()
        mon = display.get_monitor_at_point(x, y).get_workarea()
        w, h = self.get_default_size()
        px = min(max(mon.x + 8, x - w // 2), mon.x + mon.width - w - 8)
        py = y - h - 12 if y > mon.y + mon.height / 2 else y + 12
        py = min(max(mon.y + 8, py), mon.y + mon.height - h - 8)
        self.move(px, py)

    def _build_gear_menu(self):
        app = self.app
        menu = Gtk.Menu()
        paused = app.settings["paused"]
        entries = [
            ("Synchronisierung fortsetzen" if paused else "Synchronisierung anhalten",
             lambda: app.engine.set_paused(not paused)),
            ("Jetzt synchronisieren", app.engine.sync_now),
            ("Synchronisierungsprobleme anzeigen", app.show_problems),
            ("Papierkorb (online) öffnen", self._open_recycle),
            None,
            ("Einstellungen", app.show_settings),
            ("Konto hinzufügen …", app.show_wizard),
            ("Teams-/SharePoint-Ordner synchronisieren …", app.show_library_dialog),
            None,
            (f"{marke.NAME} beenden", app.quit_with_confirm),
        ]
        for e in entries:
            if e is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            it = Gtk.MenuItem.new_with_label(e[0])
            it.connect("activate", lambda _i, cb=e[1]: (self.hide(), cb()))
            menu.append(it)
        menu.show_all()
        self.gear.set_popup(menu)

    def _first_account(self):
        rts = list(self.app.engine.runtimes.values())
        return rts[0].account.id if rts else None

    def _open_folder(self):
        acc = self._first_account()
        if acc:
            self.app.open_folder(acc)
        self.hide()

    def _open_web(self):
        acc = self._first_account()
        if acc:
            self.app.open_web(acc)
        self.hide()

    def _open_recycle(self):
        for rt in self.app.engine.runtimes.values():
            if not rt.account.is_library:
                self.app.open_recycle_bin(rt.account.id)

    def _on_row(self, _lb, row):
        path = getattr(row, "odm_path", None)
        if path:
            self.app.reveal(path)
            self.hide()

    # ------------------------------------------------------------------

    def refresh(self, force=False):
        eng = self.app.engine
        state, text = eng.summary()
        self.status_icon.set_from_icon_name(STATUS_ICONS.get(state, "dialog-information"),
                                            Gtk.IconSize.LARGE_TOOLBAR)
        self.status_label.set_text(text)
        self._has_problems = bool(eng.problems())
        self.status_more.set_visible(self._has_problems)
        win = self.status_click.get_window()
        if win:
            win.set_cursor(Gdk.Cursor.new_from_name(win.get_display(), "pointer")
                           if self._has_problems else None)
        self.status_click.set_tooltip_text(
            "Synchronisierungsprobleme anzeigen" if self._has_problems else None)

        transfers = []
        for rt in eng.runtimes.values():
            for t in rt.transfers:
                transfers.append((rt, t))
        acts = list(eng.activity)[:40]
        sig = (tuple((rt.account.id, t.get("name"), t.get("bytes")) for rt, t in transfers),
               tuple((a.rel, a.kind, a.ts) for a in acts), int(time.time() // 30))
        if sig != self._sig or force:
            self._sig = sig
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            for rt, t in transfers:
                self._add_transfer_row(rt, t)
            for a in acts:
                rt = eng.runtimes.get(a.acc_id)
                if rt:
                    self._add_activity_row(rt, a)
            self.listbox.show_all()
        self._refresh_footer()

    def _row(self, icon, name, sub, path=None, progress=None, sub_icon=None):
        row = Gtk.ListBoxRow()
        row.odm_path = path
        box = Gtk.Box(spacing=10)
        box.get_style_context().add_class("odm-row")
        img = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DND)
        box.pack_start(img, False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        n = Gtk.Label(label=name, xalign=0)
        n.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        v.pack_start(n, False, False, 0)
        if progress is not None:
            pb = Gtk.ProgressBar()
            pb.set_fraction(progress)
            v.pack_start(pb, False, False, 2)
        s = Gtk.Label(label=sub, xalign=0)
        s.set_ellipsize(Pango.EllipsizeMode.END)
        s.get_style_context().add_class("odm-sub")
        v.pack_start(s, False, False, 0)
        box.pack_start(v, True, True, 0)
        if sub_icon:
            box.pack_start(Gtk.Image.new_from_icon_name(sub_icon, Gtk.IconSize.MENU),
                           False, False, 0)
        row.add(box)
        self.listbox.add(row)

    def _add_transfer_row(self, rt, t):
        name = t.get("name", "")
        size, done = t.get("size") or 0, t.get("bytes") or 0
        frac = done / size if size else 0
        direction = "Wird hochgeladen" if name in rt._recent_uploads else "Wird synchronisiert"
        sub = f"{direction} – {human_size(done)} von {human_size(size)}"
        if t.get("speed"):
            sub += f" ({human_size(t['speed'])}/s)"
        self._row(file_icon(name), os.path.basename(name), sub, rt.abs(name), frac,
                  "emblem-odm-sync")

    def _add_activity_row(self, rt, a):
        name = os.path.basename(a.rel) or a.rel
        folder = os.path.dirname(a.rel)
        where = folder.split("/")[-1] if folder else rt.account.label
        if a.kind == "error":
            sub = errors.short(a.error)
            icon = "emblem-odm-error"
        else:
            sub = f"{KIND_TEXT.get(a.kind, a.kind)} · {where} · {ago(a.ts)}"
            icon = None
        path = rt.abs(a.rel) if a.kind not in ("delete", "trash") else None
        self._row(file_icon(name), name, sub, path, sub_icon=icon)

    def _refresh_footer(self):
        for c in self.footer.get_children():
            self.footer.remove(c)
        for rt in self.app.engine.runtimes.values():
            if rt.account.is_library and not rt.error:
                continue      # Bibliotheken: Speicher gehoert zur Website, nicht zum Benutzer
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lab = Gtk.Label(xalign=0)
            if rt.quota and rt.quota[1]:
                used, total = rt.quota
                lab.set_text(f"{rt.account.label}: {human_size(used)} von "
                             f"{human_size(total)} verwendet")
                bar = Gtk.LevelBar()
                bar.set_value(min(1.0, used / total))
                box.pack_start(lab, False, False, 0)
                box.pack_start(bar, False, False, 0)
            else:
                lab.set_text(rt.account.label + (f" – {rt.error}" if rt.error else ""))
                lab.set_line_wrap(True)
                box.pack_start(lab, False, False, 0)
            lab.get_style_context().add_class("odm-sub")
            self.footer.pack_start(box, False, False, 0)
        self.footer.show_all()
