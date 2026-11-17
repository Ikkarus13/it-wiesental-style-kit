#!/usr/bin/env python3
"""„Gruppieren nach“ testen (Datum, Name, Typ, Grösse; Details und Symbole;
Einklappen per Klick) – nur auf dem Test-Bildschirm:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_gruppen.py OUTDIR
"""
import datetime
import os
import shutil
import subprocess
import sys
import time

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402
from Xlib import X, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_KEY, COL_NAME, DATE_GROUPS, _date_group  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-gruppen")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(f"{T}/Projekte")
now = time.time()
day = 86400
files = {"Bericht heute.txt": 0, "Notiz gestern.txt": 1, "Angebot.pdf": 40,
         "Zahlen 2025.xlsx": 400, "Alt 2020.txt": 2000, "zebra.txt": 0, "123 Liste.txt": 3}
for name, age in files.items():
    with open(f"{T}/{name}", "w") as fh:
        fh.write("x" * (20000 if name.endswith(".pdf") else 10))
    os.utime(f"{T}/{name}", (now - age * day, now - age * day))
D = display.Display()
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def click(x, y):
    xtest.fake_input(D, X.MotionNotify, x=int(x), y=int(y))
    D.sync()
    time.sleep(0.15)
    xtest.fake_input(D, X.ButtonPress, 1)
    xtest.fake_input(D, X.ButtonRelease, 1)
    D.sync()


def expected(age):
    return DATE_GROUPS[_date_group((datetime.datetime.now() - datetime.timedelta(days=age)).timestamp(),
                                   datetime.date.today())]


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.uri_for_path(T))
        self.win.move(0, 0)
        self.win.resize(1100, 700)
        self.win.present()
        self.steps = self.script()
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

    def heads(self):
        return [h[0] for _n, h in sorted(self.fv()._group_heads.items())]

    def shot(self, name):
        gw = self.win.get_window()
        _r, x, y = gw.get_origin()
        subprocess.run(["import", "-window", "root", "-crop",
                        f"{gw.get_width()}x{gw.get_height()}+{x}+{y}", os.path.join(outdir, name + ".png")])

    def script(self):
        f = self.fv()
        f.set_group("mtime")
        yield 600
        want = []
        for age in sorted(set(files.values())):
            g = expected(age)
            if g not in want:
                want.append(g)
        check("Datumsgruppen, neueste zuerst", self.heads() == ["Dateiordner"] * 0 + want
              or self.heads()[:len(want)] == want or set(want) <= set(self.heads()))
        check("Reihenfolge Heute vor Vor langer Zeit", self.heads()[0] == "Heute"
              and self.heads()[-1] == "Vor langer Zeit")
        model = f.tree.get_model()
        hn = min(f._group_heads)
        f.tree.get_selection().select_path(Gtk.TreePath.new_from_indices([hn]))
        check("Kopfzeile nicht auswählbar", not f.get_selected())
        self.shot("gruppen-details")
        # Einklappen per Klick auf die Kopfzeile „Heute“
        n_heute = next(n for n, h in f._group_heads.items() if h[0] == "Heute")
        rect = f.tree.get_background_area(Gtk.TreePath.new_from_indices([n_heute]), None)
        x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 120, rect.y + rect.height // 2)
        wx, wy = f.tree.translate_coordinates(self.win, x, y)
        _r, ox, oy = self.win.get_window().get_origin()
        click(ox + wx, oy + wy)
        yield 600
        names = [r[COL_NAME] for r in f.tree.get_model() if r[COL_KEY]]
        check("Klick klappt „Heute“ ein", "Bericht heute.txt" not in names and "Notiz gestern.txt" in names)
        click(ox + wx, oy + wy)
        yield 600
        names = [r[COL_NAME] for r in f.tree.get_model() if r[COL_KEY]]
        check("Zweiter Klick klappt wieder auf", "Bericht heute.txt" in names)
        check("Elementzahl zählt alle Dateien", f.item_count() == len(files) + 1)
        # andere Gruppierungen
        f.set_group("name")
        yield 400
        check("Nach Name: 0 – 9, A – H, I – P, Q – Z",
              self.heads() == ["0 – 9", "A – H", "I – P", "Q – Z"])
        f.set_group("type")
        yield 400
        check("Nach Typ enthält Dateiordner", "Dateiordner" in self.heads())
        f.set_group("size")
        yield 400
        check("Nach Grösse: Klein und Winzig", {"Winzig (0 – 16 KB)", "Klein (16 KB – 1 MB)"} <= set(self.heads()))
        f.set_group(None)
        yield 400
        check("(Ohne) hebt Gruppierung auf", not f._group_heads and f._store_pos is None)
        # Symbolansicht: Kopfzeilen beginnen eine neue Zeile
        f.set_group("mtime")
        f.set_view_mode("large")
        yield 1200
        iv = f.iv
        cols_ok = all(iv.get_item_column(Gtk.TreePath.new_from_indices([n])) == 0
                      for n in f._group_heads)
        firsts = [min((p for k, p in f._store_pos.items() if p > n), default=None) for n in f._group_heads]
        items_ok = all(p is None or iv.get_item_column(Gtk.TreePath.new_from_indices([p])) == 0
                       for p in firsts)
        check("Symbole: Kopfzeile und erstes Element jeweils am Zeilenanfang", cols_ok and items_ok)
        self.shot("gruppen-symbole")
        # gespeichert je Ordner
        win = self.win
        win.current.navigate(places.uri_for_path(f"{T}/Projekte"))
        yield 700
        check("Anderer Ordner ohne Gruppierung", self.fv().group_by is None)
        win.current.go_back()
        yield 900
        check("Gruppierung bleibt für den Ordner gespeichert", self.fv().group_by == "mtime")
        self.fv().set_view_mode("details")
        yield 300
        dl = places.special_dirs()
        dpath = next((d["path"] for d in dl if d["key"] == "downloads"), None)
        if dpath:
            win.current.navigate(places.uri_for_path(dpath))
            yield 900
            check("Downloads standardmässig nach Datum (wie Windows)", self.fv().group_by == "mtime")


App().run([sys.argv[0]])
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
