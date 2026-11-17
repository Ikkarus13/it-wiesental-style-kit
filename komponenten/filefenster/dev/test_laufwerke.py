#!/usr/bin/env python3
"""Nicht eingehängte Wechseldatenträger (USB-Stick) mit einem SIMULIERTEN
Laufwerk testen – kein echtes Gerät, keine Wirkung auf den Desktop:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_laufwerke.py OUTDIR
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
from gi.repository import Gio, GLib  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-stick")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(T)
with open(f"{T}/auf-dem-stick.txt", "w") as fh:
    fh.write("x")

REAL = Gio.VolumeMonitor.get()


class FakeDrive:
    def is_removable(self): return True
    def is_media_removable(self): return True
    def can_eject(self): return True


class FakeMount:
    def get_root(self): return Gio.File.new_for_path(T)
    def get_name(self): return "SANDISK"
    def is_shadowed(self): return False
    def can_eject(self): return True
    def can_unmount(self): return True


class FakeVolume:
    mounted = None
    mount_calls = 0

    def get_mount(self): return self.mounted
    def can_mount(self): return True
    def get_drive(self): return FakeDrive()
    def get_name(self): return "SANDISK"
    def get_uuid(self): return "1234-ABCD"
    def get_identifier(self, kind): return "/dev/sdz1" if kind == "unix-device" else None

    def mount(self, flags, op, cancellable, callback):
        FakeVolume.mount_calls += 1
        GLib.timeout_add(100, lambda: callback(self, None) and False)

    def mount_finish(self, res):
        self.mounted = FakeMount()
        return True


VOL = FakeVolume()


class FakeMonitor:
    def get_mounts(self):
        return REAL.get_mounts() + ([VOL.mounted] if VOL.mounted else [])
    def get_volumes(self): return [VOL]
    def connect(self, *a): return 0
    def disconnect(self, *a): pass


Gio.VolumeMonitor.get = staticmethod(lambda: FakeMonitor())

from filefenster import places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_NAME  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.THISPC)
        self.win.move(0, 0)
        self.win.resize(1000, 640)
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

    def script(self):
        win = self.win
        d = [x for x in places.drives() if x["label"] == "SANDISK"]
        check("Stick in der Laufwerksliste (nicht eingehängt)", d and d[0]["uri"].startswith("volume:"))
        view = win.current.views.get("thispc")
        tiles = [c.uri for f in view._flows() for c in f.get_children()] if view else []
        check("Kachel unter „Dieser PC“", any(u.startswith("volume:") for u in tiles))
        gw = win.get_window()
        _r, x, y = gw.get_origin()
        subprocess.run(["import", "-window", "root", "-crop",
                        f"{gw.get_width()}x{gw.get_height()}+{x}+{y}", os.path.join(outdir, "stick.png")])
        np = win.navpane
        tops = []
        np.store.foreach(lambda m, p, it: tops.append(m[it][2]) if p.get_depth() == 1 else None)
        check("Stick als eigener Eintrag im Navigationsbereich (wie Windows)",
              "SANDISK" in tops and tops.index("Dieser PC") < tops.index("SANDISK") < tops.index("Netzwerk"))
        # Navigationsbereich: Dieser PC aufklappen
        it = np.store.get_iter_first()
        while it is not None and np.store[it][0] != "thispc":
            it = np.store.iter_next(it)
        np._load_children(it, True)
        yield 400
        labels = []
        child = np.store.iter_children(it)
        while child is not None:
            labels.append(np.store[child][2])
            child = np.store.iter_next(child)
        check("Stick im Navigationsbereich unter Dieser PC", "SANDISK" in labels)
        # Klick → einhängen → öffnen
        win.current.navigate(d[0]["uri"])
        yield 900
        check("Klick hängt ein", FakeVolume.mount_calls == 1)
        check("… und öffnet den Stick", win.current.location == places.uri_for_path(T))
        f = win.current.views.get("folder")
        names = [r[COL_NAME] for r in f.tree.get_model()] if f and f.tree.get_model() else []
        check("Inhalt sichtbar", "auf-dem-stick.txt" in names)
        # schon eingehängt: kein zweites Einhängen
        win.current.navigate(d[0]["uri"])
        yield 500
        check("Eingehängt: nur öffnen", FakeVolume.mount_calls == 1)
        check("Nach dem Einhängen nicht mehr als „nicht eingehängt“ gelistet",
              not any((x["uri"] or "").startswith("volume:") for x in places.drives()))


App().run([sys.argv[0]])
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
