#!/usr/bin/env python3
"""Vorschaufenster (Alt+P) testen: Bilder, SVG, Text/Code, Binärdateien, PDF,
Video, Audio, Office-Miniaturansicht, Ordner/Mehrfachauswahl, schnelles
Umschalten der Auswahl, Datei in geöffnetem ZIP, Umschalten mit echten Tasten
(Alt+P / Alt+Umschalt+P, Details-Knopf, Menü „Einblenden“) und Speichern der
Einstellung – nur auf dem Test-Bildschirm:

    Xvfb :101 -screen 0 1600x1000x24 -nolisten tcp &
    DISPLAY=:101 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_vorschau.py OUTDIR

Arbeitet nur in ~/.cache/filefenster-vorschau.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
import wave
import zipfile

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (z. B. DISPLAY=:101)")
if not os.environ.get("FILEFENSTER_CONFIG_DIR"):
    sys.exit("Bitte FILEFENSTER_CONFIG_DIR auf einen eigenen Testordner setzen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")
# frische Einstellungen (nur im Testordner)
shutil.rmtree(os.environ["FILEFENSTER_CONFIG_DIR"], ignore_errors=True)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from Xlib import X, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

from filefenster import config as configmod, places, previewpane  # noqa: E402
from filefenster.app import FileFensterApp  # noqa: E402
from filefenster.folderview import COL_NAME  # noqa: E402

outdir = os.path.abspath(sys.argv[1])
os.makedirs(outdir, exist_ok=True)
T = os.path.expanduser("~/.cache/filefenster-vorschau")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(f"{T}/Ordner")
NOTES = []


def sh(*args):
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


# --- Testdateien ------------------------------------------------------
sh("convert", "-size", "800x400", "xc:#2060c0", f"{T}/bild.png")
sh("convert", "-size", "40x30", "xc:#c08020", f"{T}/klein.png")
with open(f"{T}/vektor.svg", "w") as fh:
    fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="100" viewBox="0 0 50 100">'
             '<rect width="50" height="100" fill="#10a040"/></svg>')
NOTIZ = "Grüezi mitenand\nZweite Zeile mit Umlauten: äöü\n\tEingerückt\n"
with open(f"{T}/notiz.txt", "w", encoding="utf-8") as fh:
    fh.write(NOTIZ)
SKRIPT = "#!/usr/bin/env python3\n\ndef hallo():\n    print('Hallo Welt')\n"
with open(f"{T}/skript.py", "w") as fh:
    fh.write(SKRIPT)
with open(f"{T}/daten.json", "w") as fh:
    json.dump({"name": "Test", "werte": [1, 2, 3]}, fh)
open(f"{T}/leer.txt", "w").close()
with open(f"{T}/gross.log", "w") as fh:
    for i in range(12000):
        fh.write(f"Zeile {i:05d} – Protokolleintrag mit etwas Text\n")
with open(f"{T}/kaputt.txt", "wb") as fh:
    fh.write(b"Text\0\0\0\x01\x02 mit Nullbytes" * 50)
rnd = random.Random(7)
with open(f"{T}/programm.bin", "wb") as fh:
    fh.write(bytes(rnd.randrange(256) for _ in range(4096)))
with open(f"{T}/unbekannt.xyz", "wb") as fh:
    fh.write(b"\x00\x01XYZ" + bytes(rnd.randrange(256) for _ in range(2048)))
sh("convert", "-size", "300x400", "xc:red", f"{T}/dokument.pdf")
HAVE_VIDEO = shutil.which("ffmpeg") and sh(
    "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
    "-t", "2", f"{T}/film.avi")
if not HAVE_VIDEO and shutil.which("gst-launch-1.0"):
    HAVE_VIDEO = sh("gst-launch-1.0", "-q", "videotestsrc", "num-buffers=50", "pattern=smpte", "!",
                    "video/x-raw,width=320,height=240,framerate=25/1", "!", "videoconvert", "!",
                    "jpegenc", "!", "avimux", "!", "filesink", f"location={T}/film.avi")
    if HAVE_VIDEO:
        NOTES.append("ffmpeg fehlt – Testvideo mit GStreamer erzeugt")
if not HAVE_VIDEO:
    NOTES.append("Kein Werkzeug für ein Testvideo (ffmpeg/GStreamer) – Video-Prüfung übersprungen")
HAVE_TAGS = shutil.which("gst-launch-1.0") and sh(
    "gst-launch-1.0", "-q", "audiotestsrc", "num-buffers=200", "!",
    "taginject", "tags=title=Testlied,artist=Testband", "!", "wavenc", "!",
    "filesink", f"location={T}/ton.wav")
if not HAVE_TAGS:
    with wave.open(f"{T}/ton.wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * 3)
    NOTES.append("GStreamer fehlt – WAV ohne Titel/Interpret")
# OpenDocument mit eingebetteter Miniaturansicht (gsf-office-thumbnailer)
sh("convert", "-size", "120x170", "xc:#00a000", f"{T}/.thumb.png")
with zipfile.ZipFile(f"{T}/bericht.odt", "w") as z:
    z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
    z.writestr("META-INF/manifest.xml",
               '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest='
               '"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"><manifest:file-entry '
               'manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
               '</manifest:manifest>')
    z.writestr("content.xml", '<?xml version="1.0"?><office:document-content '
               'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>')
    z.write(f"{T}/.thumb.png", "Thumbnails/thumbnail.png")
os.unlink(f"{T}/.thumb.png")
with zipfile.ZipFile(f"{T}/archiv.zip", "w") as z:
    z.writestr("innen.txt", "Text aus dem Archiv\n")
    z.write(f"{T}/klein.png", "innen.png")
os.mkfifo(f"{T}/rohr")

D = display.Display()
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print("OK  " if cond else "FAIL", name, flush=True)


def press(*names):
    codes = [D.keysym_to_keycode(XK.string_to_keysym(n)) for n in names]
    for c in codes:
        xtest.fake_input(D, X.KeyPress, c)
    for c in reversed(codes):
        xtest.fake_input(D, X.KeyRelease, c)
    D.sync()


def move(x, y):
    xtest.fake_input(D, X.MotionNotify, x=int(x), y=int(y))
    D.sync()


def click(x, y, ctrl=False):
    if ctrl:
        xtest.fake_input(D, X.KeyPress, D.keysym_to_keycode(XK.string_to_keysym("Control_L")))
    xtest.fake_input(D, X.MotionNotify, x=int(x), y=int(y))
    xtest.fake_input(D, X.ButtonPress, 1)
    xtest.fake_input(D, X.ButtonRelease, 1)
    if ctrl:
        xtest.fake_input(D, X.KeyRelease, D.keysym_to_keycode(XK.string_to_keysym("Control_L")))
    D.sync()


def pixel(pb, fx=0.5, fy=0.5):
    x = min(pb.get_width() - 1, int(pb.get_width() * fx))
    y = min(pb.get_height() - 1, int(pb.get_height() * fy))
    data = pb.get_pixels()
    off = y * pb.get_rowstride() + x * pb.get_n_channels()
    return tuple(data[off:off + 3])


def menu_label(item):
    box = item.get_child()
    if not isinstance(box, Gtk.Box):
        return "", False
    text = ""
    checked = False
    for ch in box.get_children():
        if isinstance(ch, Gtk.Label) and ch.get_style_context().has_class("menu-label"):
            text = ch.get_text()
        if isinstance(ch, Gtk.Box) and ch.get_style_context().has_class("mark"):
            checked = bool(ch.get_children())
    return text, checked


class App(FileFensterApp):
    def do_activate(self):
        self._ensure_theme()
        from filefenster.window import ExplorerWindow
        self.win = ExplorerWindow(self, places.uri_for_path(T))
        self.win.move(0, 0)
        self.win.resize(1300, 800)
        self.win.present()
        self.steps = self.script(self.win)
        GLib.timeout_add(1400, self.tick)

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

    def fv(self):
        return self.win.current.views["folder"]

    def pane(self):
        return self.win.previewpane

    def origin(self, widget, x, y):
        wx, wy = widget.translate_coordinates(self.win, x, y)
        _r, ox, oy = self.win.get_window().get_origin()
        return ox + wx, oy + wy

    def xy(self, name):
        f = self.fv()
        path = next(r.path for r in f.tree.get_model() if r[COL_NAME] == name)
        f.tree.scroll_to_cell(path, None, False, 0, 0)
        rect = f.tree.get_background_area(path, f.col_name)
        x, y = f.tree.convert_bin_window_to_widget_coords(rect.x + 40, rect.y + rect.height // 2)
        return self.origin(f.tree, x, y)

    def xy_free(self):
        f = self.fv()
        a = f.tree.get_allocation()
        return self.origin(f.tree, a.width - 80, a.height - 30)

    def select(self, *names):
        for i, n in enumerate(names):
            move(*self.xy(n))          # erst hinbewegen (Tooltip fängt sonst den Klick ab)
            yield 200
            click(*self.xy(n), ctrl=i > 0)
            yield 150

    def settle(self, want=None, limit=150):
        """Warten, bis die Vorschau (für want = URI) fertig geladen ist."""
        yield 150
        for _i in range(limit):
            p = self.pane()
            if not p.loading() and p.kind != "loading" and (want is None or p.shown_uri == want):
                break
            yield 100
        yield 100

    def preview(self, name):
        yield from self.select(name)
        yield from self.settle(self.uri(name))

    def uri(self, name):
        return Gio.File.new_for_path(f"{T}/{name}").get_uri()

    def shot(self, name):
        gw = self.win.get_window()
        _r, x, y = gw.get_origin()
        subprocess.run(["import", "-window", "root", "-crop",
                        f"{gw.get_width()}x{gw.get_height()}+{x}+{y}", os.path.join(outdir, name + ".png")])

    def right_width(self):
        dp = self.win.details_paned
        return dp.get_allocated_width() - dp.get_position()

    # ------------------------------------------------------------------
    def script(self, win):
        p = self.pane()
        check("Start: kein rechter Bereich", win.right_pane() is None and not win.right_box.get_visible())
        # --- Alt+P (echte Tasten) ---------------------------------------
        yield from self.select("bild.png")
        click(*self.xy_free())          # Auswahl aufheben, Fokus in der Liste
        yield 300
        press("Alt_L", "p")
        yield 500
        check("Alt+P blendet das Vorschaufenster ein",
              win.right_pane() == "preview" and p.get_visible() and win.right_box.get_visible())
        check("Detailbereich dabei aus", not win.detailspane.get_visible()
              and not win.btn_details.get_active())
        check("Nichts ausgewählt: Hinweistext wie Windows",
              p.kind == "placeholder" and p.message.get_text() == previewpane.PLACEHOLDER
              and p.stack.get_visible_child_name() == "message")
        check(f"Breite aus Einstellung (360 → {self.right_width()})", abs(self.right_width() - 360) <= 3)

        # --- Bilder ----------------------------------------------------
        yield from self.preview("bild.png")
        pb = p.picture.pixbuf()
        check("PNG: Bild wird gezeigt", p.kind == "image" and p.shown_uri == self.uri("bild.png")
              and pb is not None and pixel(pb) == (0x20, 0x60, 0xc0))
        yield 200
        dr = p.picture.drawn
        check("PNG: eingepasst, Seitenverhältnis bleibt",
              dr is not None and dr[2] <= p.picture.get_allocated_width() - 30
              and abs(dr[2] / dr[3] - 2.0) < 0.02)
        self.shot("vorschau-bild")
        w_before = dr[2] if dr else 0
        dp = win.details_paned
        dp.set_position(dp.get_allocated_width() - 620)
        yield 500
        dr = p.picture.drawn
        check("Trenner breiter ziehen: Bild wird neu eingepasst",
              dr is not None and dr[2] > w_before + 150 and abs(dr[2] / dr[3] - 2.0) < 0.02)
        dp.set_position(dp.get_allocated_width() - 360)
        yield 400
        yield from self.preview("klein.png")
        yield 200
        check("Kleines Bild wird nicht vergrössert", p.kind == "image" and p.picture.drawn
              and p.picture.drawn[2:] == (40, 30))
        yield from self.preview("vektor.svg")
        yield 200
        dr = p.picture.drawn
        check("SVG: als Bild, auf die Fläche vergrössert",
              p.kind == "image" and dr is not None and dr[3] > 300 and abs(dr[2] / dr[3] - 0.5) < 0.02
              and pixel(p.picture.pixbuf())[1] > 0x90)

        # --- Text und Code ---------------------------------------------
        yield from self.preview("notiz.txt")
        check("Text: Inhalt mit Umlauten", p.kind == "text" and p.text() == NOTIZ)
        check("Text: ohne Zeilenumbruch, nur lesen",
              p.textview.get_wrap_mode() == Gtk.WrapMode.NONE and not p.textview.get_editable())
        self.shot("vorschau-text")
        # in den Text klicken: Strg+A/Strg+C kopieren Text, Entf löscht keine Datei
        tv = p.textview
        tx, ty = self.origin(tv, 60, 12)
        move(tx, ty)
        yield 200
        click(tx, ty)
        yield 300
        press("Control_L", "a")
        press("Control_L", "c")
        yield 300
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
        check("Text markieren und kopieren (Strg+A, Strg+C)", tv.has_focus() and clip == NOTIZ)
        press("Delete")
        press("BackSpace")
        yield 600
        check("Entf/Rücktaste im Text: Datei bleibt, Ort bleibt",
              os.path.exists(f"{T}/notiz.txt") and win.current.location == places.uri_for_path(T))
        yield from self.preview("skript.py")
        check("Python-Datei als Text", p.kind == "text" and p.text() == SKRIPT)
        yield from self.preview("daten.json")
        check("JSON als Text", p.kind == "text" and '"werte"' in p.text())
        yield from self.preview("leer.txt")
        check("Leere Textdatei: leere Textvorschau", p.kind == "text" and p.text() == "")
        yield from self.preview("gross.log")
        size = os.path.getsize(f"{T}/gross.log")
        check("Grosse Datei: nur die ersten 256 KB",
              p.kind == "text" and size > previewpane.TEXT_MAX
              and 0 < len(p.text().encode()) <= previewpane.TEXT_MAX
              and p.text().startswith("Zeile 00000"))
        yield from self.preview("kaputt.txt")
        check("Binärinhalt in .txt: keine Textvorschau",
              p.kind == "none" and p.message.get_text() == previewpane.NO_PREVIEW)
        yield from self.preview("programm.bin")
        check("Binärdatei: „Keine Vorschau verfügbar.“",
              p.kind == "none" and p.stack.get_visible_child_name() == "message")
        yield from self.preview("unbekannt.xyz")
        check("Unbekannter Typ: keine Vorschau", p.kind == "none")
        t0 = time.monotonic()
        yield from self.preview("rohr")
        check("FIFO wird nie gelesen (kein Hängen)", p.kind == "none" and time.monotonic() - t0 < 5)

        # --- PDF, Video, Audio, Office ----------------------------------
        yield from self.preview("dokument.pdf")
        pb = p.picture.pixbuf()
        check("PDF: erste Seite über pdftoppm",
              p.kind == "pdf" and pb is not None and pb.get_height() > pb.get_width()
              and pixel(pb)[0] > 200 and pixel(pb)[1] < 60)
        self.shot("vorschau-pdf")
        if HAVE_VIDEO:
            yield from self.preview("film.avi")
            pb = p.picture.pixbuf()
            check("Video: Standbild", p.kind == "video" and pb is not None and pb.get_width() >= 200)
        yield from self.preview("ton.wav")
        for _i in range(50):
            if p.media_rows() and all(v != "…" for _k, v in p.media_rows()):
                break
            yield 100
        rows = dict(p.media_rows())
        check("Audio: Symbol, Name und Länge",
              p.kind == "audio" and p.media_name.get_text() == "ton.wav"
              and rows.get("Länge", "").startswith("00:00:0"))
        if HAVE_TAGS:
            check("Audio: Titel und Interpret",
                  rows.get("Titel") == "Testlied" and rows.get("Mitwirkende Interpreten") == "Testband")
        self.shot("vorschau-audio")
        yield from self.preview("bericht.odt")
        pb = p.picture.pixbuf()
        check("Office: Miniaturansicht des Systems (gsf-office)",
              p.kind == "thumbnail" and pb is not None and pixel(pb) == (0, 0xa0, 0))

        # --- Ordner, Mehrfachauswahl, nichts --------------------------
        yield from self.preview("Ordner")
        check("Ordner: Hinweistext", p.kind == "placeholder"
              and p.message.get_text() == previewpane.PLACEHOLDER)
        yield from self.select("notiz.txt", "skript.py")
        yield from self.settle()
        check("Mehrfachauswahl: Hinweistext, keine Vorschau",
              p.kind == "multi" and p.message.get_text() == previewpane.MULTI_TEXT
              and p.stack.get_visible_child_name() == "message")
        click(*self.xy_free())
        yield from self.settle()
        check("Auswahl aufgehoben: Hinweistext", p.kind == "placeholder")

        # --- schnelle Wechsel: nie veralteter Inhalt -------------------
        orig = previewpane._load_image

        def slow(*a, **kw):
            time.sleep(1.2)
            return orig(*a, **kw)
        previewpane._load_image = slow
        yield from self.select("bild.png")
        yield 250
        check("Während des Ladens: alte Vorschau sofort weg", p.kind == "loading"
              and p.stack.get_visible_child_name() == "blank")
        yield from self.select("notiz.txt")
        yield 2200
        previewpane._load_image = orig
        check("Langsames Bild überholt: Text bleibt stehen",
              p.kind == "text" and p.shown_uri == self.uri("notiz.txt") and p.text() == NOTIZ)
        # mit Pfeiltasten schnell durch die Liste
        yield from self.select("bericht.odt")
        for _i in range(5):
            press("Down")
            time.sleep(0.03)
        yield from self.settle()
        yield 600
        sel = self.fv().get_selected()
        check("Pfeiltasten schnell: Vorschau passt zur Auswahl",
              len(sel) == 1 and p.shown_uri == sel[0].get_uri() and not p.loading())

        # --- Datei geändert, während sie ausgewählt ist ----------------
        yield from self.preview("notiz.txt")
        with open(f"{T}/notiz.txt", "a", encoding="utf-8") as fh:
            fh.write("Neue Zeile\n")
        for _i in range(40):
            yield 100
            if "Neue Zeile" in p.text():
                break
        check("Geänderte Datei wird neu geladen", "Neue Zeile" in p.text())

        # --- Umschalten: Alt+Umschalt+P, Details-Knopf, Menü ----------
        press("Alt_L", "Shift_L", "p")
        yield 500
        check("Alt+Umschalt+P: Detailbereich statt Vorschau",
              win.right_pane() == "details" and win.detailspane.get_visible()
              and not p.get_visible() and win.btn_details.get_active())
        press("Alt_L", "p")
        yield 500
        check("Alt+P: Vorschau statt Detailbereich",
              win.right_pane() == "preview" and p.get_visible() and not win.detailspane.get_visible()
              and not win.btn_details.get_active())
        yield from self.settle()
        check("Vorschau nach dem Umschalten wieder da", p.kind == "text"
              and p.shown_uri == self.uri("notiz.txt"))
        press("Alt_L", "p")
        yield 500
        check("Alt+P nochmals: rechter Bereich aus", win.right_pane() is None
              and not win.right_box.get_visible() and p.kind == "placeholder")
        press("Alt_L", "p")
        yield 500
        b = win.btn_details
        bx, by = self.origin(b, b.get_allocated_width() // 2, b.get_allocated_height() // 2)
        move(bx, by)
        yield 250
        click(bx, by)
        yield 500
        check("Knopf „Details“ schaltet von Vorschau auf Detailbereich",
              win.right_pane() == "details" and win.detailspane.get_visible() and not p.get_visible())
        # Menü Anzeigen › Einblenden › Vorschaufenster
        bv = win.btn_view
        vx, vy = self.origin(bv, bv.get_allocated_width() // 2, bv.get_allocated_height() // 2)
        move(vx, vy)
        yield 250
        click(vx, vy)
        yield 600
        menus = [w.get_child() for w in Gtk.Window.list_toplevels()
                 if w.get_visible() and isinstance(w.get_child(), Gtk.Menu)]
        einbl = None
        for m in menus:
            for it in m.get_children():
                if menu_label(it)[0] == "Einblenden" and it.get_submenu() is not None:
                    einbl = it
        sub = {menu_label(it)[0]: (it, menu_label(it)[1]) for it in einbl.get_submenu().get_children()} \
            if einbl else {}
        check("Menü „Einblenden“ mit Vorschaufenster und Detailbereich",
              "Vorschaufenster" in sub and "Detailbereich" in sub
              and sub["Detailbereich"][1] and not sub["Vorschaufenster"][1])
        if "Vorschaufenster" in sub:
            sub["Vorschaufenster"][0].activate()
        for m in menus:
            m.popdown()
        yield 500
        check("Menüpunkt Vorschaufenster schaltet um",
              win.right_pane() == "preview" and p.get_visible() and not win.detailspane.get_visible())

        # --- Tabs teilen den Bereich -----------------------------------
        press("Control_L", "t")
        yield 800
        check("Neuer Tab: Vorschaufenster bleibt, Hinweistext",
              p.get_visible() and len(win.tabs) == 2 and p.kind == "placeholder")
        press("Control_L", "w")
        yield 600

        # --- Datei in geöffnetem ZIP -----------------------------------
        win.current.navigate(places.archive_uri(Gio.File.new_for_path(f"{T}/archiv.zip")))
        for _i in range(60):
            yield 100
            f = self.fv()
            if not f._loading and any(r[COL_NAME] == "innen.txt" for r in f.tree.get_model()):
                break
        f = self.fv()
        inner = f._gfile.get_child("innen.txt").get_uri()
        f.reveal([inner])
        yield from self.settle(inner)
        check("Datei im ZIP: Textvorschau", p.kind == "text" and p.text() == "Text aus dem Archiv\n")
        inner = f._gfile.get_child("innen.png").get_uri()
        f.reveal([inner])
        yield from self.settle(inner)
        pb = p.picture.pixbuf()
        check(f"Bild im ZIP: Vorschau ({p.kind})", p.kind == "image" and pb is not None
              and pixel(pb) == (0xc0, 0x80, 0x20))
        self.shot("vorschau-zip")
        win.current.navigate(places.uri_for_path(T))
        for _i in range(60):          # Archiv wird ausgehängt, wenn keine Ansicht es zeigt
            yield 100
            if not any(m.get_name() == "archiv.zip" for m in Gio.VolumeMonitor.get().get_mounts()):
                break
        check("ZIP danach wieder ausgehängt",
              not any(m.get_name() == "archiv.zip" for m in Gio.VolumeMonitor.get().get_mounts()))

        # --- Einstellung und Breite gespeichert, alle Fenster ----------
        dp.set_position(dp.get_allocated_width() - 480)
        yield 400
        win._on_delete()            # speichert Breite wie beim Schliessen
        with open(configmod.CONFIG_FILE, encoding="utf-8") as fh:
            saved = json.load(fh)
        check("Gespeichert: Vorschaufenster an, Detailbereich aus, Breite",
              saved.get("preview_pane") is True and saved.get("details_pane") is False
              and abs(saved.get("preview_width", 0) - 480) <= 3)
        from filefenster.window import ExplorerWindow
        win2 = ExplorerWindow(self, places.uri_for_path(T))
        win2.move(0, 0)
        win2.resize(1200, 700)
        win2.present()
        yield 1200
        w2 = win2.details_paned.get_allocated_width() - win2.details_paned.get_position()
        check("Neues Fenster: Vorschaufenster mit gemerkter Breite",
              win2.right_pane() == "preview" and win2.previewpane.get_visible()
              and abs(w2 - 480) <= 3)
        win.toggle_details_pane()
        yield 400
        check("Umschalten wirkt in allen Fenstern",
              win2.right_pane() == "details" and win2.detailspane.get_visible()
              and not win2.previewpane.get_visible())
        win2.destroy()
        win.toggle_preview_pane()
        yield 300


app = App()
app.run([sys.argv[0]])
# falls eine Prüfung schiefging: nur eigene Einträge aus dem Papierkorb entfernen
from filefenster import fileops  # noqa: E402
for g, i in fileops.trash_items():
    if (i.get_attribute_byte_string("trash::orig-path") or "").startswith(T + "/"):
        try:
            g.delete(None)
        except GLib.Error as exc:
            print("Aufräumen:", exc.message)
shutil.rmtree(T, ignore_errors=True)
for n in NOTES:
    print("Hinweis:", n)
print(f"\n{ok}/{ok + fail} Prüfungen bestanden")
