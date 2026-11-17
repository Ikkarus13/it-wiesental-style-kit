#!/usr/bin/env python3
"""Tastenkürzel mit echten (simulierten) Tastendrücken testen – nur auf dem
Test-Bildschirm. Ruft keine Methoden direkt auf, sondern drückt die Tasten:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_tasten.py

Papierkorb: arbeitet in ~/.cache/filefenster-tasten und räumt nur eigene
Einträge auf.
"""
import os
import shutil
import sys

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402
from Xlib import X, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import fileops, places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_NAME  # noqa: E402

T = os.path.expanduser("~/.cache/filefenster-tasten")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(f"{T}/unter")
for n in "abcde":
    with open(f"{T}/{n}.txt", "w") as fh:
        fh.write(n)
D = display.Display()
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def E(p):
    return os.path.exists(f"{T}/{p}")


def press(*names):
    """press("Shift_L", "Delete") = Umschalt+Entf"""
    codes = [D.keysym_to_keycode(XK.string_to_keysym(n)) for n in names]
    for c in codes:
        xtest.fake_input(D, X.KeyPress, c)
    for c in reversed(codes):
        xtest.fake_input(D, X.KeyRelease, c)
    D.sync()


def move(x, y):
    xtest.fake_input(D, X.MotionNotify, x=int(x), y=int(y))
    D.sync()


def click(x, y, ctrl=False):
    if ctrl:
        xtest.fake_input(D, X.KeyPress, D.keysym_to_keycode(XK.string_to_keysym("Control_L")))
    xtest.fake_input(D, X.MotionNotify, x=int(x), y=int(y))
    xtest.fake_input(D, X.ButtonPress, 1)
    xtest.fake_input(D, X.ButtonRelease, 1)
    if ctrl:
        xtest.fake_input(D, X.KeyRelease, D.keysym_to_keycode(XK.string_to_keysym("Control_L")))
    D.sync()


def dialogs(win):
    return [w for w in Gtk.Window.list_toplevels() if w.get_visible() and w is not win
            and isinstance(w, Gtk.Window) and w.get_window() is not None
            and w.get_window().get_type_hint() != 0 and not isinstance(w.get_child(), Gtk.Menu)
            and w.get_style_context().has_class("win-dialog")]


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.uri_for_path(T))
        self.win.move(0, 0)
        self.win.resize(1000, 640)
        self.win.present()
        self.steps = self.script(self.win)
        GLib.timeout_add(1300, self.tick)

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

    def fv(self):
        return self.win.current.views["folder"]

    def xy(self, name):
        f = self.fv()
        path = next(r.path for r in f.tree.get_model() if r[COL_NAME] == name)
        rect = f.tree.get_background_area(path, f.col_name)
        x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 40, rect.y + rect.height // 2)
        wx, wy = f.tree.translate_coordinates(self.win, x, y)
        _r, ox, oy = self.win.get_window().get_origin()
        return ox + wx, oy + wy

    def select(self, *names):
        for i, n in enumerate(names):
            # erst hinbewegen (blendet einen Tooltip aus, der sonst den Klick abfängt)
            move(*self.xy(n))
            yield 200
            click(*self.xy(n), ctrl=i > 0)
            yield 250

    def wait(self):
        yield 400
        while fileops.busy():
            yield 100
        yield 500

    def script(self, win):
        T_uri = places.uri_for_path(T)
        # Entf: zwei Dateien in den Papierkorb
        yield from self.select("a.txt", "b.txt")
        press("Delete")
        yield from self.wait()
        check("Entf löscht zwei Dateien (Papierkorb)", not E("a.txt") and not E("b.txt") and E("c.txt"))
        # Strg+Z holt sie zurück
        press("Control_L", "z")
        yield from self.wait()
        check("Strg+Z stellt wieder her", E("a.txt") and E("b.txt"))
        # Umschalt+Entf: Rückfrage, Eingabe = Ja
        yield from self.select("c.txt", "d.txt")
        press("Shift_L", "Delete")
        yield 900
        dl = dialogs(win)
        check("Umschalt+Entf fragt nach", bool(dl))
        press("Return")
        yield from self.wait()
        check("Umschalt+Entf löscht endgültig", not E("c.txt") and not E("d.txt"))
        # F2 Umbenennen, Esc bricht ab
        yield from self.select("e.txt")
        press("F2")
        yield 500
        check("F2 startet Umbenennen", self.fv()._renaming)
        press("Escape")
        yield 400
        # Navigation: Unterordner, Rücktaste, Alt+→, Alt+↑, Alt+←
        win.current.navigate(places.uri_for_path(f"{T}/unter"))
        yield 800
        click(*self.xy_free())
        yield 200
        press("BackSpace")
        yield 800
        check("Rücktaste = Zurück", win.current.location == T_uri)
        press("Alt_L", "Right")
        yield 800
        check("Alt+→ = Vor", win.current.location.endswith("/unter"))
        press("Alt_L", "Up")
        yield 800
        check("Alt+↑ = Nach oben", win.current.location == T_uri)
        press("Alt_L", "Left")
        yield 800
        check("Alt+← = Zurück", win.current.location.endswith("/unter"))
        press("Alt_L", "Up")
        yield 800
        # F5 Aktualisieren
        with open(f"{T}/neu.txt", "w") as fh:
            fh.write("n")
        self.fv()._drop_monitor()          # ohne Dateiüberwachung: nur F5 zeigt sie
        press("F5")
        yield 900
        check("F5 aktualisiert", "neu.txt" in [r[COL_NAME] for r in self.fv().tree.get_model()])
        # Tabs: Strg+T, Strg+Bild↓, Strg+W
        press("Control_L", "t")
        yield 600
        check("Strg+T neuer Tab", len(win.tabs) == 2)
        press("Control_L", "Next")
        yield 400
        check("Strg+Bild↓ wechselt Tab", win.current is win.tabs[0])
        press("Control_L", "Next")         # zurück zum neuen Tab, den schliessen
        yield 400
        press("Control_L", "w")
        yield 600
        check("Strg+W schliesst Tab", len(win.tabs) == 1)
        # Alt+Eingabe: Eigenschaften
        yield from self.select("e.txt")
        press("Alt_L", "Return")
        yield 1000
        props = [w for w in Gtk.Window.list_toplevels() if w.get_visible() and w is not win
                 and "Eigenschaften" in (w.get_title() or "")]
        check("Alt+Eingabe zeigt Eigenschaften", bool(props))
        for w in props:
            w.destroy()
        yield 300

    def xy_free(self):
        f = self.fv()
        a = f.tree.get_allocation()
        wx, wy = f.tree.translate_coordinates(self.win, a.width - 60, a.height - 40)
        _r, ox, oy = self.win.get_window().get_origin()
        return ox + wx, oy + wy


App().run([sys.argv[0]])
for g, i in fileops.trash_items():
    if (i.get_attribute_byte_string("trash::orig-path") or "").startswith(T):
        try:
            g.delete(None)
        except GLib.Error as exc:
            print("Aufräumen:", exc.message)
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
