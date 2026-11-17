#!/usr/bin/env python3
"""In eine geöffnete ZIP-Datei schreiben (wie Windows) – nur auf dem
Test-Bildschirm, Maus und Tasten per XTEST:

    Xvfb :101 -screen 0 1600x1000x24 -nolisten tcp &
    DISPLAY=:101 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_zipschreiben.py OUTDIR

Zwei Fenster: links der Quellordner, rechts die ZIP-Datei. Geprüft werden
Einfügen (Strg+C/Strg+V), Ziehen (kopieren, auf einen Ordner im ZIP,
Umschalt = verschieben), Konflikt „Ersetzen“, Löschen mit Rückfrage (Entf),
Umbenennen (F2) von Datei und Ordner, Neuer Ordner (Strg+Umschalt+N),
Abbrechen (Original bleibt unverändert) und dass TAR.GZ und ZIP im ZIP
schreibgeschützt bleiben. Nach jedem Schritt wird der Inhalt mit zipfile
geprüft. Testordner: ~/.cache/filefenster-zipschreiben.
"""
import io
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (z. B. DISPLAY=:101)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from Xlib import X as XC, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import archive, dnd, fileops, places, progress, undo  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_NAME  # noqa: E402

outdir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-zipschreiben")
Q = os.path.join(T, "quelle")
Z = os.path.join(T, "Archiv.zip")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(os.path.join(Q, "Ordner", "sub"))
for p, txt in (("neu.txt", "neu"), ("liste.txt", "Neue Liste"), ("Ordner/a.txt", "a"),
               ("Ordner/sub/b.txt", "b"), ("zieh.txt", "zieh"), ("zieh2.txt", "zieh2"),
               ("schieb.txt", "schieb"), ("gross.bin", None)):
    with open(os.path.join(Q, p), "wb") as fh:
        fh.write(os.urandom(40 << 20) if txt is None else txt.encode())
with zipfile.ZipFile(Z, "w") as zf:
    zi = zipfile.ZipInfo("Urlaub/bild1.txt", (2020, 5, 6, 7, 8, 10))
    zf.writestr(zi, "eins " * 200, compress_type=zipfile.ZIP_DEFLATED)
    zf.writestr(zipfile.ZipInfo("Urlaub/bild2.txt", (2021, 1, 2, 3, 4, 6)), "zwei",
                compress_type=zipfile.ZIP_STORED)
    zf.writestr("liste.txt", "Liste")
    zf.writestr("Dokumente/brief.txt", "Brief " * 100, compress_type=zipfile.ZIP_BZIP2)
ORIG = {i.filename: (i.CRC, i.compress_type, i.date_time, i.compress_size)
        for i in zipfile.ZipFile(Z).infolist()}
inner = io.BytesIO()
with zipfile.ZipFile(inner, "w") as zf:
    zf.writestr("innen.txt", "innen")
with zipfile.ZipFile(os.path.join(T, "aussen.zip"), "w") as zf:
    zf.writestr("innen.zip", inner.getvalue())
os.makedirs(os.path.join(T, "tarquelle", "inhalt"))
with open(os.path.join(T, "tarquelle", "inhalt", "x.txt"), "w") as fh:
    fh.write("x")
with tarfile.open(os.path.join(T, "paket.tar.gz"), "w:gz") as tf:
    tf.add(os.path.join(T, "tarquelle", "inhalt"), arcname="inhalt")
shutil.rmtree(os.path.join(T, "tarquelle"))

ok = fail = 0
ERR = []
fileops.show_errors = lambda parent, title, errors: ERR.append((title, errors))
D = display.Display()


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def members(path=Z):
    with zipfile.ZipFile(path) as zf:
        return sorted(zf.namelist())


def read(name):
    with zipfile.ZipFile(Z) as zf:
        return zf.read(name).decode()


def info(name):
    with zipfile.ZipFile(Z) as zf:
        i = zf.getinfo(name)
        return (i.CRC, i.compress_type, i.date_time, i.compress_size)


def move(x, y):
    xtest.fake_input(D, XC.MotionNotify, x=int(x), y=int(y))
    D.sync()


def button(b, down):
    xtest.fake_input(D, XC.ButtonPress if down else XC.ButtonRelease, b)
    D.sync()


def key(name, down):
    code = D.keysym_to_keycode(XK.string_to_keysym(name))
    xtest.fake_input(D, XC.KeyPress if down else XC.KeyRelease, code)
    D.sync()


def press(*names):
    """Tastenkombination, z. B. press("Control_L", "v")."""
    for n in names:
        key(n, True)
    for n in reversed(names):
        key(n, False)


