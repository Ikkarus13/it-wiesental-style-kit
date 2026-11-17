#!/usr/bin/env python3
"""Drag & Drop mit echter (simulierter) Maus testen – NUR auf dem
unsichtbaren Test-Bildschirm, nie auf dem Arbeitsbildschirm:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_dnd.py OUTDIR

Testordner ~/.cache/filefenster-dndtest (gleiches Dateisystem wie ~) und
OUTDIR/x (anderes Dateisystem, wenn OUTDIR unter /tmp liegt). Ein zweites
Programm (Hilfsfenster) prüft Ziehen von/zu fremden Programmen.
"""
import json
import os
import shutil
import subprocess
import sys

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99), siehe dev/testbildschirm.py")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

outdir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."
T = os.path.expanduser("~/.cache/filefenster-dndtest")
X = os.path.join(outdir, "x")
GOT = os.path.join(outdir, "helper-got.txt")

# ----------------------------------------------------------------------
# Hilfsfenster = "fremdes Programm" (eigener Prozess)
# ----------------------------------------------------------------------
if len(sys.argv) > 2 and sys.argv[2] == "helper":
    TL = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
    w = Gtk.Window(title="Hilfsprogramm")
    w.move(1340, 100)
    w.resize(240, 300)
    box = Gtk.EventBox()
    box.add(Gtk.Label(label="Hilfsprogramm"))
    w.add(box)
    box.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, TL,
                        Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK | Gdk.DragAction.ASK)
    box.connect("drag-data-get", lambda _w, _c, d, *_: d.set_uris(
        [Gio.File.new_for_path(os.path.join(X, "ext.txt")).get_uri()]))
    box.drag_dest_set(Gtk.DestDefaults.ALL, TL, Gdk.DragAction.COPY)

    def got(_w, ctx, _x, _y, data, _i, time):
        with open(GOT, "w") as fh:
            fh.write("\n".join(data.get_uris() or []))
    box.connect("drag-data-received", got)
    w.connect("destroy", Gtk.main_quit)
    w.show_all()
    Gtk.main()
    sys.exit(0)

from Xlib import X as XC, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import places, undo  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_NAME  # noqa: E402
from filefenster.navpane import C_LABEL  # noqa: E402

# Testdaten
shutil.rmtree(T, ignore_errors=True)
shutil.rmtree(X, ignore_errors=True)
for d in ("ziel/sub", "pinziel"):
    os.makedirs(os.path.join(T, d))
for d in ("multi", "multi2"):
    os.makedirs(os.path.join(T, d))
for n in ("m1", "m2", "m3", "n1", "n2", "n3"):
    with open(os.path.join(T, f"{n}.txt"), "w") as fh:
        fh.write(n)
for n in "abcdefg":
    with open(os.path.join(T, f"{n}.txt"), "w") as fh:
        fh.write(n)
os.makedirs(X)
with open(os.path.join(X, "ext.txt"), "w") as fh:
    fh.write("ext")
if os.path.exists(GOT):
    os.remove(GOT)
cfg = os.environ.get("FILEFENSTER_CONFIG_DIR")
if cfg:
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "settings.json"), "w") as fh:
        json.dump({"pinned": [places.uri_for_path(os.path.join(T, "pinziel"))]}, fh)

D = display.Display()
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def E(*p):
    return os.path.lexists(os.path.join(T, *p))


def move(x, y):
    xtest.fake_input(D, XC.MotionNotify, x=int(x), y=int(y))
    D.sync()


def button(b, down):
    xtest.fake_input(D, XC.ButtonPress if down else XC.ButtonRelease, b)
    D.sync()


def key(name, down):
    code = D.keysym_to_keycode(XK.string_to_keysym(name))
    xtest.fake_input(D, XC.KeyPress if down else XC.KeyRelease, code)
    D.sync()


