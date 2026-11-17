#!/usr/bin/env python3
"""Test-Harness für den Detailbereich.

    timeout 20 python3 dev/test_detailspane.py AUSGABE.png TESTORDNER

Zeigt fünf Detailbereiche (je 300 px) nebeneinander:
Bild ausgewählt | Ordner ausgewählt | Mehrfachauswahl | nichts ausgewählt im
Ordner | nichts ausgewählt bei "Dieser PC" – plus einen mit Platzhalter.
TESTORDNER sollte Foto.jpg, Bericht.txt, Daten.bin und Ordner/ enthalten.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_CONFIG_DIR",
                      os.path.join(os.environ.get("TMPDIR", "/tmp"), "filefenster-test-config"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from filefenster import devtools, places, theme  # noqa: E402
from filefenster.detailspane import DetailsPane  # noqa: E402
from filefenster.host import FakeHost  # noqa: E402


def main():
    out = sys.argv[1]
    folder = sys.argv[2]
    theme.setup()
    css = Gtk.CssProvider()
    css.load_from_data(f"""
        .test-sep {{ background-color: {theme.P['divider']}; min-width: 1px; }}
    """.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 900)

    win = Gtk.Window(title="DetailsPane-Test")
    win.get_style_context().add_class("filefenster")
    win.set_default_size(6 * 301, 640)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    win.add(box)
    host = FakeHost(win, places.uri_for_path(folder))

    def f(name):
        return Gio.File.new_for_path(os.path.join(folder, name))

    folder_uri = places.uri_for_path(folder)
    scenarios = [
        (folder_uri, [f("Foto.jpg")]),
        (folder_uri, [f("Ordner")]),
        (folder_uri, [f("Bericht.txt"), f("Daten.bin"), f("Ordner")]),
        (folder_uri, []),
        (places.THISPC, []),
        (None, []),
    ]
    extra = [a for a in sys.argv[3:]]
    for p in extra:   # zusätzliche Einzeldatei statt "Bericht.txt …"
        scenarios[2] = (folder_uri, [Gio.File.new_for_path(p)])
    for i, (loc, files) in enumerate(scenarios):
        if i:
            sep = Gtk.Box()
            sep.get_style_context().add_class("test-sep")
            box.pack_start(sep, False, False, 0)
        pane = DetailsPane(host)
        pane.set_size_request(300, -1)
        box.pack_start(pane, False, False, 0)
        pane.show_items(loc, files)

    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    devtools.snapshot_after(win, out, delay_ms=2200, quit=True)
    Gtk.main()


if __name__ == "__main__":
    main()
