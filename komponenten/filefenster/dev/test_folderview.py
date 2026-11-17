#!/usr/bin/env python3
"""Test-Harness für die Dateiliste (folderview.py, fileops.py, search.py).

Läuft auf dem echten Bildschirm, Fenster schliessen sich selbst. Immer mit
`timeout 20 python3 dev/test_folderview.py …` starten.

    setup [DIR]                       Testordner (neu) anlegen
    snap  --mode details --out a.png  Ansicht als PNG (weitere Optionen s. --help)
    menu  --kind item|bg|view|sort|new --out a.png
    rename --mode details --out a.png Umbenennen-Feld zeigen
    search --query datei --out a.png
    ops                               Kopieren/Einfügen/Löschen/Umbenennen im Testordner
    perf  --dir GROSSER_ORDNER        Ladezeit messen

Einstellungen landen in einem eigenen Konfigurationsordner (nie die echten).
"""
import argparse
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# eigener Testordner: FILEFENSTER_TEST_DIR, sonst ein Ordner pro Benutzer im
# privaten Laufzeitordner (XDG_RUNTIME_DIR) bzw. im Temp-Ordner
SCRATCH = os.environ.get("FILEFENSTER_TEST_DIR") or os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
    f"filefenster-test-{os.getuid()}")
os.makedirs(SCRATCH, mode=0o700, exist_ok=True)
TESTDIR = os.path.join(SCRATCH, "fv-test")
os.environ.setdefault("FILEFENSTER_CONFIG_DIR", os.path.join(SCRATCH, "fv-config"))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from filefenster import devtools, theme  # noqa: E402
from filefenster.config import get_config  # noqa: E402
from filefenster.host import FakeHost  # noqa: E402


# ---------------------------------------------------------------------------
def make_testdir(base=TESTDIR, big=0):
    """Testordner mit Ordnern, Textdateien, Bildern, versteckten Dateien …"""
    if os.path.exists(base):
        assert base.startswith(SCRATCH), "Testordner muss im Scratchpad liegen"
        shutil.rmtree(base)
    os.makedirs(base)
    for d in ["Projekt 2", "Projekt 10", "alpha", "Zebra", "Dokumente Test", "Bilder"]:
        os.makedirs(os.path.join(base, d))
    with open(os.path.join(base, "Projekt 2", "inhalt.txt"), "w") as fh:
        fh.write("Hallo\n")
    names = ["Datei1.txt", "Datei2.txt", "Datei10.txt", "datei3.txt", "Notizen.md",
             "Tabelle.csv", "Skript.py", "Präsentation.odp",
             "Ein sehr langer Dateiname, der umbrechen muss und noch etwas länger ist.txt"]
    for i, n in enumerate(names):
        with open(os.path.join(base, n), "w") as fh:
            fh.write("x" * (i * 1500 + 10))
    with open(os.path.join(base, ".versteckt.txt"), "w") as fh:
        fh.write("geheim\n")
    os.makedirs(os.path.join(base, ".versteckter Ordner"))
    os.symlink("Datei1.txt", os.path.join(base, "Verknüpfung zu Datei1.txt"))
    bg = "/usr/share/backgrounds/linuxmint"
    imgs = sorted(f for f in os.listdir(bg) if f.endswith((".jpg", ".png")))[:4] if os.path.isdir(bg) else []
    for f in imgs:
        shutil.copy(os.path.join(bg, f), os.path.join(base, f))
        shutil.copy(os.path.join(bg, f), os.path.join(base, "Bilder", f))
    # unterschiedliche Zeiten für die Datumssortierung
    now = time.time()
    for i, n in enumerate(sorted(os.listdir(base))):
        p = os.path.join(base, n)
        if not os.path.islink(p):
            os.utime(p, (now - i * 86400 * 3, now - i * 86400 * 3))
    os.makedirs(os.path.join(base, "leer"))
    if big:
        g = os.path.join(base, "gross")
        os.makedirs(g)
        for i in range(big):
            with open(os.path.join(g, f"Datei {i}.txt"), "w"):
                pass
    print("Testordner:", base)


