"""Hauptanwendung: Tray, Fenster, D-Bus, Engine zusammenstecken."""

import os
import subprocess
import sys
import threading
import urllib.parse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import config, graph, rclone  # noqa: E402
from .engine import Engine  # noqa: E402
from . import marke

AUTOSTART_FILE = os.path.join(GLib.get_user_config_dir(), "autostart", "onedrive-mint.desktop")
BOOKMARKS_FILE = os.path.join(GLib.get_user_config_dir(), "gtk-3.0", "bookmarks")
ICON_DIR = os.path.join(GLib.get_user_data_dir(), "icons", "hicolor", "scalable")


class OneDriveApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=config.APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.settings = config.Settings()
        self.engine = None
        self.tray = None
        self.dbus = None
        self.activity_win = None
        self.settings_win = None
        self.wizard = None
        self.problems_win = None
        self._status_pending = False
        self._notified = set()
        self._iconed = set()
        self._pending_paths = set()
        self._changed_pending = False
        self.update_release = None      # neuere Version, falls gefunden
        self._update_checked = 0
        self._update_notified = None
        self.update_win = None
        self.add_main_option("settings", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Einstellungen öffnen", None)
        self.add_main_option("setup", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Konto hinzufügen", None)
        self.add_main_option("activity", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Aktivitätscenter öffnen", None)
        self.add_main_option("quit", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             f"{marke.NAME} beenden", None)
        self.add_main_option("sync-library", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Teams-/SharePoint-Ordner synchronisieren", None)
        self.add_main_option("odopen", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "odopen://-Link verarbeiten (Schaltfläche „Synchronisieren“)", "URI")
        self.add_main_option("check-update", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Nach Updates suchen", None)
        self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Ohne Fenster starten (Autostart)", None)

    # ------------------------------------------------------------ Start

    def do_dbus_register(self, connection, object_path):
        from .dbus_service import DBusService
        self.dbus = DBusService(self, None)
        self.dbus.register(connection)
        return Gtk.Application.do_dbus_register(self, connection, object_path)

    def do_dbus_unregister(self, connection, object_path):
        if self.dbus:
            self.dbus.unregister()
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.IconTheme.get_default().append_search_path(ICON_DIR)
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "icons", "hicolor", "scalable")
        for sub in ("apps", "status", "emblems"):
            Gtk.IconTheme.get_default().append_search_path(os.path.join(here, sub))
        Gtk.Window.set_default_icon_name("onedrive-mint")
        self.hold()
        self.engine = Engine(self.settings, self._on_engine_status, self._on_engine_changed,
                             self._on_engine_notify)
        if self.dbus:
            self.dbus.engine = self.engine
        from .tray import Tray
        self.tray = Tray(self)
        # Neuer Clientschlüssel der Herausgeber-App (branding.json) vor dem Einhängen übernehmen
        neu = rclone.sync_publisher_app([a.id for a in config.load_accounts()],
                                        marke.CLIENT_ID, marke.CLIENT_SECRET)
        if neu:
            print(f"onedrive-mint: Anmelde-App-Schlüssel in {len(neu)} Konto/Konten erneuert")
        self.engine.start()
        self.set_autostart(self.settings["autostart"])
        self._favorites_once()
        GLib.timeout_add_seconds(3, self._periodic)
        act = Gio.SimpleAction.new("show-update", None)
        act.connect("activate", lambda *_: self.show_update())
        self.add_action(act)
        # erste Suche kurz nach dem Start, danach taeglich
        GLib.timeout_add_seconds(90, self._auto_update_check)
        GLib.timeout_add_seconds(3600, self._auto_update_check)
        updated = self.settings["updated_to"]
        if updated:
            self.settings.data["updated_to"] = ""
            self.settings.save()
            GLib.timeout_add_seconds(5, self.notify, f"{marke.NAME} wurde aktualisiert",
                                     f"Version {updated} ist installiert.")

    def do_command_line(self, cmdline):
        opts = cmdline.get_options_dict().end().unpack()
        uris = [a for a in cmdline.get_arguments()[1:] if a.lower().startswith("odopen:")]
        if opts.get("odopen"):
            uris.insert(0, opts["odopen"])
        if uris:
            self.handle_odopen(uris[0])
        elif opts.get("sync_library") or opts.get("sync-library"):
            self.show_library_dialog()
        elif opts.get("check_update") or opts.get("check-update"):
            self.check_updates(manual=True)
        elif opts.get("quit"):
            self.quit_with_confirm()
        elif opts.get("settings"):
            self.show_settings()
        elif opts.get("setup"):
            self.show_wizard()
        elif opts.get("activity"):
            self.show_activity()
        elif not self.engine.runtimes:
            self.show_wizard()
        elif not opts.get("background") and cmdline.get_is_remote():
            self.show_activity()
        return 0

    def do_shutdown(self):
        if self.engine:
            self.engine.stop()
        Gtk.Application.do_shutdown(self)

    def _periodic(self):
        self.tray.update()
        return True

    # ------------------------------------------------------------ Engine-Callbacks (beliebiger Thread)

    def _on_engine_status(self):
        if not self._status_pending:
            self._status_pending = True
            GLib.timeout_add(300, self._flush_status)

    def _flush_status(self):
        self._status_pending = False
        self.tray.update()
        for rt in self.engine.runtimes.values():
            if rt.status in ("ok", "syncing") and rt.account.id not in self._iconed:
                self._iconed.add(rt.account.id)
                if not rt.account.is_library:
                    self.set_folder_icon(rt.account.mount_path)
        if self.activity_win and self.activity_win.get_visible():
            self.activity_win.refresh()
        if self.problems_win and self.problems_win.get_visible():
            self.problems_win.refresh()
        if self.settings_win and self.settings_win.get_visible():
            self.settings_win.refresh_if_changed()
        return False

    def _on_engine_changed(self, paths):
        self._pending_paths.update(paths)
        if not self._changed_pending:
            self._changed_pending = True
            GLib.timeout_add(250, self._flush_changed)

    def _flush_changed(self):
        self._changed_pending = False
        paths, self._pending_paths = sorted(self._pending_paths), set()
        if self.dbus:
            self.dbus.emit_changed(paths)
        return False

    def _on_engine_notify(self, title, body, level="info"):
        GLib.idle_add(self.notify, title, body, level)

    def notify(self, title, body, level="info"):
        if not self.settings["notifications"]:
            return False
        key = (title, body)
        if level == "error" and key in self._notified:
            return False
        self._notified.add(key)
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_icon(Gio.ThemedIcon.new("dialog-error" if level == "error" else "onedrive-mint"))
        self.send_notification(None, n)
        return False

    def error_dialog(self, title, text, parent=None):
        dlg = Gtk.MessageDialog(transient_for=parent, modal=True,
                                message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.OK, text=title)
        dlg.format_secondary_text(text)
        dlg.run()
        dlg.destroy()

    # ------------------------------------------------------------ Fenster

    def show_activity(self):
        if not self.engine.runtimes:
            self.show_wizard()
            return
        from .activity import ActivityWindow
        if not self.activity_win:
            self.activity_win = ActivityWindow(self)
            self.add_window(self.activity_win)
        self.activity_win.toggle()

    def show_problems(self):
        from .problems import ProblemsWindow
        if not self.problems_win:
            self.problems_win = ProblemsWindow(self)
            self.add_window(self.problems_win)
        self.problems_win.present_refreshed()

    def open_logs(self):
        self.open_uri(Gio.File.new_for_path(config.LOG_DIR).get_uri())

    def show_settings(self):
        from .settings_ui import SettingsWindow
        if not self.settings_win:
            self.settings_win = SettingsWindow(self)
            self.add_window(self.settings_win)
        self.settings_win.refresh_accounts()
        self.settings_win.show_all()
        self.settings_win.present()

    def show_wizard(self):
        from .wizard import SetupWizard
        if self.wizard:
            self.wizard.present()
            return
        self.wizard = SetupWizard(self)
        self.wizard.connect("destroy", lambda *_: setattr(self, "wizard", None))
        self.add_window(self.wizard)
        self.wizard.show_all()
        self.wizard.present()

    def show_library_dialog(self, url="", odopen=None):
        from .library import SyncLibraryDialog
        dlg = SyncLibraryDialog(self, url, odopen)
        self.add_window(dlg)
        dlg.present()

    def handle_odopen(self, uri):
        """Von der SharePoint/Teams-Schaltflaeche „Synchronisieren“ (odopen://sync/?...)."""
        from .library import parse_odopen
        action, params = parse_odopen(uri)
        if action == "sync" and params.get("webUrl"):
            self.show_library_dialog(odopen=params)
        else:
            self.show_activity()

    def reauth(self, account, parent=None):
        from .wizard import ReauthDialog
        ReauthDialog(self, account, parent)

    def on_accounts_changed(self):
        self.tray.update()
        if self.settings_win:
            self.settings_win.refresh_accounts()
        return False

    def quit_with_confirm(self):
        pending = self.engine.unsynced_count()
        if pending:
            dlg = Gtk.MessageDialog(modal=True, message_type=Gtk.MessageType.QUESTION,
                                    buttons=Gtk.ButtonsType.NONE,
                                    text=f"{marke.NAME} wirklich beenden?")
            dlg.format_secondary_text(
                f"{pending} Datei(en) wurden noch nicht hochgeladen. Sie werden beim nächsten "
                f"Start von {marke.NAME} hochgeladen. Bis dahin sind die Dateien im OneDrive-Ordner "
                "nicht verfügbar.")
            dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                            f"{marke.NAME} beenden", Gtk.ResponseType.OK)
            res = dlg.run()
            dlg.destroy()
            if res != Gtk.ResponseType.OK:
                return
        for w in list(self.get_windows()):
            w.hide()
        self.release()
        self.quit()

    # ------------------------------------------------------------ Updates

    def _auto_update_check(self):
        import time
        from . import updater
        if updater.kit_verwaltet():
            return True                       # Updates kommen über das Style Kit
        if self.settings["update_check"] and time.time() - self._update_checked > 23 * 3600:
            self.check_updates()
        return True

    def check_updates(self, manual=False, done=None):
        """Im Hintergrund suchen. done(release, newer, error) laeuft im Hauptthread."""
        import time
        from . import updater
        self._update_checked = time.time()

        def work():
            try:
                rel, newer = updater.check()
                err = None
            except updater.KitVerwaltet as e:
                GLib.idle_add(self._kit_hinweis, str(e), manual, done)
                return
            except updater.UpdateError as e:
                rel, newer, err = None, False, str(e)
            GLib.idle_add(self._update_checked_cb, rel, newer, err, manual, done)
        threading.Thread(target=work, daemon=True).start()

    def _kit_hinweis(self, text, manual, done):
        """Updates kommen über ein Style Kit: nur bei manueller Suche Bescheid geben."""
        from . import updater
        self.set_update(None)
        if done:
            done(None, False, text)
        elif manual:
            dlg = Gtk.MessageDialog(modal=True, message_type=Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.NONE, text=text)
            dlg.format_secondary_text(f"Installiert ist Version {config.VERSION}.")
            befehl = updater.kit_befehl()
            if befehl:
                dlg.add_button(f"{updater.kit_verwaltet()} öffnen", 1)
            dlg.add_button("OK", Gtk.ResponseType.OK)
            if dlg.run() == 1 and befehl:
                try:
                    subprocess.Popen(befehl, start_new_session=True)
                except OSError:
                    pass
            dlg.destroy()
        return False

    def _update_checked_cb(self, rel, newer, err, manual, done):
        if err is None:
            skipped = not manual and rel and rel.version == self.settings["update_skip"]
            self.set_update(rel if newer and not skipped else None)
            if newer and not manual and not skipped and self._update_notified != rel.version:
                self._update_notified = rel.version
                n = Gio.Notification.new(f"Update für {marke.NAME} verfügbar")
                n.set_body(f"Version {rel.version} kann installiert werden "
                           f"(installiert: {config.VERSION}).")
                n.set_icon(Gio.ThemedIcon.new("onedrive-mint"))
                n.set_default_action("app.show-update")
                n.add_button("Anzeigen", "app.show-update")
                self.send_notification("update", n)
        if done:
            done(rel, newer, err)
        elif manual:
            if newer:
                self.show_update()
            else:
                dlg = Gtk.MessageDialog(modal=True, buttons=Gtk.ButtonsType.OK,
                                        message_type=Gtk.MessageType.ERROR if err
                                        else Gtk.MessageType.INFO,
                                        text="Suche fehlgeschlagen" if err
                                        else f"{marke.NAME} ist auf dem neuesten Stand")
                dlg.format_secondary_text(err or f"Installiert ist Version {config.VERSION}.")
                dlg.run()
                dlg.destroy()
        return False

    def set_update(self, rel):
        self.update_release = rel
        self.tray.update()

    def show_update(self):
        if not self.update_release:
            self.check_updates(manual=True)
            return
        from .update_ui import UpdateDialog
        if self.update_win:
            self.update_win.present()
            return
        self.update_win = UpdateDialog(self, self.update_release)
        self.update_win.connect("destroy", lambda *_: setattr(self, "update_win", None))
        self.add_window(self.update_win)
        self.update_win.present()

    def restart_after_update(self, version, extension_changed):
        """Neue Version ist installiert: beenden und vom Neustart-Helfer neu starten lassen."""
        from . import updater
        self.settings.data["updated_to"] = version
        self.settings.save()
        if extension_changed:
            # Nemo/Nautilus laden die neue Erweiterung erst nach einem Neustart
            for fm in ("nemo", "nautilus"):
                if GLib.find_program_in_path(fm):
                    subprocess.Popen([fm, "-q"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        updater.schedule_restart()
        for w in list(self.get_windows()):
            w.hide()
        self.release()
        self.quit()

    # ------------------------------------------------------------ Aktionen

    def _rt(self, acc_id):
        return self.engine.runtimes.get(acc_id)

    def open_uri(self, uri):
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            subprocess.Popen(["xdg-open", uri])

    def open_path(self, path):
        self.open_uri(Gio.File.new_for_path(path).get_uri())

    def open_folder(self, acc_id):
        rt = self._rt(acc_id)
        if rt:
            self.open_uri(Gio.File.new_for_path(rt.account.mount_path).get_uri())

    def open_web(self, acc_id):
        rt = self._rt(acc_id)
        if rt:
            self.open_uri(rt.account.web_url or "https://onedrive.live.com/")

    def open_recycle_bin(self, acc_id):
        rt = self._rt(acc_id)
        if rt:
            self.open_uri(graph.recycle_bin_url(rt.account.kind, rt.account.web_url,
                                                rt.account.site_url))

    def reveal(self, path):
        """Datei im Dateimanager anzeigen und markieren."""
        uri = Gio.File.new_for_path(path).get_uri()
        try:
            bus = self.get_dbus_connection()
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([uri], "")), None, Gio.DBusCallFlags.NONE,
                          3000, None)
        except GLib.Error:
            self.open_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri())

    def open_online(self, path):
        def work():
            url = self.engine.web_url(path)
            GLib.idle_add(self.open_uri, url or "https://onedrive.live.com/")
        threading.Thread(target=work, daemon=True).start()

    def share(self, path):
        def work():
            try:
                url = self.engine.share_link(path)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self.notify, "Freigeben fehlgeschlagen", str(e), "error")
                return
            GLib.idle_add(self._copy_link, os.path.basename(path), url)
        threading.Thread(target=work, daemon=True).start()

    def _copy_link(self, name, url):
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.set_text(url, -1)
        cb.store()
        self.notify("Link kopiert", f"Der Link zu „{name}“ wurde in die Zwischenablage kopiert.")
        return False

    # ------------------------------------------------------------ Desktop-Integration

    def set_autostart(self, enabled):
        if enabled:
            os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
            exe = os.path.expanduser("~/.local/bin/onedrive-mint")
            if not os.path.exists(exe):
                exe = f"{sys.executable} -m onedrive_mint"
            with open(AUTOSTART_FILE, "w") as f:
                f.write(f"[Desktop Entry]\nType=Application\nName={marke.NAME}\n"
                        f"Exec={exe} --background\nIcon=onedrive-mint\n"
                        "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\n"
                        f"NoDisplay=false\nComment=Synchronisierung mit Microsoft OneDrive\n")
        else:
            try:
                os.unlink(AUTOSTART_FILE)
            except OSError:
                pass

    @staticmethod
    def _favorite(path, add):
        """Favoriten von Nemo/Cinnamon (XApp): in der Seitenleiste mit Wolken-/Firmensymbol."""
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ValueError, ImportError):
            return
        fav = XApp.Favorites.get_default()
        uri = Gio.File.new_for_path(path).get_uri()
        known = fav.find_by_uri(uri) is not None
        if add and not known:
            fav.add(uri)
        elif not add and known:
            fav.remove(uri)

    def _favorites_once(self):
        """Bis 1.1 waren die Ordner als Laufwerke in der Seitenleiste; jetzt einmalig als Favoriten."""
        if self.settings["favorites_added"]:
            return
        paths = []
        for rt in self.engine.runtimes.values():
            p = rt.account.mount_path
            p = os.path.dirname(p) if rt.account.is_library else p
            if p not in paths:
                paths.append(p)
        for p in paths:
            self._favorite(p, True)
        self.settings["favorites_added"] = True

    def add_bookmark(self, path, label):
        self._favorite(path, True)
        uri = Gio.File.new_for_path(path).get_uri()
        try:
            with open(BOOKMARKS_FILE) as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        if any(l.split(" ", 1)[0] == uri for l in lines):
            return
        lines.append(f"{uri} {label}")
        os.makedirs(os.path.dirname(BOOKMARKS_FILE), exist_ok=True)
        with open(BOOKMARKS_FILE, "w") as f:
            f.write("\n".join(lines) + "\n")

    def remove_bookmark(self, path):
        self._favorite(path, False)
        uri = Gio.File.new_for_path(path).get_uri()
        try:
            with open(BOOKMARKS_FILE) as f:
                lines = f.read().splitlines()
            with open(BOOKMARKS_FILE, "w") as f:
                f.write("\n".join(l for l in lines if l.split(" ", 1)[0] != uri) + "\n")
        except OSError:
            pass
        return False

    def set_folder_icon(self, path, name="odm-folder"):
        icon = os.path.join(ICON_DIR, "apps", name + ".svg")
        if os.path.exists(icon):
            subprocess.run(["gio", "set", path, "metadata::custom-icon",
                            "file://" + urllib.parse.quote(icon)], capture_output=True)


def main():
    GLib.set_prgname("onedrive-mint")
    GLib.set_application_name(marke.NAME)
    app = OneDriveApp()
    return app.run(sys.argv)
