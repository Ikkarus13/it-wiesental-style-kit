"""Gtk.Application: eine Instanz, jedes Öffnen = neues Fenster (wie Windows)."""
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION, devtools, places, theme  # noqa: E402


class FileFensterApp(Gtk.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.HANDLES_OPEN
        if os.environ.get("FILEFENSTER_NO_UNIQUE") == "1":
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        GLib.set_application_name(APP_NAME)
        self._themed = False

    def _ensure_theme(self):
        if not self._themed:
            theme.setup()
            self._themed = True

    def _new_window(self, loc):
        self._ensure_theme()
        from .window import ExplorerWindow
        win = ExplorerWindow(self, loc)
        win.present()
        devtools.maybe_snapshot_from_env(win)
        return win

    def do_shutdown(self):
        try:   # geöffnete Archive nicht als gvfs-Mount zurücklassen
            from . import archive
            archive.unmount_all()
        except Exception as exc:
            print("[filefenster] Archive aushängen:", exc)
        Gtk.Application.do_shutdown(self)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        from . import update_ui
        update_ui.manager(self)     # tägliche Update-Suche (nur installierte Kopie)

    def do_activate(self):
        from . import update_ui
        if update_ui.restore_session(self):
            return                  # Neustart nach Update: Fenster wie vorher
        start = os.environ.get("FILEFENSTER_START")
        if not start:
            # Ordneroptionen: „Datei-Explorer öffnen für“ Start oder Dieser PC
            from .folderoptions import start_location
            start = start_location()
        self._new_window(start or places.HOME)

    def do_open(self, files, n_files, hint):
        # Ordner → neues Fenster; Dateien → übergeordneter Ordner mit der Datei
        # ausgewählt (wie "explorer /select,…"), je Ordner nur ein Fenster
        reveal = {}
        for f in files:
            if places.is_remote(f.get_uri()):
                # Netzwerkort: nicht im Hauptthread abfragen (ein hängender Server
                # würde alle Fenster einfrieren) – das Fenster kommt mit der Antwort
                self.hold()
                f.query_info_async("standard::type", Gio.FileQueryInfoFlags.NONE,
                                   GLib.PRIORITY_DEFAULT, None, self._open_remote_done, None)
                continue
            try:
                info = f.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
                is_dir = info.get_file_type() in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE)
            except GLib.Error:
                is_dir = True
            parent = f.get_parent()
            if is_dir or parent is None:
                self._new_window(f.get_uri())
            else:
                reveal.setdefault(parent.get_uri(), []).append(f.get_uri())
        for loc, uris in reveal.items():
            self._reveal_in_new_window(loc, uris)

    def _reveal_in_new_window(self, loc, uris):
        win = self._new_window(loc)
        fv = getattr(win.current, "views", {}).get("folder")
        if fv is not None and hasattr(fv, "reveal"):
            fv.reveal(uris)

    def _open_remote_done(self, f, res, _data):
        try:
            try:
                info = f.query_info_finish(res)
                is_dir = info.get_file_type() in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE)
            except GLib.Error:
                is_dir = True        # z. B. noch nicht eingehängt: als Ort öffnen
            parent = f.get_parent()
            if is_dir or parent is None:
                self._new_window(f.get_uri())
            else:
                self._reveal_in_new_window(parent.get_uri(), [f.get_uri()])
        finally:
            self.release()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print(f"{APP_NAME} {VERSION}")
        return 0
    app = FileFensterApp()
    return app.run(argv)
