#!/usr/bin/env python3
"""Testet das Hauptfenster: python3 dev/test_window.py OUT.png [szenario]

Szenarien: tabs (Standard), folder:<pfad>, menu-view, menu-new, light
FILEFENSTER_CONFIG_DIR für Tests auf einen Scratchpad-Ordner setzen.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from filefenster import devtools, places, theme  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402

out = sys.argv[1]
scen = sys.argv[2] if len(sys.argv) > 2 else "tabs"
if scen == "light":
    from filefenster.config import get_config
    get_config()._data["theme"] = "light"


class TestApp(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        start = places.HOME
        if scen.startswith("folder:"):
            start = places.uri_for_path(scen.split(":", 1)[1])
        win = ExplorerWindow(self, start)
        win.resize(1280, 800)
        win.present()
        if scen in ("tabs", "light"):
            win.new_tab(places.uri_for_path(places.home_dir()), select=False)
            win.new_tab(places.THISPC, select=False)
        if scen.startswith("menu-"):
            def popup():
                if scen == "menu-view":
                    win._on_view_menu(win.btn_view)
                elif scen == "menu-new":
                    win._on_new_menu(win.btn_new)
                elif scen == "menu-more":
                    win._on_more_menu(win.btn_more)
                return False
            GLib.timeout_add(1200, popup)

        def snap():
            # ganzer Bildschirmbereich des Fensters inkl. Menüs
            import subprocess
            x, y = win.get_window().get_origin()[1:]
            w, h = win.get_window().get_width(), win.get_window().get_height()
            subprocess.run(["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", out])
            print("snap", out)
            self.quit()
            return False
        GLib.timeout_add(int(os.environ.get("DELAY", "2600")), snap)


TestApp().run([sys.argv[0]])
