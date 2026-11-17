#!/usr/bin/env python3
"""Weitere Spalten (Kopf-Menü, „Weitere…“, Metadaten, Sortieren, Breite
anpassen) und Suchoptionen (Filter, Dateiinhalte, Suche schliessen) – nur auf
dem Test-Bildschirm:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_spalten_suche.py OUTDIR
"""
import os
import shutil
import struct
import subprocess
import sys
import time
import wave
import zipfile

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

from filefenster import columns, places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_KEY, COL_NAME  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-spalten")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(f"{T}/Unterordner")
subprocess.run(["convert", "-size", "640x480", "gradient:#88f-#004", f"{T}/foto.png"], check=True)
subprocess.run(["convert", "-size", "100x50", "xc:red", f"{T}/klein.png"], check=True)
with wave.open(f"{T}/ton.wav", "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(8000)
    w.writeframes(struct.pack("<h", 0) * 8000 * 3)
with zipfile.ZipFile(f"{T}/brief.docx", "w") as zf:
    zf.writestr("[Content_Types].xml", "<Types/>")
    zf.writestr("docProps/core.xml", '<cp:coreProperties xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:cp="x"><dc:title>Angebot Frühling</dc:title><dc:creator>Erika Muster</dc:creator>'
                '</cp:coreProperties>')
with open(f"{T}/handbuch.pdf", "wb") as fh:
    fh.write(b"%PDF-1.4\n1 0 obj << /Title (Testhandbuch) /Author (Max Muster) >> endobj\ntrailer << /Info 1 0 R >>\n%%EOF\n")
with open(f"{T}/bericht.txt", "w") as fh:
    fh.write("Hier steht das Geheimwort im Inhalt.")
open(f"{T}/leer.txt", "w").close()
with open(f"{T}/alt.txt", "w") as fh:
    fh.write("alt")
old = time.time() - 40 * 86400
os.utime(f"{T}/alt.txt", (old, old))
D = display.Display()
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def menus():
    labels, wins = [], []

    def walk(w):
        if isinstance(w, Gtk.Label) and w.get_text():
            labels.append(w.get_text())
        if isinstance(w, Gtk.Container):
            w.forall(walk)
    for t in Gtk.Window.list_toplevels():
        if t.get_visible() and isinstance(t.get_child(), Gtk.Menu):
            walk(t.get_child())
            wins.append(t.get_child())
    return labels, wins


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.uri_for_path(T))
        self.win.move(0, 0)
        self.win.resize(1500, 640)
        self.win.present()
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

    def fv(self):
        return self.win.current.views["folder"]

    def cell(self, key, name):
        f = self.fv()
        k = next(r[COL_KEY] for r in f.tree.get_model() if r[COL_NAME] == name)
        gf, info = f._entries[k]
        return columns.text(key, gf, info)

    def names(self):
        return [r[COL_NAME] for r in self.fv().tree.get_model() or [] if r[COL_KEY]]

    def script(self):
        win, f = self.win, self.fv()
        cfg = win.config
        # 1 Rechtsklick auf den Spaltenkopf → Menü mit Spalten
        btn = f.col_date.get_button()
        x, y = btn.translate_coordinates(win, 30, 12)
        _r, ox, oy = win.get_window().get_origin()
        xtest.fake_input(D, X.MotionNotify, x=ox + x, y=oy + y)
        D.sync()
        yield 200
        xtest.fake_input(D, X.ButtonPress, 3)
        xtest.fake_input(D, X.ButtonRelease, 3)
        D.sync()
        yield 500
        labels, wins = menus()
        check("Kopf-Menü mit Spalten und „Weitere…“",
              {"Erstelldatum", "Abmessungen", "Länge", "Autoren", "Weitere…",
               "Spaltenbreite anpassen"} <= set(labels))
        for m in wins:
            m.popdown()
        # 2 Spalten einblenden
        for key in ("dims", "length", "authors", "title", "ctime"):
            f._toggle_column(key)
        yield 300
        check("Spalten sichtbar und gespeichert",
              all(f._opt_cols[k].get_visible() for k in ("dims", "length", "authors", "title", "ctime"))
              and "dims" in cfg.get("columns"))
        for _i in range(40):                      # Metadaten im Hintergrund
            yield 150
            if self.cell("length", "ton.wav") and self.cell("authors", "handbuch.pdf"):
                break
        yield 300
        check("Abmessungen (Bild)", self.cell("dims", "foto.png") == "640 x 480")
        check("Länge (Ton)", self.cell("length", "ton.wav") == "00:00:03")
        check("Autor/Titel (DOCX)", self.cell("authors", "brief.docx") == "Erika Muster"
              and self.cell("title", "brief.docx") == "Angebot Frühling")
        check("Autor/Titel (PDF)", self.cell("authors", "handbuch.pdf") == "Max Muster"
              and self.cell("title", "handbuch.pdf") == "Testhandbuch")
        k = next(r[COL_KEY] for r in self.fv().tree.get_model() if r[COL_NAME] == "bericht.txt")
        has_btime = bool(columns._birthtime(*self.fv()._entries[k]))
        check("Erstelldatum (leer, wenn das Dateisystem keins speichert)",
              bool(self.cell("ctime", "bericht.txt")) == has_btime)
        subprocess.run(["import", "-window", "root", "-crop", "1500x640+0+0", os.path.join(outdir, "spalten.png")])
        # 3 Sortieren nach Abmessungen (Klick auf den Kopf)
        f.set_sort("dims", False)
        yield 400
        files = [n for n in self.names() if n.endswith(".png")]
        check("Sortieren nach Abmessungen", files == ["foto.png", "klein.png"])
        f.set_sort("name", True)
        # 4 Breite anpassen
        f._opt_cols["title"].set_fixed_width(40)
        f._autosize(f._opt_cols["title"])
        check("Spaltenbreite anpassen", f._opt_cols["title"].get_fixed_width() > 90)
        # 5 „Weitere…“: Typ ausblenden
        dlg = columns.ChooseDialog(win, cfg)
        yield 300
        dlg.checks["type"].set_active(False)
        dlg._ok()
        yield 300
        check("„Details auswählen“ blendet Typ aus", not f.col_type.get_visible() and "type" not in cfg.get("columns"))
        f._toggle_column("type")
        # 6 Suchoptionen
        win.start_search("Art:Bild")
        yield 1200
        check("Suchoptionen-Knopf während der Suche", win.search_box.get_visible())
        check("Filter Art:Bild", sorted(self.names()) == ["foto.png", "klein.png"])
        win._set_search_filter("Grösse", "Leer (0 KB)")
        yield 1200
        check("Filter Art + Grösse kombiniert (nichts)", self.names() == [])
        win.start_search("Grösse:Leer")
        yield 1200
        check("Filter Grösse:Leer", self.names() == ["leer.txt"])
        win.start_search("txt Änderungsdatum:Heute")
        yield 1200
        check("Text + Änderungsdatum:Heute", sorted(self.names()) == ["bericht.txt", "leer.txt"])
        win.start_search("Geheimwort")
        yield 1200
        check("Ohne Dateiinhalte: kein Treffer", self.names() == [])
        win._toggle_search_content()
        yield 1500
        check("Mit Dateiinhalte: bericht.txt", self.names() == ["bericht.txt"])
        win._toggle_search_content()
        win._close_search()
        yield 800
        check("Suche schliessen", not win.current.search_text and not win.search_box.get_visible())


App().run([sys.argv[0]])
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