# ---------------------------------------------------------------------------
def make_window(args):
    cfg = get_config()
    cfg.set("theme", "light" if getattr(args, "light", False) else "dark")
    cfg.set("folder_views", {})
    cfg.set("folder_sort", {})
    cfg.set("compact", bool(getattr(args, "compact", False)))
    cfg.set("show_hidden", bool(getattr(args, "hidden", False)))
    cfg.set("show_extensions", not getattr(args, "noext", False))
    if getattr(args, "mode", None):
        cfg.set("view_mode", args.mode)
    theme.setup()
    from filefenster.folderview import FolderView
    win = Gtk.Window(title="FileFenster – Test Dateiliste")
    win.get_style_context().add_class("filefenster")
    w, h = (int(v) for v in getattr(args, "size", "1000x620").split("x"))
    win.set_default_size(w, h)
    host = FakeHost(window=win)
    fv = FolderView(host)
    win.add(fv)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    return win, host, fv


def when_loaded(fv, cb, extra_ms=300):
    def poll():
        if fv.is_loading():
            return True
        GLib.timeout_add(extra_ms, lambda: cb() and False)
        return False
    GLib.timeout_add(50, poll)


def synth_motion(fv, key_index):
    """Mauszeiger (simuliert) über das n-te Element bewegen."""
    view = fv._view()
    key = fv._rows[key_index]
    path = fv._store_path(key)
    ev = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
    ev.send_event = True
    ev.set_device(Gdk.Display.get_default().get_default_seat().get_pointer())
    if view is fv.tree:
        area = fv.tree.get_background_area(path, fv.col_name)
        ev.window = fv.tree.get_bin_window()
        ev.x, ev.y = 60, area.y + area.height / 2
    else:
        ok, r = fv.iv.get_cell_rect(path, None)
        ev.window = fv.iv.get_window().get_children()[0] if fv.iv.get_window().get_children() else fv.iv.get_window()
        ev.x, ev.y = r.x + r.width / 2, r.y + r.height / 2
    view.event(ev)


def cmd_snap(args):
    win, host, fv = make_window(args)
    fv.load(Gio.File.new_for_path(args.dir).get_uri())
    if args.sort:
        col, _, d = args.sort.partition(":")
        fv.set_sort(col, d != "desc")

    def after():
        if args.select:
            idx = [int(i) for i in args.select.split(",")]
            fv._select_keys([fv._rows[i] for i in idx], cursor_key=fv._rows[idx[0]])
        if args.cut:
            idx = [int(i) for i in args.cut.split(",")]
            fv._select_keys([fv._rows[i] for i in idx])
            fv.cut()
            if args.select:
                idx = [int(i) for i in args.select.split(",")]
                fv._select_keys([fv._rows[i] for i in idx])
            else:
                fv.select_none()
        if args.hover is not None:
            synth_motion(fv, args.hover)
        print("Elemente:", fv.item_count(), "ausgewählt:", len(fv.get_selected()),
              "Grösse:", fv.selected_size(), "Modus:", fv.view_mode)
        if args.print:
            print([fv._display_name(fv._entries[k][1]) for k in fv._rows])
        devtools.snapshot_after(win, args.out, delay_ms=args.delay, quit=True)
    when_loaded(fv, after)
    Gtk.main()


def snapshot_menu(menu, path, then=None):
    def do():
        top = menu.menu.get_toplevel()
        ok = devtools.save_window_png(top, path)
        print("Menü-Schnappschuss", "gespeichert" if ok else "FEHLGESCHLAGEN", path)
        if then:
            then()
        else:
            Gtk.main_quit()
        return False
    GLib.timeout_add(700, do)


def cmd_menu(args):
    win, host, fv = make_window(args)
    fv.load(Gio.File.new_for_path(args.dir).get_uri())

    def after():
        if args.kind in ("item", "dir"):
            i = 0 if args.kind == "dir" else next(
                i for i, k in enumerate(fv._rows) if not fv._entries[k][1].get_file_type() == Gio.FileType.DIRECTORY)
            fv._select_keys([fv._rows[i]], cursor_key=fv._rows[i])
            menus = []
            orig = fv._popup
            fv._popup = lambda m, ev: (menus.append(m), orig(m, None))
            fv._popup_item_menu(None)
        elif args.kind == "bg":
            if args.copy_first:
                fv._select_keys([fv._rows[-1]])
                fv.copy()
                fv.select_none()
            menus = []
            orig = fv._popup
            fv._popup = lambda m, ev: (menus.append(m), orig(m, None))
            fv._popup_background_menu(None)
        else:
            m = {"view": fv.build_view_submenu, "sort": fv.build_sort_menu,
                 "new": fv.build_new_menu}[args.kind]()
            fv._popup(m, None)
            menus = [m]
        snapshot_menu(menus[0], args.out)
    when_loaded(fv, after)
    Gtk.main()


