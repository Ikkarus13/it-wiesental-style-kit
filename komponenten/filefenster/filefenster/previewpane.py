"""Vorschaufenster rechts (Alt+P) wie im Windows-11-Explorer: zeigt den Inhalt
der ausgewählten Datei.

    pane = PreviewPane(host)
    pane.show_items(gfiles)     # Auswahl; leer, mehrere oder Ordner → Hinweis
    pane.clear()                # ausgeblendet: laufende Arbeit abbrechen

Was gezeigt wird:
  * Bilder (alle GdkPixbuf-Formate, auch SVG) – eingepasst, Seitenverhältnis
    bleibt, passt sich beim Ziehen des Trenners an
  * Text und Code – die ersten 256 KB, nur lesen, ohne Zeilenumbruch;
    Binärdateien werden nicht als Text gezeigt
  * PDF – erste Seite über pdftoppm
  * Video – Standbild über ffmpegthumbnailer (sonst Miniaturansicht)
  * Audio – Symbol bzw. Cover mit Titel, Interpret und Länge (metadata.py)
  * alles andere – Miniaturansicht des Systems (Cache, Thumbnailer aus
    /usr/share/thumbnailers wie gsf-office), sonst „Keine Vorschau verfügbar.“

Lesen und Rendern laufen in einem Hintergrund-Thread. Eine neue Auswahl bricht
die laufende Arbeit ab (auch pdftoppm & Co.); veraltete Ergebnisse werden
verworfen. Gelesen wird über GIO, deshalb gehen auch Dateien in geöffneten
Archiven und auf Netzlaufwerken; Hilfsprogramme bekommen den FUSE-Pfad.
"""
import atexit
import hashlib
import math
import os
import shutil
import tempfile
import threading
import time

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import icons, metadata, sandbox, theme
from .mica import too_many_pixels

PLACEHOLDER = "Wählen Sie die Datei aus, deren Vorschau angezeigt werden soll."
# Windows zeigt bei mehreren markierten Dateien keine Vorschau, sondern
# denselben Hinweis wie ohne Auswahl.
MULTI_TEXT = PLACEHOLDER
NO_PREVIEW = "Keine Vorschau verfügbar."

TEXT_MAX = 256 * 1024           # so viel Text wird höchstens gelesen
LINE_MAX = 4000                 # längere Zeilen werden gekürzt (GTK bleibt flüssig)
IMAGE_MAX_BYTES = 150 * 1024 * 1024   # grössere Bilder nur als Miniaturansicht
LOAD_BOX = 2048                 # Bilder höchstens so gross laden (wird beim Zeichnen eingepasst)
TOOL_TIMEOUT = 20               # Sekunden für pdftoppm, ffmpegthumbnailer, Thumbnailer
RADIUS = 4                      # leicht abgerundete Bildecken
DEBOUNCE_MS = 50

_ATTRS = ("standard::type,standard::name,standard::display-name,standard::content-type,"
          "standard::fast-content-type,standard::icon,standard::size,time::modified,"
          "thumbnail::path,thumbnail::is-valid")

# Text und Code, die GIO nicht als text/plain kennt
_TEXT_EXT = (
    ".txt", ".log", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".ini", ".conf",
    ".cfg", ".properties", ".toml", ".sh", ".bash", ".zsh", ".fish", ".py", ".pyw", ".js",
    ".mjs", ".ts", ".tsx", ".jsx", ".css", ".scss", ".less", ".html", ".htm", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".cs", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".pl", ".lua",
    ".sql", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".reg", ".srt", ".tex", ".nfo", ".diff",
    ".patch", ".swift", ".r", ".gradle", ".cmake", ".mk", ".desktop", ".service", ".env",
    ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore", ".inf", ".manifest",
    ".csproj", ".sln", ".vcxproj", ".xaml", ".plist", ".rst", ".adoc", ".asc",
)
_TEXT_TYPES = ("application/json", "application/xml", "application/javascript",
               "application/x-shellscript", "application/toml", "application/yaml",
               "application/sql", "application/x-desktop")


# ----------------------------------------------------------------------
# Hilfen ohne GTK (laufen im Hintergrund-Thread)
# ----------------------------------------------------------------------
_PIXBUF_MIMES = None
_VECTOR_MIMES = ("image/svg+xml", "image/svg+xml-compressed", "image/svg")


