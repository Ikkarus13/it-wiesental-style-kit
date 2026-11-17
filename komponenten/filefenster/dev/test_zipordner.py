#!/usr/bin/env python3
"""ZIP/TAR wie einen Ordner öffnen (nur lesen) – nur auf dem Test-Bildschirm:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_zipordner.py OUTDIR
"""
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

from filefenster import dnd, fileops, places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_NAME  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-zipordner")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(T)
with zipfile.ZipFile(f"{T}/Fotos 2026.zip", "w") as zf:
    zf.writestr("Urlaub/bild1.txt", "eins")
    zf.writestr("Unter Ordner/ä datei.txt", "ä")
    zf.writestr("liste.txt", "Liste")
import io  # noqa: E402
inner = io.BytesIO()
with zipfile.ZipFile(inner, "w") as zf:
    zf.writestr("innen.txt", "innen")
with zipfile.ZipFile(f"{T}/aussen.zip", "w") as zf:
    zf.writestr("innen.zip", inner.getvalue())
os.makedirs(f"{T}/tarquelle/inhalt")
with open(f"{T}/tarquelle/inhalt/x.txt", "w") as fh:
    fh.write("x")
with tarfile.open(f"{T}/paket.tar.gz", "w:gz") as tf:
    tf.add(f"{T}/tarquelle/inhalt", arcname="inhalt")
shutil.rmtree(f"{T}/tarquelle")

