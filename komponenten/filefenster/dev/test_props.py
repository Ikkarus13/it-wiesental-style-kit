#!/usr/bin/env python3
"""Test-Harness für den Eigenschaften-Dialog und message().

    timeout 20 python3 dev/test_props.py AUSGABE.png PFAD [PFAD …]
    timeout 20 python3 dev/test_props.py AUSGABE.png --message

Öffnet show_properties() für die Pfade (ein Pfad = Einzeldialog, mehrere =
Mehrfachauswahl), wechselt bei --perm auf "Berechtigungen" und speichert
einen Schnappschuss des Dialogs.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_CONFIG_DIR",
                      os.path.join(os.environ.get("TMPDIR", "/tmp"), "filefenster-test-config"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from filefenster import devtools, propsdialog, theme  # noqa: E402


def main():
    out = sys.argv[1]
    args = sys.argv[2:]
    theme.setup()
    parent = Gtk.Window(title="Eltern")
    parent.get_style_context().add_class("filefenster")
    parent.set_default_size(300, 200)
    parent.show_all()

    if "--message" in args:
        def show():
            def snap():
                for w in Gtk.Window.list_toplevels():
                    if w.get_style_context().has_class("win-dialog") and w.get_visible():
                        devtools.save_window_png(w, out)
                        print("gespeichert", out)
                        w.destroy()
                return False
            GLib.timeout_add(900, snap)
            r = propsdialog.message(parent, 'FileFenster findet "/gibt/es/nicht" nicht. '
                                    'Überprüfen Sie die Schreibweise, und wiederholen Sie den '
                                    'Vorgang.', kind="error")
            print("Antwort", r)
            Gtk.main_quit()
            return False
        GLib.timeout_add(200, show)
        Gtk.main()
        return

    paths = [a for a in args if not a.startswith("--")]
    gfiles = [Gio.File.new_for_path(p) for p in paths]
    dlg = propsdialog.show_properties(parent, gfiles)
    if "--perm" in args:
        dlg.notebook.set_current_page(1)
    if "--toggle" in args:
        GLib.timeout_add(300, lambda: dlg.chk_readonly.set_active(not dlg.chk_readonly.get_active())
                         or False)
    dlg.connect("destroy", Gtk.main_quit)
    delay = 1500
    for a in args:
        if a.startswith("--delay="):
            delay = int(a.split("=")[1])
    devtools.snapshot_after(dlg, out, delay_ms=delay, quit=True)
    Gtk.main()


if __name__ == "__main__":
    main()
