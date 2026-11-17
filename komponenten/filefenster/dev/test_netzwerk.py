#!/usr/bin/env python3
"""Netzwerk/SMB testen – nur auf dem Test-Bildschirm. Das Einhängen wird
simuliert (kein echter Server, keine Anmeldung); die Netzwerk-Ansicht wird
nur gelesen.

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_netzwerk.py OUTDIR
"""
import os
import subprocess
import sys

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99)")
if not os.environ.get("FILEFENSTER_CONFIG_DIR"):
    sys.exit("FILEFENSTER_CONFIG_DIR setzen (gemerkte Laufwerke landen in der Konfiguration)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from filefenster import network, places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_INFO, COL_KEY, is_dir  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
ok = fail = 0
MOUNTS = []
RESULT = {"ok": True}


def fake_mount(gfile, parent, callback):
    MOUNTS.append(gfile.get_uri())
    GLib.timeout_add(150, lambda: callback(RESULT["ok"], None if RESULT["ok"] else "Zugriff verweigert") and False)


network.mount = fake_mount


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def menu_labels():
    out = []

    def walk(w):
        if isinstance(w, Gtk.Label) and w.get_text():
            out.append(w.get_text())
        if isinstance(w, Gtk.Container):
            w.forall(walk)
    for t in Gtk.Window.list_toplevels():
        if t.get_visible() and isinstance(t.get_child(), Gtk.Menu):
            walk(t.get_child())
            t.get_child().popdown()
    return out


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, "network:///")
        self.win.move(0, 0)
        self.win.resize(1000, 640)
        self.win.present()
        self.opened = []
        self.win.current.open_in_new_window = lambda loc: self.opened.append(loc)
        self.steps = self.script()
        GLib.timeout_add(2500, self.tick)

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
        fv = win.current.views["folder"]
        rows = [(r[COL_KEY], r[COL_INFO]) for r in fv.tree.get_model() or [] if r[COL_KEY]]
        check("Netzwerk-Ansicht lädt (nur lesen)", fv._error in (None, "") and not fv._loading)
        check("Gefundene Geräte gelten als Ordner", all(is_dir(info) for _k, info in rows))
        check("Brotkrume „Netzwerk“", [c[0] for c in places.breadcrumbs(win.current.location)] == ["Netzwerk"])
        # ···-Menü bietet „Netzlaufwerk verbinden“
        win._on_more_menu(win.btn_more)
        yield 400
        check("···-Menü: Netzlaufwerk verbinden", "Netzlaufwerk verbinden" in menu_labels())
        # Dialog: ungültige Eingabe
        dlg = network.connect_dialog(win, win.current)
        yield 400
        dlg.entry.set_text("kein pfad")
        dlg._go()
        yield 200
        check("Ungültiger Pfad: Hinweis im Dialog", dlg.error.get_visible() and dlg.get_visible())
        # Anmeldung schlägt fehl
        RESULT["ok"] = False
        dlg.entry.set_text(r"\\nas\Daten")
        dlg._go()
        yield 500
        check("Fehlgeschlagen: Dialog bleibt mit Meldung", dlg.get_visible() and "Zugriff verweigert" in dlg.error.get_text())
        subprocess.run(["import", "-window", "root", "-crop", "1000x640+0+0", os.path.join(outdir, "netzlaufwerk.png")])
        # klappt
        RESULT["ok"] = True
        dlg._go()
        yield 500
        check("Verbinden hängt smb://nas/Daten ein", MOUNTS and MOUNTS[-1].rstrip("/").lower() == "smb://nas/daten")
        check("… öffnet das Laufwerk", self.opened == ["smb://nas/Daten"])
        check("… und merkt es sich", network.is_saved("smb://nas/Daten"))
        # Dieser PC zeigt das gemerkte Laufwerk unter Netzwerkadressen
        win.current.navigate(places.THISPC)
        yield 900
        view = win.current.views.get("thispc")
        net = [c for c in view.flow_net.get_children()] if view else []
        check("Dieser PC: Laufwerk unter Netzwerkadressen",
              any(c.drive["label"] == "Daten (\\\\nas)" for c in net) and view.sec_net.get_visible())
        tile = next((c for c in net if c.drive["label"] == "Daten (\\\\nas)"), None)
        if tile is not None:
            menu = view.tile_menu(tile)
            labels = []

            def walk(w):
                if isinstance(w, Gtk.Label) and w.get_text():
                    labels.append(w.get_text())
                if isinstance(w, Gtk.Container):
                    w.forall(walk)
            walk(menu.menu)
            check("Kontextmenü: Trennen", "Trennen" in labels)
            network.disconnect(win, tile.uri, None, None)
            yield 300
            check("Trennen entfernt das Laufwerk", not network.is_saved("smb://nas/Daten")
                  and not any(d["kind"] == "network" and "nas" in d["label"] for d in places.drives()))


App().run([sys.argv[0]])
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
