"""Hintergrunddienst: hält das Menü bereit und nimmt D-Bus-Aktionen an.

Aktionen (org.gtk.Actions auf /org/startklar/App):
    toggle(s)   Menü zeigen/verbergen; s = JSON mit Monitor/Panel vom Applet
    show(s)     zeigen
    search(s)   zeigen und Suchtext setzen (JSON: {"text": …, …})
    hide        verbergen
    quit        beenden
Signal org.startklar.Menu.VisibilityChanged(b) – für den Startknopf im Panel.
"""
import json
import os
import threading
import time

from gi.repository import Gio, GLib, Gtk

from . import APP_ID, DBUS_PATH, SIGNAL_IFACE, VERSION, theme, updater
from .config import get_config
from .apps import AppIndex
from .files import FileIndex


def _parse(param):
    if param is None:
        return None
    try:
        data = json.loads(param.get_string() or "null")
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


class App(Gtk.Application):
    def __init__(self, initial=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None
        self.initial = initial          # (aktion, json) nach dem Start ausführen

    def do_startup(self):
        Gtk.Application.do_startup(self)
        try:
            os.chdir(GLib.get_home_dir())
        except OSError:
            pass
        theme.load_fonts()
        theme.apply()
        self.index = AppIndex()
        self.file_index = FileIndex(os.environ.get("STARTKLAR_INDEX_ROOT") or None)
        from .window import MenuWindow
        self.win = MenuWindow(self, self.index, self.file_index)
        for name, ptype, cb in (
                ("toggle", "s", lambda p: self.win.toggle(_parse(p))),
                ("show", "s", lambda p: self.win.show_menu(_parse(p))),
                ("search", "s", self._search),
                ("hide", None, lambda p: self.win.hide_menu()),
                ("check-update", None, lambda p: self.check_update(manual=True)),
                ("install-update", None, lambda p: self.install_update()),
                ("open-kit", None, lambda p: updater.kit_oeffnen()),
                ("settings", None, lambda p: self.open_settings()),
                ("quit", None, lambda p: self.quit())):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            act.connect("activate", lambda _a, p, cb=cb: cb(p))
            self.add_action(act)
        self.hold()
        # Dateiindex erst aufbauen, wenn die Anmeldung vorbei ist
        GLib.timeout_add_seconds(int(os.environ.get("STARTKLAR_INDEX_DELAY", "30")),
                                 lambda: self.file_index.refresh() and False)
        # Update-Suche: täglich still im Hintergrund (Punkt am Ein/Aus-Knopf)
        if not os.environ.get("STARTKLAR_NO_UPDATE_CHECK"):
            GLib.timeout_add_seconds(120, self._auto_check)
            GLib.timeout_add_seconds(6 * 3600, self._auto_check)
        self._release = None
        self._busy = False

    # ------------------------------------------------------------ Updates
    def _auto_check(self):
        cfg = get_config()
        if updater.kit_verwaltet():         # Updates kommen über das Style Kit
            self.win.set_update(None)
            return True
        if cfg.get("update_check") and time.time() - cfg.get("update_checked") > 20 * 3600:
            self.check_update(manual=False)
        return True

    def check_update(self, manual):
        if self._busy:
            return
        kit = updater.kit_verwaltet()
        if kit:
            self._release = None
            self.win.set_update(None)
            if manual:
                self.notify_kit(kit)
            return
        self._busy = True

        def work():
            try:
                rel, err = updater.check(), None
            except updater.UpdateError as e:
                rel, err = None, str(e)
            GLib.idle_add(done, rel, err)

        def done(rel, err):
            self._busy = False
            if err is None:
                get_config().set("update_checked", int(time.time()))
            self._release = rel
            self.win.set_update(rel["version"] if rel else None)
            if manual:
                if err:
                    self.notify("Update-Suche fehlgeschlagen", err)
                elif rel:
                    self.notify(f"Startklar {rel['version']} ist verfügbar",
                                f"Installiert ist {VERSION}.", "Jetzt aktualisieren")
                else:
                    self.notify("Startklar ist auf dem neuesten Stand", f"Version {VERSION}")
            return False
        threading.Thread(target=work, daemon=True).start()

    def install_update(self):
        kit = updater.kit_verwaltet()
        if kit:
            self._release = None
            self.win.set_update(None)
            self.notify_kit(kit)
            return
        rel = self._release
        if rel is None or self._busy:
            self.check_update(manual=True)
            return
        self._busy = True
        self.notify("Startklar wird aktualisiert …", f"Version {rel['version']}")

        def work():
            try:
                updater.install(rel)
                err = None
            except updater.UpdateError as e:
                err = str(e)
            except OSError as e:
                err = str(e)
            GLib.idle_add(done, err)

        def done(err):
            self._busy = False
            if err:
                self.notify("Update fehlgeschlagen", err)
            return False
        threading.Thread(target=work, daemon=True).start()

    def notify_kit(self, kit):
        has_cmd = bool((updater.kit_info() or {}).get("verwalten"))
        self.notify(updater.kit_meldung(kit), f"Startklar {VERSION}",
                    f"{kit} öffnen" if has_cmd else None, "app.open-kit")

    def notify(self, title, body, button=None, action="app.install-update"):
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "start.svg"))))
        if button:
            n.add_button(button, action)
        self.send_notification("startklar-update", n)

    def open_settings(self):
        from .settings import SettingsWindow
        if getattr(self, "_settings_win", None) is None:
            self._settings_win = SettingsWindow(self)
            self._settings_win.connect("destroy", lambda *_a: setattr(self, "_settings_win", None))
        self._settings_win.show_all()
        self._settings_win.present()

    def _search(self, param):
        data = _parse(param) or {}
        self.win.show_menu(data, text=str(data.get("text") or ""))

    def do_activate(self):
        if self.initial:
            name, arg = self.initial
            self.initial = None
            self.activate_action(name, GLib.Variant("s", arg) if arg is not None else None)

    def emit_visibility(self, visible):
        conn = self.get_dbus_connection()
        if conn is None:
            return
        try:
            conn.emit_signal(None, DBUS_PATH, SIGNAL_IFACE, "VisibilityChanged",
                             GLib.Variant("(b)", (bool(visible),)))
        except GLib.Error:
            pass
