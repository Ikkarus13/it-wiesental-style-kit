#!/usr/bin/env python3
"""Ablauf-Test des Gesamtfensters mit Screenshots:
    python3 dev/test_flow.py OUTDIR ORDNER
Schritte: Details → Grosse Symbole → Suche "pdf" → Suche aus, erste Datei
auswählen + Detailbereich → Navigation nach oben + neuer Tab.
Nur lesende Aktionen am Ordner. FILEFENSTER_CONFIG_DIR setzen!
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

from filefenster import places  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402

outdir, folder = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)


class FlowApp(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        win = ExplorerWindow(self, places.uri_for_path(folder))
        win.resize(1300, 820)
        win.move(80, 60)
        win.present()

        def shot(name):
            gw = win.get_window()
            _, x, y = gw.get_origin()
            w, h = gw.get_width(), gw.get_height()
            path = os.path.join(outdir, name + ".png")
            subprocess.run(["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", path])
            print("shot", path, flush=True)

        def fv():
            return win.current.folder_view()

        steps = [
            (1500, lambda: shot("1_details")),
            (300, lambda: win.set_view_mode("large")),
            (1500, lambda: shot("2_large")),
            (300, lambda: win.set_view_mode("details")),
            (300, lambda: (win.navrow.set_search_text("pdf"), win.start_search("pdf"))),
            (2500, lambda: shot("3_search")),
            (300, lambda: (win.navrow.clear_search(), win.stop_search())),
            (800, lambda: win.btn_details.set_active(True)),
            (300, lambda: _select_first_file(fv())),
            (1500, lambda: shot("4_details_pane")),
            (300, lambda: win.btn_details.set_active(False)),
            (300, lambda: win.go_up()),
            (300, lambda: win.new_tab(places.THISPC, select=True)),
            (1500, lambda: shot("5_thispc_tab")),
            (300, lambda: self.quit()),
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
            return False
        run()


def _select_first_file(view):
    """Erste Datei (kein Ordner) in der Detailansicht auswählen."""
    from gi.repository import Gio
    tv = getattr(view, "tree", None) or getattr(view, "treeview", None)
    if tv is None:
        for name in dir(view):
            obj = getattr(view, name, None)
            if obj.__class__.__name__ == "TreeView":
                tv = obj
                break
    model = tv.get_model()
    sel = tv.get_selection()
    it = model.get_iter_first()
    while it is not None:
        path = model.get_path(it)
        sel.unselect_all()
        sel.select_path(path)
        files = view.get_selected_infos()
        if files and files[0].get_file_type() != Gio.FileType.DIRECTORY:
            tv.set_cursor(path, None, False)
            return
        it = model.iter_next(it)


FlowApp().run([sys.argv[0]])
