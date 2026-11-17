#!/usr/bin/env python3
"""Fortschritts-/Konfliktfenster testen (mit Screenshots):
    python3 dev/test_progress.py OUTDIR
Legt Testdaten in OUTDIR/data an (100 × 512 KB) und bremst das Kopieren
künstlich (Sleep je Datei), damit das Fenster sichtbar wird.
Szenarien: A Kopie + Details + Anhalten + Abbrechen, B Konflikte,
C Verschieben über Dateisystemgrenzen (OUTDIR → ~/.cache), D Fenster
schliessen während des Kopierens. FILEFENSTER_CONFIG_DIR setzen!
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib  # noqa: E402

from filefenster import fileops, places, progress  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
data = os.path.join(outdir, "data")
shutil.rmtree(data, ignore_errors=True)
src = os.path.join(data, "Quelle")
os.makedirs(src)
for i in range(100):
    with open(os.path.join(src, f"Datei {i:03d}.bin"), "wb") as fh:
        fh.write(os.urandom(512 * 1024))
# Konflikt-Daten
c_src = os.path.join(data, "KQ")
c_dst = os.path.join(data, "KZ")
os.makedirs(os.path.join(c_src, "Ordner"))
os.makedirs(os.path.join(c_dst, "Ordner"))
for path, text in [(f"{c_src}/Bericht.txt", "neu"), (f"{c_dst}/Bericht.txt", "alt"),
                   (f"{c_src}/Ordner/Liste.txt", "neu"), (f"{c_dst}/Ordner/Liste.txt", "alt"),
                   (f"{c_src}/Ordner/Nur-neu.txt", "x")]:
    with open(path, "w") as fh:
        fh.write(text)
xfs = os.path.expanduser("~/.cache/filefenster-test")
shutil.rmtree(xfs, ignore_errors=True)
os.makedirs(xfs)

# Künstlich bremsen: 60 ms je Datei
_orig = fileops.TransferJob._copy_file


def _slow(self, *a):
    time.sleep(0.06)
    return _orig(self, *a)


fileops.TransferJob._copy_file = _slow
results = {}


def G(p):
    return Gio.File.new_for_path(p)


class T(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        win = ExplorerWindow(self, places.uri_for_path(data))
        win.resize(1100, 700)
        win.move(80, 60)
        win.present()
        self.win = win

        def shot(name):
            gw = progress._win.get_window()
            if gw is None or not progress._win.get_visible():
                print("shot", name, "– Fenster nicht sichtbar", flush=True)
                return
            _, x, y = gw.get_origin()
            path = os.path.join(outdir, name + ".png")
            subprocess.run(["import", "-window", "root", "-crop",
                            f"{gw.get_width()}x{gw.get_height()}+{x}+{y}", path])
            print("shot", path, flush=True)

        def job():
            return fileops.running_jobs()[0]

        def start(sources, dest, move=False):
            fileops.transfer([G(s) for s in sources], G(dest), move=move, parent=win,
                             on_done=lambda c: results.setdefault(dest, []).append(len(c)))

        def answer(choice, for_all=False):
            j = next((j for j in fileops.running_jobs() if j.conflict), None)
            if j is None:
                print("keine offene Rückfrage", flush=True)
                return
            print("Konflikt:", j.conflict and j.conflict["dst"].get_basename(), "→", choice)
            j.answer(choice, for_all)

        A = os.path.join(data, "ZielA")
        os.makedirs(A)
        steps = [
            (500, lambda: start([src], A)),
            (1500, lambda: shot("A1_kompakt")),
            (100, lambda: progress._win._on_toggle()),
            (1500, lambda: shot("A2_details")),
            (100, lambda: progress._win.panels[0]._on_pause()),
            (800, lambda: shot("A3_angehalten")),
            (100, lambda: progress._win.panels[0]._on_pause()),
            (700, lambda: progress._win.panels[0].cancel()),
            (1200, lambda: check_a(A)),
            (100, lambda: progress._win._on_toggle()),
            # B Konflikte
            (300, lambda: start([f"{c_src}/Bericht.txt", f"{c_src}/Ordner"], c_dst)),
            (900, lambda: shot("B1_konflikt")),
            (100, lambda: answer("keep")),
            (600, lambda: shot("B2_konflikt_ordner")),
            (100, lambda: answer("replace")),
            (800, lambda: check_b()),
            # C Verschieben über Dateisystemgrenzen
            (300, lambda: start([f"{c_src}/Ordner"], xfs, move=True)),
            (1000, lambda: check_c()),
            # D Fenster schliessen während des Kopierens
            (300, lambda: os.makedirs(os.path.join(data, "ZielD"))),
            (100, lambda: start([src], os.path.join(data, "ZielD"))),
            (1200, lambda: win.close()),
        ]

        def run(i=0):
            if i >= len(steps):
                return False
            delay, fn = steps[i]

            def go():
                try:
                    fn()
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    print("FEHLER in Schritt", i, exc, flush=True)
                run(i + 1)
                return False
            GLib.timeout_add(delay, go)
        run()


def check_a(A):
    d = os.path.join(A, "Quelle")
    files = os.listdir(d)
    bad = [f for f in files if os.path.getsize(os.path.join(d, f)) != 512 * 1024]
    print(f"A: {len(files)} von 100 kopiert, unvollständige Dateien: {bad}, "
          f"läuft noch: {fileops.busy()}", flush=True)


def check_b():
    names = sorted(os.listdir(c_dst))
    rd = lambda p: open(p).read()  # noqa: E731
    print("B:", names, "| Bericht.txt =", rd(f"{c_dst}/Bericht.txt"),
          "| Bericht (2).txt =", rd(f"{c_dst}/Bericht (2).txt") if os.path.exists(f"{c_dst}/Bericht (2).txt") else "-",
          "| Ordner/Liste.txt =", rd(f"{c_dst}/Ordner/Liste.txt"),
          "| Ordner:", sorted(os.listdir(f"{c_dst}/Ordner")), flush=True)


def check_c():
    print("C: Ziel", sorted(os.listdir(os.path.join(xfs, "Ordner"))) if os.path.isdir(os.path.join(xfs, "Ordner")) else "fehlt",
          "| Quelle noch da:", os.path.exists(f"{c_src}/Ordner"), flush=True)


app = T()
t0 = time.time()
app.run([sys.argv[0]])
d = os.path.join(data, "ZielD", "Quelle")
n = len(os.listdir(d)) if os.path.isdir(d) else 0
print(f"D: App beendet nach {time.time() - t0:.1f} s, ZielD enthält {n} von 100 Dateien", flush=True)
shutil.rmtree(xfs, ignore_errors=True)
