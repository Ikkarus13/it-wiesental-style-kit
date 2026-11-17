#!/usr/bin/env python3
"""ZIP komprimieren/extrahieren, TAR, 7z, Zip-Slip-Schutz, Konflikte,
Rückgängig, Extrahieren-Dialog und ISO bereitstellen testen – nur auf dem
Test-Bildschirm:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_archive.py OUTDIR

Mit FILEFENSTER_ISO_TEST=1 wird zusätzlich ein Test-ISO über UDisks wirklich
kurz eingehängt (/media/…) und wieder ausgehängt – das wirkt auf den echten
Desktop (Automounter öffnet ein Fenster), deshalb standardmässig aus. Papierkorb: nur eigene Einträge werden aufgeräumt.
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
from gi.repository import Gio, GLib  # noqa: E402

from filefenster import archive, diskimage, fileops, places, progress, undo  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-ziptest")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(f"{T}/Projekt/sub")
os.makedirs(f"{T}/Projekt/leer")
for p, txt in (("Projekt/a.txt", "A" * 5000), ("Projekt/sub/b.txt", "B"), ("Bericht.txt", "Bericht")):
    with open(f"{T}/{p}", "w") as fh:
        fh.write(txt)
with open(f"{T}/Projekt/start.sh", "w") as fh:
    fh.write("#!/bin/sh\necho hallo\n")
os.chmod(f"{T}/Projekt/start.sh", 0o755)
with zipfile.ZipFile(f"{T}/boese.zip", "w") as zf:
    zf.writestr("../ausbruch.txt", "x")
    zf.writestr("/absolut.txt", "x")
    zf.writestr("gut.txt", "gut")
with tarfile.open(f"{T}/paket.tar.gz", "w:gz") as tf:
    tf.add(f"{T}/Projekt/sub", arcname="tarinhalt")
subprocess.run(["7z", "a", "-bso0", "-bsp0", f"{T}/sieben.7z", f"{T}/Bericht.txt"], check=False)
ISO = f"{T}/TestCD.iso"
os.makedirs(f"{T}/isoinhalt")
with open(f"{T}/isoinhalt/hallo.txt", "w") as fh:
    fh.write("Hallo von der CD")
subprocess.run(["genisoimage", "-quiet", "-V", "FFTESTCD", "-J", "-r", "-o", ISO, f"{T}/isoinhalt"],
               check=True)

ok = fail = 0
ERR = []
fileops.show_errors = lambda parent, title, errors: ERR.append((title, errors))


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def G(p):
    return Gio.File.new_for_path(f"{T}/{p}")


def E(p):
    return os.path.lexists(f"{T}/{p}")


def shot(win, name):
    gw = win.get_window()
    _r, x, y = gw.get_origin()
    subprocess.run(["import", "-window", "root", "-crop",
                    f"{gw.get_width()}x{gw.get_height()}+{x}+{y}", os.path.join(outdir, name + ".png")])


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.uri_for_path(T))
        self.win.move(0, 0)
        self.win.resize(1100, 700)
        self.win.present()
        self.steps = self.script(self.win)
        GLib.timeout_add(1200, self.tick)

    def tick(self):
        try:
            d = next(self.steps)
            GLib.timeout_add(d, self.tick)
        except StopIteration:
            self.quit()
        except Exception:
            import traceback
            traceback.print_exc()
            self.quit()
        return False

    def wait_jobs(self):
        while fileops.busy():
            yield 100

    def script(self, win):
        hdr = {}
        orig = progress.watch

        def watch(job, parent):
            orig(job, parent)
            hdr[job.kind] = True
        progress.watch = watch
        # 1 Komprimieren
        fv = win.current.views["folder"]
        fv.compress([G("Projekt"), G("Bericht.txt")])
        yield from self.wait_jobs()
        yield 400
        names = sorted(zipfile.ZipFile(f"{T}/Projekt.zip").namelist()) if E("Projekt.zip") else []
        check("Komprimieren: Projekt.zip mit Inhalt",
              names == ["Bericht.txt", "Projekt/a.txt", "Projekt/leer/", "Projekt/start.sh", "Projekt/sub/b.txt"])
        check("Komprimieren: danach Umbenennen aktiv", fv._renaming)
        fv._cancel_rename()
        check("Rückgängig-Text nach Komprimieren", undo.undo_label() == "Rückgängig: Neu")
        archive.compress([G("Projekt")])
        yield from self.wait_jobs()
        check("Zweites Mal: Projekt (2).zip", E("Projekt (2).zip"))
        # 2 Extrahieren
        archive.extract(G("Projekt.zip"), G("aus"))
        yield from self.wait_jobs()
        check("Extrahieren: Inhalt", open(f"{T}/aus/Projekt/a.txt").read() == "A" * 5000
              and E("aus/Projekt/leer") and E("aus/Bericht.txt"))
        check("Extrahieren: Ausführrecht erhalten", os.access(f"{T}/aus/Projekt/start.sh", os.X_OK))
        # 3 Konflikt: noch einmal in denselben Ordner → „Beide behalten“ für alle
        with open(f"{T}/aus/Bericht.txt", "w") as fh:
            fh.write("geändert")
        archive.extract(G("Projekt.zip"), G("aus"))
        for _i in range(50):
            j = next((j for j in fileops.running_jobs() if j.conflict), None)
            if j:
                break
            yield 100
        check("Konflikt-Rückfrage beim Extrahieren", j is not None)
        if j:
            yield 600
            shot(progress._win, "konflikt")
            j.answer("keep", True)
        yield from self.wait_jobs()
        check("Beide behalten", open(f"{T}/aus/Bericht.txt").read() == "geändert"
              and E("aus/Bericht (2).txt"))
        # 4 Zip-Slip
        ERR.clear()
        archive.extract(G("boese.zip"), G("boese"))
        yield from self.wait_jobs()
        check("Zip-Slip abgewehrt", not E("ausbruch.txt") and not os.path.exists("/absolut.txt")
              and E("boese/gut.txt") and E("boese/absolut.txt") and ERR and len(ERR[0][1]) == 1)
        # 5 TAR und 7z
        archive.extract(G("paket.tar.gz"), G("tar"))
        yield from self.wait_jobs()
        check("TAR.GZ extrahieren", E("tar/tarinhalt/b.txt"))
        if shutil.which("7z"):
            archive.extract(G("sieben.7z"), G("sieben"))
            yield from self.wait_jobs()
            check("7z extrahieren", E("sieben/Bericht.txt"))
        # 6 Rückgängig nach Extrahieren
        undo.undo(win)
        yield from self.wait_jobs()
        yield 300
        check("Rückgängig Extrahieren entfernt neuen Ordner",
              not E("sieben") if shutil.which("7z") else not E("tar"))
        check("Fortschrittstexte vorhanden", hdr.get("zip") and hdr.get("extract"))
        # 7 Dialog „Alle extrahieren“
        dlg = archive.extract_dialog(G("Projekt.zip"), win, win.current)
        yield 600
        check("Dialog: Standardziel", dlg.entry.get_text() == f"{T}/Projekt")
        shot(dlg, "extrahieren-dialog")
        dlg.show_after.set_active(False)
        dlg._go()
        yield from self.wait_jobs()
        check("Dialog: extrahiert", E("Projekt/Projekt/a.txt"))
        # 8 ISO bereitstellen – hängt wirklich ein; der Desktop-Automounter öffnet
        # dabei ein Fenster auf dem ECHTEN Bildschirm → nur auf ausdrücklichen Wunsch
        if os.environ.get("FILEFENSTER_ISO_TEST") != "1":
            print("ISO-Test übersprungen (FILEFENSTER_ISO_TEST=1 schaltet ihn ein; "
                  "öffnet ein Fenster auf dem Arbeitsbildschirm)", flush=True)
            return
        got = {}
        ERR.clear()
        diskimage.mount(G("TestCD.iso"), win, lambda p, *_: got.setdefault("p", p))
        for _i in range(100):
            if "p" in got or ERR:
                break
            yield 100
        p = got.get("p")
        check("ISO bereitgestellt", bool(p) and open(os.path.join(p, "hallo.txt")).read() == "Hallo von der CD")
        if ERR:
            print("Fehler:", ERR[-1])
        got.clear()
        diskimage.mount(G("TestCD.iso"), win, lambda p, *_: got.setdefault("p", p))
        for _i in range(50):
            if "p" in got:
                break
            yield 100
        check("Zweites Bereitstellen öffnet nur", got.get("p") == p)
        self.unmount_iso()
        yield 800
        check("ISO wieder ausgehängt", not p or not os.path.exists(os.path.join(p, "hallo.txt")))

    def unmount_iso(self):
        bus = diskimage._bus()
        objs = diskimage._objects(bus)
        loop = diskimage._find_loop(objs, ISO)
        if not loop:
            return
        for obj, mounts in diskimage._filesystems(objs, loop):
            if mounts:
                bus.call_sync(diskimage.UD, obj, diskimage.UD + ".Filesystem", "Unmount",
                              GLib.Variant("(a{sv})", ({},)), None, Gio.DBusCallFlags.NONE, 20000, None)
        try:
            bus.call_sync(diskimage.UD, loop, diskimage.UD + ".Loop", "Delete",
                          GLib.Variant("(a{sv})", ({},)), None, Gio.DBusCallFlags.NONE, 20000, None)
        except GLib.Error:
            pass     # Autoclear hat es schon entfernt


App().run([sys.argv[0]])
for g, i in fileops.trash_items():
    if (i.get_attribute_byte_string("trash::orig-path") or "").startswith(T):
        try:
            g.delete(None)
        except GLib.Error as exc:
            print("Aufräumen:", exc.message)
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
