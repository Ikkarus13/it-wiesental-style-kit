#!/usr/bin/env python3
"""Funktionstest für NavPane/HomeView/ThisPCView mit synthetischen Ereignissen
(keine echten Maus-/Tastatureingaben).

    timeout 30 python3 dev/test_nav_logic.py

Benutzt eine Test-Konfiguration (FILEFENSTER_CONFIG_DIR im Scratchpad) und einen
temporären Ordnerbaum im Scratchpad.
"""
import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# eigener Testordner: FILEFENSTER_SCRATCH, sonst ein Ordner pro Benutzer im
# privaten Laufzeitordner (XDG_RUNTIME_DIR) bzw. im Temp-Ordner
SCRATCH = os.environ.get("FILEFENSTER_SCRATCH") or os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
    f"filefenster-test-{os.getuid()}")
os.makedirs(SCRATCH, mode=0o700, exist_ok=True)
CFG = os.path.join(SCRATCH, "logiccfg")
shutil.rmtree(CFG, ignore_errors=True)
os.environ["FILEFENSTER_CONFIG_DIR"] = CFG
sys.path.insert(0, ROOT)

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from filefenster import places, theme  # noqa: E402
from filefenster.host import FakeHost, pinned_uris  # noqa: E402

# Testbaum: tree/a/{a1,a2,a10}, tree/b (leer), tree/.hidden/x
TREE = os.path.join(SCRATCH, "navtree")
shutil.rmtree(TREE, ignore_errors=True)
for p in ("a/a1", "a/a2", "a/a10/deep", "b", ".hidden/x"):
    os.makedirs(os.path.join(TREE, p))
open(os.path.join(TREE, "a", "datei.txt"), "w").close()
TREE_URI = Gio.File.new_for_path(TREE).get_uri()