def _pixbuf_mimes():
    global _PIXBUF_MIMES
    if _PIXBUF_MIMES is None:
        _PIXBUF_MIMES = {m for f in GdkPixbuf.Pixbuf.get_formats() for m in f.get_mime_types()}
    return _PIXBUF_MIMES


_THUMBNAILERS = None
_thumbnailers_lock = threading.Lock()


def _thumbnailers():
    """MIME-Typ → Exec-Zeile aus den .thumbnailer-Dateien (Benutzer vor System)."""
    global _THUMBNAILERS
    with _thumbnailers_lock:
        if _THUMBNAILERS is not None:
            return _THUMBNAILERS
        out = {}
        dirs = [os.path.join(d, "thumbnailers") for d in
                [GLib.get_user_data_dir()] + list(GLib.get_system_data_dirs())]
        for d in dirs:
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                if not n.endswith(".thumbnailer"):
                    continue
                kf = GLib.KeyFile()
                try:
                    kf.load_from_file(os.path.join(d, n), GLib.KeyFileFlags.NONE)
                    exe = kf.get_string("Thumbnailer Entry", "Exec")
                    mimes = kf.get_string_list("Thumbnailer Entry", "MimeType")
                except GLib.Error:
                    continue
                try:
                    tryexec = kf.get_string("Thumbnailer Entry", "TryExec")
                except GLib.Error:
                    tryexec = None
                if tryexec and not shutil.which(tryexec):
                    continue
                for m in mimes:
                    if m and m not in out:
                        out[m] = exe
        _THUMBNAILERS = out
        return out


def kind_of(content_type, name):
    """Art der Vorschau: image, pdf, video, audio, text oder other."""
    ct = content_type or ""
    low = (name or "").lower()
    if ct in _pixbuf_mimes():
        return "image"
    if ct == "application/pdf" or low.endswith(".pdf"):
        return "pdf"
    if ct.startswith("video/") or ct in ("application/x-matroska", "application/mxf",
                                         "application/vnd.rn-realmedia"):
        return "video"
    if ct.startswith("audio/"):
        return "audio"
    if ct.startswith("image/"):
        return "other"            # kein GdkPixbuf-Lader (z. B. XCF) → Miniaturansicht
    if (ct.startswith("text/") or ct in _TEXT_TYPES or low.endswith(_TEXT_EXT)
            or (ct and Gio.content_type_is_a(ct, "text/plain"))):
        return "text"
    return "other"


_tmpdir = None


def _temp_dir():
    global _tmpdir
    if _tmpdir is None:
        base = GLib.get_user_runtime_dir() or None
        try:
            _tmpdir = tempfile.mkdtemp(prefix="filefenster-vorschau-", dir=base)
        except OSError:
            _tmpdir = tempfile.mkdtemp(prefix="filefenster-vorschau-")
        atexit.register(shutil.rmtree, _tmpdir, True)
    return _tmpdir


def _run(argv, cancel, timeout=TOOL_TIMEOUT, inputs=(), outdir=None):
    """Hilfsprogramm abgeschottet ausführen (sandbox.py: bwrap, sonst direkt);
    bricht bei cancel/Zeitüberschreitung ab. → stdout (bytes) oder None bei Fehler."""
    return sandbox.run(argv, cancel, timeout, inputs, outdir)


def _pixbuf_from_bytes(data, box=None):
    if not data:
        return None
    loader = GdkPixbuf.PixbufLoader()
    if box:
        def on_size(ld, w, h):
            if w > box or h > box:
                s = min(box / w, box / h)
                ld.set_size(max(1, int(w * s)), max(1, int(h * s)))
        loader.connect("size-prepared", on_size)
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    return loader.get_pixbuf()


def _pixbuf_from_file(path, box):
    try:
        _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
        if w and h and (w > box or h > box):
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, box, box, True)
        else:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
    except (GLib.Error, TypeError):
        return None
    if pb is None:
        return None
    return pb.apply_embedded_orientation() or pb


