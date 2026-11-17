#!/usr/bin/env python3
"""Tabs ziehen (umsortieren, herausziehen, in anderes Fenster) mit echter
(simulierter) Maus – nur auf dem Test-Bildschirm:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_tabs.py OUTDIR
"""
import os
import shutil
import subprocess
import sys

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402
from Xlib import X, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-tabs")
shutil.rmtree(T, ignore_errors=True)
for n in ("A", "B/unter", "C", "D"):
    os.makedirs(os.path.join(T, n))
U = {n: places.uri_for_path(os.path.join(T, n)) for n in "ABCD"}
D = display.Display()
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def move(x, y):
    xtest.fake_input(D, X.MotionNotify, x=int(x), y=int(y))
    D.sync()


def btn(down):
    xtest.fake_input(D, X.ButtonPress if down else X.ButtonRelease, 1)
    D.sync()


def tab_xy(tab):
    w = tab.widget
    win = tab.win
    x, y = w.translate_coordinates(win, w.get_allocated_width() // 2, w.get_allocated_height() // 2 + 4)
    _r, ox, oy = win.get_window().get_origin()
    return ox + x, oy + y


def locs(win):
    return [t.location for t in win.tabs]


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.w1 = ExplorerWindow(self, U["A"])
        self.w1.new_tab(U["B"])
        self.w1.new_tab(U["C"])
        self.w1.move(0, 0)
        self.w1.resize(900, 420)
        self.w1.present()
        self.w2 = ExplorerWindow(self, U["D"])
        self.w2.move(0, 500)
        self.w2.resize(900, 420)
        self.w2.present()
        self.steps = self.script()
        GLib.timeout_add(1500, self.tick)

    def tick(self):
        try:
            GLib.timeout_add(next(self.steps), self.tick)
        except StopIteration:
            self.quit()
        except Exception:
            import traceback
            traceback.print_exc()
            self.quit()
        return False

    def drag(self, a, b, snap=None):
        move(*a)
        yield 200
        btn(True)
        yield 100
        for i in range(1, 16):
            move(a[0] + (b[0] - a[0]) * i / 15, a[1] + (b[1] - a[1]) * i / 15)
            yield 40
        yield 300
        if snap:
            subprocess.run(["import", "-window", "root", "-crop", "1000x960+0+0",
                            os.path.join(outdir, snap + ".png")])
        btn(False)
        yield 700

    def script(self):
        w1, w2 = self.w1, self.w2
        check("Start: drei Tabs", locs(w1) == [U["A"], U["B"], U["C"]])
        # 1 Klick ohne Bewegen wählt nur aus
        a = tab_xy(w1.tabs[0])
        move(*a)
        yield 200
        btn(True)
        btn(False)
        yield 400
        check("Klick wählt Tab, keine Umsortierung",
              w1.current is w1.tabs[0] and locs(w1) == [U["A"], U["B"], U["C"]])
        # 2 Umsortieren: C ganz nach links
        yield from self.drag(tab_xy(w1.tabs[2]), (tab_xy(w1.tabs[0])[0] - 40, tab_xy(w1.tabs[0])[1]))
        check("Umsortieren: C nach vorne", locs(w1) == [U["C"], U["A"], U["B"]])
        # 3 Verlauf in B erzeugen, dann B auf die Leiste von Fenster 2 ziehen
        tb = w1.tabs[2]
        tb.navigate(U["B"] + "/unter")
        yield 600
        bx, by = tab_xy(tb)
        tx, ty = tab_xy(w2.tabs[0])
        yield from self.drag((bx, by), (tx + 150, ty), snap="tab-ziehen")
        check("Tab in anderes Fenster verschoben",
              locs(w1) == [U["C"], U["A"]] and any(l.endswith("/B/unter") for l in locs(w2)))
        moved = next((t for t in w2.tabs if t.location.endswith("/B/unter")), None)
        check("Verlauf mitgenommen (Zurück geht)", moved is not None and U["B"] in moved.back)
        # 4 A herausziehen (nach unten in den Inhalt) = neues Fenster
        n_before = len(self.get_windows())
        ax, ay = tab_xy(w1.tabs[1])
        yield from self.drag((ax, ay), (ax + 100, ay + 250))
        check("Herausziehen öffnet neues Fenster", len(self.get_windows()) == n_before + 1
              and locs(w1) == [U["C"]])
        neu = [w for w in self.get_windows() if w not in (w1, w2)]
        check("Neues Fenster zeigt den Ort", neu and locs(neu[0]) == [U["A"]])
        # 5 ✕ im Tab, Mittelklick und Rechtsklick funktionieren weiter
        for w in neu:            # liegt sonst über Fenster 2
            w.destroy()
        w2.present()
        w2.new_tab(U["C"])
        w2.new_tab(U["A"])
        yield 500
        n = len(w2.tabs)
        cb = w2.tabs[-1].widget.close_btn
        x, y = cb.translate_coordinates(w2, cb.get_allocated_width() // 2, cb.get_allocated_height() // 2)
        _r, ox, oy = w2.get_window().get_origin()
        move(ox + x, oy + y)
        yield 300
        btn(True)
        btn(False)
        yield 600
        check("✕ schliesst den Tab", len(w2.tabs) == n - 1)
        move(*tab_xy(w2.tabs[-1]))
        yield 200
        xtest.fake_input(D, X.ButtonPress, 2)
        xtest.fake_input(D, X.ButtonRelease, 2)
        D.sync()
        yield 600
        check("Mittelklick schliesst den Tab", len(w2.tabs) == n - 2)
        move(*tab_xy(w2.tabs[0]))
        yield 200
        xtest.fake_input(D, X.ButtonPress, 3)
        xtest.fake_input(D, X.ButtonRelease, 3)
        D.sync()
        yield 600
        from gi.repository import Gtk
        menus = [w for w in Gtk.Window.list_toplevels() if w.get_visible() and isinstance(w.get_child(), Gtk.Menu)]
        check("Rechtsklick zeigt das Tab-Menü", bool(menus))
        for m in menus:
            m.get_child().popdown()
        yield 300


App().run([sys.argv[0]])
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