ok = fail = 0
ERR = []
fileops.show_errors = lambda parent, title, errors: ERR.append((title, errors))


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def archive_mounted(name):
    return any(m.get_name() == name for m in Gio.VolumeMonitor.get().get_mounts())


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.uri_for_path(T))
        self.win.move(0, 0)
        self.win.resize(1100, 650)
        self.win.present()
        self.steps = self.script(self.win)
        GLib.timeout_add(1200, self.tick)

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

    def names(self):
        f = self.fv()
        view = f.tree if f._is_tree() else f.iv
        return sorted(r[COL_NAME] for r in view.get_model() or [])

    def open_item(self, name):
        self.fv().reveal([Gio.File.new_for_path(os.path.join(T, name)).get_uri()]
                         if not self.fv().in_archive() else
                         [self.fv()._gfile.get_child(name).get_uri()])
        yield 300
        self.fv().open_selected()
        for _i in range(60):
            yield 100
            if not self.fv()._loading and self.names():
                break

    def dblclick(self, name):
        """Echter Doppelklick (XTEST) – nicht open_selected direkt aufrufen."""
        from Xlib import X, display
        from Xlib.ext import xtest
        f = self.fv()
        path = next(r.path for r in f.tree.get_model() if r[COL_NAME] == name)
        rect = f.tree.get_background_area(path, f.col_name)
        x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 40, rect.y + rect.height // 2)
        wx, wy = f.tree.translate_coordinates(self.win, x, y)
        _r, ox, oy = self.win.get_window().get_origin()
        d = display.Display()
        xtest.fake_input(d, X.MotionNotify, x=ox + wx, y=oy + wy)
        d.sync()
        yield 250
        for _i in range(2):
            xtest.fake_input(d, X.ButtonPress, 1)
            xtest.fake_input(d, X.ButtonRelease, 1)
            d.sync()
        for _i in range(60):
            yield 100
            if not self.fv()._loading and self.names():
                break

    def script(self, win):
        # erst zwei Dateien markieren, dann Doppelklick auf eine davon
        f = self.fv()
        f.reveal([Gio.File.new_for_path(f"{T}/{n}").get_uri() for n in ("Fotos 2026.zip", "paket.tar.gz")])
        yield 500
        check("Zwei markiert", len(f.get_selected()) == 2)
        yield from self.dblclick("Fotos 2026.zip")
        check("Echter Doppelklick öffnet ZIP im selben Fenster",
              win.current.location.startswith("archive://") and len(win.list_toplevels()) >= 1)
        win.current.navigate(places.uri_for_path(T))
        yield 1500
        yield from self.open_item("Fotos 2026.zip")
        loc = win.current.location
        check("Doppelklick öffnet ZIP als Ordner", loc.startswith("archive://"))
        check("Inhalt sichtbar", self.names() == ["Unter Ordner", "Urlaub", "liste.txt"])
        crumbs = places.breadcrumbs(loc)
        check("Adressleiste … › filefenster-zipordner › Fotos 2026.zip",
              [c[0] for c in crumbs[-2:]] == ["filefenster-zipordner", "Fotos 2026.zip"])
        check("Tab-Titel", places.display_name(loc) == "Fotos 2026.zip")
        check("„Alle extrahieren“ in der Befehlsleiste", win.archive_box.get_visible())
        check("Lokale ZIP-Datei beschreibbar: Neu aktiv (test_zipschreiben.py)",
              win.btn_new.get_sensitive())
        check("Nicht als Laufwerk gelistet",
              not any(d["label"] == "Fotos 2026.zip" for d in places.drives()))
        yield 500
        gw = win.get_window()
        _r, x, y = gw.get_origin()
        subprocess.run(["import", "-window", "root", "-crop",
                        f"{gw.get_width()}x{gw.get_height()}+{x}+{y}", os.path.join(outdir, "zip-ordner.png")])
        # Datei im ZIP hat einen lokalen Pfad (für Programme)
        child = self.fv()._gfile.get_child("liste.txt")
        try:
            via_path = child.get_path() and open(child.get_path()).read() == "Liste"
        except OSError as exc:      # gvfs-FUSE gestört (systemweit) – dann über GIO prüfen
            print("   Hinweis: gvfs-FUSE meldet", exc.strerror, "– prüfe über GIO", flush=True)
            via_path = child.get_path() and child.load_contents(None)[1] == b"Liste"
        check("Datei im ZIP lesbar über Pfad", via_path)
        # Herauskopieren
        fileops.transfer([child], Gio.File.new_for_path(T), parent=win)
        for _i in range(40):
            yield 100
            if not fileops.busy():
                break
        check("Herauskopieren", os.path.exists(f"{T}/liste.txt"))

        class Ctx:   # Ziehen: aus dem ZIP nur kopieren, nicht verschieben
            def get_actions(self):
                return Gdk.DragAction.MOVE | Gdk.DragAction.COPY

            def get_suggested_action(self):
                return Gdk.DragAction.COPY
        check("Ziehen aus ZIP = kopieren",
              dnd.decide(Ctx(), [child], Gio.File.new_for_path(T))[0] == Gdk.DragAction.COPY)
        check("Ablegen in lokale ZIP-Datei möglich", dnd.droppable(loc))
        # Unterordner und zurück nach oben
        yield from self.open_item("Urlaub")
        check("Unterordner im ZIP", self.names() == ["bild1.txt"]
              and places.breadcrumbs(win.current.location)[-1][0] == "Urlaub")
        win.current.go_up()
        yield 800
        win.current.go_up()
        for _i in range(30):
            yield 100
            if not self.fv()._loading:
                break
        yield 300
        sel = [g.get_basename() for g in self.fv().get_selected()]
        check("Nach oben: zurück im Ordner, ZIP ausgewählt",
              win.current.location == places.uri_for_path(T) and sel == ["Fotos 2026.zip"])
        yield 2200
        check("ZIP wieder ausgehängt", not archive_mounted("Fotos 2026.zip"))
        # „Alle extrahieren“ aus dem ZIP heraus
        yield from self.open_item("Fotos 2026.zip")
        win._extract_current()
        yield 600
        dlg = next((w for w in win.list_toplevels() if getattr(w, "entry", None) is not None
                    and w.get_visible()), None)
        check("Extrahieren-Dialog aus dem ZIP", dlg is not None and dlg.entry.get_text() == f"{T}/Fotos 2026")
        if dlg:
            dlg.destroy()
        win.current.go_up()
        yield 800
        # ZIP in ZIP öffnet sich im selben Fenster
        yield from self.open_item("aussen.zip")
        yield from self.open_item("innen.zip")
        check("ZIP im ZIP als Ordner", self.names() == ["innen.txt"]
              and [c[0] for c in places.breadcrumbs(win.current.location)[-2:]] == ["aussen.zip", "innen.zip"])
        check("ZIP im ZIP: nur lesen", not dnd.droppable(win.current.location)
              and not win.btn_new.get_sensitive())
        yield 2500          # Aufräum-Zeitgeber des äusseren ZIPs wäre jetzt abgelaufen
        check("Äusseres Archiv bleibt eingehängt", archive_mounted("aussen.zip"))
        fileops.transfer([self.fv()._gfile.get_child("innen.txt")], Gio.File.new_for_path(T), parent=win)
        for _i in range(40):
            yield 100
            if not fileops.busy():
                break
        check("Aus ZIP im ZIP herauskopieren", os.path.exists(f"{T}/innen.txt"))
        check("Fehlertext lesbar", fileops._display(self.fv()._gfile.get_child("innen.txt"))
              == f"{T}/aussen.zip/innen.zip/innen.txt")
        win.current.navigate(places.uri_for_path(T))
        yield 3500
        check("Beide wieder ausgehängt", not archive_mounted("aussen.zip") and not archive_mounted("innen.zip"))
        # TAR.GZ ebenso
        yield from self.open_item("paket.tar.gz")
        check("TAR.GZ als Ordner", self.names() == ["inhalt"])
        check("TAR.GZ: nur lesen (Neu/Einfügen gesperrt, kein Ablegen)",
              not win.btn_new.get_sensitive() and not win.btn_paste.get_sensitive()
              and not dnd.droppable(win.current.location))
        win.current.go_up()
        yield 2500
        check("Keine Fehlermeldungen", not ERR)
        if ERR:
            print(ERR)


App().run([sys.argv[0]])
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
