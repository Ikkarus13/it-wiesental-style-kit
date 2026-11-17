#!/usr/bin/env python3
"""Assistent Seite für Seite durchklicken und Bildschirmfotos machen.

    WST_TROCKEN=1 DISPLAY=:99 dev/gui_bilder.py ZIELORDNER [--installieren] [--verwalten]

Nur auf einem Testbildschirm (Xvfb) und mit Test-HOME verwenden: mit
--installieren läuft die Installation wirklich (ohne Systemteil bei WST_TROCKEN=1).
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gi.repository import GLib, Gtk  # noqa: E402

from installer import gui  # noqa: E402

if os.environ.get("DISPLAY") in (":0", ":1", None):
    sys.exit("Nur auf einem Testbildschirm (DISPLAY=:99).")
ziel = sys.argv[1]
installieren = "--installieren" in sys.argv
os.makedirs(ziel, exist_ok=True)
win = gui.Assistent(verwalten="--verwalten" in sys.argv)
win.connect("destroy", Gtk.main_quit)
win.show_all()
win.zeige(win.reihenfolge[0])
win.move(40, 40)
n = [0]


def foto(name):
    while Gtk.events_pending():
        Gtk.main_iteration()
    xid = win.get_window().get_xid()
    pfad = os.path.join(ziel, f"{n[0]:02d}-{name}.png")
    subprocess.run(["import", "-window", hex(xid), pfad], check=False)
    print("Bild:", pfad, flush=True)
    n[0] += 1


def ablauf():
    yield 600
    while True:
        sid = win.stack.get_visible_child_name()
        foto(sid)
        if sid == "auswahl" and "--teile" in sys.argv and not getattr(win, "_teile", False):
            win._teile = True
            win.rb_teile.set_active(True)
            win.auswahl_checks["onedrive"].set_active(False)
            win.auswahl_checks["emoji-panel"].set_active(False)
            yield 300
            continue
        if sid == "zusammenfassung" and not installieren:
            break
        if sid == "installation":
            while win.laeuft:
                yield 500
            foto("installation-ende")
        if sid == "fertig":
            break
        win._weiter()
        yield 500
    win.laeuft = False
    win.destroy()


it = ablauf()


def tick():
    try:
        GLib.timeout_add(next(it), tick)
    except StopIteration:
        pass
    return False


GLib.timeout_add(100, tick)
Gtk.main()