def _load_image(gfile, box, vector, cancel):
    """Bild über GIO laden (geht auch in Archiven/Netz), höchstens box×box;
    Vektorbilder (SVG) werden auf box eingepasst, also auch vergrössert."""
    loader = GdkPixbuf.PixbufLoader()
    state = {"size": False, "big": False}

    def on_size(ld, w, h):
        state["size"] = True
        if w <= 0 or h <= 0:
            return
        fmt = ld.get_format()
        if too_many_pixels(fmt.get_name() if fmt else "", w, h):
            state["big"] = True      # Dekompressionsbombe o. Ä.: 0×0 = gar nicht dekodieren
            ld.set_size(0, 0)
            return
        if vector or w > box or h > box:
            s = min(box / w, box / h)
            ld.set_size(max(1, int(w * s)), max(1, int(h * s)))
    loader.connect("size-prepared", on_size)
    try:
        stream = gfile.read(None)
    except GLib.Error:
        return None
    ok = True
    try:
        while True:
            if cancel.is_set() or state["big"]:
                ok = False
                break
            # bis die Grösse bekannt ist, nur kleine Stücke (sonst dekodiert ein
            # Bomben-PNG schon vor der Prüfung Hunderte MB)
            data = stream.read_bytes(256 * 1024 if state["size"] else 4096, None)
            if data.get_size() == 0:
                break
            loader.write_bytes(data)
    except GLib.Error:
        ok = False
    finally:
        try:
            stream.close(None)
        except GLib.Error:
            pass
        try:
            loader.close()
        except GLib.Error:
            ok = ok and loader.get_pixbuf() is not None
    if not ok:
        return None
    pb = loader.get_pixbuf()
    if pb is not None:
        pb = pb.apply_embedded_orientation() or pb
    return pb


def _read_head(gfile, limit, cancel):
    """Die ersten limit Bytes (über GIO). → (bytes, abgeschnitten) oder (None, False)."""
    try:
        stream = gfile.read(None)
    except GLib.Error:
        return None, False
    chunks = []
    total = 0
    truncated = False
    try:
        while total < limit:
            if cancel.is_set():
                return None, False
            data = stream.read_bytes(min(65536, limit - total), None)
            n = data.get_size()
            if n == 0:
                break
            chunks.append(data.get_data())
            total += n
        else:
            # genau am Limit: steht noch etwas dahinter?
            truncated = stream.read_bytes(1, None).get_size() > 0
    except GLib.Error:
        return None, False
    finally:
        try:
            stream.close(None)
        except GLib.Error:
            pass
    return b"".join(chunks), truncated


def looks_binary(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False                    # UTF-16 mit BOM
    sample = data[:8192]
    if not sample:
        return False
    if b"\0" in sample:
        return True
    ctrl = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13, 27))
    return ctrl > len(sample) * 0.05


