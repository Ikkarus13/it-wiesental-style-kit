"""Hilfen für Tests: Fenster-Schnappschuss als PNG.

    from filefenster import devtools
    devtools.snapshot_after(window, "/pfad/bild.png", delay_ms=1500, quit=True)

Umgebungsvariablen beim Start der App:
    FILEFENSTER_SNAPSHOT=/pfad/bild.png   Schnappschuss nach FILEFENSTER_SNAPSHOT_DELAY ms
    FILEFENSTER_SNAPSHOT_QUIT=1           danach beenden
"""
import os

from gi.repository import Gdk, GLib, Gtk


def save_window_png(window, path):
    gdkwin = window.get_window()
    if gdkwin is None:
        return False
    w, h = gdkwin.get_width(), gdkwin.get_height()
    pb = Gdk.pixbuf_get_from_window(gdkwin, 0, 0, w, h)
    if pb is None:
        return False
    pb.savev(path, "png", [], [])
    return True


def snapshot_after(window, path, delay_ms=1500, quit=False, callback=None):
    def _do():
        ok = save_window_png(window, path)
        print(f"[filefenster] Schnappschuss {'gespeichert' if ok else 'FEHLGESCHLAGEN'}: {path}")
        if callback:
            callback()
        if quit:
            app = window.get_application() if hasattr(window, "get_application") else None
            if app:
                app.quit()
            else:
                Gtk.main_quit()
        return False
    GLib.timeout_add(delay_ms, _do)


def maybe_snapshot_from_env(window):
    path = os.environ.get("FILEFENSTER_SNAPSHOT")
    if path:
        delay = int(os.environ.get("FILEFENSTER_SNAPSHOT_DELAY", "1800"))
        snapshot_after(window, path, delay, quit=os.environ.get("FILEFENSTER_SNAPSHOT_QUIT") == "1")
