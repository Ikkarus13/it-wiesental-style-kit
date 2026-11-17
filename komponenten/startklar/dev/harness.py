#!/usr/bin/env python3
"""Testlauf im selben Prozess (nur auf einem Testbildschirm, nie auf :0).

    DISPLAY=:99 dev/harness.py OUT.png [schritt …]

Schritte: type:TEXT  key:NAME  view:category|grid|list  wait:MS  dump
          filter:KEY  click:power|user  folder:N  letters  sel:+N
Umgebung: STARTKLAR_CONFIG_DIR (Pflicht), STARTKLAR_DRY_RUN=1 empfohlen.
"""
import os
import subprocess
import sys

if os.environ.get("DISPLAY", "") in ("", ":0", ":0.0"):
    sys.exit("Nur auf einem Testbildschirm (DISPLAY=:99) ausführen.")
if not os.environ.get("STARTKLAR_CONFIG_DIR"):
    sys.exit("STARTKLAR_CONFIG_DIR setzen.")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from startklar import theme  # noqa: E402
from startklar.app import App  # noqa: E402

OUT = sys.argv[1]
STEPS = sys.argv[2:]
GEOM = {"monitor": [0, 0, 1600, 1000], "panel": [0, 960, 1600, 40],
        "side": "bottom", "zone": "center"}


def dump(w, depth=0, maxdepth=6):
    a = w.get_allocation()
    name = type(w).__name__
    cls = ",".join(w.get_style_context().list_classes())
    print("  " * depth + f"{name}[{cls}] {a.x},{a.y} {a.width}x{a.height}"
          f" vis={w.get_visible()}")
    if depth < maxdepth and isinstance(w, Gtk.Container):
        for c in w.get_children():
            dump(c, depth + 1, maxdepth)


def key(win, name):
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.keyval = Gdk.keyval_from_name(name)
    ev.window = win.get_window()
    ev.send_event = True
    ev.set_device(Gdk.Display.get_default().get_default_seat().get_keyboard())
    win.event(ev)


def run_steps(app):
    win = app.win
    steps = list(STEPS)

    def next_step():
        if not steps:
            subprocess.run(["import", "-display", os.environ["DISPLAY"], "-window", "root", OUT])
            print("Bild:", OUT)
            app.quit()
            return False
        s = steps.pop(0)
        cmd, _, arg = s.partition(":")
        delay = 250
        if cmd == "type":
            win.entry.grab_focus()
            win.entry.set_text(arg)
            win.entry.set_position(-1)
        elif cmd == "key":
            key(win, arg)
        elif cmd == "view":
            win.start.set_view(arg)
        elif cmd == "wait":
            delay = int(arg)
        elif cmd == "shot":
            subprocess.run(["import", "-display", os.environ["DISPLAY"], "-window", "root", arg])
        elif cmd == "drag":
            # drag:A:B  Kachel A (Angeheftet) auf Platz von Kachel B ziehen (XTEST)
            # drag:allN:B  N-te Raster-Kachel aus „Alle“ auf Platz B
            a, b = arg.split(":")
            tiles = list(reversed(win.start.pinned_grid.get_children()))
            tiles = [t for t in tiles if hasattr(t, "entry")]
            if a.startswith("all="):
                src_w = [t for t in _all_tiles(win) if t.entry.name.startswith(a[4:])][0]
            elif a.startswith("all"):
                src_w = _all_tiles(win)[int(a[3:])]
            else:
                src_w = tiles[int(a)]
            dst_w = tiles[int(b)]
            p1 = _root_center(src_w)
            p2 = _root_center(dst_w)
            print("Ziehen", src_w.entry.name, p1, "->", p2)
            import threading
            threading.Thread(target=_xtest_drag, args=(p1, p2), daemon=True).start()
            delay = 2500
        elif cmd == "dbg":
            g = win.start.pinned_grid
            for sig in ("drag-motion", "drag-drop", "drag-leave"):
                g.connect_after(sig, lambda *a, sig=sig: print("Signal", sig, win.drag_id,
                                                               win.start._order and len(win.start._order)))
            orig = win.drag_started
            win.drag_started = lambda i: (print("Start", i), orig(i))
        elif cmd == "update":
            win.set_update(arg)
        elif cmd == "settings":
            app.open_settings()
            delay = 900
        elif cmd == "set":
            k, _, v = arg.partition("=")
            val = {"true": True, "false": False}.get(v, v)
            from startklar.config import get_config
            get_config().set(k, val)
            win.reconfigure()
        elif cmd == "show":
            win.show_menu(GEOM)
            delay = 700
        elif cmd == "pinned":
            print("Angeheftet:", len(win.pinned), win.pinned[:10], "sichtbar:", win.get_visible())
        elif cmd == "dump":
            dump(win, 0, int(arg) if arg else 7)
        elif cmd == "filter":
            win.search.set_filter(arg)
        elif cmd == "click":
            {"power": lambda: win._power_menu(win.power_btn)}[arg]()
        elif cmd == "folder":
            cats = win.index.categories()
            k, label, items = cats[int(arg)]
            win.start.open_folder(label, items)
        elif cmd == "letters":
            win.start.letter_picker({"A", "B", "C"})
        elif cmd == "sel":
            win.search.move(int(arg))
        elif cmd == "scroll":
            adj = win.start.get_vadjustment()
            adj.set_value(float(arg))
        elif cmd == "ctx":
            kids = win.start.pinned_grid.get_children()
            btn = kids[-1 - int(arg or 0)]
            win.app_menu(btn, 40, 40, btn.entry, pinned=True)
        print("Schritt:", s)
        GLib.timeout_add(delay, next_step)
        return False

    win.show_menu(GEOM)
    GLib.timeout_add(900, next_step)


def _all_tiles(win):
    out = []

    def walk(w):
        if hasattr(w, "entry") and w.get_mapped():
            out.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)
    walk(win.start.all_box)
    return out


def _root_center(w):
    gw = w.get_window()
    ox, oy = gw.get_origin()[1:]
    x, y = w.translate_coordinates(w.get_toplevel(), 0, 0)
    tx, ty = w.get_toplevel().get_window().get_origin()[1:]
    a = w.get_allocation()
    return tx + x + a.width // 2, ty + y + a.height // 2


def _xtest_drag(p1, p2):
    import time
    from Xlib import X, display
    from Xlib.ext import xtest
    d = display.Display(os.environ["DISPLAY"])

    def move(x, y):
        xtest.fake_input(d, X.MotionNotify, x=int(x), y=int(y))
        d.sync()
    move(*p1)
    time.sleep(0.3)
    xtest.fake_input(d, X.ButtonPress, 1)
    d.sync()
    time.sleep(0.15)
    steps = 25
    for i in range(1, steps + 1):
        move(p1[0] + (p2[0] - p1[0]) * i / steps, p1[1] + (p2[1] - p1[1]) * i / steps)
        time.sleep(0.03)
    time.sleep(0.4)
    xtest.fake_input(d, X.ButtonRelease, 1)
    d.sync()


class TestApp(App):
    def do_activate(self):
        run_steps(self)


if __name__ == "__main__":
    app = TestApp()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    sys.exit(app.run([sys.argv[0]]))