results = []
_KEEP = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("OK   " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def button_event(tree, path, button=1, etype=Gdk.EventType.BUTTON_PRESS, on_chevron=False):
    # Signal-Handler bekommen die konkrete Struktur (Gdk.EventButton), nicht die Union
    ev = Gdk.Event.new(etype)
    _KEEP.append(ev)            # Union am Leben halten, sonst zeigt eb ins Leere
    eb = ev.button
    eb.type = etype
    eb.window = tree.get_bin_window()
    area = tree.get_cell_area(path, tree.get_column(0))
    eb.x = float(area.x + (8 if on_chevron else 120))
    eb.y = float(area.y + area.height / 2)
    eb.button = button
    return eb


def key_event(keyval):
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    _KEEP.append(ev)
    ek = ev.key
    ek.type = Gdk.EventType.KEY_PRESS
    ek.keyval = keyval
    ek.state = 0
    return ek


def find_top(nav, loc):
    from filefenster.navpane import C_KIND, C_LOC, norm_loc
    it = nav.store.get_iter_first()
    while it is not None:
        if nav.store[it][C_KIND] not in ("sep",) and norm_loc(nav.store[it][C_LOC]) == norm_loc(loc):
            return nav.store.get_path(it)
        it = nav.store.iter_next(it)
    return None


def child_labels(nav, path):
    from filefenster.navpane import C_LABEL
    it = nav.store.get_iter(path)
    out = []
    c = nav.store.iter_children(it)
    while c is not None:
        out.append(nav.store[c][C_LABEL])
        c = nav.store.iter_next(c)
    return out


def run_steps(steps):
    """Schritte nacheinander mit Pausen (damit Async-Laden fertig wird)."""
    def nxt():
        if not steps:
            Gtk.main_quit()
            return False
        fn, delay = steps.pop(0)
        try:
            fn()
        except Exception:
            traceback.print_exc()
            check(f"Ausnahme in {fn.__name__}", False)
        GLib.timeout_add(delay, nxt)
        return False
    GLib.timeout_add(600, nxt)


def main():
    from filefenster.config import get_config
    cfg = get_config()
    cfg.set("pinned", places.default_pinned() + [TREE_URI])
    theme.setup()
    from filefenster.homeview import HomeView
    from filefenster.navpane import C_KIND, C_LOADED, NavPane, natural_key
    from filefenster.thispcview import ThisPCView

    win = Gtk.Window()
    win.get_style_context().add_class("filefenster")
    win.set_default_size(1000, 640)
    host = FakeHost(window=win)
    nav = NavPane(host)
    nav.set_size_request(240, -1)
    home = HomeView(host)
    pc = ThisPCView(host)
    box = Gtk.Box()
    box.pack_start(nav, False, False, 0)
    stack = Gtk.Stack()
    stack.add_named(home, "home")
    stack.add_named(pc, "pc")
    box.pack_start(stack, True, True, 0)
    win.add(box)
    win.show_all()
    tree = nav.tree
    st = {}

    check("natural_key sortiert 2 vor 10",
          sorted(["a10", "a2", "a1"], key=natural_key) == ["a1", "a2", "a10"])

    def s_initial():
        host.log.clear()
        nav.select_location(places.HOME)
        sel = tree.get_selection().get_selected()[1]
        check("select_location(home:) markiert Start", sel is not None and
              nav.store[sel][C_KIND] == "home")
        check("select_location löst kein navigate aus", not host.log, host.log)
        nav.select_location("file:///gibt/es/nicht")
        check("unbekannter Ort leert Auswahl",
              tree.get_selection().count_selected_rows() == 0)
        st["tree_path"] = find_top(nav, TREE_URI)
        check("angehefteter Testordner im Baum", st["tree_path"] is not None)

    def s_click_navigate():
        host.log.clear()
        p = st["tree_path"]
        nav._on_button_press(tree, button_event(tree, p))
        check("Klick navigiert", ("navigate", TREE_URI) in host.log, host.log)
        check("Klick markiert", tree.get_selection().path_is_selected(p))

    def s_chevron():
        host.log.clear()
        p = st["tree_path"]
        nav._on_button_press(tree, button_event(tree, p, on_chevron=True))
        check("Chevron-Klick navigiert nicht", not any(e[0] == "navigate" for e in host.log),
              host.log)

    def s_after_expand():
        p = st["tree_path"]
        check("Chevron-Klick klappt auf", tree.row_expanded(p))
        labels = child_labels(nav, p)
        check("Unterordner sortiert, versteckte ausgeblendet", labels == ["a", "b"], labels)

    def s_check_chevrons():
        p = st["tree_path"]
        it = nav.store.get_iter(p)
        a = nav.store.iter_nth_child(it, 0)
        b = nav.store.iter_nth_child(it, 1)
        check("Ordner ohne Unterordner verliert Chevron (b)",
              nav.store[b][C_LOADED] and not nav.store.iter_has_child(b))
        check("Ordner mit Unterordnern behält Platzhalter (a)", nav.store.iter_has_child(a))
        st["a_path"] = nav.store.get_path(a)
        # Mittelklick
        host.log.clear()
        nav._on_button_press(tree, button_event(tree, st["a_path"], button=2))
        check("Mittelklick öffnet neuen Tab",
              host.log and host.log[0][0] == "open_in_new_tab", host.log)
        # Tastatur: Cursor auf a, Rechts klappt auf
        tree.set_cursor(st["a_path"], None, False)
        nav._on_key_press(tree, key_event(Gdk.KEY_Right))

    def s_keyboard():
        check("Pfeil rechts klappt auf", tree.row_expanded(st["a_path"]))
        labels = child_labels(nav, st["a_path"])
        check("natürliche Sortierung a1, a2, a10", labels == ["a1", "a2", "a10"], labels)
        nav._on_key_press(tree, key_event(Gdk.KEY_Left))
        check("Pfeil links klappt zu", not tree.row_expanded(st["a_path"]))
        nav._on_key_press(tree, key_event(Gdk.KEY_Left))
        cur = tree.get_cursor()[0]
        check("Pfeil links springt zum Elternordner", cur is not None and
              cur.compare(st["tree_path"]) == 0)
        # aufgeklapptes Kind per select_location finden
        tree.expand_row(st["a_path"], False)
        nav.select_location(Gio.File.new_for_path(os.path.join(TREE, "a")).get_uri())
        sel = tree.get_selection().get_selected()[1]
        check("select_location findet aufgeklapptes Kind", sel is not None and
              nav.store.get_path(sel).compare(st["a_path"]) == 0)

    def s_menu():
        m = nav.build_menu(st["a_path"])
        labels = []
        for mi in m.menu.get_children():
            box = mi.get_child()
            if isinstance(box, Gtk.Box):
                for w in box.get_children():
                    if isinstance(w, Gtk.Label):
                        labels.append(w.get_text())
                        break
        check("Kontextmenü Ordner", labels[:3] == ["Reduzieren", "In neuem Tab öffnen",
                                                   "In neuem Fenster öffnen"], labels)
        check("Kontextmenü hat Anheften/Eigenschaften/Terminal",
              {"An Schnellzugriff anheften", "Eigenschaften", "In Terminal öffnen",
               "Als Pfad kopieren"} <= set(labels), labels)
        st["n_tiles"] = len(home.flow.get_children())
        # Anheften über den Host -> Navigationsbereich und Start bauen neu auf
        host.pin(Gio.File.new_for_path(os.path.join(TREE, "b")).get_uri())

    def s_pinned():
        uri_b = Gio.File.new_for_path(os.path.join(TREE, "b")).get_uri()
        check("Pin erscheint im Navigationsbereich", find_top(nav, uri_b) is not None)
        check("Pin erscheint als Kachel", len(home.flow.get_children()) == st["n_tiles"] + 1)
        check("Aufklappzustand bleibt nach Neuaufbau",
              find_top(nav, TREE_URI) is not None and tree.row_expanded(find_top(nav, TREE_URI)))
        host.unpin(uri_b)

    def s_unpinned():
        uri_b = Gio.File.new_for_path(os.path.join(TREE, "b")).get_uri()
        check("Lösen entfernt Pin", find_top(nav, uri_b) is None)
        check("Lösen entfernt Kachel", len(home.flow.get_children()) == st["n_tiles"])
        # Start-Kacheln: Auswahl + get_selected + Doppelklick (child-activated)
        host.log.clear()
        child = home.flow.get_child_at_index(0)
        home.flow.select_child(child)
        check("Kachel-Auswahl -> state_changed", ("state_changed",) in host.log, host.log)
        check("get_selected liefert Kachel", [g.get_uri() for g in home.get_selected()] ==
              [pinned_uris(cfg)[0]])
        home.flow.emit("child-activated", child)
        check("Kachel-Doppelklick navigiert", ("navigate", child.uri) in host.log)
        if len(home.recent_store):
            home.recent.get_selection().select_path(Gtk.TreePath.new_first())
            check("Auswahl in Liste hebt Kachel-Auswahl auf",
                  not home.flow.get_selected_children())
            check("get_selected liefert Datei", len(home.get_selected()) == 1)
        check("item_count Start", home.item_count() ==
              len(home.flow.get_children()) + len(home.recent_store))
        # Dieser PC
        check("Dieser PC hat Laufwerke", pc.item_count() >= 1)
        c = pc.flow_drives.get_child_at_index(0)
        pc.flow_drives.select_child(c)
        check("Dieser PC get_selected", [g.get_uri() for g in pc.get_selected()] == [c.uri])
        host.log.clear()
        pc.flow_drives.emit("child-activated", c)
        check("Laufwerk-Doppelklick navigiert", ("navigate", c.uri) in host.log)
        # Dieser PC im Baum aufklappen -> Laufwerke
        nav.expand_location(places.THISPC)
        pth = find_top(nav, places.THISPC)
        check("Dieser PC klappt auf (Laufwerke)", tree.row_expanded(pth) and
              len(child_labels(nav, pth)) == pc.item_count() - len(pc.flow_net.get_children()))
        # versteckte Elemente einschalten -> .hidden erscheint
        cfg.set("show_hidden", True)

    def s_hidden():
        p = find_top(nav, TREE_URI)
        labels = child_labels(nav, p) if p is not None and tree.row_expanded(p) else []
        check("show_hidden zeigt versteckte Ordner", ".hidden" in labels, labels)
        cfg.set("show_hidden", False)

    def s_live_create():
        p = find_top(nav, TREE_URI)
        a = nav.store.iter_nth_child(nav.store.get_iter(p), 0)
        check("Unterordner a nach Neuaufbau wieder aufgeklappt",
              tree.row_expanded(nav.store.get_path(a)))
        os.makedirs(os.path.join(TREE, "c_neu"))
        shutil.rmtree(os.path.join(TREE, "b"))

    def s_live_check():
        p = find_top(nav, TREE_URI)
        labels = child_labels(nav, p) if p is not None else []
        check("neuer Ordner erscheint, gelöschter verschwindet (Live)",
              labels == ["a", "c_neu"], labels)
        a = nav.store.iter_nth_child(nav.store.get_iter(p), 0)
        check("Aufklappzustand bleibt nach Live-Aktualisierung",
              tree.row_expanded(nav.store.get_path(a)))
        # Zwischenablage
        from filefenster.navpane import copy_as_path, copy_files_to_clipboard
        g = Gio.File.new_for_path(os.path.join(TREE, "a"))
        copy_as_path([g])
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        check("Als Pfad kopieren", clip.wait_for_text() == f'"{g.get_path()}"',
              clip.wait_for_text())
        copy_files_to_clipboard([g])
        check("Kopieren -> text/uri-list", clip.wait_for_uris() == [g.get_uri()],
              clip.wait_for_uris())
        sel = clip.wait_for_contents(Gdk.Atom.intern("x-special/gnome-copied-files", False))
        data = sel.get_data().decode() if sel else ""
        check("Kopieren -> x-special/gnome-copied-files",
              data == "copy\n" + g.get_uri(), data)

    run_steps([(s_initial, 200), (s_click_navigate, 100), (s_chevron, 500),
               (s_after_expand, 600), (s_check_chevrons, 600), (s_keyboard, 300),
               (s_menu, 600), (s_pinned, 600), (s_unpinned, 900), (s_hidden, 900),
               (s_live_create, 1500), (s_live_check, 200)])
    Gtk.main()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} bestanden")
    shutil.rmtree(TREE, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
