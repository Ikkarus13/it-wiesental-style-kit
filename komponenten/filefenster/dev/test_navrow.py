#!/usr/bin/env python3
"""Test-Harness für die Adresszeile (NavRow).

    timeout 20 python3 dev/test_navrow.py AUSGABE.png [--edit] [--menu N] [--history]

Zeigt mehrere NavRows untereinander (tiefer Pfad, Start, /usr/share/…, Dieser PC,
schmale Zeile mit Überlauf, Suchtext) und speichert einen Schnappschuss.
--edit     : erste Zeile in den Bearbeitungsmodus
--menu N   : Chevron-Menü der N-ten Brotkrume (erste Zeile) öffnen und den
             Bildschirmbereich inkl. Menü speichern (AUSGABE-menu.png)
--complete : Bearbeitungsmodus + "/usr/sh" tippen (Vervollständigung)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_CONFIG_DIR",
                      os.path.join(os.environ.get("TMPDIR", "/tmp"), "filefenster-test-config"))

import gi  # noqa: E402
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from filefenster import devtools, places, theme  # noqa: E402
from filefenster.host import FakeHost  # noqa: E402
from filefenster.navrow import NavRow  # noqa: E402


class Host(FakeHost):
    def __init__(self, window, loc):
        super().__init__(window, loc)
        self.row = None

    def navigate(self, loc):
        super().navigate(loc)
        if self.row:
            self.row.set_location(loc)


def screen_shot(path, x, y, w, h):
    root = Gdk.get_default_root_window()
    pb = Gdk.pixbuf_get_from_window(root, x, y, w, h)
    if pb:
        pb.savev(path, "png", [], [])
        print("Bildschirmausschnitt gespeichert:", path)


def main():
    out = sys.argv[1]
    args = sys.argv[2:]
    theme.setup()
    css = Gtk.CssProvider()
    css.load_from_data(f"""
        .test-toolbar {{ background-color: {theme.P['toolbar_bg']}; }}
        .test-label {{ color: {theme.P['text3']}; padding: 2px 10px; }}
    """.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 900)

    win = Gtk.Window(title="NavRow-Test")
    win.get_style_context().add_class("filefenster")
    win.set_default_size(1100, 10)
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    win.add(vbox)

    home = places.home_dir()
    deep = os.path.join(home, "Claude", "filefenster", "filefenster", "style")
    if not os.path.isdir(deep):
        deep = home
    docs = [d for d in places.special_dirs() if d["key"] == "documents"]
    scenarios = [
        ("Tiefer Pfad", places.uri_for_path(deep), None, (True, False, True)),
        ("Start", places.HOME, None, (False, False, False)),
        ("/usr/share/icons/hicolor", places.uri_for_path("/usr/share/icons/hicolor/48x48"),
         None, (True, True, True)),
        ("Dieser PC", places.THISPC, None, (True, False, True)),
        ("Dokumente + Suchtext", docs[0]["uri"] if docs else places.uri_for_path(home),
         "bericht", (True, False, True)),
        ("Schmal (Überlauf)", places.uri_for_path("/usr/share/icons/hicolor/48x48/apps"),
         None, (True, False, True)),
    ]
    rows = []
    for title, loc, search, nav in scenarios:
        lab = Gtk.Label(label=title, xalign=0)
        lab.get_style_context().add_class("test-label")
        vbox.pack_start(lab, False, False, 0)
        bg = Gtk.Box()
        bg.get_style_context().add_class("test-toolbar")
        host = Host(win, loc)
        row = NavRow(host)
        host.row = row
        row.set_location(loc)
        row.set_nav_state(*nav)
        if search:
            row.set_search_text(search)
        bg.pack_start(row, True, True, 0)
        if title.startswith("Schmal"):
            # Raster mit 3 gleich breiten Spalten, Zeile belegt zwei davon (~ 730 px)
            grid = Gtk.Grid(column_homogeneous=True)
            grid.attach(bg, 0, 0, 2, 1)
            grid.attach(Gtk.Label(label=""), 2, 0, 1, 1)
            vbox.pack_start(grid, False, False, 0)
        else:
            vbox.pack_start(bg, False, False, 0)
        rows.append(row)

    win.show_all()

    if "--edit" in args or "--complete" in args:
        GLib.timeout_add(300, lambda: rows[0].focus_address() or False)
    if "--complete" in args:
        def typed():
            rows[0].entry.set_text("/usr/s")
            rows[0].entry.set_position(-1)
            return False
        GLib.timeout_add(600, typed)

        def key_h():
            # echtes Tastenereignis (nur in diesem Prozess) → Vervollständigung öffnet
            ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            ev.key.window = win.get_window()
            ev.key.keyval = Gdk.KEY_h
            ev.key.time = Gtk.get_current_event_time()
            ev.set_device(Gdk.Display.get_default().get_default_seat().get_keyboard())
            Gtk.main_do_event(ev)
            return False
        GLib.timeout_add(800, key_h)

        def shot():
            x, y = win.get_window().get_origin()[1:]
            screen_shot(out.replace(".png", "-complete.png"), x, y, 1100, 500)
            return False
        GLib.timeout_add(1500, shot)
    if "--menu" in args:
        n = int(args[args.index("--menu") + 1])

        def open_menu():
            strip = rows[0].strip
            if n < 0:
                strip.root_chev.clicked()
            else:
                strip.pairs[n][1].clicked()
            return False
        GLib.timeout_add(400, open_menu)

        def shot():
            x, y = win.get_window().get_origin()[1:]
            screen_shot(out.replace(".png", "-menu.png"), x, y, 1100, 600)
            return False
        GLib.timeout_add(1400, shot)
    if "--history" in args:
        rows[0].host.config.set("address_history", [places.HOME, places.uri_for_path("/usr/share"),
                                                    places.uri_for_path(home)])
        GLib.timeout_add(400, lambda: rows[0].hist_btn.clicked() or False)

        def shot():
            x, y = win.get_window().get_origin()[1:]
            screen_shot(out.replace(".png", "-history.png"), x, y, 1100, 400)
            return False
        GLib.timeout_add(1400, shot)

    win.connect("destroy", Gtk.main_quit)
    devtools.snapshot_after(win, out, delay_ms=1800, quit=True)
    Gtk.main()


if __name__ == "__main__":
    main()
