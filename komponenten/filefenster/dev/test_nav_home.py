#!/usr/bin/env python3
"""Testfenster für Navigationsbereich + Start / Dieser PC.

    timeout 20 python3 dev/test_nav_home.py [home|thispc] [Optionen]

Optionen:
    --png PFAD          Schnappschuss (Standard: $SCRATCH/nav_<ansicht>.png)
    --expand LOC        Eintrag der obersten Ebene aufklappen (mehrfach möglich),
                        z. B. thispc: oder file://$HOME/Downloads (ausgeschrieben)
    --expand-child N    zusätzlich das N-te Kind des ersten aufgeklappten Eintrags aufklappen
    --hover N           Maus über Navigationsbereich simulieren (Chevrons) + Zeile N hovern
    --select LOC        Ort im Navigationsbereich markieren (select_location)
    --tile N            N-te Kachel auswählen
    --hover-tile N      N-te Kachel als "Maus darüber" zeichnen
    --recent N          N-te Zeile von "Zuletzt verwendet" auswählen
    --menu nav:N|tile:N|recent
                        Kontextmenü öffnen und Bildschirmausschnitt speichern
    --pin URI           zusätzlich anheften (Test-Konfiguration)
    --light             helles Design erzwingen
    --size BxH          Fenstergrösse (Standard 1180x720)
    --delay MS          Wartezeit bis zum Schnappschuss (Standard 1600)
    --keep              nicht automatisch beenden

Die Konfiguration liegt immer in einem Testordner (FILEFENSTER_CONFIG_DIR,
Standard: Scratchpad/testcfg), die echte Konfiguration bleibt unberührt.
"""
import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# eigener Testordner: FILEFENSTER_SCRATCH, sonst ein Ordner pro Benutzer im
# privaten Laufzeitordner (XDG_RUNTIME_DIR) bzw. im Temp-Ordner
SCRATCH = os.environ.get("FILEFENSTER_SCRATCH") or os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
    f"filefenster-test-{os.getuid()}")
