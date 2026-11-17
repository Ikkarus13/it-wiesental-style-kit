#!/usr/bin/env python3
"""Katalog (Galerie wie Windows 11) mit echten (simulierten) Eingaben testen –
nur auf einem eigenen Test-Bildschirm und mit eigener Konfiguration:

    Xvfb :102 -screen 0 1600x1000x24 -nolisten tcp &
    DISPLAY=:102 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_katalog.py OUTDIR

Die Sammlung zeigt nur auf ~/.cache/filefenster-katalog(-2) mit erzeugten
Bildern (convert) und einem kurzen Video (gst-launch-1.0) – nie auf den echten
Ordner „Bilder“. Öffnen wird abgefangen (es startet nie ein Programm).
Papierkorb, Vorschaubild-Cache: nur die eigenen Einträge werden aufgeräumt.
"""
import datetime
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (z. B. DISPLAY=:102)")
CFG = os.environ.get("FILEFENSTER_CONFIG_DIR")
if not CFG or os.path.realpath(CFG) == os.path.realpath(os.path.expanduser("~/.config/filefenster")):
    sys.exit("FILEFENSTER_CONFIG_DIR auf einen eigenen Testordner setzen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

outdir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-katalog")
T2 = os.path.expanduser("~/.cache/filefenster-katalog-2")
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


# ---------------------------------------------------------------------------
# Testdaten
# ---------------------------------------------------------------------------
def months_ago(n, day, hour=12):
    d = datetime.date.today().replace(day=1)
    y, m = d.year, d.month - n
    while m < 1:
        m += 12
        y -= 1
    return datetime.datetime(y, m, day, hour, 0).timestamp()


def add_exif(path, when):
    """APP1-EXIF mit DateTimeOriginal in eine JPEG-Datei einfügen."""
    stamp = when.encode() + b"\x00"
    tiff = b"II*\x00" + struct.pack("<I", 8)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, 20, 44) + struct.pack("<I", 0)
    tiff += stamp
    payload = b"Exif\x00\x00" + tiff
    with open(path, "rb") as fh:
        data = fh.read()
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    with open(path, "wb") as fh:
        fh.write(data[:2] + seg + data[2:])


now = time.time()
FILES = {   # Name -> (Änderungszeit, convert-Argumente oder None)
    "neu.jpg": (now - 60, ["-size", "800x600", "gradient:#3a7bd5-#00d2ff"]),
    "heute.png": (now - 120, ["-size", "640x480", "xc:#e8603c"]),
    "exif.jpg": (now - 30, ["-size", "640x480", "gradient:#f7971e-#ffd200"]),
    "oktober.jpg": (months_ago(1, 11), ["-size", "600x800", "plasma:fractal"]),
    "sommer/strand.jpg": (months_ago(4, 15), ["-size", "1600x400", "gradient:#56ccf2-#2f80ed"]),
    "Urlaub/2024/berg.jpg": (datetime.datetime(2024, 3, 3, 9).timestamp(),
                             ["-size", "640x480", "gradient:#2e9e62-#a8e063"]),
    "Urlaub/2024/see.png": (datetime.datetime(2024, 3, 20, 9).timestamp(),
                            ["-size", "500x500", "xc:#4b79a1"]),
    "alt.bmp": (datetime.datetime(2021, 7, 1, 9).timestamp(), ["-size", "320x240", "xc:#8a5cf5"]),
    ".versteckt/geheim.jpg": (now, ["-size", "64x64", "xc:gray"]),
}
VIDEO = "video.mp4"
EXIF_WHEN = "2019:05:04 12:30:00"