def shot(name):
    subprocess.run(["import", "-display", os.environ["DISPLAY"], "-window", "root",
                    "-crop", "1600x900+0+0", os.path.join(outdir, name + ".png")])


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = win = ExplorerWindow(self, places.uri_for_path(T))
        win.move(0, 0)
        win.resize(1300, 820)
        win.present()
        self.helper = subprocess.Popen([sys.executable, __file__, outdir, "helper"])
        self.steps = self.scenario(win)
        GLib.timeout_add(1500, self.tick)

    def tick(self):
        try:
            delay = next(self.steps)
        except StopIteration:
            self.helper.terminate()
            self.quit()
            return False
        except Exception:
            import traceback
            traceback.print_exc()
            self.helper.terminate()
            self.quit()
            return False
        GLib.timeout_add(delay, self.tick)
        return False

    # -- Koordinaten ---------------------------------------------------
    @staticmethod
    def root_of(widget, x, y):
        top = widget.get_toplevel()
        wx, wy = widget.translate_coordinates(top, int(x), int(y))
        _r, ox, oy = top.get_window().get_origin()
        return ox + wx, oy + wy

    def fv(self):
        return self.win.current.views["folder"]

    def item_xy(self, name):
        f = self.fv()
        view = f.tree if f._is_tree() else f.iv
        model = view.get_model()
        path = next(r.path for r in model if r[COL_NAME] == name)
        if f._is_tree():
            rect = f.tree.get_background_area(path, f.col_name)
            x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 40, rect.y + rect.height // 2)
            return self.root_of(f.tree, x, y)
        _ok, rect = f.iv.get_cell_rect(path, None)
        return self.root_of(f.iv, rect.x + rect.width // 2, rect.y + rect.height // 3)

    def background_xy(self):
        f = self.fv()
        view = f._view()
        a = view.get_allocation()
        return self.root_of(view, a.width - 120, a.height - 60)

    def nav_xy(self, label):
        np = self.win.navpane
        found = []
        np.store.foreach(lambda m, p, it: found.append(p.copy()) if m[it][C_LABEL] == label else None)
        rect = np.tree.get_background_area(found[0], None)
        x, y = np.tree.convert_bin_window_to_widget_coords(rect.x + 70, rect.y + rect.height // 2)
        return self.root_of(np.tree, x, y)

    def crumb_xy(self, text):
        hits = []

        def walk(w):
            if isinstance(w, Gtk.Button) and w.get_style_context().has_class("crumb"):
                ch = w.get_child()
                if isinstance(ch, Gtk.Label) and ch.get_text() == text:
                    hits.append(w)
            if isinstance(w, Gtk.Container):
                w.forall(walk)
        walk(self.win.navrow)
        a = hits[0].get_allocation()
        return self.root_of(hits[0], a.width // 2, a.height // 2)

    def tab_xy(self, i):
        w = self.win.tabs[i].widget
        a = w.get_allocation()
        return self.root_of(w, a.width // 2, a.height // 2 + 4)

    # -- Ziehen --------------------------------------------------------
    def select_multi(self, names):
        """Klick auf das erste, Strg+Klick auf die weiteren (Maus vorher hinbewegen)."""
        for i, n in enumerate(names):
            move(*self.item_xy(n))
            yield 250
            if i:
                key("Control_L", True)
            button(1, True)
            button(1, False)
            if i:
                key("Control_L", False)
            yield 300

    def drag(self, a, b, btn=1, mods=(), hold=300, snap=None, then=None):
        """b darf eine Funktion sein (Ziel erst während des Ziehens bestimmen)."""
        for m in mods:
            key(m, True)
        move(*a)
        yield 120
        button(btn, True)
        yield 120
        if callable(b):
            b = b()
        for i in range(1, 15):
            move(a[0] + (b[0] - a[0]) * i / 14, a[1] + (b[1] - a[1]) * i / 14)
            yield 35
        yield hold
        if then is not None:            # z. B. nach Tab-Wechsel weiterziehen
            b2 = then()
            for i in range(1, 11):
                move(b[0] + (b2[0] - b[0]) * i / 10, b[1] + (b2[1] - b[1]) * i / 10)
                yield 35
            yield 400
        if snap:
            shot(snap)
        button(btn, False)
        yield 150
        for m in mods:
            key(m, False)
        yield 900

    def scenario(self, win):
        win.current.open_in_new_tab(places.uri_for_path(X))
        yield 800
        # 1 Verschieben auf Ordner (gleiches Laufwerk)
        yield from self.drag(self.item_xy("a.txt"), self.item_xy("ziel"), snap="1_ziehen_details")
        check("Ziehen auf Ordner verschiebt", E("ziel", "a.txt") and not E("a.txt"))
        check("Rückgängig-Text", undo.undo_label() == "Rückgängig: Verschieben")
        # 2 Strg = kopieren
        yield from self.drag(self.item_xy("b.txt"), self.item_xy("ziel"), mods=("Control_L",),
                             snap="2_ziehen_strg")
        check("Strg kopiert", E("ziel", "b.txt") and E("b.txt"))
        # 3 Alt = Verknüpfung
        yield from self.drag(self.item_xy("c.txt"), self.item_xy("ziel"), mods=("Alt_L",))
        check("Alt erstellt Verknüpfung", os.path.islink(os.path.join(T, "ziel", "c - Verknüpfung.txt")))
        # 4 Ordner auf sich selbst: nichts
        yield from self.drag(self.item_xy("ziel"), self.item_xy("ziel"))
        check("Ordner auf sich selbst: nichts passiert", E("ziel", "sub") and E("ziel"))
        # 5 Navigationsbereich (angehefteter Ordner)
        yield from self.drag(self.item_xy("d.txt"), self.nav_xy("pinziel"), snap="5_ziehen_navi")
        check("Ziehen in den Navigationsbereich", E("pinziel", "d.txt") and not E("d.txt"))
        # 6 auf Tab verweilen → Tab wechselt, dann ablegen (anderes Dateisystem = kopieren)
        yield from self.drag(self.item_xy("e.txt"), self.tab_xy(1), hold=1300,
                             then=self.background_xy, snap="6_ziehen_tab")
        check("Verweilen wechselt den Tab", win.current is win.tabs[1])
        check("Anderes Laufwerk kopiert", os.path.exists(os.path.join(X, "e.txt")) and E("e.txt"))
        win.select_tab(win.tabs[0])
        yield 600
        # 7 rechte Maustaste → Menü → „Hierher kopieren“
        yield from self.drag(self.item_xy("f.txt"), self.item_xy("ziel"), btn=3)
        menu = getattr(self.fv().tree, "_dnd_menu", None)
        check("Menü nach Ziehen mit rechts", menu is not None and menu.menu.get_visible())
        if menu is not None:
            shot("7_menue_rechts")
            menu.menu.get_children()[0].activate()
            menu.menu.popdown()
        yield 900
        check("Menü: Hierher kopieren", E("ziel", "f.txt") and E("f.txt"))
        # 8 Brotkrumen: aus ziel zurück in den Testordner
        win.current.navigate(places.uri_for_path(os.path.join(T, "ziel")))
        yield 900
        yield from self.drag(self.item_xy("a.txt"), self.crumb_xy("filefenster-dndtest"))
        check("Ziehen auf Brotkrume", E("a.txt") and not E("ziel", "a.txt"))
        win.current.navigate(places.uri_for_path(T))
        yield 900
        # 9 Symbolansicht
        self.fv().set_view_mode("large")
        yield 900
        yield from self.drag(self.item_xy("g.txt"), self.item_xy("pinziel"), snap="9_ziehen_symbole")
        check("Symbolansicht: auf Ordner verschieben", E("pinziel", "g.txt") and not E("g.txt"))
        self.fv().set_view_mode("details")
        yield 900
        # 10 fremdes Programm → FileFenster
        yield from self.drag((1450, 200), self.background_xy, hold=500)
        check("Von fremdem Programm ablegen (kopiert)",
              E("ext.txt") and os.path.exists(os.path.join(X, "ext.txt")))
        # 11 FileFenster → fremdes Programm
        yield from self.drag(self.item_xy("b.txt"), (1450, 200))
        got = open(GOT).read() if os.path.exists(GOT) else ""
        check("An fremdes Programm übergeben", Gio.File.new_for_path(os.path.join(T, "b.txt")).get_uri() in got)
        # 12 Rückgängig nach Ziehen
        undo.undo(win)        # zuletzt: g.txt verschoben? → letzte Aktion ist ext-Kopie
        yield 900
        check("Rückgängig nach Ablegen", not E("ext.txt"))
        # 14 Mehrfachauswahl ziehen: Drücken auf ein markiertes Element behält die Auswahl
        yield from self.select_multi(["m1.txt", "m2.txt", "m3.txt"])
        check("Drei markiert", len(self.fv().get_selected()) == 3)
        yield from self.drag(self.item_xy("m2.txt"), self.item_xy("multi"))
        check("Details: alle drei gezogen", all(E("multi", f"m{i}.txt") for i in (1, 2, 3)))
        # … und ein einfacher Klick auf ein markiertes Element wählt nur noch dieses
        yield from self.select_multi(["e.txt", "f.txt"])
        move(*self.item_xy("e.txt"))
        yield 250
        button(1, True)
        button(1, False)
        yield 600
        check("Klick in Mehrfachauswahl wählt nur dieses", [g.get_basename() for g in self.fv().get_selected()] == ["e.txt"])
        self.fv().set_view_mode("large")
        yield 900
        yield from self.select_multi(["n1.txt", "n2.txt", "n3.txt"])
        yield from self.drag(self.item_xy("n2.txt"), self.item_xy("multi2"))
        check("Symbole: alle drei gezogen", all(E("multi2", f"n{i}.txt") for i in (1, 2, 3)))
        self.fv().set_view_mode("details")
        yield 900
        # 13 Rechtsklick ohne Ziehen: Kontextmenü (öffnet beim Loslassen)
        move(*self.item_xy("c.txt"))
        yield 150
        button(3, True)
        yield 120
        button(3, False)
        yield 600
        menus = [w for w in Gtk.Window.list_toplevels()
                 if w.get_visible() and isinstance(w.get_child(), Gtk.Menu)]
        check("Rechtsklick ohne Ziehen öffnet Kontextmenü", bool(menus))
        for w in menus:
            w.get_child().popdown()
        yield 300


App().run([sys.argv[0]])
D.close()
# Aufräumen: nur eigene Papierkorb-Einträge (Rückgängig legt dort ab)
from filefenster import fileops  # noqa: E402
for g, i in fileops.trash_items():
    if (i.get_attribute_byte_string("trash::orig-path") or "").startswith(T):
        try:
            g.delete(None)
        except GLib.Error as exc:
            print("Aufräumen:", exc.message)
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