def cmd_rename(args):
    win, host, fv = make_window(args)
    fv.load(Gio.File.new_for_path(args.dir).get_uri())

    def after():
        i = args.index
        fv._select_keys([fv._rows[i]], cursor_key=fv._rows[i])
        fv.begin_rename()
        print("Umbenennen aktiv:", fv._renaming is not None, "Eintrag:", fv._rename_entry)
        GLib.timeout_add(500, lambda: print("Markiert:", fv._rename_entry.get_selection_bounds()
                                             if fv._rename_entry else None) and False)
        devtools.snapshot_after(win, args.out, delay_ms=900, quit=True)
    when_loaded(fv, after)
    Gtk.main()


def cmd_search(args):
    win, host, fv = make_window(args)
    uri = Gio.File.new_for_path(args.dir).get_uri()
    fv.load(uri)

    def after():
        fv.start_search(uri, args.query)

        def done():
            if fv.is_loading():
                return True
            print("Treffer:", fv.item_count(), "Suche aktiv:", fv.is_searching())
            for k in fv._rows[:20]:
                print("  ", fv._entries[k][0].get_path())
            devtools.snapshot_after(win, args.out, delay_ms=400, quit=True)
            return False
        GLib.timeout_add(100, done)
    when_loaded(fv, after)
    Gtk.main()


def cmd_perf(args):
    win, host, fv = make_window(args)
    t0 = time.monotonic()
    fv.load(Gio.File.new_for_path(args.dir).get_uri())

    def after():
        print(f"Geladen: {fv.item_count()} Elemente in {time.monotonic() - t0:.2f} s (Modus {fv.view_mode})")
        t1 = time.monotonic()
        fv.set_sort("size")
        print(f"Umsortieren: {time.monotonic() - t1:.2f} s")
        for mode in ("medium", "list", "details"):
            t1 = time.monotonic()
            fv.set_view_mode(mode)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            print(f"Ansicht {mode}: {time.monotonic() - t1:.2f} s")
        devtools.snapshot_after(win, args.out, delay_ms=300, quit=True)
    when_loaded(fv, after, extra_ms=0)
    Gtk.main()