def type_text(text):
    for ch in text:
        if ch.isupper():
            press("Shift_L", ch.lower())
        else:
            press(ch)


def shot(name):
    subprocess.run(["import", "-display", os.environ["DISPLAY"], "-window", "root",
                    "-crop", "1600x920+0+0", os.path.join(outdir, name + ".png")])


def root_of(widget, x, y):
    top = widget.get_toplevel()
    wx, wy = widget.translate_coordinates(top, int(x), int(y))
    _r, ox, oy = top.get_window().get_origin()
    return ox + wx, oy + wy


def find_button(window, label):
    hits = []

    def walk(w):
        if isinstance(w, Gtk.Button) and (w.get_label() == label or any(
                isinstance(c, Gtk.Label) and c.get_text() == label
                for c in (w.get_child().get_children() if isinstance(w.get_child(), Gtk.Box)
                          else [w.get_child()]))):
            hits.append(w)
        if isinstance(w, Gtk.Container):
            w.forall(walk)
    walk(window)
    return hits[0] if hits else None


def dialog():
    return next((w for w in Gtk.Window.list_toplevels()
                 if w.get_visible() and w.get_style_context().has_class("win-dialog")), None)


def dialog_text(dlg):
    texts = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            texts.append(w.get_text())
        if isinstance(w, Gtk.Container):
            w.forall(walk)
    walk(dlg)
    return "\n".join(texts)


