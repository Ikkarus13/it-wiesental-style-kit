#!/usr/bin/env python3
"""Ordneroptionen (··· → Optionen) testen – nur auf dem Test-Bildschirm und
nur mit eigenem Einstellungsordner:

    python3 dev/testbildschirm.py :99 &
    DISPLAY=:99 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_optionen.py

Klicks, Doppelklicks, Zeigen und Tasten laufen über XTEST. Dateien werden
nie geöffnet (host.launch_file ist ersetzt), es entstehen nur Fenster auf dem
Test-Bildschirm. Arbeitet in ~/.cache/filefenster-optionen.
"""
import json
import os
import shutil
import sys

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (DISPLAY=:99)")
if not os.environ.get("FILEFENSTER_CONFIG_DIR"):
    sys.exit("Nur mit eigenem Einstellungsordner starten (FILEFENSTER_CONFIG_DIR=…)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402
from Xlib import X, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import config as cfgmod  # noqa: E402
from filefenster import folderoptions, places  # noqa: E402
from filefenster import host as hostmod  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import CHECK_SIZE, COL_KEY, COL_NAME  # noqa: E402

T = os.path.expanduser("~/.cache/filefenster-optionen")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(f"{T}/Alpha/Innen")      # „Innen“ liegt in Alpha an derselben Stelle wie Alpha in T
os.makedirs(f"{T}/Beta")
for n in ("a.txt", "b.txt", "c.txt", ".versteckt.txt", "Alpha/x.txt"):
    with open(f"{T}/{n}", "w") as fh:
        fh.write(n)
T_URI = places.uri_for_path(T)
ALPHA = places.uri_for_path(f"{T}/Alpha")
BETA = places.uri_for_path(f"{T}/Beta")
XBEL = os.path.join(GLib.get_user_data_dir(), "recently-used.xbel")

D = display.Display()
ok = fail = 0
launched = []
hostmod.launch_file = lambda gfile, *_a: launched.append(gfile.get_uri())   # nie echt öffnen


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def key(name, down):
    xtest.fake_input(D, X.KeyPress if down else X.KeyRelease, D.keysym_to_keycode(XK.string_to_keysym(name)))


def press(*names):
    for n in names:
        key(n, True)
    for n in reversed(names):
        key(n, False)
    D.sync()


def move(xy):
    xtest.fake_input(D, X.MotionNotify, x=int(xy[0]), y=int(xy[1]))
    D.sync()


def button(down):
    xtest.fake_input(D, X.ButtonPress if down else X.ButtonRelease, 1)
    D.sync()


def center(w):
    top = w.get_toplevel()
    _r, ox, oy = top.get_window().get_origin()
    a = w.get_allocation()
    x, y = w.translate_coordinates(top, a.width // 2, a.height // 2)
    return ox + x, oy + y


def mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.EW = ExplorerWindow
        self.cfg = cfgmod.get_config()
        # sauberer Ausgangszustand (nur im Test-Einstellungsordner)
        for k in folderoptions.GENERAL_KEYS + folderoptions.VIEW_KEYS + (
                "view_mode", "folder_views", "folder_sort", "folder_group", "address_history",
                "folder_visits"):
            self.cfg.set(k, cfgmod.DEFAULTS[k])
        self.cfg.set("home_collapsed", [])
        self.win = ExplorerWindow(self, T_URI)
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

    # -- Hilfen ------------------------------------------------------------
    def fv(self, win=None):
        return (win or self.win).current.views["folder"]

    def names(self, win=None):
        return [r[COL_NAME] for r in self.fv(win).store if r[COL_KEY]]

    def selected(self):
        return sorted(os.path.basename(g.get_path()) for g in self.fv().get_selected())

    def row_xy(self, name, dx=60):
        f = self.fv()
        path = next(r.path for r in f.tree.get_model() if r[COL_NAME] == name)
        rect = f.tree.get_background_area(path, f.col_name)
        x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + dx, rect.y + rect.height // 2)
        wx, wy = f.tree.translate_coordinates(self.win, x, y)
        _r, ox, oy = self.win.get_window().get_origin()
        return ox + wx, oy + wy

    def date_xy(self, name):
        f = self.fv()
        path = next(r.path for r in f.tree.get_model() if r[COL_NAME] == name)
        rect = f.tree.get_background_area(path, f.col_date)
        x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 30, rect.y + rect.height // 2)
        wx, wy = f.tree.translate_coordinates(self.win, x, y)
        _r, ox, oy = self.win.get_window().get_origin()
        return ox + wx, oy + wy

    def icon_xy(self, name, check=False):
        f = self.fv()
        path = next(r.path for r in f.iv.get_model() if r[COL_NAME] == name)
        _ok, rect = f.iv.get_cell_rect(path, None)
        if check:
            x, y = f._iv_check_pos(rect)
            x, y = x + CHECK_SIZE // 2, y + CHECK_SIZE // 2
        else:
            x, y = rect.x + rect.width // 2, rect.y + rect.height // 2
        wx, wy = f.iv.translate_coordinates(self.win, x, y)
        _r, ox, oy = self.win.get_window().get_origin()
        return ox + wx, oy + wy

    def click(self, xy, ctrl=False):
        move(xy)
        yield 200                      # Tooltip abwarten lassen, sonst fängt er den Klick
        if ctrl:
            key("Control_L", True)
        button(True)
        button(False)
        if ctrl:
            key("Control_L", False)
        D.sync()
        yield 350

    def dclick(self, xy, ctrl=False):
        move(xy)
        yield 200
        if ctrl:
            key("Control_L", True)
        for _ in range(2):
            button(True)
            button(False)
        if ctrl:
            key("Control_L", False)
        D.sync()
        yield 900

    def dialog(self):
        return next((w for w in Gtk.Window.list_toplevels()
                     if isinstance(w, folderoptions.FolderOptionsDialog) and w.get_visible()), None)

    def open_dialog(self, page=0):
        d = folderoptions.show_dialog(self.win)
        yield 500
        if page:
            yield from self.click(center(d.notebook.get_tab_label(d.notebook.get_nth_page(page))))
        self.d = d

    def ok(self):
        yield from self.click(center(self.d.btn_ok))
        yield 300

    def other_windows(self):
        return [w for w in self.get_windows() if isinstance(w, self.EW) and w is not self.win
                and w is not getattr(self, "win2", None)]

    def close_other_windows(self):
        for w in self.other_windows():
            w.destroy()

    # -- Ablauf --------------------------------------------------------------
    def script(self):
        win, cfg = self.win, self.cfg
        # zweites Fenster rechts daneben: Einstellungen wirken in allen Fenstern
        self.win2 = self.EW(self, T_URI)
        self.win2.move(1010, 0)
        self.win2.resize(580, 600)
        self.win2.present()
        yield 900

        # 1 ··· → Optionen öffnet den Dialog
        yield from self.click(center(win.btn_more))
        yield 300
        menus = [w.get_child() for w in Gtk.Window.list_toplevels()
                 if w.get_visible() and isinstance(w.get_child(), Gtk.Menu)]
        item = None
        for m in menus:
            for mi in m.get_children():
                box = mi.get_child()
                labels = [c for c in (box.get_children() if isinstance(box, Gtk.Box) else [])
                          if isinstance(c, Gtk.Label)]
                if any(lab.get_text() == "Optionen" for lab in labels):
                    item = mi
        check("··· enthält „Optionen“", item is not None)
        if item is None:
            return
        check("„Optionen“ ist der letzte Eintrag", item is item.get_parent().get_children()[-1])
        yield from self.click(center(item))
        yield 600
        d = self.d = self.dialog()
        check("Dialog „Ordneroptionen“ geht auf", d is not None and d.get_title() == "Ordneroptionen")
        if d is None:
            return
        tabs = [d.notebook.get_tab_label_text(d.notebook.get_nth_page(i)) for i in range(2)]
        check("Registerkarten Allgemein und Ansicht", tabs == ["Allgemein", "Ansicht"])
        check("Übernehmen anfangs deaktiviert", not d.btn_apply.get_sensitive())

        # 2 Abbrechen verwirft
        yield from self.click(center(d.notebook.get_tab_label(d.notebook.get_nth_page(1))))
        yield from self.click(center(d.adv_checks["show_hidden"]))
        check("Änderung aktiviert Übernehmen", d.btn_apply.get_sensitive())
        yield from self.click(center(d.btn_cancel))
        yield 300
        check("Abbrechen schliesst und verwirft", self.dialog() is None and not cfg.get("show_hidden")
              and ".versteckt.txt" not in self.names())

        # 3 Übernehmen: ausgeblendete Dateien in allen Fenstern
        yield from self.open_dialog(1)
        yield from self.click(center(self.d.adv_checks["show_hidden"]))
        yield from self.click(center(self.d.btn_apply))
        yield 400
        check("Übernehmen speichert, Dialog bleibt offen", cfg.get("show_hidden") is True
              and self.dialog() is self.d and not self.d.btn_apply.get_sensitive())
        check("Ausgeblendete Dateien in beiden Fenstern sichtbar",
              ".versteckt.txt" in self.names() and ".versteckt.txt" in self.names(self.win2))
        yield from self.ok()
        # Strg+H im Fenster bleibt mit dem Dialog im Einklang
        yield from self.click(self.row_xy("a.txt"))
        press("Control_L", "h")
        yield 500
        yield from self.open_dialog(1)
        check("Strg+H und Dialog im Einklang", not cfg.get("show_hidden")
              and not self.d.adv_checks["show_hidden"].get_active())
        cfg.set("compact", True)            # wie ein Umschalter in einem anderen Fenster
        yield 200
        check("Offener Dialog folgt Änderungen von aussen", self.d.adv_checks["compact"].get_active()
              and not self.d.btn_apply.get_sensitive())
        cfg.set("compact", False)
        yield 200

        # 4 Erweiterungen, Statusleiste, Pfad, Kompaktansicht, Kontrollkästchen
        for k in ("show_extensions", "status_bar", "full_path_title", "compact", "item_checkboxes"):
            yield from self.click(center(self.d.adv_checks[k]))
        yield from self.ok()
        check("Erweiterungen ausgeblendet (beide Fenster)", "a" in self.names() and "a.txt" not in self.names()
              and "a" in self.names(self.win2))
        check("Statusleiste ausgeblendet (beide Fenster)", not win.statusbar.get_visible()
              and not self.win2.statusbar.get_visible())
        check("Vollständiger Pfad in Titelleiste und Tab", T in win.get_title()
              and win.current.widget.label.get_text() == T)
        check("Kompaktansicht an", self.fv()._compact and self.fv(self.win2)._compact)
        check("Kontrollkästchen-Spalte sichtbar", self.fv().name_check.get_visible())

        # 5 Kontrollkästchen anklicken: Mehrfachauswahl ohne Strg
        f = self.fv()
        f.select_none()
        yield from self.click(self.row_xy("a", dx=12))
        yield from self.click(self.row_xy("c", dx=12))
        check("Kästchen wählen a und c", self.selected() == ["a.txt", "c.txt"])
        yield from self.click(self.row_xy("a", dx=12))
        check("Kästchen wählt a wieder ab", self.selected() == ["c.txt"])
        yield from self.dclick(self.row_xy("Beta", dx=12))
        check("Doppelklick aufs Kästchen öffnet nicht", win.current.location == T_URI)
        f.set_view_mode("large")
        yield 900
        yield from self.click(self.icon_xy("b", check=True))
        check("Kästchen in Symbolansicht", "b.txt" in self.selected() and "c.txt" in self.selected())
        yield from self.click(self.icon_xy("a"))
        check("Normaler Klick wählt nur ein Element", self.selected() == ["a.txt"])
        f.set_view_mode("details")
        yield 700

        # 6 Öffnen durch einfachen Klick (Kästchen wieder aus)
        yield from self.open_dialog(0)
        yield from self.click(center(self.d.rb_click.radios[0][0]))
        yield from self.click(center(self.d.notebook.get_tab_label(self.d.notebook.get_nth_page(1))))
        yield from self.click(center(self.d.adv_checks["item_checkboxes"]))
        yield from self.ok()
        check("Einfachklick eingestellt", cfg.get("single_click") is True and self.fv()._single)
        f = self.fv()
        f.select_none()
        move(self.row_xy("b"))
        yield 900
        check("Auswahl durch Zeigen", self.selected() == ["b.txt"] and win.current.location == T_URI)
        yield from self.click(self.row_xy("c"), ctrl=True)
        yield 600
        check("Strg+Klick wählt dazu ohne zu öffnen", self.selected() == ["b.txt", "c.txt"]
              and not launched and win.current.location == T_URI)
        # Auswahlrahmen (Datumsspalte) öffnet nichts
        move(self.date_xy("Alpha"))
        yield 200
        button(True)
        for i in range(1, 7):
            x0, y0 = self.date_xy("Alpha")
            x1, y1 = self.date_xy("a")
            move((x0 + (x1 - x0) * i / 6, y0 + (y1 - y0) * i / 6 + 4))
            yield 60
        button(False)
        yield 500
        check("Auswahlrahmen wählt mehrere, öffnet nichts", self.selected() == ["Alpha", "Beta", "a.txt"]
              and win.current.location == T_URI)
        yield from self.click(self.row_xy("Alpha"))
        yield 600
        check("Einfachklick öffnet Ordner (Details)", win.current.location == ALPHA)
        win.current.go_back()
        yield 800
        yield from self.dclick(self.row_xy("Alpha"))
        check("Doppelklick öffnet im Einfachklick-Modus nur einmal", win.current.location == ALPHA)
        win.current.go_back()
        yield 800
        f = self.fv()
        f.set_view_mode("large")
        yield 900
        yield from self.click(self.icon_xy("Beta"))
        yield 600
        check("Einfachklick öffnet Ordner (Symbole)", win.current.location == BETA)
        win.current.go_back()
        yield 800
        self.fv().set_view_mode("details")
        yield 600
        check("Einfachklick hat keine Datei geöffnet", not launched)

        # 7 Jeden Ordner in einem eigenen Fenster (wieder Doppelklick)
        yield from self.open_dialog(0)
        yield from self.click(center(self.d.rb_click.radios[1][0]))
        yield from self.click(center(self.d.rb_window.radios[1][0]))
        yield from self.ok()
        n0 = len(self.other_windows())
        yield from self.dclick(self.row_xy("Beta"))
        new = self.other_windows()
        check("Doppelklick öffnet Ordner in neuem Fenster", len(new) == n0 + 1
              and new[-1].current.location == BETA and win.current.location == T_URI)
        self.close_other_windows()
        win.present()
        yield 600
        yield from self.click(self.row_xy("Alpha"))
        press("Return")
        yield 900
        new = self.other_windows()
        check("Eingabe öffnet Ordner in neuem Fenster", len(new) == 1 and new[0].current.location == ALPHA
              and win.current.location == T_URI)
        self.close_other_windows()
        win.present()
        yield 600
        ntabs = len(win.tabs)
        yield from self.dclick(self.row_xy("Beta"), ctrl=True)
        check("Strg+Doppelklick öffnet weiter einen Tab", len(win.tabs) == ntabs + 1
              and not self.other_windows())
        win.close_tab(win.tabs[-1])
        yield 400

        # 8 Datei-Explorer öffnen für: Dieser PC
        yield from self.open_dialog(0)
        self.d.combo_start.set_active_id("thispc")
        yield from self.ok()
        check("Startort gespeichert", folderoptions.start_location() == places.THISPC)
        FileFensterApp.do_activate(self)        # wie ein Start ohne Pfad
        yield 800
        new = self.other_windows()
        check("Neues Fenster öffnet Dieser PC", len(new) == 1 and new[0].current.location == places.THISPC)
        self.close_other_windows()
        win.present()
        yield 500
        yield from self.click(self.row_xy("a"))
        press("Control_L", "t")
        yield 600
        check("Strg+T öffnet Dieser PC", win.current.location == places.THISPC)
        win.close_tab(win.current)
        yield 400

        # 9 Datenschutz: häufig verwendete Ordner, zuletzt verwendete Dateien
        for _ in range(2):
            win.current.navigate(ALPHA)
            yield 400
            win.current.go_back()
            yield 400
        check("Ordnerbesuche gezählt", (cfg.get("folder_visits") or {}).get(ALPHA, [0])[0] >= 2)
        win.current.navigate(places.HOME)
        yield 1000
        home = win.current.views["home"]

        def tiles():
            return {c.uri: c.pinned for c in home.flow.get_children()}
        check("Häufig verwendeter Ordner im Schnellzugriff (ohne Nadel)", tiles().get(ALPHA) is False)
        yield from self.open_dialog(0)
        check("Für Ordner übernehmen nur aus einem Ordner", not self.d.btn_apply_folders.get_sensitive())
        yield from self.click(center(self.d.chk_frequent))
        yield from self.click(center(self.d.chk_recent))
        yield from self.ok()
        yield 600
        check("Häufig verwendete Ordner ausgeblendet", ALPHA not in tiles())
        check("Zuletzt verwendet ausgeblendet", not home.sec_recent.get_visible()
              and len(home.recent_store) == 0)
        yield from self.open_dialog(0)
        yield from self.click(center(self.d.chk_frequent))
        yield from self.click(center(self.d.chk_recent))
        yield from self.ok()
        yield 800
        check("Wieder eingeblendet", tiles().get(ALPHA) is False and home.sec_recent.get_visible())
        # Löschen: nur FileFenster-eigener Verlauf
        cfg.set("address_history", [T_URI])
        before = mtime(XBEL)
        yield from self.open_dialog(0)
        yield from self.click(center(self.d.btn_clear))
        yield 800
        check("Löschen leert Adressverlauf und Ordnerbesuche",
              cfg.get("address_history") == [] and cfg.get("folder_visits") == {})
        check("Löschen nimmt den Ordner aus dem Schnellzugriff", ALPHA not in tiles())
        check("Systemweite zuletzt verwendete Dateien unberührt", mtime(XBEL) == before)

        # 10 Standardwerte wiederherstellen (Allgemein): erst mit OK gespeichert
        self.d.rb_click.radios[0][0].set_active(True)
        self.d.chk_recent.set_active(False)
        yield from self.ok()
        yield from self.open_dialog(0)
        yield from self.click(center(self.d.btn_defaults_general))
        d = self.d
        check("Standardwerte setzen die Bedienelemente", d.combo_start.get_active_id() == "home"
              and d.rb_click.radios[1][0].get_active() and d.rb_window.radios[0][0].get_active()
              and d.chk_recent.get_active() and d.chk_frequent.get_active())
        yield from self.click(center(d.btn_cancel))
        check("Abbrechen behält die alten Werte", cfg.get("single_click") is True
              and cfg.get("start_location") == "thispc" and cfg.get("show_recent") is False)
        yield from self.open_dialog(0)
        yield from self.click(center(self.d.btn_defaults_general))
        yield from self.ok()
        check("Standardwerte mit OK gespeichert (Allgemein)",
              all(cfg.get(k) == cfgmod.DEFAULTS[k] for k in folderoptions.GENERAL_KEYS))

        # 11 Standardwerte wiederherstellen (Ansicht)
        win.current.navigate(T_URI)
        yield 800
        yield from self.open_dialog(1)
        yield from self.click(center(self.d.adv_checks["show_hidden"]))
        yield from self.click(center(self.d.adv_checks["item_checkboxes"]))
        yield from self.ok()
        yield from self.open_dialog(1)
        yield from self.click(center(self.d.btn_defaults_view))
        yield from self.ok()
        check("Standardwerte mit OK gespeichert (Ansicht)",
              all(cfg.get(k) == cfgmod.DEFAULTS[k] for k in folderoptions.VIEW_KEYS))
        f = self.fv()
        check("Ansicht folgt den Standardwerten", win.statusbar.get_visible() and not f._compact
              and not f.name_check.get_visible() and "a.txt" in self.names()
              and ".versteckt.txt" not in self.names() and win.get_title().startswith("filefenster-optionen"))

        # 12 Für Ordner übernehmen
        f.set_view_mode("list")
        fvs = dict(cfg.get("folder_views") or {})
        fvs[BETA] = "tiles"
        cfg.set("folder_views", fvs)
        yield 400
        yield from self.open_dialog(1)
        check("Für Ordner übernehmen aus einem Ordner möglich", self.d.btn_apply_folders.get_sensitive())
        yield from self.click(center(self.d.btn_apply_folders))
        yield 700
        press("Return")                    # Rückfrage: „Ja“
        yield 600
        check("Ansicht gilt für alle Ordner", cfg.get("view_mode") == "list"
              and cfg.get("folder_views") == {T_URI: "list"})
        yield from self.ok()
        win.current.navigate(BETA)
        yield 800
        check("Anderer Ordner zeigt jetzt Liste", self.fv().view_mode == "list")
        win.current.go_back()
        yield 800

        # 13 Ordner zurücksetzen (Nein ändert nichts, Ja setzt zurück)
        f = self.fv()
        f.set_sort("size", False)
        f.set_group("type")
        yield 400
        yield from self.open_dialog(1)
        yield from self.click(center(self.d.btn_reset_folders))
        yield 700
        press("Escape")                    # Rückfrage abbrechen
        yield 500
        check("Rückfrage abgebrochen: nichts zurückgesetzt", cfg.get("view_mode") == "list"
              and (cfg.get("folder_sort") or {}).get(T_URI) == ["size", False])
        yield from self.click(center(self.d.btn_reset_folders))
        yield 700
        press("Return")
        yield 700
        check("Ordner zurücksetzen leert Ansichten, Sortierung, Gruppierung",
              cfg.get("folder_views") == {} and cfg.get("folder_sort") == {}
              and cfg.get("folder_group") == {} and cfg.get("view_mode") == "details")
        f = self.fv()
        check("Offener Ordner sofort zurückgesetzt", f.view_mode == "details" and f.sort_column == "name"
              and f.group_by is None and self.fv(self.win2).view_mode == "details")
        yield from self.click(center(self.d.btn_cancel))

        # 14 Einstellungen bleiben erhalten
        yield from self.open_dialog(0)
        self.d.combo_start.set_active_id("thispc")
        self.d.rb_click.radios[0][0].set_active(True)
        self.d.adv_checks["full_path_title"].set_active(True)
        yield from self.ok()
        cfg.save_now()
        with open(cfgmod.CONFIG_FILE, encoding="utf-8") as fh:
            saved = json.load(fh)
        fresh = cfgmod.Config()
        check("In settings.json gespeichert", saved.get("start_location") == "thispc"
              and saved.get("single_click") is True and saved.get("full_path_title") is True)
        check("Neu geladen wieder da", fresh.get("start_location") == "thispc"
              and fresh.get("single_click") is True and fresh.get("full_path_title") is True)
        for k in folderoptions.GENERAL_KEYS + folderoptions.VIEW_KEYS:
            cfg.set(k, cfgmod.DEFAULTS[k])
        cfg.save_now()
        check("Keine Datei geöffnet", not launched)
        self.close_other_windows()
        self.win2.destroy()
        yield 300


App().run([sys.argv[0]])
D.close()
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