os.makedirs(SCRATCH, mode=0o700, exist_ok=True)
os.environ.setdefault("FILEFENSTER_CONFIG_DIR", os.path.join(SCRATCH, "testcfg"))
sys.path.insert(0, ROOT)

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from filefenster import devtools, places, theme  # noqa: E402
from filefenster.config import get_config  # noqa: E402
from filefenster.host import FakeHost, pinned_uris  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("view", nargs="?", default="home", choices=["home", "thispc"])
    ap.add_argument("--png")
    ap.add_argument("--expand", action="append", default=[])
    ap.add_argument("--expand-child", type=int)
    ap.add_argument("--hover", type=int)
    ap.add_argument("--select")
    ap.add_argument("--tile", type=int)
    ap.add_argument("--hover-tile", type=int)
    ap.add_argument("--recent", type=int)
    ap.add_argument("--menu")
    ap.add_argument("--pin", action="append", default=[])
    ap.add_argument("--light", action="store_true")
    ap.add_argument("--size", default="1180x720")
    ap.add_argument("--delay", type=int, default=1600)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    cfg = get_config()
    cfg.set("theme", "light" if args.light else "auto")
    if args.pin:
        lst = pinned_uris(cfg)
        for u in args.pin:
            if u not in lst:
                lst.append(u)
        cfg.set("pinned", lst)
    theme.setup()

    from filefenster.homeview import HomeView
    from filefenster.navpane import NavPane
    from filefenster.thispcview import ThisPCView

    win = Gtk.Window(title="FileFenster – Test Navigation/Start")
    win.get_style_context().add_class("filefenster")
    w, h = (int(v) for v in args.size.split("x"))
    win.set_default_size(w, h)
    win.connect("destroy", Gtk.main_quit)

    loc = places.THISPC if args.view == "thispc" else places.HOME
    host = FakeHost(window=win, location=loc)
    nav = NavPane(host)
    nav.set_size_request(240, -1)
    view = ThisPCView(host) if args.view == "thispc" else HomeView(host)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    box.pack_start(nav, False, False, 0)
    box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
    box.pack_start(view, True, True, 0)
    win.add(box)
    win.show_all()

    nav.select_location(args.select or loc)
    for e in args.expand:
        nav.expand_location(e)

    def later_setup():
        if args.expand_child is not None:
            # erstes aufgeklapptes Element der obersten Ebene suchen
            it = nav.store.get_iter_first()
            while it is not None:
                path = nav.store.get_path(it)
                if nav.tree.row_expanded(path):
                    child = nav.store.iter_nth_child(it, args.expand_child)
                    if child is not None:
                        nav._expand(nav.store.get_path(child))
                    break
                it = nav.store.iter_next(it)
        if args.hover is not None:
            nav._pane_hover = True
            nav.highlighter.set_hover(Gtk.TreePath.new_from_string(str(args.hover)))
            nav.tree.queue_draw()
        if args.tile is not None and hasattr(view, "flow"):
            child = view.flow.get_child_at_index(args.tile)
            if child:
                view.flow.select_child(child)
        if args.tile is not None and hasattr(view, "flow_drives"):
            child = view.flow_drives.get_child_at_index(args.tile)
            if child:
                view.flow_drives.select_child(child)
        if args.hover_tile is not None:
            flow = view.flow if hasattr(view, "flow") else view.flow_drives
            child = flow.get_child_at_index(args.hover_tile)
            if child:
                child.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
        if args.recent is not None and hasattr(view, "recent"):
            view.recent.get_selection().select_path(Gtk.TreePath.new_from_string(str(args.recent)))
        print("[test] selected:", [g.get_uri() for g in view.get_selected()],
              "count:", view.item_count())
        return False

    GLib.timeout_add(700, later_setup)

    png = args.png or os.path.join(SCRATCH, f"nav_{args.view}.png")

    if args.menu:
        def open_menu():
            kind, _, idx = args.menu.partition(":")
            menu = None
            attach = None
            if kind == "nav":
                path = Gtk.TreePath.new_from_string(idx or "0")
                menu = nav.build_menu(path)
                attach = nav.tree
            elif kind == "tile":
                flow = view.flow if hasattr(view, "flow") else view.flow_drives
                child = flow.get_child_at_index(int(idx or 0))
                flow.select_child(child)
                menu = view.tile_menu(child)
                attach = child
            elif kind == "recent":
                view.recent.get_selection().select_path(Gtk.TreePath.new_from_string("0"))
                menu = view._recent_menu()
                attach = view.recent
            if menu is None:
                print("[test] kein Menü")
                return False
            menu._prepare(attach)
            alloc = attach.get_allocation()
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = 60, 20, 1, 1
            gw = attach.get_window()
            menu.menu.popup_at_rect(gw, r, Gdk.Gravity.NORTH_WEST, Gdk.Gravity.NORTH_WEST, None)
            del alloc
            return False

        def grab_screen():
            root = Gdk.get_default_root_window()
            ox, oy = win.get_window().get_origin()[1:]
            ww, wh = win.get_allocated_width(), win.get_allocated_height()
            sw, sh = root.get_width(), root.get_height()
            x0, y0 = max(0, ox), max(0, oy)
            pb = Gdk.pixbuf_get_from_window(root, x0, y0, min(ww + 200, sw - x0),
                                            min(wh + 100, sh - y0))
            pb.savev(png, "png", [], [])
            print(f"[test] Bildschirmausschnitt gespeichert: {png}")
            if not args.keep:
                Gtk.main_quit()
            return False

        GLib.timeout_add(args.delay - 600, open_menu)
        GLib.timeout_add(args.delay, grab_screen)
    elif not args.keep:
        devtools.snapshot_after(win, png, delay_ms=args.delay, quit=True)

    Gtk.main()
    print("[test] host log:", host.log[-5:])


if __name__ == "__main__":
    main()