def setup():
    for d in (T, T2):
        shutil.rmtree(d, ignore_errors=True)
    for name, (mt, args) in FILES.items():
        p = os.path.join(T, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        subprocess.run(["convert", *args, p], check=True)
    add_exif(os.path.join(T, "exif.jpg"), EXIF_WHEN)
    with open(os.path.join(T, "notiz.txt"), "w") as fh:
        fh.write("kein Bild")
    have_video = False
    if shutil.which("gst-launch-1.0"):
        r = subprocess.run(["gst-launch-1.0", "-q", "videotestsrc", "num-buffers=40", "pattern=smpte", "!",
                            "video/x-raw,width=320,height=180,framerate=20/1", "!", "videoconvert", "!",
                            "avenc_mpeg4", "!", "mp4mux", "!", "filesink",
                            f"location={os.path.join(T, VIDEO)}"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        have_video = r.returncode == 0 and os.path.getsize(os.path.join(T, VIDEO)) > 0
    if have_video:
        FILES[VIDEO] = (months_ago(1, 20), None)
    for name, (mt, _a) in FILES.items():
        os.utime(os.path.join(T, name), (mt, mt))
    os.makedirs(T2)
    subprocess.run(["convert", "-size", "300x200", "xc:#26a0da", os.path.join(T2, "zweiter.jpg")], check=True)
    return have_video


HAVE_VIDEO = setup()
VISIBLE = [n for n in FILES if not n.startswith(".")]


def taken(name):
    if name == "exif.jpg":
        return datetime.datetime(2019, 5, 4, 12, 30).timestamp()
    return FILES[name][0]


def label(ts):
    d = datetime.datetime.fromtimestamp(ts)
    return f"{MONATE[d.month - 1]} {d.year}"


EXPECTED_ORDER = sorted(VISIBLE, key=lambda n: (-taken(n), os.path.basename(n).casefold()))
EXPECTED_MONTHS = []
for _n in EXPECTED_ORDER:
    if label(taken(_n)) not in EXPECTED_MONTHS:
        EXPECTED_MONTHS.append(label(taken(_n)))

# Testkonfiguration: Sammlung = nur der Testordner
os.makedirs(CFG, exist_ok=True)
with open(os.path.join(CFG, "settings.json"), "w", encoding="utf-8") as fh:
    json.dump({"gallery_folders": [{"path": T, "enabled": True}], "gallery_size": 160,
               "details_pane": False}, fh)
try:
    os.unlink(os.path.join(CFG, "katalog-daten.json"))
except OSError:
    pass

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402
from Xlib import X, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import fileops, gallery, places, undo  # noqa: E402
from filefenster import host as hostmod  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.navpane import C_KIND  # noqa: E402
from filefenster.gallery import PAD_X as PAD  # noqa: E402

LAUNCHED = []
hostmod.launch_file = lambda gfile, parent=None: LAUNCHED.append(gfile.get_uri()) or True

D = display.Display()
ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, f"  [{info}]" if info and not cond else "", flush=True)


def kc(name):
    return D.keysym_to_keycode(XK.string_to_keysym(name))


def press(*names):
    codes = [kc(n) for n in names]
    for c in codes:
        xtest.fake_input(D, X.KeyPress, c)
    for c in reversed(codes):
        xtest.fake_input(D, X.KeyRelease, c)
    D.sync()


def move(x, y):
    xtest.fake_input(D, X.MotionNotify, x=int(x), y=int(y))
    D.sync()


def button(b, n=1, ctrl=False):
    if ctrl:
        xtest.fake_input(D, X.KeyPress, kc("Control_L"))
    for _ in range(n):
        xtest.fake_input(D, X.ButtonPress, b)
        xtest.fake_input(D, X.ButtonRelease, b)
    if ctrl:
        xtest.fake_input(D, X.KeyRelease, kc("Control_L"))
    D.sync()


def uri(name, base=T):
    return Gio.File.new_for_path(os.path.join(base, name)).get_uri()


def cleanup():
    # eigene Papierkorb-Einträge (falls Rückgängig nicht geklappt hat)
    for g, info in fileops.trash_items():
        orig = info.get_attribute_byte_string("trash::orig-path") or ""
        if orig.startswith((T + "/", T2 + "/")):
            try:
                g.delete(None)
            except GLib.Error:
                pass
    # eigene Vorschaubilder im freedesktop-Cache
    names = list(FILES) + [VIDEO]
    uris = [uri(n) for n in names] + [uri("zweiter.jpg", T2)]
    root = os.path.join(GLib.get_user_cache_dir(), "thumbnails")
    for u in uris:
        h = hashlib.md5(u.encode()).hexdigest() + ".png"
        for sub in ("normal", "large", "x-large", "xx-large", "fail/gnome-thumbnail-factory"):
            try:
                os.unlink(os.path.join(root, sub, h))
            except OSError:
                pass
    for d in (T, T2):
        shutil.rmtree(d, ignore_errors=True)


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.HOME)
        self.win.move(0, 0)
        self.win.resize(1200, 800)
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
            global fail
            fail += 1
            self.quit()
        return False

    # -- Hilfen ---------------------------------------------------------
    def gv(self):
        return self.win.current.views.get("gallery")

    def wait(self, cond, ms=8000):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            if cond():
                return
            yield 100

    def origin(self):
        _r, ox, oy = self.win.get_window().get_origin()
        return ox, oy

    def widget_xy(self, widget, x, y):
        wx, wy = widget.translate_coordinates(self.win, int(x), int(y))
        ox, oy = self.origin()
        return ox + wx, oy + wy

    def item_xy(self, name, base=T):
        g = self.gv()
        idx = g._index[os.path.join(base, name)]
        g._scroll_to(idx)
        x, y, c = g._item_rect(idx)
        return self.widget_xy(g.canvas, x + c / 2, y - g._scroll_y() + c / 2)

    def names(self):
        return [os.path.relpath(it.path, T) for it in self.gv()._order]

    def labels(self):
        return [gallery.month_label(m) for m in self.gv()._months]

    def shot(self, name):
        gw = self.win.get_window()
        ox, oy = self.origin()
        subprocess.run(["import", "-display", os.environ["DISPLAY"], "-window", "root", "-crop",
                        f"{gw.get_width()}x{gw.get_height()}+{ox}+{oy}",
                        os.path.join(outdir, name + ".png")])

    def menu_item(self, menu, text):
        for mi in menu.get_children():
            box = mi.get_child()
            if isinstance(box, Gtk.Box):
                for w in box.get_children():
                    if isinstance(w, Gtk.Label) and w.get_text() == text:
                        return mi
        return None

    def open_menus(self):
        return [w.get_child() for w in Gtk.Window.list_toplevels()
                if w.get_visible() and isinstance(w.get_child(), Gtk.Menu)]

    # -- Ablauf -----------------------------------------------------------
    def script(self):
        win = self.win
        nav = win.navpane
        tab = win.current
        it0 = nav.store.get_iter_first()
        it1 = nav.store.iter_next(it0)
        check("Navigationsbereich: „Katalog“ direkt unter „Start“",
              nav.store[it0][C_KIND] == "home" and nav.store[it1][C_KIND] == "gallery"
              and nav.store[it1][2] == "Katalog")
        check("Adresseingabe „katalog“ wird zum Katalog", places.resolve_input("katalog") == places.GALLERY
              and places.resolve_input("Galerie") == places.GALLERY)

        # echter Klick auf den Eintrag im Navigationsbereich
        path = nav.store.get_path(it1)
        rect = nav.tree.get_cell_area(path, nav.column)
        x, y = nav.tree.convert_bin_window_to_widget_coords(rect.x + 60, rect.y + rect.height // 2)
        x, y = self.widget_xy(nav.tree, x, y)
        move(x, y)
        yield 250
        button(1)
        yield 600
        check("Klick öffnet den Katalog", tab.location == places.GALLERY and tab.view_kind() == "gallery")
        check("Tab, Titel und Adresse heissen „Katalog“",
              tab.widget.label.get_text() == "Katalog" and win.get_title().startswith("Katalog")
              and win.navrow._crumbs == [("Katalog", places.GALLERY)], (win.get_title(), win.navrow._crumbs))
        check("Befehlsleiste: „Sammlung“ statt „Neu“",
              win.gallery_box.get_visible() and not win.new_box.get_visible())

        g = self.gv()
        yield from self.wait(lambda: not g.is_loading() and g.item_count() == len(VISIBLE))
        check("Alle Bilder/Videos rekursiv, ohne versteckte Ordner und Textdateien",
              sorted(self.names()) == sorted(VISIBLE), self.names())
        check("Monatsüberschriften neueste zuerst", self.labels() == EXPECTED_MONTHS,
              (self.labels(), EXPECTED_MONTHS))
        check("Reihenfolge nach Datum (neueste zuerst)", self.names() == EXPECTED_ORDER,
              (self.names(), EXPECTED_ORDER))
        exif_item = g._items[os.path.join(T, "exif.jpg")]
        check("EXIF-Aufnahmedatum: exif.jpg unter „Mai 2019“",
              gallery.month_label(exif_item.month) == "Mai 2019" and exif_item.taken)
        check("Statusleiste zählt die Elemente",
              win.status_count.get_text() == f"{len(VISIBLE)} Elemente", win.status_count.get_text())

        # Vorschaubilder der sichtbaren Elemente (auch Video)
        yield from self.wait(lambda: set(g.visible_items()) <= g.thumbnails_loaded(), 15000)
        vis = set(g.visible_items())
        check("Vorschaubilder der sichtbaren Elemente geladen",
              vis and vis <= g.thumbnails_loaded(), (len(vis), len(g.thumbnails_loaded()), g._failed))
        if HAVE_VIDEO:
            vpath = os.path.join(T, VIDEO)
            yield from self.wait(lambda: vpath in g.thumbnails_loaded(), 15000)
            check("Video-Vorschau (ffmpegthumbnailer)", vpath in g.thumbnails_loaded())
        th = os.path.join(GLib.get_user_cache_dir(), "thumbnails", "large",
                          hashlib.md5(uri("neu.jpg").encode()).hexdigest() + ".png")
        check("Neues Vorschaubild im freedesktop-Cache", os.path.isfile(th))
        self.shot("katalog")

        # Doppelklick öffnet mit der Standard-App (abgefangen)
        first = EXPECTED_ORDER[0]
        x, y = self.item_xy(first)
        move(x, y)
        yield 250
        LAUNCHED.clear()
        button(1, n=2)
        yield 500
        check("Doppelklick öffnet die Datei", LAUNCHED == [uri(first)], LAUNCHED)
        check("Doppelklick wählt das Element aus", [f.get_path() for f in g.get_selected()]
              == [os.path.join(T, first)])

        # Tastatur: Pfeil rechts + Eingabe
        LAUNCHED.clear()
        press("Right")
        yield 200
        second = EXPECTED_ORDER[1]
        check("Pfeil rechts wählt das nächste Bild",
              [f.get_path() for f in g.get_selected()] == [os.path.join(T, second)])
        press("Return")
        yield 300
        check("Eingabe öffnet das ausgewählte Bild", LAUNCHED == [uri(second)], LAUNCHED)
        press("Shift_L", "Right")
        yield 200
        check("Umschalt+Pfeil erweitert die Auswahl", len(g.get_selected()) == 2)
        press("Control_L", "a")
        yield 200
        check("Strg+A wählt alles", len(g.get_selected()) == len(VISIBLE))
        check("Statusleiste zeigt die Auswahl",
              win.status_sel.get_text().startswith(f"{len(VISIBLE)} Elemente ausgewählt"))

        # Strg+Klick ergänzt die Auswahl
        x, y = self.item_xy(first)
        move(x, y)
        yield 200
        button(1)
        yield 200
        x, y = self.item_xy(EXPECTED_ORDER[2])
        move(x, y)
        yield 200
        button(1, ctrl=True)
        yield 300
        check("Strg+Klick ergänzt die Auswahl", sorted(f.get_path() for f in g.get_selected())
              == sorted([os.path.join(T, first), os.path.join(T, EXPECTED_ORDER[2])]))
        # Auswahlrechteck: von der freien Fläche rechts im ersten Monat nach links ziehen
        row = g._rows[1]
        n_first = row[5] - row[4]
        if n_first < g._cols:
            x0 = PAD + n_first * (g._cell + 4) + g._cell / 2
            y0 = row[1] - g._scroll_y() + g._cell / 2
            g.canvas.get_property("vadjustment").set_value(0)
            yield 200
            sx, sy = self.widget_xy(g.canvas, x0, y0)
            ex, ey = self.widget_xy(g.canvas, PAD + g._cell / 2, y0 + 10)
            move(sx, sy)
            yield 200
            xtest.fake_input(D, X.ButtonPress, 1)
            D.sync()
            for i in range(1, 9):
                move(sx + (ex - sx) * i / 8, sy + (ey - sy) * i / 8)
                yield 40
            xtest.fake_input(D, X.ButtonRelease, 1)
            D.sync()
            yield 300
            check("Auswahlrechteck wählt die überstrichenen Bilder",
                  sorted(f.get_path() for f in g.get_selected())
                  == sorted(os.path.join(T, n) for n in EXPECTED_ORDER[:n_first]))
        # Ziehen startet Drag & Drop (Loslassen über dem Katalog bricht ab)
        began = []
        hid = g.canvas.connect("drag-begin", lambda *_a: began.append(1))
        x, y = self.item_xy(EXPECTED_ORDER[1])
        move(x, y)
        yield 200
        xtest.fake_input(D, X.ButtonPress, 1)
        D.sync()
        for i in range(1, 7):
            move(x + 15 * i, y + 10 * i)
            yield 50
        xtest.fake_input(D, X.ButtonRelease, 1)
        D.sync()
        yield 600
        g.canvas.disconnect(hid)
        check("Ziehen eines Bildes startet Drag & Drop", began and not g._dragging)
        x, y = self.item_xy(first)
        move(x, y)
        yield 200
        button(1)
        yield 200
        x, y = self.item_xy(EXPECTED_ORDER[2])
        move(x, y)
        yield 200
        button(1, ctrl=True)
        yield 300
        check("Kopieren/Löschen/Teilen in der Befehlsleiste aktiv",
              win.btn_copy.get_sensitive() and win.btn_delete.get_sensitive()
              and win.btn_share.get_sensitive() and not win.btn_rename.get_sensitive())

        # Zoom: Strg+Plus (Ziffernblock) und Strg+Mausrad
        size0 = g.size
        press("Control_L", "KP_Add")
        yield 400
        check("Strg+Plus vergrössert die Vorschaubilder", g.size > size0
              and win.config.get("gallery_size") == g.size, (size0, g.size))
        x, y = self.item_xy(first)
        move(x, y)
        yield 200
        button(5, ctrl=True)        # Mausrad nach unten mit Strg
        yield 400
        check("Strg+Mausrad verkleinert wieder", g.size == size0, (size0, g.size))

        # Kontextmenü (Rechtsklick) → „Speicherort öffnen“
        captured = []
        orig_popup = g._popup

        def spy(menu, event):
            captured.append(menu)
            orig_popup(menu, event)
        g._popup = spy
        target = "Urlaub/2024/berg.jpg"
        x, y = self.item_xy(target)
        move(x, y)
        yield 250
        button(3)
        yield 600
        g._popup = orig_popup
        menu = captured[-1].menu if captured else None
        labels = [w.get_text() for mi in (menu.get_children() if menu else [])
                  for w in (mi.get_child().get_children() if isinstance(mi.get_child(), Gtk.Box) else [])
                  if isinstance(w, Gtk.Label)]
        check("Kontextmenü: Öffnen, Öffnen mit, Speicherort öffnen, Eigenschaften",
              {"Öffnen", "Öffnen mit", "Speicherort öffnen", "Eigenschaften"} <= set(labels), labels)
        mi = self.menu_item(menu, "Speicherort öffnen") if menu else None
        if mi is not None and mi.get_window() is not None:
            a = mi.get_allocation()
            _r, mx, my = mi.get_window().get_origin()
            move(mx + a.x + 40, my + a.y + a.height // 2)
            yield 250
            button(1)
        yield 1200
        fv = tab.views.get("folder")
        check("„Speicherort öffnen“ zeigt den Ordner", tab.location == uri("Urlaub/2024"), tab.location)
        yield from self.wait(lambda: fv is not None and not fv.is_loading()
                             and [f.get_uri() for f in fv.get_selected()] == [uri(target)], 4000)
        check("… und wählt die Datei aus", fv is not None and
              [f.get_uri() for f in fv.get_selected()] == [uri(target)])
        press("Alt_L", "Left")       # zurück zum Katalog
        yield 800
        check("Zurück führt wieder in den Katalog", tab.location == places.GALLERY
              and win.gallery_box.get_visible())

        # Löschen mit Entf (Papierkorb) und Strg+Z
        victim = "oktober.jpg"
        undo.reset()
        x, y = self.item_xy(victim)
        move(x, y)
        yield 250
        button(1)
        yield 300
        press("Delete")
        yield from self.wait(lambda: os.path.join(T, victim) not in g._items, 6000)
        check("Entf verschiebt in den Papierkorb und entfernt das Bild",
              not os.path.exists(os.path.join(T, victim)) and os.path.join(T, victim) not in g._items
              and g.item_count() == len(VISIBLE) - 1)
        press("Control_L", "z")
        yield from self.wait(lambda: os.path.join(T, victim) in g._items, 8000)
        check("Strg+Z holt es zurück (Ordnerüberwachung)",
              os.path.exists(os.path.join(T, victim)) and os.path.join(T, victim) in g._items)

        # Sammlung: Knopf öffnet das Menü
        btn = win.btn_collection
        x, y = self.widget_xy(btn, btn.get_allocated_width() // 2, btn.get_allocated_height() // 2)
        move(x, y)
        yield 250
        button(1)
        yield 500
        menus = self.open_menus()
        check("„Sammlung“ öffnet das Menü mit „Ordner hinzufügen…“",
              any(self.menu_item(m, "Ordner hinzufügen…") is not None for m in menus))
        press("Escape")
        yield 300
        # Ordner hinzufügen / ausblenden / entfernen (ohne Dateiauswahl-Dialog)
        g.add_collection_folder(T2)
        yield from self.wait(lambda: not g.is_loading() and g.item_count() == len(VISIBLE) + 1)
        check("Ordner hinzufügen lädt neu", os.path.join(T2, "zweiter.jpg") in g._items)
        win.config.save_now()
        with open(os.path.join(CFG, "settings.json"), encoding="utf-8") as fh:
            saved = json.load(fh)["gallery_folders"]
        check("Sammlung wird gespeichert", [e["path"] for e in saved] == [T, T2], saved)
        m = g.build_collection_menu().menu
        check("Sammlung-Menü listet beide Ordner",
              self.menu_item(m, "filefenster-katalog") is not None
              and self.menu_item(m, "filefenster-katalog-2") is not None)
        g.set_collection_folder_enabled(T2, False)
        yield from self.wait(lambda: not g.is_loading() and g.item_count() == len(VISIBLE))
        win.config.save_now()
        with open(os.path.join(CFG, "settings.json"), encoding="utf-8") as fh:
            saved = json.load(fh)["gallery_folders"]
        check("Häkchen entfernen blendet den Ordner aus",
              os.path.join(T2, "zweiter.jpg") not in g._items and saved[1]["enabled"] is False)
        g.remove_collection_folder(T2)
        yield 300
        win.config.save_now()
        with open(os.path.join(CFG, "settings.json"), encoding="utf-8") as fh:
            saved = json.load(fh)["gallery_folders"]
        check("Ordner entfernen", [e["path"] for e in saved] == [T])
        # Unterordner der Sammlung ausschalten blendet nur ihn aus
        sub = os.path.join(T, "Urlaub")
        g.add_collection_folder(sub)
        g.set_collection_folder_enabled(sub, False)
        n_sub = sum(1 for n in VISIBLE if n.startswith("Urlaub/"))
        yield from self.wait(lambda: not g.is_loading() and g.item_count() == len(VISIBLE) - n_sub)
        check("Ausgeschalteter Unterordner wird ausgeblendet",
              not any(n.startswith("Urlaub/") for n in self.names())
              and g.item_count() == len(VISIBLE) - n_sub, self.names())
        g.remove_collection_folder(sub)
        yield from self.wait(lambda: not g.is_loading() and g.item_count() == len(VISIBLE))
        check("… und erscheint wieder, wenn er nicht mehr ausgeschaltet ist",
              g.item_count() == len(VISIBLE))

        # Adresszeile: „Katalog“ eintippen
        tab.navigate(places.HOME)
        yield 500
        check("Ausserhalb des Katalogs wieder „Neu“", win.new_box.get_visible()
              and not win.gallery_box.get_visible())
        press("Control_L", "l")
        yield 300
        for ch in "katalog":
            press(ch)
        press("Return")
        yield 800
        check("Adresszeile „katalog“ öffnet den Katalog", tab.location == places.GALLERY)
        yield from self.wait(lambda: set(g.visible_items()) <= g.thumbnails_loaded(), 8000)
        self.shot("katalog-ende")


try:
    App().run([sys.argv[0]])
finally:
    cleanup()
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