def decode_text(data, truncated=False):
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        enc = "utf-16-le" if data.startswith(b"\xff\xfe") else "utf-16-be"
        body = data[2:]
        if len(body) % 2:
            body = body[:-1]
        return body.decode(enc, "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(data) - 3:
            # beim Abschneiden mitten in einem Zeichen gelandet
            try:
                return data[:exc.start].decode("utf-8")
            except UnicodeDecodeError:
                pass
        return data.decode("cp1252", "replace")      # alte Windows-Texte


def _tidy_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > LINE_MAX and any(len(line) > LINE_MAX for line in text.split("\n")):
        text = "\n".join(line if len(line) <= LINE_MAX else line[:LINE_MAX] + " …"
                         for line in text.split("\n"))
    return text


def _cached_thumbnails(uris):
    """Vorhandene Miniaturansichten im freedesktop-Cache, grösste zuerst."""
    base = os.path.join(GLib.get_user_cache_dir(), "thumbnails")
    out = []
    for sub, px in (("xx-large", 1024), ("x-large", 512), ("large", 256), ("normal", 128)):
        for uri in uris:
            p = os.path.join(base, sub, hashlib.md5(uri.encode()).hexdigest() + ".png")
            if os.path.exists(p):
                out.append((p, px))
                break
    return out


def _run_thumbnailer(path, ctype, size, cancel):
    exe = _thumbnailers().get(ctype)
    if not exe or not path:
        return None
    try:
        ok, argv = GLib.shell_parse_argv(exe)
    except GLib.Error:
        return None
    if not ok or not argv:
        return None
    path = os.path.realpath(path)
    with sandbox.private_dir(_temp_dir()) as outdir:
        out = os.path.join(outdir, "vorschau.png")
        uri = GLib.filename_to_uri(path, None)
        rep = {"i": path, "u": uri, "o": out, "s": str(size), "%": "%"}
        args = []
        for a in argv:
            res = []
            i = 0
            while i < len(a):
                if a[i] == "%" and i + 1 < len(a) and a[i + 1] in rep:
                    res.append(rep[a[i + 1]])
                    i += 2
                else:
                    res.append(a[i])
                    i += 1
            args.append("".join(res))
        if _run(args, cancel, inputs=(path,), outdir=outdir) is None:
            return None
        return _pixbuf_from_file(out, max(size, 256)) if os.path.exists(out) else None


def factory_thumbnail(fac, uri, path, mime, mtime, size=256):
    """Wie fac.generate_thumbnail(), aber abgeschottet (sandbox.py) – Cinnamons
    Thumbnail-Factory startet die Thumbnailer ohne Sandbox. Speichert im gemeinsamen
    Cache (wie Nemo). Nur lokale Dateien; None, wenn es nichts gibt."""
    if not path or not mime or not _thumbnailers().get(mime):
        return None
    if fac.has_valid_failed_thumbnail(uri, mtime) or not fac.can_thumbnail(uri, mime, mtime):
        return None
    pb = _run_thumbnailer(path, mime, size, threading.Event())
    if pb is None:
        fac.create_failed_thumbnail(uri, mtime)
        return None
    fac.save_thumbnail(pb, uri, mtime)
    return pb


def _system_thumbnail(gfile, info, ct, path, size, cancel):
    """Miniaturansicht wie in den Symbolansichten – möglichst gross."""
    uris = [gfile.get_uri()]
    if path:
        file_uri = GLib.filename_to_uri(path, None)
        if file_uri not in uris:
            uris.append(file_uri)
    cached = _cached_thumbnails(uris)
    tp = None
    if info.has_attribute("thumbnail::path"):
        valid = (not info.has_attribute("thumbnail::is-valid")
                 or info.get_attribute_boolean("thumbnail::is-valid"))
        tp = info.get_attribute_byte_string("thumbnail::path") if valid else None
    for p, px in cached:
        if px >= 512:
            pb = _pixbuf_from_file(p, LOAD_BOX)
            if pb is not None:
                return pb
    if cancel.is_set():
        return None
    pb = _run_thumbnailer(path, ct, size, cancel)
    if pb is not None or cancel.is_set():
        return pb
    for p in ([tp] if tp else []) + [p for p, _px in cached]:
        pb = _pixbuf_from_file(p, LOAD_BOX)
        if pb is not None:
            return pb
    return None


def _pdf_page(path, size, cancel):
    path = os.path.realpath(path)
    data = _run(["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-png",
                 "-scale-to", str(size), path], cancel, inputs=(path,))
    return _pixbuf_from_bytes(data)


def _video_still(path, size, cancel):
    path = os.path.realpath(path)
    with sandbox.private_dir(_temp_dir()) as outdir:
        out = os.path.join(outdir, "standbild.png")
        if _run(["ffmpegthumbnailer", "-i", path, "-o", out, "-s", str(size), "-t", "10%"],
                cancel, inputs=(path,), outdir=outdir) is None or not os.path.exists(out):
            return None
        return _pixbuf_from_file(out, LOAD_BOX)


def _signature(info):
    mt = info.get_attribute_uint64("time::modified") if info.has_attribute("time::modified") else 0
    return (mt, _size(info))


# Das Archiv-Backend (gvfsd-archive) liefert nicht alle Attribute – die
# GFileInfo-Getter melden dann CRITICAL; deshalb immer über has_attribute.
def _size(info):
    return info.get_attribute_uint64("standard::size") if info.has_attribute("standard::size") else 0


def _name(info, gfile):
    if info.has_attribute("standard::name"):
        return info.get_attribute_byte_string("standard::name") or gfile.get_basename() or ""
    return gfile.get_basename() or ""


def _display_name(info, gfile):
    if info.has_attribute("standard::display-name"):
        return info.get_attribute_string("standard::display-name") or ""
    return GLib.filename_display_name(_name(info, gfile))


def content_type(info, gfile):
    ct = ""
    for attr in ("standard::content-type", "standard::fast-content-type"):
        if info.has_attribute(attr):
            ct = info.get_attribute_string(attr) or ""
            if ct:
                break
    if not ct or ct == "application/x-zerosize":
        # fehlt (Archiv) oder leere Datei: nach dem Namen (leere .txt = Text)
        guess, _uncertain = Gio.content_type_guess(_name(info, gfile), None)
        if guess and guess != "application/octet-stream":
            ct = guess
    return ct


def _file_type(info):
    if info.has_attribute("standard::type"):
        return Gio.FileType(info.get_attribute_uint32("standard::type"))
    return Gio.FileType.UNKNOWN


def _format_length(seconds):
    s = int(round(seconds))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def _rgba(spec):
    c = Gdk.RGBA()
    c.parse(spec)
    return c


# ----------------------------------------------------------------------
# Bildfläche: passt das Bild bei jeder Grösse neu ein
# ----------------------------------------------------------------------
class _Picture(Gtk.DrawingArea):
    PAD = 16

    def __init__(self):
        super().__init__()
        self._pb = None
        self._upscale = False
        self._frame = False
        self._cache = None
        self.drawn = None            # (x, y, b, h) der letzten Zeichnung (für Tests)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.connect("draw", self._on_draw)

    def set_pixbuf(self, pb, upscale=False, frame=False):
        self._pb = pb
        self._upscale = upscale
        self._frame = frame
        self._cache = None
        self.drawn = None
        self.queue_draw()

    def pixbuf(self):
        return self._pb

    def fit(self, aw, ah):
        """Grösse (b, h) des eingepassten Bildes für eine Fläche aw×ah."""
        pb = self._pb
        pw, ph = pb.get_width(), pb.get_height()
        bw, bh = max(1, aw - 2 * self.PAD), max(1, ah - 2 * self.PAD)
        s = min(bw / pw, bh / ph)
        if not self._upscale:
            s = min(s, 1.0)
        return max(1, int(round(pw * s))), max(1, int(round(ph * s)))

    def _on_draw(self, _w, cr):
        pb = self._pb
        if pb is None:
            return False
        aw, ah = self.get_allocated_width(), self.get_allocated_height()
        dw, dh = self.fit(aw, ah)
        sf = self.get_scale_factor()
        key = (dw, dh, sf)
        if self._cache is None or self._cache[0] != key:
            tw, th = dw * sf, dh * sf
            spb = pb if (tw, th) == (pb.get_width(), pb.get_height()) else \
                pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
            surf = Gdk.cairo_surface_create_from_pixbuf(spb, sf, self.get_window())
            self._cache = (key, surf)
        surf = self._cache[1]
        x = (aw - dw) // 2
        y = (ah - dh) // 2
        self.drawn = (x, y, dw, dh)
        cr.save()
        _rounded_rect(cr, x, y, dw, dh, RADIUS)
        cr.clip()
        cr.set_source_surface(surf, x, y)
        cr.paint()
        cr.restore()
        if self._frame:            # PDF-Seite: feiner Rand wie ein Blatt
            Gdk.cairo_set_source_rgba(cr, _rgba(theme.P.get("border", "#e5e5e5")))
            _rounded_rect(cr, x + 0.5, y + 0.5, dw - 1, dh - 1, RADIUS)
            cr.set_line_width(1)
            cr.stroke()
        return False


def _rounded_rect(cr, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# ----------------------------------------------------------------------
class PreviewPane(Gtk.Box):
    def __init__(self, host):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.host = host
        self._gen = 0
        self._timer = 0
        self._cancel = None
        self._key = None             # was angefordert ist: None, "multi" oder URI
        self._loading = False
        self._sig = None             # (mtime, Grösse) der gezeigten Datei
        self._rechecking = False
        self._media_path = None
        self._media_meta = None
        # Für Tests und Aufrufer: was gerade zu sehen ist
        self.kind = "placeholder"    # placeholder, multi, loading, image, text, pdf,
        #                              video, audio, thumbnail, none
        self.shown_uri = None

        self.get_style_context().add_class("preview-pane")
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.pack_start(self.stack, True, True, 0)

        self.message = Gtk.Label(label=PLACEHOLDER)
        self.message.get_style_context().add_class("pv-message")
        self.message.set_line_wrap(True)
        self.message.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.message.set_justify(Gtk.Justification.CENTER)
        self.message.set_max_width_chars(26)
        self.message.set_valign(Gtk.Align.CENTER)
        self.message.set_halign(Gtk.Align.CENTER)
        self.message.set_margin_start(20)
        self.message.set_margin_end(20)
        self.stack.add_named(self.message, "message")

        self.stack.add_named(Gtk.Box(), "blank")

        self.picture = _Picture()
        self.stack.add_named(self.picture, "image")

        self.text_scroll = Gtk.ScrolledWindow()
        self.text_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.textview = Gtk.TextView()
        self.textview.get_style_context().add_class("pv-text")
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.NONE)
        self.textview.set_monospace(True)
        for side in ("left", "right", "top", "bottom"):
            getattr(self.textview, f"set_{side}_margin")(12)
        self.text_scroll.add(self.textview)
        self.stack.add_named(self.text_scroll, "text")

        self.stack.add_named(self._build_media(), "media")

        self.stack.show_all()
        self.stack.set_visible_child_name("message")
        metadata.connect(self._on_metadata)
        self.connect("destroy", lambda *_: self._cancel_work())

    def _build_media(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.FILL)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self.media_image = Gtk.Image()
        self.media_image.set_halign(Gtk.Align.CENTER)
        self.media_image.set_margin_bottom(16)
        box.pack_start(self.media_image, False, False, 0)
        self.media_name = Gtk.Label(xalign=0.5)
        self.media_name.get_style_context().add_class("pv-name")
        self.media_name.set_line_wrap(True)
        self.media_name.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.media_name.set_justify(Gtk.Justification.CENTER)
        self.media_name.set_max_width_chars(28)
        box.pack_start(self.media_name, False, False, 0)
        self.media_grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        self.media_grid.set_halign(Gtk.Align.CENTER)
        self.media_grid.set_margin_top(14)
        box.pack_start(self.media_grid, False, False, 0)
        return box

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------
    def show_items(self, gfiles):
        gfiles = [g for g in (gfiles or []) if g is not None]
        if not gfiles:
            key = None
        elif len(gfiles) > 1:
            key = "multi"
        else:
            key = gfiles[0].get_uri()
        if key == self._key and key is not None and key != "multi":
            # gleiche Datei (update_state kommt oft): nur bei Änderung neu laden
            if not self._loading:
                self._recheck(gfiles[0])
            return
        self._cancel_work()
        self._gen += 1
        self._key = key
        self._sig = None
        if key is None:
            self._show_message(PLACEHOLDER, "placeholder")
            return
        if key == "multi":
            self._show_message(MULTI_TEXT, "multi")
            return
        # alte Vorschau sofort weg – nie den Inhalt einer anderen Datei zeigen
        self._loading = True
        self.kind = "loading"
        self.shown_uri = None
        self.stack.set_visible_child_name("blank")
        self._timer = GLib.timeout_add(DEBOUNCE_MS, self._start, self._gen, gfiles[0])

    def clear(self):
        """Ausgeblendet: Arbeit abbrechen, Speicher freigeben."""
        self._cancel_work()
        self._gen += 1
        self._key = None
        self._sig = None
        self.picture.set_pixbuf(None)
        self.textview.get_buffer().set_text("")
        self._show_message(PLACEHOLDER, "placeholder")

    def text_has_focus(self):
        return self.textview.has_focus()

    def loading(self):
        return self._loading

    # ------------------------------------------------------------------
    def _cancel_work(self):
        if self._cancel is not None:
            self._cancel.set()
            self._cancel = None
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._loading = False

    def _show_message(self, text, kind):
        self.message.set_text(text)
        self.stack.set_visible_child_name("message")
        self.kind = kind
        self.shown_uri = None
        self._media_path = None

    def _target(self):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        if w <= 1 or h <= 1:
            w, h = 360, 600
        return w, h, self.get_scale_factor()

    def _start(self, gen, gfile):
        self._timer = 0
        if gen != self._gen:
            return False
        cancel = threading.Event()
        self._cancel = cancel
        threading.Thread(target=self._worker, daemon=True,
                         args=(gen, cancel, gfile, self._target())).start()
        return False

    def _recheck(self, gfile):
        if self._rechecking or self._sig is None:
            return
        self._rechecking = True
        gen = self._gen

        def work():
            try:
                info = gfile.query_info("time::modified,standard::size",
                                        Gio.FileQueryInfoFlags.NONE, None)
                sig = _signature(info)
            except GLib.Error:
                sig = None
            GLib.idle_add(done, sig)

        def done(sig):
            self._rechecking = False
            if gen == self._gen and sig != self._sig and self._key == gfile.get_uri():
                self._key = None          # erzwingt neues Laden
                self.show_items([gfile])
            return False
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    # Hintergrund
    # ------------------------------------------------------------------
    def _worker(self, gen, cancel, gfile, target):
        try:
            res = self._produce(gfile, target, cancel)
        except Exception as exc:          # nie den Thread mit halbem Zustand verlassen
            print("[filefenster] Vorschau:", exc)
            res = {"page": "message", "text": NO_PREVIEW, "kind": "none"}
        if not cancel.is_set():
            GLib.idle_add(self._apply, gen, gfile, res)

    def _produce(self, gfile, target, cancel):
        w, h, sf = target
        try:
            info = gfile.query_info(_ATTRS, Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return {"page": "message", "text": NO_PREVIEW, "kind": "none"}
        sig = _signature(info)
        ftype = _file_type(info)
        if ftype in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE):
            return {"page": "message", "text": PLACEHOLDER, "kind": "placeholder", "sig": sig}
        if ftype != Gio.FileType.REGULAR:     # FIFO, Gerät …: nie lesen (blockiert)
            return {"page": "message", "text": NO_PREVIEW, "kind": "none", "sig": sig}
        ct = content_type(info, gfile)
        kind = kind_of(ct, _name(info, gfile))
        path = gfile.get_path()               # bei Archiven/Netz der FUSE-Pfad
        size = _size(info)
        big = max(w, h) * sf
        res = None

        if kind == "image":
            pb = None
            vector = ct in _VECTOR_MIMES
            # Vektorbilder nur etwas grösser als die Fläche rendern (kleine Icons
            # sonst als riesiges Pixbuf); Pixelbilder höchstens LOAD_BOX
            box = int(min(LOAD_BOX, max(512, big * 1.5))) if vector else LOAD_BOX
            if size <= IMAGE_MAX_BYTES:
                pb = _load_image(gfile, box, vector, cancel)
            if pb is not None:
                res = {"page": "image", "pixbuf": pb, "kind": "image", "upscale": vector}
        elif kind == "text":
            data, truncated = _read_head(gfile, TEXT_MAX, cancel)
            if cancel.is_set():
                return None
            if data is not None and not looks_binary(data):
                return {"page": "text", "text": _tidy_text(decode_text(data, truncated)),
                        "kind": "text", "sig": sig}
            return {"page": "message", "text": NO_PREVIEW, "kind": "none", "sig": sig}
        elif kind == "pdf" and path:
            pb = _pdf_page(path, int(min(2400, max(800, big * 1.25))), cancel)
            if pb is not None:
                res = {"page": "image", "pixbuf": pb, "kind": "pdf", "frame": True}
        elif kind == "video" and path:
            pb = _video_still(path, int(min(1280, max(320, w * sf * 1.25))), cancel)
            if pb is not None:
                res = {"page": "image", "pixbuf": pb, "kind": "video"}
        if cancel.is_set():
            return None
        if res is not None:
            res["sig"] = sig
            return res

        # Miniaturansicht des Systems (Office, Videos ohne Standbild, Cover …)
        thumb = _system_thumbnail(gfile, info, ct, path, int(min(1024, max(256, big))), cancel)
        if cancel.is_set():
            return None
        media = {"page": "media", "kind": kind, "cover": thumb, "path": path, "sig": sig,
                 "ctype": ct, "name": _display_name(info, gfile),
                 "icon": (info.get_attribute_object("standard::icon")
                          if info.has_attribute("standard::icon") else None)
                 or Gio.content_type_get_icon(ct)}
        if kind == "audio":
            return media
        if thumb is not None:
            return {"page": "image", "pixbuf": thumb, "sig": sig,
                    "kind": "video" if kind == "video" else "thumbnail"}
        if kind == "video":
            return media
        return {"page": "message", "text": NO_PREVIEW, "kind": "none", "sig": sig}

    # ------------------------------------------------------------------
    # UI füllen (Hauptthread)
    # ------------------------------------------------------------------
    def _apply(self, gen, gfile, res):
        if gen != self._gen or res is None:
            return False
        self._cancel = None
        self._loading = False
        self._sig = res.get("sig")
        page = res["page"]
        self._media_path = None
        if page == "message":
            self._show_message(res["text"], res["kind"])
            self.shown_uri = gfile.get_uri()
            return False
        if page == "image":
            self.picture.set_pixbuf(res["pixbuf"], upscale=res.get("upscale", False),
                                    frame=res.get("frame", False))
        elif page == "text":
            buf = self.textview.get_buffer()
            buf.set_text(res["text"])
            buf.place_cursor(buf.get_start_iter())
            self.text_scroll.get_hadjustment().set_value(0)
            self.text_scroll.get_vadjustment().set_value(0)
        elif page == "media":
            self._fill_media(res)
        self.stack.set_visible_child_name(page)
        if page != "image":
            self.picture.set_pixbuf(None)
        if page != "text":
            self.textview.get_buffer().set_text("")
        self.kind = res["kind"]
        self.shown_uri = gfile.get_uri()
        return False

    def _fill_media(self, res):
        cover = res.get("cover")
        if cover is not None:
            s = min(1.0, 220 / max(cover.get_width(), cover.get_height()))
            if s < 1.0:
                cover = cover.scale_simple(max(1, int(cover.get_width() * s)),
                                           max(1, int(cover.get_height() * s)),
                                           GdkPixbuf.InterpType.BILINEAR)
            self.media_image.set_from_pixbuf(cover)
        else:
            self.media_image.set_from_pixbuf(icons.gicon_pixbuf(res.get("icon"), 128))
        self.media_name.set_text(res.get("name") or "")
        self._media_path = res.get("path")
        self._media_meta = (res.get("path"), res["sig"][0], res.get("ctype"))
        meta = metadata.get(*self._media_meta) if self._media_path else {}
        self._fill_media_rows(meta)

    def _fill_media_rows(self, meta):
        for child in self.media_grid.get_children():
            child.destroy()
        rows = []
        if meta:
            if meta.get("title"):
                rows.append(("Titel", meta["title"]))
            if meta.get("authors"):
                rows.append(("Mitwirkende Interpreten", meta["authors"]))
            if meta.get("length"):
                rows.append(("Länge", _format_length(meta["length"])))
        elif meta is None:
            rows.append(("Länge", "…"))
        for i, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=1)
            kl.get_style_context().add_class("pv-key")
            vl = Gtk.Label(label=v, xalign=0)
            vl.get_style_context().add_class("pv-value")
            vl.set_line_wrap(True)
            vl.set_max_width_chars(24)
            vl.set_selectable(False)
            self.media_grid.attach(kl, 0, i, 1, 1)
            self.media_grid.attach(vl, 1, i, 1, 1)
        self.media_grid.show_all()

    def media_rows(self):
        """[(Bezeichnung, Wert)] der Audio-/Videoangaben (für Tests)."""
        out = {}
        for child in self.media_grid.get_children():
            top = self.media_grid.child_get_property(child, "top-attach")
            left = self.media_grid.child_get_property(child, "left-attach")
            out.setdefault(top, ["", ""])[left] = child.get_text()
        return [tuple(out[k]) for k in sorted(out)]

    def _on_metadata(self, path):
        if self._media_path and path == self._media_path and self.kind in ("audio", "video"):
            meta = metadata.get(*self._media_meta)
            if meta is not None:
                self._fill_media_rows(meta)

    def text(self):
        buf = self.textview.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