# ---------------------------------------------------------------------------
def cmd_ops(args):
    """Dateioperationen programmatisch prüfen – nur im Testordner."""
    from filefenster import fileops
    base = os.path.join(TESTDIR, "ops")
    assert base.startswith(SCRATCH)
    if os.path.exists(base):
        shutil.rmtree(base)
    src = os.path.join(base, "quelle")
    dst = os.path.join(base, "ziel")
    os.makedirs(os.path.join(src, "Unterordner", "tief"))
    os.makedirs(dst)
    for p, data in [("a.txt", "A"), ("b.txt", "B"), ("Unterordner/c.txt", "C"),
                    ("Unterordner/tief/d.txt", "D")]:
        with open(os.path.join(src, p), "w") as fh:
            fh.write(data)
    os.symlink("a.txt", os.path.join(src, "link.txt"))
    os.symlink("/etc", os.path.join(src, "etc-link"))  # darf beim Löschen NIE verfolgt werden

    win, host, fv = make_window(args)
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(("OK   " if cond else "FEHL ") + name)

    steps = []

    def run_next():
        if not steps:
            ok = all(r for _n, r in results)
            print(f"\n{sum(r for _n, r in results)}/{len(results)} Prüfungen bestanden")
            GLib.timeout_add(200, lambda: Gtk.main_quit() and False)
            if not ok:
                os.environ["FV_FAIL"] = "1"
            return False
        fn = steps.pop(0)
        fn()
        return False

    def step(fn):
        steps.append(fn)
        return fn

    def wait_loaded(then, extra=200):
        when_loaded(fv, then, extra_ms=extra)

    @step
    def s_load():
        fv.load(Gio.File.new_for_path(src).get_uri())
        wait_loaded(lambda: (check("Quelle geladen (5 Elemente)", fv.item_count() == 5), run_next()))

    @step
    def s_copy_paste_same():
        keys = [k for k in fv._rows if k.endswith(("/a.txt", "/Unterordner"))]
        fv._select_keys(keys)
        fv.copy()

        def pasted(*_a):
            names = sorted(os.listdir(src))
            check("Kopie im selben Ordner: 'a - Kopie.txt'", "a - Kopie.txt" in names)
            check("Ordnerkopie: 'Unterordner - Kopie' rekursiv",
                  os.path.isfile(os.path.join(src, "Unterordner - Kopie", "tief", "d.txt")))
            sel = [g.get_basename() for g in fv.get_selected()]
            check(f"neue Elemente ausgewählt {sel}", set(sel) == {"a - Kopie.txt", "Unterordner - Kopie"})
            run_next()
        from filefenster.folderview import FolderView

        def hook(created):
            FolderView._after_paste(fv, created)
            GLib.timeout_add(300, lambda: pasted() and False)
        fv._after_paste = hook
        fv.paste()

    @step
    def s_paste_again():
        from filefenster.folderview import FolderView
        fv._after_paste = lambda created: (FolderView._after_paste(fv, created),
                                           GLib.timeout_add(300, lambda: (
                                               check("zweite Kopie: 'a - Kopie (2).txt'",
                                                     os.path.exists(os.path.join(src, "a - Kopie (2).txt"))),
                                               run_next()) and False))
        fv.paste()

    @step
    def s_clipboard_format():
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        def got(_c, sdata, *_a):
            txt = bytes(sdata.get_data()).decode() if sdata and sdata.get_length() > 0 else ""
            check("Zwischenablage x-special/gnome-copied-files beginnt mit 'copy'", txt.startswith("copy\nfile://"))

            def got2(_c, text, *_a):
                check("Text-Ziel enthält Pfade", text and text.startswith("/"))
                run_next()
            cb.request_text(got2)
        cb.request_contents(Gdk.Atom.intern("x-special/gnome-copied-files", False), got)

    @step
    def s_cut_paste_other():
        fv._select_keys([k for k in fv._rows if k.endswith("/b.txt")])
        fv.cut()
        check("Ausgeschnitten: Icon halbtransparent markiert",
              any(k.endswith("/b.txt") for k in fv._cut))
        fv.load(Gio.File.new_for_path(dst).get_uri())

        def loaded():
            from filefenster.folderview import FolderView
            fv._after_paste = lambda created: (FolderView._after_paste(fv, created),
                                               GLib.timeout_add(300, lambda: (
                                                   check("Verschieben: b.txt im Ziel",
                                                         os.path.exists(os.path.join(dst, "b.txt"))),
                                                   check("Verschieben: b.txt in Quelle weg",
                                                         not os.path.exists(os.path.join(src, "b.txt"))),
                                                   check("Zwischenablage nach Ausschneiden geleert",
                                                         not fv._cut),
                                                   run_next()) and False))
            fv.paste()
        wait_loaded(loaded)

    @step
    def s_copy_into_self():
        fv.load(Gio.File.new_for_path(src).get_uri())

        def loaded():
            from filefenster import fileops as fo
            sub = Gio.File.new_for_path(os.path.join(src, "Unterordner"))

            def done(created):
                check("Ordner nicht in sich selbst kopiert", not created)
                run_next()
            orig = fo.show_errors
            fo.show_errors = lambda parent, title, errors: print("  (Fehlerdialog):", title, errors)
            fo.transfer([sub], sub.get_child("tief"), move=False, parent=win, on_done=done)
            GLib.timeout_add(1500, lambda: setattr(fo, "show_errors", orig) and False)
        wait_loaded(loaded)

    @step
    def s_new_folder():
        fv.new_folder()
        fv.new_folder()

        def later():
            check("Neuer Ordner + 'Neuer Ordner (2)'",
                  os.path.isdir(os.path.join(src, "Neuer Ordner")) and
                  os.path.isdir(os.path.join(src, "Neuer Ordner (2)")))
            check("Umbenennen nach 'Neuer Ordner' aktiv", fv._renaming is not None)
            ent = fv._rename_entry
            if ent is not None:
                ent.set_text("Umbenannt")
                ent.activate()

            def after():
                check("Umbenennen per Eingabe übernommen",
                      os.path.isdir(os.path.join(src, "Umbenannt")))
                sel = [g.get_basename() for g in fv.get_selected()]
                check(f"umbenanntes Element ausgewählt {sel}", sel == ["Umbenannt"])
                run_next()
            GLib.timeout_add(500, lambda: after() and False)
        GLib.timeout_add(400, lambda: later() and False)

    @step
    def s_new_file_rename_selection():
        fv.new_file()

        def later():
            check("Neues Textdokument.txt", os.path.isfile(os.path.join(src, "Neues Textdokument.txt")))
            ent = fv._rename_entry
            b = ent.get_selection_bounds() if ent else None
            check(f"nur Name ohne Erweiterung markiert {b}", b and tuple(b) == (0, len("Neues Textdokument")))
            # Escape bricht ab
            if ent is not None:
                ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
                ev.keyval = Gdk.KEY_Escape
                ev.window = ent.get_window()
                ev.send_event = True
                ent.event(ev)

            def after():
                check("Esc bricht ab (Name unverändert)",
                      os.path.isfile(os.path.join(src, "Neues Textdokument.txt")) and fv._renaming is None)
                run_next()
            GLib.timeout_add(400, lambda: after() and False)
        GLib.timeout_add(500, lambda: later() and False)

    @step
    def s_rename_focus_out():
        k = next(k for k in fv._rows if k.endswith("/a.txt"))
        fv._select_keys([k], cursor_key=k)
        fv.set_view_mode("large")

        def later():
            fv.begin_rename()
            ent = fv._rename_entry
            check("Umbenennen in Symbolansicht aktiv", ent is not None)
            if ent is not None:
                ent.set_text("a2.txt")
                fv.iv.grab_focus()  # Fokus weg -> übernehmen (wie Windows)

            def after():
                check("Fokusverlust übernimmt Namen (a2.txt)", os.path.isfile(os.path.join(src, "a2.txt")))
                fv.set_view_mode("details")
                run_next()
            GLib.timeout_add(500, lambda: after() and False)
        GLib.timeout_add(400, lambda: later() and False)

    @step
    def s_rename_invalid():
        from filefenster import fileops as fo
        msgs = []
        orig = fo.info_dialog
        fo.info_dialog = lambda parent, title, text, secondary=None: msgs.append(text)
        k = next(k for k in fv._rows if k.endswith("/a2.txt"))
        fv._commit_rename(k, "a/b.txt")
        fv._commit_rename(k, "   ")
        fo.info_dialog = orig
        check(f"ungültige Namen -> Hinweis {msgs}", len(msgs) == 2 and os.path.isfile(os.path.join(src, "a2.txt")))
        GLib.timeout_add(300, lambda: (fv._cancel_rename(), run_next()) and False)

    @step
    def s_monitor():
        with open(os.path.join(src, "extern.txt"), "w") as fh:
            fh.write("von aussen\n")

        def later():
            check("Ordnerüberwachung: extern.txt erscheint",
                  any(k.endswith("/extern.txt") for k in fv._rows))
            os.remove(os.path.join(src, "extern.txt"))
            GLib.timeout_add(700, lambda: (check("Ordnerüberwachung: extern.txt verschwindet",
                                                 not any(k.endswith("/extern.txt") for k in fv._rows)),
                                           run_next()) and False)
        GLib.timeout_add(900, lambda: later() and False)

    @step
    def s_delete_permanent():
        from filefenster import fileops as fo
        orig = fo.confirm_permanent_delete
        asked = []
        fo.confirm_permanent_delete = lambda parent, files, reason=None: (asked.append(len(files)), True)[1]
        keys = [k for k in fv._rows
                if fv._entries[k][0].get_basename() in ("Unterordner - Kopie", "etc-link", "link.txt")]
        fv._select_keys(keys)

        def done_check():
            fo.confirm_permanent_delete = orig
            check("endgültig löschen fragt nach", asked == [3])
            check("Ordner rekursiv gelöscht", not os.path.exists(os.path.join(src, "Unterordner - Kopie")))
            check("Symlink gelöscht, Ziel /etc unberührt",
                  not os.path.lexists(os.path.join(src, "etc-link")) and os.path.isfile("/etc/hostname"))
            check("Link gelöscht, Ziel a2/a erhalten", os.path.isfile(os.path.join(src, "a2.txt")))
            check("Zeilen sofort entfernt", not any(k.endswith("/etc-link") for k in fv._rows))
            run_next()
        fv.delete(permanent=True)
        GLib.timeout_add(800, lambda: done_check() and False)

    @step
    def s_trash():
        # Papierkorb: im Scratchpad (/tmp, Systemlaufwerk) nicht möglich -> Rückfrage
        # "endgültig löschen" mit Hinweis. Der echte Papierkorb wird nicht berührt.
        name = "filefenster-papierkorb-test.txt"
        p = os.path.join(src, name)
        with open(p, "w") as fh:
            fh.write("test\n")
        f = Gio.File.new_for_path(p)
        from filefenster import fileops as fo
        orig = fo.confirm_permanent_delete
        asked = []
        fo.confirm_permanent_delete = lambda parent, files, reason=None: (asked.append(reason), True)[1]

        def done(deleted):
            fo.confirm_permanent_delete = orig
            check(f"Papierkorb nicht verfügbar -> Rückfrage {asked}", asked and asked[0])
            check("danach endgültig gelöscht", not os.path.exists(p) and len(deleted) == 1)
            run_next()
        fo.trash_files(win, [f], on_done=done)

    @step
    def s_select_ops():
        fv.select_all()
        n = len(fv.get_selected())
        fv.invert_selection()
        m = len(fv.get_selected())
        check(f"Alles auswählen ({n}) / umkehren ({m})", n == fv.item_count() and m == 0)
        fv._select_keys([fv._rows[0]])
        fv.invert_selection()
        check("Umkehren mit einem Element", len(fv.get_selected()) == fv.item_count() - 1)
        fv.copy_path()
        txt = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or ""
        check("Als Pfad kopieren: Pfade in Anführungszeichen, zeilenweise",
              txt.startswith('"/') and txt.count("\n") == fv.item_count() - 2)
        run_next()

    GLib.timeout_add(200, run_next)
    Gtk.main()
    sys.exit(1 if os.environ.get("FV_FAIL") else 0)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--dir", default=TESTDIR)
        p.add_argument("--mode", default="details")
        p.add_argument("--out", default=os.path.join(SCRATCH, "fv.png"))
        p.add_argument("--light", action="store_true")
        p.add_argument("--compact", action="store_true")
        p.add_argument("--hidden", action="store_true")
        p.add_argument("--noext", action="store_true")
        p.add_argument("--size", default="1000x620")

    p = sub.add_parser("setup")
    p.add_argument("base", nargs="?", default=TESTDIR)
    p.add_argument("--big", type=int, default=0)
    p = sub.add_parser("snap")
    common(p)
    p.add_argument("--select")
    p.add_argument("--cut")
    p.add_argument("--hover", type=int)
    p.add_argument("--sort")
    p.add_argument("--print", action="store_true")
    p.add_argument("--delay", type=int, default=900)
    p = sub.add_parser("menu")
    common(p)
    p.add_argument("--kind", default="item")
    p.add_argument("--copy-first", action="store_true")
    p = sub.add_parser("rename")
    common(p)
    p.add_argument("--index", type=int, default=6)
    p = sub.add_parser("search")
    common(p)
    p.add_argument("--query", default="datei")
    p = sub.add_parser("perf")
    common(p)
    p = sub.add_parser("ops")
    common(p)
    args = ap.parse_args()
    if args.cmd == "setup":
        make_testdir(args.base, args.big)
    else:
        globals()["cmd_" + args.cmd](args)


if __name__ == "__main__":
    main()
