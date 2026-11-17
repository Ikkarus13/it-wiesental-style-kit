#!/usr/bin/env python3
"""Papierkorb und Rückgängig/Wiederholen mit dem ECHTEN Papierkorb testen:
    python3 dev/test_undo_trash.py OUTDIR
Arbeitet nur in ~/.cache/filefenster-undotest (Papierkorb braucht dasselbe
Dateisystem wie ~) und entfernt danach nur die eigenen Papierkorb-Einträge.
Leert NIE den ganzen Papierkorb. Screenshot der Papierkorb-Ansicht nach OUTDIR.
FILEFENSTER_CONFIG_DIR setzen!
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib  # noqa: E402

from filefenster import fileops, places, undo  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-undotest")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(f"{T}/ordner")
os.makedirs(f"{T}/ziel")
for n, txt in (("a.txt", "A"), ("b.txt", "B"), ("ordner/x.txt", "X")):
    with open(f"{T}/{n}", "w") as fh:
        fh.write(txt)

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("OK  ", name, flush=True)
    else:
        fail += 1
        print("FAIL", name, flush=True)


def G(p):
    return Gio.File.new_for_path(f"{T}/{p}")


def E(p):
    return os.path.exists(f"{T}/{p}")


def own_trash():
    return [(g, i) for g, i in fileops.trash_items()
            if (i.get_attribute_byte_string("trash::orig-path") or "").startswith(T)]


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = win = ExplorerWindow(self, places.uri_for_path(T))
        win.resize(1150, 560)
        win.move(80, 60)
        win.present()

        def shot(name):
            gw = win.get_window()
            _, x, y = gw.get_origin()
            path = os.path.join(outdir, name + ".png")
            subprocess.run(["import", "-window", "root", "-crop",
                            f"{gw.get_width()}x{gw.get_height()}+{x}+{y}", path])
            print("shot", path, flush=True)

        steps = [
            lambda: fileops.rename(G("a.txt"), "umbenannt.txt"),
            lambda: check("Umbenennen", E("umbenannt.txt") and not E("a.txt")),
            lambda: check("Menütext", undo.undo_label() == "Rückgängig: Umbenennen"),
            lambda: undo.undo(win),
            lambda: check("Rückgängig Umbenennen", E("a.txt") and not E("umbenannt.txt")),
            lambda: undo.redo(win),
            lambda: check("Wiederholen Umbenennen", E("umbenannt.txt")),
            lambda: undo.undo(win),
            # Kopieren
            lambda: fileops.transfer([G("b.txt")], G("ziel"), parent=win),
            lambda: check("Kopieren", E("ziel/b.txt")),
            lambda: undo.undo(win),
            lambda: check("Rückgängig Kopieren (Kopie im Papierkorb)",
                          not E("ziel/b.txt") and E("b.txt")
                          and any(i.get_attribute_byte_string("trash::orig-path") == f"{T}/ziel/b.txt"
                                  for _g, i in own_trash())),
            lambda: undo.redo(win),
            lambda: check("Wiederholen Kopieren (aus Papierkorb)", E("ziel/b.txt")),
            # Verschieben
            lambda: fileops.transfer([G("ordner")], G("ziel"), move=True, parent=win),
            lambda: check("Verschieben", E("ziel/ordner/x.txt") and not E("ordner")),
            lambda: undo.undo(win),
            lambda: check("Rückgängig Verschieben", E("ordner/x.txt") and not E("ziel/ordner")),
            lambda: undo.redo(win),
            lambda: check("Wiederholen Verschieben", E("ziel/ordner/x.txt")),
            lambda: undo.undo(win),
            # Löschen (Papierkorb)
            lambda: fileops.delete([G("a.txt")], parent=win),
            lambda: check("Löschen in Papierkorb", not E("a.txt")),
            lambda: undo.undo(win),
            lambda: check("Rückgängig Löschen", E("a.txt") and open(f"{T}/a.txt").read() == "A"),
            lambda: undo.redo(win),
            lambda: check("Wiederholen Löschen", not E("a.txt")),
            # Papierkorb-Ansicht
            lambda: win.current.navigate("trash:///"),
            lambda: None,
            lambda: shot("papierkorb"),
            lambda: check("Papierkorb-Knöpfe sichtbar", win.trash_box.get_visible()),
            lambda: fileops.restore([g for g, i in own_trash()
                                     if i.get_attribute_byte_string("trash::orig-path") == f"{T}/a.txt"],
                                    parent=win),
            lambda: check("Wiederherstellen", E("a.txt")),
            lambda: undo.undo(win),
            lambda: check("Rückgängig Wiederherstellen", not E("a.txt")),
            # Neuer Ordner
            lambda: fileops.new_folder(Gio.File.new_for_path(T)),
            lambda: check("Neuer Ordner", E("Neuer Ordner")),
            lambda: undo.undo(win),
            lambda: check("Rückgängig Neu", not E("Neuer Ordner")),
            lambda: undo.redo(win),
            lambda: check("Wiederholen Neu", E("Neuer Ordner")),
            # Ordner im Papierkorb endgültig löschen (nur eigener Eintrag)
            lambda: fileops.delete([G("ziel")], parent=win, record=False),
            lambda: fileops._delete_permanently(
                [g for g, i in own_trash()
                 if i.get_attribute_byte_string("trash::orig-path") == f"{T}/ziel"], win, None),
            lambda: check("Papierkorb-Ordner endgültig gelöscht",
                          not any(i.get_attribute_byte_string("trash::orig-path") == f"{T}/ziel"
                                  for _g, i in own_trash())),
            lambda: self.quit(),
        ]

        def run(i=0):
            if i >= len(steps):
                return False
            if fileops.busy():
                GLib.timeout_add(100, lambda: run(i))
                return False
            try:
                steps[i]()
            except Exception as exc:
                import traceback
                traceback.print_exc()
                print("FEHLER in Schritt", i, exc, flush=True)
            GLib.timeout_add(700 if i in (27, 28) else 250, lambda: run(i + 1))
            return False
        GLib.timeout_add(800, run)


App().run([sys.argv[0]])
# Aufräumen: nur eigene Papierkorb-Einträge
for g, _i in own_trash():
    try:
        g.delete(None)
    except GLib.Error as exc:
        print("Aufräumen:", exc.message)
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden; eigene Papierkorb-Reste: {len(own_trash())}")