def menus(fv):
    """Kontextmenüs (Element, Hintergrund, Neu) → {Text: aktiv} statt sie zu öffnen."""
    got = []
    fv._popup = lambda m, _ev: got.append(m)

    def state(m):
        out = {}
        for mi in m.menu.get_children():
            labels = []

            def walk(w):
                if isinstance(w, Gtk.Label):
                    labels.append(w.get_text())
                if isinstance(w, Gtk.Container):
                    w.forall(walk)
            walk(mi)
            name = mi.get_tooltip_text() or (labels[0] if labels else None)
            if name:
                out[name] = mi.get_sensitive()
        return out
    try:
        fv._popup_item_menu(None)
        fv._popup_background_menu(None)
    finally:
        del fv._popup
    return state(got[0]), state(got[1]), state(fv.build_new_menu())


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.a = ExplorerWindow(self, places.uri_for_path(Q))     # links: Quelle
        self.a.move(0, 0)
        self.a.resize(780, 900)
        self.a.present()
        self.b = ExplorerWindow(self, places.uri_for_path(T))     # rechts: ZIP
        self.b.move(810, 0)
        self.b.resize(780, 900)
        self.b.present()
        self.steps = self.script()
        GLib.timeout_add(1500, self.tick)

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
    def fv(self, win):
        return win.current.views["folder"]

    def names(self, win):
        f = self.fv(win)
        view = f.tree if f._is_tree() else f.iv
        return sorted(r[COL_NAME] for r in view.get_model() or [])

    def selected(self, win):
        return sorted(g.get_basename() for g in self.fv(win).get_selected())

    def item_xy(self, win, name):
        f = self.fv(win)
        path = next(r.path for r in f.tree.get_model() if r[COL_NAME] == name)
        rect = f.tree.get_background_area(path, f.col_name)
        x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 40, rect.y + rect.height // 2)
        return root_of(f.tree, x, y)

    def background_xy(self, win):
        view = self.fv(win)._view()
        a = view.get_allocation()
        return root_of(view, a.width - 150, a.height - 80)

    def click(self, xy, ctrl=False):
        move(*xy)
        yield 250                     # Tooltips fangen sonst den Klick ab
        if ctrl:
            key("Control_L", True)
        button(1, True)
        button(1, False)
        if ctrl:
            key("Control_L", False)
        yield 250

    def select(self, win, *names):
        win.present()
        yield 200
        for i, n in enumerate(names):
            yield from self.click(self.item_xy(win, n), ctrl=i > 0)

    def keys(self, win, *names):
        win.present()                 # ohne Fenstermanager: Tastaturfokus setzen
        yield 200
        press(*names)
        yield 300

    def dblclick(self, win, name):
        move(*self.item_xy(win, name))
        yield 250
        for _i in range(2):
            button(1, True)
            button(1, False)
        yield from self.settle(win)

    def drag(self, a, b, mods=()):
        for m in mods:
            key(m, True)
        move(*a)
        yield 150
        button(1, True)
        yield 150
        for i in range(1, 15):
            move(a[0] + (b[0] - a[0]) * i / 14, a[1] + (b[1] - a[1]) * i / 14)
            yield 40
        yield 400
        button(1, False)
        yield 150
        for m in mods:
            key(m, False)
        yield 300

    def settle(self, win, cond=None, timeout=100):
        """Warten, bis alle Vorgänge fertig sind, das Archiv neu eingehängt und
        die Ansicht neu geladen ist (und cond() gilt)."""
        for _i in range(timeout):
            yield 100
            if fileops.busy() or self.fv(win)._loading:
                continue
            if cond is None or cond():
                break
        yield 400

    def confirm(self, answer):
        """Rückfrage abwarten und per Mausklick beantworten → Dialogtext."""
        dlg = None
        for _i in range(40):
            dlg = dialog()
            if dlg is not None:
                break
            yield 100
        if dlg is None:
            return ""
        text = dialog_text(dlg)
        yield 300
        btn = find_button(dlg, answer)
        a = btn.get_allocation()
        yield from self.click(root_of(btn, a.width // 2, a.height // 2))
        yield 300
        self._last_dialog = text
        return text

    # -- Ablauf ------------------------------------------------------------
    def script(self):
        a, b = self.a, self.b
        undo.reset()
        yield from self.settle(b)
        yield from self.dblclick(b, "Archiv.zip")
        loc = b.current.location
        check("Doppelklick öffnet Archiv.zip als Ordner",
              loc.startswith("archive://") and "Urlaub" in self.names(b))
        fvb = self.fv(b)
        check("Lokale ZIP-Datei ist beschreibbar", not fvb.archive_read_only() and dnd.droppable(loc))
        check("Befehlsleiste: Neu aktiv, Ausschneiden gesperrt",
              b.btn_new.get_sensitive() and not b.btn_cut.get_sensitive())

        # 1 Einfügen: Strg+C links, Strg+V rechts
        yield from self.select(a, "neu.txt", "Ordner")
        yield from self.keys(a, "Control_L", "c")
        b.present()
        yield 300
        check("Einfügen aktiv", b.btn_paste.get_sensitive())
        yield from self.click(self.background_xy(b))
        yield from self.keys(b, "Control_L", "v")
        yield from self.settle(b, lambda: "neu.txt" in self.names(b))
        m = members()
        check("Strg+V: Datei und Ordner im ZIP",
              {"neu.txt", "Ordner/", "Ordner/a.txt", "Ordner/sub/", "Ordner/sub/b.txt"} <= set(m)
              and read("Ordner/sub/b.txt") == "b")
        check("Ansicht neu geladen, Neues ausgewählt",
              "neu.txt" in self.names(b) and self.selected(b) == ["Ordner", "neu.txt"])
        check("Quellen bleiben beim Kopieren", os.path.exists(os.path.join(Q, "neu.txt")))
        shot("zip-eingefuegt")
        item, bg, new = menus(fvb)
        check("Kontextmenü im ZIP: Umbenennen/Löschen aktiv, Ausschneiden/Komprimieren nicht",
              item.get("Umbenennen") and item.get("Löschen") and item.get("Ausschneiden") is False
              and "In ZIP-Datei komprimieren" not in item)
        check("Hintergrundmenü im ZIP: Einfügen und Neu › Ordner aktiv, Neu › Textdokument nicht",
              bg.get("Einfügen") and bg.get("Neu") and new.get("Ordner")
              and new.get("Textdokument") is False)
        check("Unveränderte Einträge: Kompression und Zeit erhalten",
              all(info(n) == ORIG[n] for n in ORIG))

        # 2 Konflikt → Ersetzen
        yield from self.select(a, "liste.txt")
        yield from self.keys(a, "Control_L", "c")
        yield from self.click(self.background_xy(b))
        yield from self.keys(b, "Control_L", "v")
        job = None
        for _i in range(50):
            job = next((j for j in fileops.running_jobs() if j.conflict), None)
            if job:
                break
            yield 100
        check("Konflikt-Rückfrage bei liste.txt", job is not None)
        if job is not None:
            yield 900
            shot("zip-konflikt")
            win = progress._win
            btn = find_button(win, "Die Datei im Ziel ersetzen") if win else None
            if btn is not None:
                al = btn.get_allocation()
                yield from self.click(root_of(btn, al.width // 2, al.height // 2))
            else:
                job.answer("replace")
        yield from self.settle(b, lambda: read("liste.txt") == "Neue Liste")
        check("Ersetzen: neuer Inhalt, nur ein Eintrag",
              read("liste.txt") == "Neue Liste" and members().count("liste.txt") == 1)

        # 3 Ziehen: kopieren auf die Fläche, auf einen Ordner im ZIP, Umschalt = verschieben
        yield from self.drag(self.item_xy(a, "zieh.txt"), self.background_xy(b))
        yield from self.settle(b, lambda: "zieh.txt" in members())
        check("Ziehen ins ZIP kopiert", "zieh.txt" in members()
              and os.path.exists(os.path.join(Q, "zieh.txt")) and "zieh.txt" in self.names(b))
        yield from self.drag(self.item_xy(a, "zieh2.txt"), self.item_xy(b, "Urlaub"))
        yield from self.settle(b, lambda: "Urlaub/zieh2.txt" in members())
        check("Ziehen auf Ordner im ZIP", "Urlaub/zieh2.txt" in members()
              and read("Urlaub/zieh2.txt") == "zieh2")
        yield from self.drag(self.item_xy(a, "schieb.txt"), self.background_xy(b), mods=("Shift_L",))
        yield from self.settle(b, lambda: "schieb.txt" in members())
        yield from self.settle(a)
        check("Umschalt+Ziehen verschiebt ins ZIP", "schieb.txt" in members()
              and not os.path.exists(os.path.join(Q, "schieb.txt")))

        # 4 Umbenennen (F2): Datei und Ordner
        yield from self.select(b, "neu.txt")
        yield from self.keys(b, "F2")
        check("F2: Umbenennen beginnt", bool(fvb._renaming))
        type_text("umbenannt")
        press("Return")
        yield from self.settle(b, lambda: "umbenannt.txt" in self.names(b))
        check("Datei umbenannt", "umbenannt.txt" in members() and "neu.txt" not in members()
              and read("umbenannt.txt") == "neu" and self.selected(b) == ["umbenannt.txt"])
        yield from self.select(b, "Urlaub")
        yield from self.keys(b, "F2")
        type_text("Ferien")
        press("Return")
        yield from self.settle(b, lambda: "Ferien" in self.names(b))
        m = members()
        check("Ordner umbenannt (alle Einträge)",
              {"Ferien/bild1.txt", "Ferien/bild2.txt", "Ferien/zieh2.txt"} <= set(m)
              and not any(n.startswith("Urlaub/") for n in m))
        check("Umbenannt: Daten, Kompression und Zeit erhalten",
              info("Ferien/bild1.txt") == ORIG["Urlaub/bild1.txt"]
              and info("Ferien/bild2.txt") == ORIG["Urlaub/bild2.txt"])

        # 5 Neuer Ordner (Strg+Umschalt+N) → gleich umbenennen
        yield from self.click(self.background_xy(b))
        yield from self.keys(b, "Control_L", "Shift_L", "n")
        yield from self.settle(b, lambda: fvb._renaming)
        check("Neuer Ordner im ZIP, Umbenennen aktiv", "Neuer Ordner/" in members()
              and bool(fvb._renaming))
        type_text("Projekte")
        press("Return")
        yield from self.settle(b, lambda: "Projekte" in self.names(b))
        check("Neuer Ordner umbenannt", "Projekte/" in members() and "Neuer Ordner/" not in members())

        # 6 Löschen mit Rückfrage (Entf): erst „Nein“, dann „Ja“
        yield from self.select(b, "liste.txt")
        yield from self.keys(b, "Delete")
        text = yield from self.confirm("Nein")
        check("Entf fragt wie Windows", "Möchten Sie diese Datei endgültig löschen?" in text)
        yield from self.settle(b)
        check("Nein: nichts gelöscht", "liste.txt" in members())
        yield from self.select(b, "liste.txt", "Dokumente")
        yield from self.keys(b, "Delete")
        text = yield from self.confirm("Ja")
        yield from self.settle(b, lambda: "liste.txt" not in self.names(b))
        m = members()
        check("Datei und Ordner gelöscht", "Möchten Sie diese 2 Elemente endgültig löschen?" in text
              and "liste.txt" not in m and not any(n.startswith("Dokumente/") for n in m)
              and "liste.txt" not in self.names(b) and "Dokumente" not in self.names(b))
        check("Kein Papierkorb-Eintrag für ZIP-Inhalte", not any(
            (i.get_attribute_byte_string("trash::orig-path") or "").startswith(T)
            for _g, i in fileops.trash_items()))

        # 7 Im Unterordner: Ansicht bleibt dort; letzte Datei löschen lässt den Ordner stehen
        yield from self.dblclick(b, "Ferien")
        sub = b.current.location
        yield from self.select(b, "bild1.txt", "bild2.txt", "zieh2.txt")
        yield from self.keys(b, "Delete")
        yield from self.confirm("Ja")
        yield from self.settle(b, lambda: not self.names(b))
        check("Ansicht bleibt im Ordner im ZIP", b.current.location == sub and self.names(b) == []
              and not self.fv(b)._error)
        check("Leerer Ordner bleibt erhalten (wie Windows)", "Ferien/" in members()
              and not any(n.startswith("Ferien/") and n != "Ferien/" for n in members()))
        yield from self.select(a, "zieh.txt")
        yield from self.keys(a, "Control_L", "c")
        yield from self.click(self.background_xy(b))
        yield from self.keys(b, "Control_L", "v")
        yield from self.settle(b, lambda: "zieh.txt" in self.names(b))
        check("Einfügen im Unterordner", "Ferien/zieh.txt" in members()
              and b.current.location == sub)
        b.current.go_up()
        yield from self.settle(b)

        # 8 Abbrechen: Original bleibt unverändert, keine Reste
        before = open(Z, "rb").read()
        job = archive.zip_add([Gio.File.new_for_path(os.path.join(Q, "gross.bin"))],
                              Gio.File.new_for_uri(b.current.location), parent=b)
        for _i in range(100):
            if job.done_bytes > 0 or job.phase == "done":
                break
            yield 20
        check("Grosser Vorgang läuft", job.phase == "run")
        job.cancel()
        yield from self.settle(b)
        check("Abgebrochen: ZIP unverändert, keine Teildatei",
              open(Z, "rb").read() == before and not job.committed
              and not any(n.endswith(".part") for n in os.listdir(T)))

        check("Kein Rückgängig für ZIP-Änderungen", not undo.can_undo())
        with zipfile.ZipFile(Z) as zf:
            check("ZIP-Datei unbeschädigt", zf.testzip() is None)

        # 9 Schreibgeschützt: TAR.GZ und ZIP im ZIP
        b.current.navigate(places.uri_for_path(T))
        yield from self.settle(b)
        tar_before = open(os.path.join(T, "paket.tar.gz"), "rb").read()
        yield from self.dblclick(b, "paket.tar.gz")
        loc = b.current.location
        f = self.fv(b)
        check("TAR.GZ: nur lesen", loc.startswith("archive://") and f.archive_read_only()
              and not dnd.droppable(loc) and not b.btn_new.get_sensitive()
              and not b.btn_paste.get_sensitive())
        yield from self.select(b, "inhalt")
        item, bg, new = menus(f)
        check("TAR.GZ: Umbenennen/Löschen gesperrt (Befehlsleiste, Kontextmenüs)",
              not b.btn_rename.get_sensitive() and not b.btn_delete.get_sensitive()
              and item.get("Umbenennen") is False and item.get("Löschen") is False
              and bg.get("Neu") is False and new.get("Ordner") is False)
        yield from self.keys(b, "Delete")
        yield from self.keys(b, "F2")
        yield from self.keys(b, "Control_L", "v")
        yield 600
        check("TAR.GZ: Tasten ändern nichts", dialog() is None and not f._renaming
              and not fileops.busy() and open(os.path.join(T, "paket.tar.gz"), "rb").read() == tar_before)

        class Ctx:
            def get_actions(self):
                return Gdk.DragAction.COPY | Gdk.DragAction.MOVE

            def get_suggested_action(self):
                return Gdk.DragAction.COPY
        src = [Gio.File.new_for_path(os.path.join(Q, "zieh.txt"))]
        check("TAR.GZ: kein Ablegen", dnd.decide(Ctx(), src, f._gfile)[0] == 0)
        b.current.go_up()
        yield from self.settle(b)
        outer_before = open(os.path.join(T, "aussen.zip"), "rb").read()
        yield from self.dblclick(b, "aussen.zip")
        check("Äussere ZIP-Datei beschreibbar", not self.fv(b).archive_read_only())
        yield from self.dblclick(b, "innen.zip")
        loc = b.current.location
        f = self.fv(b)
        check("ZIP im ZIP: nur lesen", "innen.txt" in self.names(b) and f.archive_read_only()
              and not dnd.droppable(loc) and not b.btn_new.get_sensitive()
              and dnd.decide(Ctx(), src, f._gfile)[0] == 0)
        yield from self.select(b, "innen.txt")
        yield from self.keys(b, "Delete")
        yield from self.keys(b, "F2")
        yield 600
        check("ZIP im ZIP: Tasten ändern nichts", dialog() is None and not f._renaming
              and open(os.path.join(T, "aussen.zip"), "rb").read() == outer_before)
        b.current.navigate(places.uri_for_path(T))
        yield 2500
        check("Keine Fehlermeldungen", not ERR)
        if ERR:
            print(ERR)


App().run([sys.argv[0]])
shutil.rmtree(T, ignore_errors=True)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
