"""Katalog wie im Windows-11-Explorer (23H2; englisch „Gallery“).

Zeigt alle Bilder und Videos der Sammlungsordner (Standard: der XDG-Ordner
„Bilder“, mit Unterordnern), neueste zuerst – nach Aufnahmedatum (EXIF),
sonst nach Änderungsdatum – unter Monatsüberschriften („November 2026“) als
gleichmässiges Raster quadratischer Vorschaubilder ohne Namen (Name als
Tooltip). Strg+Mausrad bzw. Strg+Plus/Minus ändern die Grösse.

Technik:
    _Scan        Hintergrund-Thread: durchsucht die Sammlung (os.scandir) und
                 liest das Aufnahmedatum aus dem Kopf von JPEG/TIFF-Dateien
                 (nie bei OneDrive-Dateien, die nur online sind; Ergebnis wird
                 zwischengespeichert). Liefert Zwischenstände an GTK.
    _ThumbPool   zwei Arbeits-Threads für Vorschaubilder – nur für sichtbare
                 Elemente: freedesktop-Cache (~/.cache/thumbnails), sonst
                 GdkPixbuf bzw. ffmpegthumbnailer (Videos); Neues landet im Cache.
    _Canvas      DrawingArea mit Gtk.Scrollable: zeichnet nur den sichtbaren
                 Ausschnitt, keine Widgets je Bild (auch bei 50 000 Fotos).
    GalleryView  die Ansicht (host-Schnittstelle wie FolderView: Auswahl,
                 Kopieren, Löschen, Zoom, Kontextmenü, Sammlung).

Ordnerüberwachung (Gio.FileMonitor je Ordner) aktualisiert die Ansicht, wenn
Dateien dazukommen oder verschwinden (auch Rückgängig nach dem Löschen).

Einstellungen: gallery_folders ([{"path", "enabled"}] oder None = „Bilder“),
gallery_size (Kantenlänge der Vorschaubilder in Pixeln).
"""
import bisect
import collections
import hashlib
import json
import math
import os
import shutil
import stat
import struct
import subprocess
import threading
import time

import cairo
import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango, PangoCairo  # noqa: E402

from . import cloudstatus, fileops, icons, places, sandbox, theme  # noqa: E402
from . import host as host_mod  # noqa: E402
from .menu import WinMenu  # noqa: E402

# Kamera-Rohdaten: Vorschau nur über die System-Thumbnailer (xapp-raw-thumbnailer …)
RAW_EXT = frozenset((".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"))
IMAGE_EXT = frozenset((
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".jxl")) | RAW_EXT
VIDEO_EXT = frozenset((
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg", ".3gp",
    ".mts", ".m2ts", ".flv", ".ogv"))
EXIF_EXT = frozenset((".jpg", ".jpeg", ".jpe", ".jfif", ".tif", ".tiff",
                      ".cr2", ".nef", ".arw", ".dng", ".pef", ".srw"))    # TIFF-basiert

SIZES = [96, 128, 160, 200, 256, 320]      # Zoomstufen (Kantenlänge)
DEFAULT_SIZE = 160
SIZE_PRESETS = [(320, "Extra grosse Symbole"), (256, "Grosse Symbole"),
                (160, "Mittelgrosse Symbole"), (96, "Kleine Symbole")]
GAP = 4             # Abstand zwischen den Bildern
PAD_X = 20          # Rand links/rechts
PAD_TOP = 2
HEAD_H = 46         # Monatsüberschrift
GROUP_GAP = 10
RADIUS = 4
CHECK = 20          # Auswahlkästchen oben rechts
MAX_MONITORS = 1500
SURFACE_BUDGET = 160 * 1024 * 1024      # Vorschaubilder im Speicher
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def _rgba(color, alpha=None):
    c = Gdk.RGBA()
    c.parse(color)
    if alpha is not None:
        c.alpha = alpha
    return c


def _round_rect(cr, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


# ---------------------------------------------------------------------------
# Sammlung (Einstellung gallery_folders)
# ---------------------------------------------------------------------------
def default_folders():
    """Windows: Standardmässig der Ordner „Bilder“."""
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    if not path or os.path.normpath(path) == os.path.normpath(places.home_dir()):
        return []
    return [{"path": os.path.normpath(path), "enabled": True}]


def collection(config):
    """Liste von {"path", "enabled"} – None in der Einstellung = Standard."""
    lst = config.get("gallery_folders")
    if not isinstance(lst, list):
        return default_folders()
    out = []
    for e in lst:
        if isinstance(e, str):
            e = {"path": e, "enabled": True}
        if isinstance(e, dict) and e.get("path"):
            out.append({"path": os.path.normpath(os.path.expanduser(e["path"])),
                        "enabled": bool(e.get("enabled", True))})
    return out


def active_roots(config):
    return [e["path"] for e in collection(config) if e["enabled"] and os.path.isdir(e["path"])]


def hidden_roots(config):
    """Ausgeschaltete Sammlungsordner – auch innerhalb eines anderen ausgeblendet."""
    return [e["path"] for e in collection(config) if not e["enabled"]]


def _shown(path, entries):
    """Entscheidet der nächstgelegene Sammlungsordner: eingeschaltet → anzeigen."""
    best = None
    for e in entries:
        f = e["path"]
        if path.startswith(f.rstrip(os.sep) + os.sep) and (best is None or len(f) > len(best["path"])):
            best = e
    return best is not None and best["enabled"]


def add_folder(config, path):
    path = os.path.normpath(path)
    lst = collection(config)
    for e in lst:
        if e["path"] == path:
            e["enabled"] = True
            break
    else:
        lst.append({"path": path, "enabled": True})
    config.set("gallery_folders", lst)


def remove_folder(config, path):
    path = os.path.normpath(path)
    config.set("gallery_folders", [e for e in collection(config) if e["path"] != path])


def set_folder_enabled(config, path, enabled):
    path = os.path.normpath(path)
    lst = collection(config)
    for e in lst:
        if e["path"] == path:
            e["enabled"] = bool(enabled)
    config.set("gallery_folders", lst)


def folder_label(path):
    try:
        return places.display_name(places.uri_for_path(path))
    except Exception:
        return os.path.basename(path) or path


def month_label(month):
    year, mon = month
    return f"{MONTHS[mon - 1]} {year}"


# ---------------------------------------------------------------------------
# Aufnahmedatum (EXIF DateTimeOriginal) – nur der Dateikopf wird gelesen
# ---------------------------------------------------------------------------
def _exif_time(raw):
    """b"2019:05:04 12:30:00" → Unix-Zeit (lokal) oder 0."""
    try:
        s = raw.decode("ascii", "replace")
        y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        h, mi, se = int(s[11:13]), int(s[14:16]), int(s[17:19])
        if not (1900 < y < 2200 and 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and se < 62):
            return 0
        return int(time.mktime((y, mo, d, h, mi, min(se, 59), 0, 0, -1)))
    except (ValueError, TypeError, OverflowError, AttributeError):
        return 0


def _tiff_date(buf):
    """Aufnahmedatum aus einem TIFF-Block (EXIF-Inhalt) oder 0."""
    if len(buf) < 8:
        return 0
    e = {b"II": "<", b"MM": ">"}.get(bytes(buf[:2]))
    if e is None:
        return 0

    def u16(o):
        return struct.unpack_from(e + "H", buf, o)[0]

    def u32(o):
        return struct.unpack_from(e + "I", buf, o)[0]

    def entries(off):
        n = u16(off)
        for i in range(min(n, 400)):
            p = off + 2 + 12 * i
            yield u16(p), u16(p + 2), u32(p + 4), p + 8

    def text(typ, cnt, vp):
        if typ != 2 or cnt < 19:
            return None
        off = u32(vp) if cnt > 4 else vp
        return bytes(buf[off:off + 19])

    try:
        exif_ifd = None
        for tag, _typ, _cnt, vp in entries(u32(4)):
            if tag == 0x8769:
                exif_ifd = u32(vp)
        if not exif_ifd:
            return 0
        found = {}
        for tag, typ, cnt, vp in entries(exif_ifd):
            if tag in (0x9003, 0x9004):          # DateTimeOriginal, DateTimeDigitized
                found[tag] = text(typ, cnt, vp)
        for tag in (0x9003, 0x9004):
            ts = _exif_time(found.get(tag))
            if ts:
                return ts
    except (struct.error, IndexError):
        pass
    return 0


def exif_date(path):
    """Aufnahmedatum (Unix-Zeit) einer JPEG-/TIFF-Datei oder 0."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
            if head in (b"II*\x00", b"MM\x00*"):
                return _tiff_date(head + fh.read(196608))
            if head[:2] != b"\xff\xd8":
                return 0
            fh.seek(2)
            for _ in range(48):
                m = fh.read(4)
                if len(m) < 4 or m[0] != 0xFF:
                    return 0
                marker, ln = m[1], (m[2] << 8) | m[3]
                if marker in (0xDA, 0xD9) or ln < 2:
                    return 0              # Bilddaten beginnen – kein EXIF
                if marker == 0xE1:
                    data = fh.read(ln - 2)
                    if data[:6] == b"Exif\x00\x00":
                        return _tiff_date(memoryview(data)[6:])
                else:
                    fh.seek(ln - 2, 1)
                if fh.tell() > 262144:
                    return 0
    except (OSError, ValueError):
        return 0
    return 0


_cloud_lock = threading.Lock()


def _online_only(path):
    """OneDrive-Datei, die nur online ist? (Lesen würde sie herunterladen)"""
    if not cloudstatus.available():
        return False
    with _cloud_lock:
        try:
            return cloudstatus.state(path, False) == "cloud"
        except Exception:
            return False


# Zwischenspeicher der Aufnahmedaten: Pfad -> [mtime, Grösse, Datum]
# (Datum 0 = keins, -1 = nur online, nicht gelesen – erst F5 prüft erneut)
_dates = None
_dates_lock = threading.Lock()


def _dates_file():
    base = os.environ.get("FILEFENSTER_CONFIG_DIR") or os.path.join(
        GLib.get_user_cache_dir(), "filefenster")
    return os.path.join(base, "katalog-daten.json")


def _load_dates():
    global _dates
    with _dates_lock:
        if _dates is None:
            try:
                with open(_dates_file(), encoding="utf-8") as fh:
                    data = json.load(fh)
                _dates = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                _dates = {}
        return _dates


def _save_dates(keep):
    """Nur Einträge der gerade gefundenen Dateien behalten."""
    snapshot = dict(_dates or {})          # Kopie in einem Schritt (GIL)
    data = {p: v for p, v in snapshot.items() if p in keep}
    path = _dates_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}-{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            # ensure_ascii: Dateinamen mit ungültigem UTF-8 (Surrogate) gingen sonst
            # nicht zu schreiben (UnicodeEncodeError, Cache nie gespeichert)
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Durchsuchen (Thread)
# ---------------------------------------------------------------------------
class _Scan(threading.Thread):
    """Sammlung durchsuchen; Meldungen per GLib.idle_add an deliver(gen, art, daten):
    ("batch", [(pfad, name, mtime, grösse, aufnahme, video, cloud), …]) und am
    Ende ("done", (alle Pfade, Ordnerliste))."""

    def __init__(self, gen, roots, deliver, recheck=False, skip=()):
        super().__init__(daemon=True)
        self.gen = gen
        self.roots = roots          # [(Pfad, in OneDrive)]
        self.skip = set()           # ausgeschaltete Sammlungsordner (echte Pfade)
        for p in skip:
            try:
                self.skip.add(os.path.realpath(p))
            except OSError:
                pass
        self.deliver = deliver
        self.recheck = recheck      # auch „nur online“ erneut prüfen (F5)
        self.cancelled = threading.Event()

    def _send(self, kind, payload):
        if not self.cancelled.is_set():
            GLib.idle_add(self.deliver, self.gen, kind, payload)

    def run(self):
        dates = _load_dates()
        changed = False
        seen, dirs, real_seen, batch = set(), [], set(), []
        last = time.monotonic()
        for root, cloud in self.roots:
            stack = [root]
            while stack:
                if self.cancelled.is_set():
                    return
                d = stack.pop()
                try:
                    real = os.path.realpath(d)
                except OSError:
                    continue
                if real in real_seen or real in self.skip:   # doppelt / ausgeschaltet
                    continue
                real_seen.add(real)
                subs = []
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            name = e.name
                            if name.startswith("."):
                                continue
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    subs.append(e.path)
                                    continue
                            except OSError:
                                continue
                            ext = os.path.splitext(name)[1].lower()
                            video = ext in VIDEO_EXT
                            if not video and ext not in IMAGE_EXT:
                                continue
                            try:
                                st = e.stat()
                            except OSError:
                                continue
                            if not stat.S_ISREG(st.st_mode):
                                continue
                            path, mt, size = e.path, st.st_mtime, st.st_size
                            taken = 0
                            if ext in EXIF_EXT:
                                old = dates.get(path)
                                if old and old[0] == mt and old[1] == size \
                                        and not (old[2] == -1 and self.recheck):
                                    taken = max(0, old[2])
                                else:
                                    online = cloud and _online_only(path)
                                    taken = 0 if online else exif_date(path)
                                    dates[path] = [mt, size, -1 if online else taken]
                                    changed = True
                            seen.add(path)
                            batch.append((path, name, mt, size, taken, video, cloud))
                except OSError:
                    continue
                dirs.append(d)
                subs.sort(key=str.casefold, reverse=True)
                stack.extend(subs)
                now = time.monotonic()
                if batch and (len(batch) >= 600 or now - last > 0.25):
                    self._send("batch", batch)
                    batch, last = [], now
        if self.cancelled.is_set():
            return
        self._send("batch", batch)
        self._send("done", (seen, dirs))
        if changed or len(dates) > len(seen) + 1000:
            _save_dates(seen)


# ---------------------------------------------------------------------------
# Vorschaubilder (Threads)
# ---------------------------------------------------------------------------
THUMB_ROOT = os.path.join(GLib.get_user_cache_dir(), "thumbnails")
LEVELS = {"normal": 128, "large": 256, "x-large": 512, "xx-large": 1024}
_RANK = {"normal": 0, "large": 1, "x-large": 2, "xx-large": 3}


def thumb_name(uri):
    return hashlib.md5(uri.encode()).hexdigest() + ".png"


def _cached_thumb(uri, mtime, order):
    """Gültiges Vorschaubild aus ~/.cache/thumbnails (Thumb::MTime passt)."""
    name = thumb_name(uri)
    for lvl in order:
        p = os.path.join(THUMB_ROOT, lvl, name)
        if not os.path.isfile(p):
            continue
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(p)
        except GLib.Error:
            continue
        mt = pb.get_option("tEXt::Thumb::MTime")
        try:
            if mt is not None and int(float(mt)) != int(mtime):
                continue
        except ValueError:
            continue
        return pb, lvl
    return None, None


def _failed_before(uri, mtime):
    p = os.path.join(THUMB_ROOT, "fail", "gnome-thumbnail-factory", thumb_name(uri))
    if not os.path.isfile(p):
        return False
    try:
        mt = GdkPixbuf.Pixbuf.new_from_file(p).get_option("tEXt::Thumb::MTime")
        return mt is not None and int(float(mt)) == int(mtime)
    except (GLib.Error, ValueError):
        return False


def _save_thumb(pb, uri, mtime, level):
    d = os.path.join(THUMB_ROOT, level)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        final = os.path.join(d, thumb_name(uri))
        tmp = f"{final}.filefenster-{os.getpid()}-{threading.get_ident()}"
        pb.savev(tmp, "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime", "tEXt::Software"],
                 [uri, str(int(mtime)), "FileFenster"])
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except (OSError, GLib.Error):
        pass


def _image_thumb(path, edge):
    fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
    if fmt is None:
        return None
    from .mica import MAX_SVG_BYTES, too_many_pixels
    if too_many_pixels(fmt.get_name(), w, h) or (
            fmt.get_name() == "svg" and os.path.getsize(path) > MAX_SVG_BYTES):
        return None          # Dekompressionsbombe o. Ä. – nie im eigenen Prozess dekodieren
    if w and h and (w > edge or h > edge):
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, edge, edge, True)
    else:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
    if pb is None:
        return None
    return pb.apply_embedded_orientation() or pb


def _video_thumb(path, edge):
    """Standbild über ffmpegthumbnailer – abgeschottet (sandbox.py)."""
    if not shutil.which("ffmpegthumbnailer"):
        return None
    path = os.path.realpath(path)
    try:
        with sandbox.private_dir(GLib.get_user_runtime_dir() or None) as outdir:
            out = os.path.join(outdir, "standbild.png")
            if sandbox.run(["ffmpegthumbnailer", "-i", path, "-o", out, "-s", str(edge),
                            "-c", "png", "-t", "10%"], timeout=30, inputs=(path,),
                           outdir=outdir) is None or not os.path.getsize(out):
                return None
            return GdkPixbuf.Pixbuf.new_from_file(out)
    except (OSError, GLib.Error):
        return None


_factory_lock = threading.Lock()


def _system_thumb(uri, name, mtime):
    """Thumbnailer des Systems (wie Nemo; speichert selbst im Cache) oder None."""
    try:
        from .folderview import _thumb_factory
        fac = _thumb_factory()
    except Exception:
        fac = None
    if fac is None:
        return None
    mime = Gio.content_type_guess(name, None)[0] or ""
    mt = int(mtime)
    try:
        path = GLib.filename_from_uri(uri)[0] if uri.startswith("file:") else None
    except GLib.Error:
        path = None
    with _factory_lock:
        try:
            from .previewpane import factory_thumbnail      # abgeschottet
            return factory_thumbnail(fac, uri, path, mime, mt)
        except Exception:
            return None


def _square(pb, px):
    """Quadratischer Ausschnitt (Hochformat etwas oberhalb der Mitte) in px×px."""
    w, h = pb.get_width(), pb.get_height()
    s = min(w, h)
    x = (w - s) // 2
    y = (h - s) // 3 if h > w else (h - s) // 2
    sub = pb.new_subpixbuf(x, y, s, s)
    if s == px:
        return sub.copy()
    return sub.scale_simple(px, px, GdkPixbuf.InterpType.BILINEAR)


def make_thumb(job):
    """Vorschaubild (px×px) für job = {path, uri, mtime, size, video, cloud, px} oder None."""
    px, uri, mt = job["px"], job["uri"], job["mtime"]
    want = "large" if px <= 200 else "x-large" if px <= 512 else "xx-large"
    order = sorted(LEVELS, key=lambda lvl: (_RANK[lvl] < _RANK[want], abs(_RANK[lvl] - _RANK[want])))
    pb, lvl = _cached_thumb(uri, mt, order)
    if pb is not None and _RANK[lvl] >= _RANK[want]:
        return _square(pb, px)
    path = job["path"]
    if job["cloud"] and _online_only(path):
        return _square(pb, px) if pb is not None else None     # nicht herunterladen
    edge = LEVELS[want]
    made = None
    ext = os.path.splitext(path)[1].lower()
    if job["video"]:
        if not _failed_before(uri, mt):
            made = _video_thumb(path, edge)
    elif ext not in RAW_EXT and job["size"] < 200 * 1024 * 1024:
        try:
            made = _image_thumb(path, edge)
        except GLib.Error:
            made = None
    if made is not None:
        _save_thumb(made, uri, mt, want)
    elif pb is None:        # RAW, JPEG XL … oder kein ffmpegthumbnailer
        made = _system_thumb(uri, os.path.basename(path), mt)
    if made is None:
        return _square(pb, px) if pb is not None else None
    return _square(made, px)


class _ThumbPool:
    """Arbeits-Threads; die Warteschlange wird bei jedem Scrollen ersetzt
    (nur Sichtbares). Ergebnis per GLib.idle_add an deliver(key, job, pixbuf)."""

    def __init__(self, deliver, workers=2):
        self._deliver = deliver
        self._cv = threading.Condition()
        self._queue = collections.OrderedDict()
        self._busy = set()
        self._stop = False
        self._threads = []
        self._workers = workers

    def set_queue(self, jobs):
        with self._cv:
            if self._stop:
                return
            self._queue = collections.OrderedDict(
                (k, j) for k, j in jobs if k not in self._busy)
            self._cv.notify_all()
            while len(self._threads) < self._workers and self._queue:
                t = threading.Thread(target=self._run, daemon=True)
                self._threads.append(t)
                t.start()

    def stop(self):
        with self._cv:
            self._stop = True
            self._queue.clear()
            self._cv.notify_all()

    def _run(self):
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                key, job = self._queue.popitem(last=False)
                self._busy.add(key)
            try:
                pb = make_thumb(job)
            except Exception as exc:       # nie den Thread verlieren
                print("[filefenster] Katalog-Vorschau:", exc)
                pb = None
            with self._cv:
                self._busy.discard(key)
                if self._stop:
                    return
            GLib.idle_add(self._deliver, key, job, pb)


# ---------------------------------------------------------------------------
# Elemente
# ---------------------------------------------------------------------------
class _Item:
    __slots__ = ("path", "name", "mtime", "size", "taken", "date", "video", "cloud",
                 "key", "month", "_mime")

    def __init__(self, path, name, mtime, size, taken, video, cloud):
        self.path, self.name, self.mtime, self.size = path, name, mtime, size
        self.taken, self.video, self.cloud = taken, video, cloud
        self.date = taken or mtime
        self.key = (-self.date, name.casefold(), path)
        try:
            lt = time.localtime(self.date)
            self.month = (lt.tm_year, lt.tm_mon)
        except (OverflowError, OSError, ValueError):
            self.month = (1970, 1)
        self._mime = None

    @property
    def mime(self):
        if self._mime is None:
            self._mime = Gio.content_type_guess(self.name, None)[0] or ""
        return self._mime

    def gfile(self):
        return Gio.File.new_for_path(self.path)

    def uri(self):
        return Gio.File.new_for_path(self.path).get_uri()


class _Canvas(Gtk.DrawingArea, Gtk.Scrollable):
    """Zeichenfläche, die Gtk.ScrolledWindow direkt scrollen kann."""
    hadjustment = GObject.Property(type=Gtk.Adjustment)
    vadjustment = GObject.Property(type=Gtk.Adjustment)
    hscroll_policy = GObject.Property(type=Gtk.ScrollablePolicy,
                                      default=Gtk.ScrollablePolicy.MINIMUM)
    vscroll_policy = GObject.Property(type=Gtk.ScrollablePolicy,
                                      default=Gtk.ScrollablePolicy.MINIMUM)

    def __init__(self):
        super().__init__()
        self.get_style_context().add_class("gallery-canvas")


# ---------------------------------------------------------------------------
# Ansicht
# ---------------------------------------------------------------------------
class GalleryView(Gtk.ScrolledWindow):
    """Katalog. host = Tab (siehe host.py)."""

    is_command_target = True       # Befehlsleiste/Tasten des Fensters wirken hier

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.config = host.config
        ctx = self.get_style_context()
        ctx.add_class("content-area")
        ctx.add_class("gallery-view")
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.NONE)

        c = self.canvas = _Canvas()
        c.set_can_focus(True)
        c.set_has_tooltip(True)
        c.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                     | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                     | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
                     | Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.FOCUS_CHANGE_MASK)
        c.connect("draw", self._on_draw)
        c.connect("size-allocate", self._on_alloc)
        c.connect("notify::vadjustment", self._on_vadj)
        c.connect("button-press-event", self._on_press)
        c.connect("button-release-event", self._on_release)
        c.connect("motion-notify-event", self._on_motion)
        c.connect("leave-notify-event", self._on_leave)
        c.connect("scroll-event", self._on_scroll)
        c.connect("key-press-event", self._on_key)
        c.connect("query-tooltip", self._on_tooltip)
        c.connect("focus-in-event", lambda *_: c.queue_draw())
        c.connect("focus-out-event", lambda *_: c.queue_draw())
        c.connect("popup-menu", lambda *_: (self._popup_menu(None), True)[1])
        c.connect("drag-begin", self._on_drag_begin)
        c.connect("drag-data-get", self._on_drag_data_get)
        c.connect("drag-end", self._on_drag_end)

        self._items = {}            # Pfad -> _Item
        self._order = []            # Anzeigereihenfolge (neueste zuerst)
        self._index = {}            # Pfad -> Position in _order
        self._rows = []             # (art, y, höhe, monat, start, ende)
        self._row_y = []
        self._item_row = []
        self._months = []           # Monatsüberschriften in Reihenfolge
        self._height = 0
        self._layout_w = 0
        self._cell = float(DEFAULT_SIZE)
        self._cols = 1
        self.size = self._valid_size(self.config.get("gallery_size", DEFAULT_SIZE))

        self._sel = set()           # Pfade
        self._anchor = None
        self._cursor = None
        self._hover = None
        self._kbd = False           # Fokusrahmen nur nach Tastaturbedienung
        self._press = None          # (x, y, Pfad) für Ziehen/Einzelauswahl
        self._pending_single = None
        self._band = None           # Auswahlrechteck
        self._dragging = False
        self._zoom_acc = 0.0
        self._del_from = None
        self._hooked_adj = None

        self._surfaces = collections.OrderedDict()   # Pfad -> (px, Fläche, Bytes)
        self._surf_bytes = 0
        self._failed = set()
        self._head_font = None

        self._gen = 0
        self._scan = None
        self._scanned = False
        self._scan_end = 0.0        # Ende des letzten Durchsuchens (monotonic)
        self._stale = False         # Änderung gemeldet, während unsichtbar
        self._rescan_pending = False
        self._monitors = {}
        self._timers = {}
        self._destroyed = False
        self._thumbs = _ThumbPool(self._on_thumb)
        self.config.connect_changed(self._on_config_changed)
        self.connect("destroy", self._on_destroy)
        self.connect("map", lambda *_: self._stale and self._start_scan())
        self.add(c)          # setzt die Adjustments – erst nach dem Zustand

    # ================================================================ Hilfen
    @staticmethod
    def _valid_size(v):
        try:
            v = int(v)
        except (TypeError, ValueError):
            return DEFAULT_SIZE
        return min(SIZES, key=lambda s: abs(s - v))

    def _cancel_timer(self, name):
        src = self._timers.pop(name, 0)
        if src:
            GLib.source_remove(src)

    def _later_once(self, name, ms, fn):
        """Entprellt: fn nach ms (eine Quelle je Name)."""
        if self._destroyed:
            return
        self._cancel_timer(name)

        def run():
            self._timers.pop(name, None)
            if not self._destroyed:
                fn()
            return False
        self._timers[name] = GLib.timeout_add(ms, run)

    @staticmethod
    def _defer(fn, *args):
        """Menüeinträge: erst nach dem Schliessen des Menüs ausführen."""
        def run():
            GLib.idle_add(lambda: (fn(*args), False)[1])
        return run

    def _vadj(self):
        return self.canvas.get_property("vadjustment")

    def _scroll_y(self):
        adj = self._vadj()
        return adj.get_value() if adj is not None else 0.0

    # ================================================================ Durchsuchen
    def _start_scan(self, restart=False, recheck=False):
        if self._destroyed:
            return
        self._stale = False
        if self._scan is not None:
            if not restart:
                self._rescan_pending = True
                return
            self._scan.cancelled.set()
            self._scan = None
        self._rescan_pending = False
        roots = []
        for r in active_roots(self.config):
            try:
                cloud = cloudstatus.in_cloud(r)      # im GTK-Thread vorbereiten
            except Exception:
                cloud = False
            roots.append((r, cloud))
        self._gen += 1
        self._scan = _Scan(self._gen, roots, self._on_scan_msg, recheck=recheck,
                           skip=hidden_roots(self.config))
        self._scan.start()
        self.canvas.queue_draw()

    def _on_scan_msg(self, gen, kind, payload):
        if gen != self._gen or self._destroyed:
            return False
        if kind == "batch":
            changed = False
            for path, name, mt, size, taken, video, cloud in payload:
                old = self._items.get(path)
                if old is not None and old.mtime == mt and old.size == size and old.taken == taken:
                    continue
                self._items[path] = _Item(path, name, mt, size, taken, video, cloud)
                if old is not None:
                    self._drop_surface(path)
                    self._failed.discard(path)
                changed = True
            if changed:
                # während des Durchsuchens gesammelt neu anordnen
                self._later_once("resort", 200 if self._order else 30, self._resort)
        else:
            seen, dirs = payload
            removed = [p for p in self._items if p not in seen]
            self._remove_paths(removed, relayout=False)
            self._scan = None
            self._scanned = True
            self._scan_end = time.monotonic()
            self._update_monitors(dirs)
            self._resort()
            if self._rescan_pending:
                self._start_scan()
        return False

    def _remove_paths(self, paths, relayout=True):
        if not paths:
            return False
        for p in paths:
            self._items.pop(p, None)
            self._sel.discard(p)
            self._failed.discard(p)
            self._drop_surface(p)
        if relayout:
            self._resort()
        return True

    def _prune_roots(self):
        """Elemente ausserhalb der aktiven Sammlungsordner sofort entfernen."""
        entries = collection(self.config)
        gone = [p for p in self._items if not _shown(p, entries)]
        self._remove_paths(gone)

    # -- Ordner überwachen (Dateien kommen dazu/verschwinden) -------------
    def _update_monitors(self, dirs):
        want = set(dirs[:MAX_MONITORS])
        for d in [d for d in self._monitors if d not in want]:
            self._monitors.pop(d).cancel()
        for d in want:
            if d in self._monitors:
                continue
            try:
                mon = Gio.File.new_for_path(d).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error:
                continue
            mon.set_rate_limit(800)
            mon.connect("changed", self._on_dir_changed)
            self._monitors[d] = mon

    _EVENTS = (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED,
               Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.MOVED_OUT,
               Gio.FileMonitorEvent.RENAMED, Gio.FileMonitorEvent.CHANGES_DONE_HINT)

    def _on_dir_changed(self, _mon, _f, _other, event):
        if event not in self._EVENTS:
            return
        if not self.get_mapped():
            self._stale = True        # beim nächsten Öffnen des Katalogs
            return
        self._later_once("rescan", 600, self._start_scan)

    # ================================================================ Anordnen
    def _resort(self):
        self._cancel_timer("resort")
        self._order = sorted(self._items.values(), key=lambda it: it.key)
        self._index = {it.path: i for i, it in enumerate(self._order)}
        if self._anchor not in self._index:
            self._anchor = None
        if self._cursor not in self._index:
            self._cursor = None
        self._layout()
        self._hover_from_pointer()        # Positionen haben sich verschoben
        self._later_once("state", 30, self.host.state_changed)

    def _layout(self):
        width = self.canvas.get_allocated_width()
        self._layout_w = width
        avail = max(1, width - 2 * PAD_X)
        cols = max(1, int(round((avail + GAP) / (self.size + GAP))))
        cell = max(24.0, (avail - (cols - 1) * GAP) / cols)
        self._cols, self._cell = cols, cell
        rows, row_y, months = [], [], []
        order = self._order
        item_row = [0] * len(order)
        y = PAD_TOP
        i, n = 0, len(order)
        while i < n:
            mk = order[i].month
            j = i
            while j < n and order[j].month == mk:
                j += 1
            months.append(mk)
            rows.append(("head", y, HEAD_H, mk, i, j))
            row_y.append(y)
            y += HEAD_H
            for s in range(i, j, cols):
                e = min(s + cols, j)
                ri = len(rows)
                rows.append(("items", y, cell, mk, s, e))
                row_y.append(y)
                for k in range(s, e):
                    item_row[k] = ri
                y += cell + GAP
            y += GROUP_GAP
            i = j
        self._rows, self._row_y, self._item_row, self._months = rows, row_y, item_row, months
        self._height = int(y + 16) if n else 0
        self._update_adjustment()
        self.canvas.queue_draw()
        self._schedule_thumbs()

    def _update_adjustment(self):
        adj = self._vadj()
        h = self.canvas.get_allocated_height()
        if adj is not None:
            upper = max(self._height, h)
            val = min(adj.get_value(), max(0, upper - h))
            adj.configure(val, 0, upper, 48, max(1, h * 0.9), h)
        hadj = self.canvas.get_property("hadjustment")
        if hadj is not None:
            w = self.canvas.get_allocated_width()
            hadj.configure(0, 0, w, 1, w, w)

    def _on_vadj(self, *_a):
        adj = self._vadj()
        if adj is not None and adj is not self._hooked_adj:
            self._hooked_adj = adj
            adj.connect("value-changed", self._on_scrolled)
        self._update_adjustment()

    def _on_alloc(self, _w, alloc):
        if alloc.width != self._layout_w:
            self._layout()
        else:
            self._update_adjustment()
            self._schedule_thumbs()

    def _on_scrolled(self, *_a):
        self.canvas.queue_draw()
        self._schedule_thumbs()
        self._hover_from_pointer()

    def _item_rect(self, idx):
        """(x, y im Inhalt, Kante) des Elements idx."""
        row = self._rows[self._item_row[idx]]
        col = idx - row[4]
        return PAD_X + col * (self._cell + GAP), row[1], self._cell

    def _hit(self, x, y):
        """Index des Elements unter (x, y) (Widget-Koordinaten) oder None."""
        if not self._rows:
            return None
        yy = y + self._scroll_y()
        ri = bisect.bisect_right(self._row_y, yy) - 1
        if ri < 0:
            return None
        kind, ry, h, _m, s, e = self._rows[ri]
        if kind != "items" or yy > ry + h:
            return None
        if x < PAD_X:
            return None
        col = int((x - PAD_X) // (self._cell + GAP))
        if col >= e - s or x > PAD_X + col * (self._cell + GAP) + self._cell:
            return None
        return s + col

    def _rows_between(self, top, bottom):
        ri = max(0, bisect.bisect_right(self._row_y, top) - 1)
        while ri < len(self._rows):
            row = self._rows[ri]
            if row[1] > bottom:
                break
            if row[1] + row[2] >= top:
                yield row
            ri += 1

    # ================================================================ Zeichnen
    def _on_draw(self, w, cr):
        P = theme.P
        width, height = w.get_allocated_width(), w.get_allocated_height()
        Gdk.cairo_set_source_rgba(cr, _rgba(P["content_bg"]))
        cr.paint()
        if not self._order:
            self._draw_empty(cr, width, height)
            return False
        off = self._scroll_y()
        if self._head_font is None:
            desc = w.get_style_context().get_font(Gtk.StateFlags.NORMAL).copy()
            desc.set_weight(Pango.Weight.SEMIBOLD)
            desc.set_size(int(desc.get_size() * 1.25))
            self._head_font = desc
        focus = w.has_focus() and self._kbd
        cur = self._index.get(self._cursor) if self._cursor else None
        for kind, y, h, mk, s, e in self._rows_between(off, off + height):
            if kind == "head":
                self._draw_head(cr, w, month_label(mk), y - off, width)
                continue
            for k in range(s, e):
                x, _y, c = self._item_rect(k)
                self._draw_item(cr, k, round(x), round(y - off), round(c), focus and k == cur)
        if self._band is not None:
            x0, y0, x1, y1 = self._band_rect()
            x0, x1 = min(x0, x1), max(x0, x1)
            y0, y1 = min(y0, y1) - off, max(y0, y1) - off
            Gdk.cairo_set_source_rgba(cr, _rgba(P["accent"], 0.25))
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.fill()
            Gdk.cairo_set_source_rgba(cr, _rgba(P["accent"], 0.9))
            cr.set_line_width(1)
            cr.rectangle(x0 + 0.5, y0 + 0.5, max(0, x1 - x0 - 1), max(0, y1 - y0 - 1))
            cr.stroke()
        return False

    def _draw_empty(self, cr, width, height):
        if not active_roots(self.config):
            lines = ["Die Sammlung enthält keine Ordner.",
                     "Fügen Sie über „Sammlung“ einen Ordner mit Bildern hinzu."]
        elif not self._scanned:
            lines = ["Wird geladen…"]
        else:
            lines = ["Keine Bilder oder Videos gefunden.",
                     "Fügen Sie über „Sammlung“ weitere Ordner hinzu."]
        y = 60
        for i, text in enumerate(lines):
            layout = self.canvas.create_pango_layout(text)
            tw, th = layout.get_pixel_size()
            Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["text"] if i == 0 else theme.P["text2"]))
            cr.move_to(max(PAD_X, (width - tw) / 2), y)
            PangoCairo.show_layout(cr, layout)
            y += th + 6

    def _draw_head(self, cr, w, text, y, width):
        layout = w.create_pango_layout(text)
        layout.set_font_description(self._head_font)
        tw, th = layout.get_pixel_size()
        ty = y + (HEAD_H - th) / 2 + 3
        Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["accent"]))
        cr.move_to(PAD_X, ty)
        PangoCairo.show_layout(cr, layout)
        lx = PAD_X + tw + 12
        if lx < width - PAD_X:
            Gdk.cairo_set_source_rgba(cr, _rgba(theme.P["divider"]))
            cr.rectangle(lx, int(ty + th / 2), width - PAD_X - lx, 1)
            cr.fill()

    def _draw_item(self, cr, k, x, y, c, focused):
        P = theme.P
        it = self._order[k]
        ent = self._surfaces.get(it.path)
        cr.save()
        _round_rect(cr, x, y, c, c, RADIUS)
        cr.clip()
        if ent is not None:
            self._surfaces.move_to_end(it.path)
            px, surf, _b = ent
            Gdk.cairo_set_source_rgba(cr, _rgba(P["divider"]))
            cr.paint()
            cr.translate(x, y)
            cr.scale(c / px, c / px)
            cr.set_source_surface(surf, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
        else:
            Gdk.cairo_set_source_rgba(cr, _rgba(P["divider"]))
            cr.paint()
            if it.path in self._failed:
                isz = 48 if c >= 96 else 32
                gicon = Gio.content_type_get_icon(it.mime) if it.mime else None
                pb = icons.gicon_pixbuf(gicon, isz)
                if pb is not None:
                    Gdk.cairo_set_source_pixbuf(cr, pb, x + (c - isz) / 2, y + (c - isz) / 2)
                    cr.paint()
        cr.restore()
        if it.video:
            self._draw_video_badge(cr, x, y, c)
        sel = it.path in self._sel
        hover = k == self._hover
        if sel or hover:
            cr.save()
            _round_rect(cr, x, y, c, c, RADIUS)
            cr.clip()
            Gdk.cairo_set_source_rgba(cr, _rgba(P["accent"], 0.18) if sel
                                      else _rgba("#ffffff", 0.12))
            cr.paint()
            cr.restore()
            if sel:
                Gdk.cairo_set_source_rgba(cr, _rgba(P["accent"]))
                cr.set_line_width(2)
                _round_rect(cr, x + 1, y + 1, c - 2, c - 2, RADIUS)
                cr.stroke()
            self._draw_check(cr, x + c - CHECK - 6, y + 6, sel)
        if focused:
            col = _rgba(P["text"], 0.85)
            Gdk.cairo_set_source_rgba(cr, col)
            cr.set_line_width(1.5)
            _round_rect(cr, x - 2.25, y - 2.25, c + 4.5, c + 4.5, RADIUS + 2)
            cr.stroke()

    @staticmethod
    def _draw_video_badge(cr, x, y, c):
        r = 12 if c >= 96 else 9
        cx, cy = x + 8 + r, y + c - 8 - r
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.fill()
        t = r * 0.42
        cr.move_to(cx - t * 0.7, cy - t)
        cr.line_to(cx + t * 1.05, cy)
        cr.line_to(cx - t * 0.7, cy + t)
        cr.close_path()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.fill()

    @staticmethod
    def _draw_check(cr, x, y, checked):
        P = theme.P
        _round_rect(cr, x, y, CHECK, CHECK, 4)
        if checked:
            Gdk.cairo_set_source_rgba(cr, _rgba(P["accent"]))
            cr.fill()
            Gdk.cairo_set_source_rgba(cr, _rgba(P["accent_text"]))
            cr.set_line_width(1.8)
            cr.move_to(x + 5, y + 10.5)
            cr.line_to(x + 8.5, y + 14)
            cr.line_to(x + 15, y + 6.5)
            cr.stroke()
        else:
            cr.set_source_rgba(0, 0, 0, 0.3)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(1.2)
            cr.stroke()

    def _queue_item(self, idx):
        if idx is None or not (0 <= idx < len(self._order)):
            return
        x, y, c = self._item_rect(idx)
        y -= self._scroll_y()
        self.canvas.queue_draw_area(int(x) - 4, int(y) - 4, int(c) + 8, int(c) + 8)

    # ================================================================ Vorschaubilder
    def _render_px(self):
        """Pixel je Vorschaubild (bei HiDPI entsprechend mehr)."""
        scale = max(1, self.canvas.get_scale_factor())
        return min(1024, int(math.ceil(self._cell * scale / 8.0)) * 8)

    def _schedule_thumbs(self):
        self._later_once("thumbs", 50, self._request_thumbs)

    def _request_thumbs(self):
        if not self._order or not self.get_mapped():
            return
        h = max(1, self.canvas.get_allocated_height())
        off = self._scroll_y()
        px = self._render_px()
        jobs, seen = [], set()
        for top, bottom in ((off, off + h), (off + h, off + h * 1.6), (off - h * 0.4, off)):
            for kind, _y, _h, _m, s, e in self._rows_between(top, bottom):
                if kind != "items":
                    continue
                for k in range(s, e):
                    it = self._order[k]
                    if it.path in seen or it.path in self._failed:
                        continue
                    seen.add(it.path)
                    ent = self._surfaces.get(it.path)
                    if ent is not None and ent[0] == px:
                        continue
                    jobs.append(((it.path, px), {
                        "path": it.path, "uri": it.uri(), "mtime": it.mtime, "size": it.size,
                        "video": it.video, "cloud": it.cloud, "px": px}))
        self._thumbs.set_queue(jobs)

    def _on_thumb(self, key, job, pb):
        if self._destroyed:
            return False
        path, px = key
        it = self._items.get(path)
        if it is None or it.mtime != job["mtime"]:
            return False
        if pb is None:
            if path not in self._surfaces:
                self._failed.add(path)
        else:
            surf = Gdk.cairo_surface_create_from_pixbuf(pb, 1, None)
            self._drop_surface(path)
            nbytes = px * px * 4
            self._surfaces[path] = (px, surf, nbytes)
            self._surf_bytes += nbytes
            while self._surf_bytes > SURFACE_BUDGET and len(self._surfaces) > 1:
                _p, (_px, _s, b) = self._surfaces.popitem(last=False)
                self._surf_bytes -= b
        self._queue_item(self._index.get(path))
        return False

    def _drop_surface(self, path):
        ent = self._surfaces.pop(path, None)
        if ent is not None:
            self._surf_bytes -= ent[2]

    def thumbnails_loaded(self):
        """Pfade mit Vorschaubild (für Tests)."""
        return set(self._surfaces)

    def visible_items(self):
        """Pfade der sichtbaren Elemente (für Tests)."""
        off = self._scroll_y()
        h = self.canvas.get_allocated_height()
        out = []
        for kind, _y, _h, _m, s, e in self._rows_between(off, off + h):
            if kind == "items":
                out.extend(self._order[k].path for k in range(s, e))
        return out

    # ================================================================ Grösse
    def _apply_size(self, size, keep=True):
        size = self._valid_size(size)
        if size == self.size:
            return
        # das oberste sichtbare Element bleibt an seinem Platz
        anchor, delta = None, 0
        if keep and self._order:
            off = self._scroll_y()
            for kind, y, _h, _m, s, _e in self._rows_between(off, off + 1):
                if kind == "items":
                    anchor, delta = s, y - off
            if anchor is None and self._cursor in self._index:
                anchor = self._index[self._cursor]
        self.size = size
        self._layout()
        if anchor is not None:
            _x, y, _c = self._item_rect(anchor)
            adj = self._vadj()
            if adj is not None:
                adj.set_value(max(0, min(y - delta, adj.get_upper() - adj.get_page_size())))

    def zoom(self, delta):
        i = SIZES.index(self.size) if self.size in SIZES else SIZES.index(DEFAULT_SIZE)
        i = max(0, min(len(SIZES) - 1, i + (1 if delta > 0 else -1)))
        if SIZES[i] != self.size:
            self.config.set("gallery_size", SIZES[i])     # wirkt über _on_config_changed

    def set_size(self, size):
        self.config.set("gallery_size", self._valid_size(size))

    def build_size_menu(self):
        m = WinMenu()
        for size, label in SIZE_PRESETS:
            m.item(label, lambda s=size: self.set_size(s), icon="grid",
                   radio=self.size == size)
        m.separator()
        m.item("Vergrössern", lambda: self.zoom(1), accel="Strg+Plus",
               sensitive=self.size < SIZES[-1])
        m.item("Verkleinern", lambda: self.zoom(-1), accel="Strg+Minus",
               sensitive=self.size > SIZES[0])
        return m

    # ================================================================ Auswahl
    def _sel_changed(self):
        self.canvas.queue_draw()
        self._later_once("state", 30, self.host.state_changed)

    def _select_only(self, idx):
        self._sel = {self._order[idx].path} if idx is not None else set()

    def _select_range(self, a, b, add=False):
        lo, hi = min(a, b), max(a, b)
        rng = {self._order[k].path for k in range(lo, hi + 1)}
        self._sel = (self._sel | rng) if add else rng

    def _set_cursor(self, idx, scroll=True):
        self._cursor = self._order[idx].path if idx is not None else None
        if scroll and idx is not None:
            self._scroll_to(idx)

    def _scroll_to(self, idx):
        adj = self._vadj()
        if adj is None:
            return
        _x, y, c = self._item_rect(idx)
        val, page = adj.get_value(), adj.get_page_size()
        # Überschrift des ersten Monats mitzeigen
        top = y - (HEAD_H if self._rows[self._item_row[idx] - 1][0] == "head" else 8)
        if top < val:
            adj.set_value(max(0, top))
        elif y + c + 8 > val + page:
            adj.set_value(min(adj.get_upper() - page, y + c + 8 - page))

    def _selected_items(self):
        return [self._order[i] for i in sorted(self._index[p] for p in self._sel if p in self._index)]

    def get_selected(self):
        return [it.gfile() for it in self._selected_items()]

    def selected_size(self):
        return sum(it.size for it in self._selected_items())

    def item_count(self):
        return len(self._items)

    def select_all(self):
        self._sel = set(self._index)
        self._sel_changed()

    def select_none(self):
        self._sel = set()
        self._sel_changed()

    def invert_selection(self):
        self._sel = set(self._index) - self._sel
        self._sel_changed()

    def select_paths(self, paths):
        """Elemente auswählen (für Tests/„Im Katalog anzeigen“)."""
        paths = [p for p in paths if p in self._index]
        self._sel = set(paths)
        if paths:
            self._anchor = paths[0]
            self._set_cursor(self._index[paths[0]])
        self._sel_changed()

    def focus_view(self):
        self.canvas.grab_focus()

    # ================================================================ Maus
    def _check_hit(self, idx, x, y):
        """Klick auf das Auswahlkästchen oben rechts?"""
        if idx is None:
            return False
        ix, iy, c = self._item_rect(idx)
        iy -= self._scroll_y()
        cx, cy = ix + c - CHECK - 6, iy + 6
        return cx - 3 <= x <= cx + CHECK + 3 and cy - 3 <= y <= cy + CHECK + 3

    def _on_press(self, w, ev):
        w.grab_focus()
        self._kbd = False
        state = ev.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        idx = self._hit(ev.x, ev.y)
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            if ev.button == 1 and idx is not None and not ctrl and not shift:
                self._pending_single = None
                self._select_only(idx)
                self._sel_changed()
                self.open_selected()
            return True
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if ev.button == 1:
            self._pending_single = None
            if idx is None:
                base = set(self._sel) if (ctrl or shift) else set()
                if not (ctrl or shift):
                    self._sel = set()
                    self._sel_changed()
                self._band = {"x0": ev.x, "y0": ev.y + self._scroll_y(), "x": ev.x,
                              "y": ev.y + self._scroll_y(), "base": base}
                return True
            path = self._order[idx].path
            anchor = self._index.get(self._anchor) if self._anchor else None
            if shift and anchor is not None:
                self._select_range(anchor, idx, add=ctrl)
            elif ctrl or self._check_hit(idx, ev.x, ev.y):
                self._sel ^= {path}
                self._anchor = path
            else:
                if path in self._sel and len(self._sel) > 1:
                    self._pending_single = path      # erst beim Loslassen (Ziehen mehrerer)
                else:
                    self._select_only(idx)
                self._anchor = path
            self._set_cursor(idx, scroll=False)
            self._press = (ev.x, ev.y, path)
            self._sel_changed()
            return True
        if ev.button == 3:
            if idx is None:
                self._sel = set()
            elif self._order[idx].path not in self._sel:
                self._select_only(idx)
                self._anchor = self._order[idx].path
            if idx is not None:
                self._set_cursor(idx, scroll=False)
            self._sel_changed()
            self._popup_menu(ev)
            return True
        return False

    def _on_release(self, _w, ev):
        if ev.button != 1:
            return False
        if self._band is not None:
            self._band = None
            self._cancel_timer("band")
            self.canvas.queue_draw()
        if self._pending_single is not None and not self._dragging:
            self._sel = {self._pending_single}
            self._sel_changed()
        self._pending_single = None
        self._press = None
        return True

    def _on_motion(self, w, ev):
        idx = self._hit(ev.x, ev.y)
        if idx != self._hover:
            old, self._hover = self._hover, idx
            self._queue_item(old)
            self._queue_item(idx)
        if self._band is not None:
            self._band["x"], self._band["y"] = ev.x, ev.y + self._scroll_y()
            self._update_band()
            h = w.get_allocated_height()
            if ev.y < 24 or ev.y > h - 24:
                self._band["edge"] = -1 if ev.y < 24 else 1
                if "band" not in self._timers:
                    self._timers["band"] = GLib.timeout_add(40, self._band_scroll)
            else:
                self._band["edge"] = 0
            return True
        if self._press is not None and ev.state & Gdk.ModifierType.BUTTON1_MASK and not self._dragging:
            px, py, ppath = self._press
            if ppath in self._index and w.drag_check_threshold(int(px), int(py), int(ev.x), int(ev.y)):
                if ppath not in self._sel:
                    self._sel = {ppath}
                    self._sel_changed()
                self._dragging = True
                self._pending_single = None
                tl = Gtk.TargetList.new([])
                tl.add_uri_targets(0)
                w.drag_begin_with_coordinates(
                    tl, Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK,
                    1, ev, int(px), int(py))
        return False

    def _on_leave(self, _w, ev):
        if ev.detail != Gdk.NotifyType.INFERIOR and self._hover is not None:
            old, self._hover = self._hover, None
            self._queue_item(old)
        return False

    def _hover_from_pointer(self):
        win = self.canvas.get_window()
        seat = self.canvas.get_display().get_default_seat()
        if win is None or seat is None:
            return
        _w, x, y, _m = win.get_device_position(seat.get_pointer())
        w, h = self.canvas.get_allocated_width(), self.canvas.get_allocated_height()
        idx = self._hit(x, y) if 0 <= x < w and 0 <= y < h else None
        if idx != self._hover:
            self._hover = idx
            self.canvas.queue_draw()

    # -- Auswahlrechteck ------------------------------------------------
    def _band_rect(self):
        b = self._band
        return b["x0"], b["y0"], b["x"], b["y"]

    def _update_band(self):
        x0, y0, x1, y1 = self._band_rect()
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        hits = set()
        step = self._cell + GAP
        for kind, ry, h, _m, s, e in self._rows_between(y0, y1):
            if kind != "items" or ry > y1 or ry + h < y0:
                continue
            c0 = max(0, int((x0 - PAD_X) // step))
            c1 = min(e - s - 1, int((x1 - PAD_X) // step))
            for col in range(c0, c1 + 1):
                ix = PAD_X + col * step
                if ix <= x1 and ix + self._cell >= x0:
                    hits.add(self._order[s + col].path)
        new = self._band["base"] | hits
        if new != self._sel:
            self._sel = new
            self._sel_changed()
        self.canvas.queue_draw()

    def _band_scroll(self):
        b = self._band
        adj = self._vadj()
        if b is None or adj is None or not b.get("edge"):
            self._timers.pop("band", None)
            return False
        old = adj.get_value()
        adj.set_value(max(0, min(adj.get_upper() - adj.get_page_size(), old + 24 * b["edge"])))
        b["y"] += adj.get_value() - old
        self._update_band()
        return True

    # -- Ziehen (Dateien in andere Ordner/Programme) -------------------
    def _on_drag_begin(self, w, ctx):
        ent = self._surfaces.get(self._press[2]) if self._press else None
        if ent is None:
            Gtk.drag_set_icon_name(ctx, "image-x-generic", 0, 0)
            return
        px, surf, _b = ent
        s = 96
        img = cairo.ImageSurface(cairo.FORMAT_ARGB32, s, s)
        c = cairo.Context(img)
        _round_rect(c, 0, 0, s, s, RADIUS)
        c.clip()
        c.scale(s / px, s / px)
        c.set_source_surface(surf, 0, 0)
        c.paint_with_alpha(0.85)
        img.set_device_offset(-s / 2, -s / 2)
        Gtk.drag_set_icon_surface(ctx, img)

    def _on_drag_data_get(self, _w, _ctx, data, _info, _time):
        data.set_uris([it.uri() for it in self._selected_items()])

    def _on_drag_end(self, *_a):
        self._dragging = False
        self._press = None
        self._pending_single = None

    # -- Mausrad: Strg = Grösse -----------------------------------------
    def _on_scroll(self, _w, ev):
        state = ev.state & Gtk.accelerator_get_default_mod_mask()
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        step = 0
        if ev.direction == Gdk.ScrollDirection.UP:
            step = 1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            step = -1
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            ok, _dx, dy = ev.get_scroll_deltas()
            self._zoom_acc += dy if ok else 0
            if abs(self._zoom_acc) >= 1:
                step = -1 if self._zoom_acc > 0 else 1
                self._zoom_acc = 0.0
        if step:
            self.zoom(step)
        return True

    # ================================================================ Tastatur
    def _on_key(self, _w, ev):
        state = ev.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        kv = ev.keyval
        K = Gdk
        if kv in (K.KEY_Return, K.KEY_KP_Enter, K.KEY_ISO_Enter) and not state:
            self.open_selected()
            return True
        if kv == K.KEY_Menu or (kv == K.KEY_F10 and shift):
            self._popup_menu(None)
            return True
        n = len(self._order)
        if not n:
            return False
        cur = self._index.get(self._cursor) if self._cursor else None
        if kv == K.KEY_space:
            if cur is None:
                cur = 0
            path = self._order[cur].path
            if ctrl:
                self._sel ^= {path}
            else:
                self._select_only(cur)
            self._anchor = path
            self._set_cursor(cur)
            self._kbd = True
            self._sel_changed()
            return True
        target = None
        if kv in (K.KEY_Left, K.KEY_KP_Left):
            target = 0 if cur is None else max(0, cur - 1)
        elif kv in (K.KEY_Right, K.KEY_KP_Right):
            target = 0 if cur is None else min(n - 1, cur + 1)
        elif kv in (K.KEY_Up, K.KEY_KP_Up):
            target = 0 if cur is None else self._vertical(cur, -1)
        elif kv in (K.KEY_Down, K.KEY_KP_Down):
            target = 0 if cur is None else self._vertical(cur, 1)
        elif kv in (K.KEY_Home, K.KEY_KP_Home):
            target = 0
        elif kv in (K.KEY_End, K.KEY_KP_End):
            target = n - 1
        elif kv in (K.KEY_Page_Up, K.KEY_KP_Page_Up, K.KEY_Page_Down, K.KEY_KP_Page_Down):
            target = 0 if cur is None else self._page(
                cur, -1 if kv in (K.KEY_Page_Up, K.KEY_KP_Page_Up) else 1)
        else:
            return False
        self._kbd = True
        path = self._order[target].path
        anchor = self._index.get(self._anchor) if self._anchor else None
        if shift and anchor is not None:
            self._select_range(anchor, target, add=ctrl)
        elif not ctrl:
            self._select_only(target)
            self._anchor = path
        self._set_cursor(target)
        self._sel_changed()
        return True

    def _vertical(self, cur, d):
        ri = self._item_row[cur]
        col = cur - self._rows[ri][4]
        r = ri + d
        while 0 <= r < len(self._rows) and self._rows[r][0] != "items":
            r += d
        if not 0 <= r < len(self._rows):
            return cur
        _k, _y, _h, _m, s, e = self._rows[r]
        return s + min(col, e - s - 1)

    def _page(self, cur, d):
        adj = self._vadj()
        page = adj.get_page_size() if adj is not None else 400
        x, y, _c = self._item_rect(cur)
        col = cur - self._rows[self._item_row[cur]][4]
        target_y = y + d * page
        ri = max(0, min(len(self._rows) - 1, bisect.bisect_right(self._row_y, target_y) - 1))
        while 0 <= ri < len(self._rows) and self._rows[ri][0] != "items":
            ri += 1 if d > 0 else -1
        if not 0 <= ri < len(self._rows):
            return 0 if d < 0 else len(self._order) - 1
        _k, _y, _h, _m, s, e = self._rows[ri]
        return s + min(col, e - s - 1)

    # ================================================================ Tooltip
    def _on_tooltip(self, w, x, y, keyboard, tooltip):
        if self._band is not None or self._dragging or Gtk.grab_get_current() is not None:
            return False        # auch nicht über einem offenen Kontextmenü
        if keyboard:
            idx = self._index.get(self._cursor) if self._cursor else None
        else:
            idx = self._hit(x, y)
        if idx is None:
            return False
        it = self._order[idx]
        lines = [it.name,
                 ("Aufnahmedatum: " if it.taken else "Änderungsdatum: ") + places.format_date(it.date),
                 "Grösse: " + places.format_size(it.size)]
        lines.append("Ordner: " + os.path.dirname(it.path))
        tooltip.set_text("\n".join(lines))
        ix, iy, c = self._item_rect(idx)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(ix), int(iy - self._scroll_y()), int(c), int(c)
        tooltip.set_tip_area(r)
        return True

    # ================================================================ Befehle
    def _launch(self, gf):
        fn = getattr(self.host, "launch_file", None)
        if callable(fn):
            fn(gf)
        else:
            host_mod.launch_file(gf, self.get_toplevel())

    def open_selected(self):
        items = self._selected_items()
        if not items:
            return
        if len(items) > 15 and not fileops.ask_yes_no(
                self.get_toplevel(), "FileFenster",
                f"Möchten Sie wirklich {len(items)} Elemente öffnen?"):
            return
        for it in items:
            self._launch(it.gfile())

    def open_location(self):
        """„Speicherort öffnen“: Ordner im Tab öffnen und die Datei auswählen."""
        items = self._selected_items()
        if not items:
            return
        it = items[0]
        self.host.navigate(places.uri_for_path(os.path.dirname(it.path)))
        views = getattr(self.host, "views", None) or {}
        fv = views.get("folder")
        if fv is not None and hasattr(fv, "reveal"):
            fv.reveal([it.uri()])

    def copy(self):
        files = self.get_selected()
        if files:
            fileops.copy_files(files)

    def cut(self):
        files = self.get_selected()
        if files:
            fileops.cut_files(files)

    def copy_path(self):
        files = self.get_selected()
        if files:
            fileops.copy_paths(files)

    def delete(self, permanent=False):
        files = self.get_selected()
        if not files:
            return
        self._del_from = self._index.get(self._cursor) if self._cursor else None
        fileops.delete(files, parent=self.get_toplevel(), permanent=permanent,
                       on_done=self._after_delete)

    def _after_delete(self, deleted):
        if self._destroyed:
            return
        paths = [g.get_path() for g in deleted if g.get_path()]
        if self._remove_paths(paths):
            pos = self._del_from
            if pos is not None and self._order:     # Fokus auf das nächste Element
                self._cursor = self._order[min(pos, len(self._order) - 1)].path
            self._sel_changed()

    def show_properties(self):
        files = self.get_selected()
        if files:
            self.host.show_properties(files)

    def refresh(self):
        """Beim Öffnen des Katalogs: im Hintergrund (erneut) durchsuchen – nicht
        bei jedem Hin und Her, die Ordnerüberwachung meldet lokale Änderungen."""
        if not self._scanned or self._stale or time.monotonic() - self._scan_end > 30:
            self._start_scan()

    def reload(self):
        """F5: neu durchsuchen, fehlgeschlagene Vorschaubilder erneut versuchen."""
        self._failed.clear()
        self._start_scan(restart=True, recheck=True)
        self._schedule_thumbs()

    def is_loading(self):
        return self._scan is not None

    # -- Sammlung --------------------------------------------------------
    def add_collection_folder(self, path):
        add_folder(self.config, path)

    def remove_collection_folder(self, path):
        remove_folder(self.config, path)

    def set_collection_folder_enabled(self, path, enabled):
        set_folder_enabled(self.config, path, enabled)

    def choose_folder(self):
        top = self.get_toplevel()
        dlg = Gtk.FileChooserDialog(title="Ordner zur Sammlung hinzufügen",
                                    transient_for=top if isinstance(top, Gtk.Window) else None,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                        "Ordner hinzufügen", Gtk.ResponseType.OK)
        dlg.set_current_folder(places.home_dir())

        def resp(d, r):
            path = d.get_filename() if r == Gtk.ResponseType.OK else None
            d.destroy()
            if path:
                self.add_collection_folder(path)
        dlg.connect("response", resp)
        dlg.show()

    def build_collection_menu(self):
        m = WinMenu()
        entries = collection(self.config)
        for e in entries:
            mi = m.item(folder_label(e["path"]),
                        lambda p=e["path"], on=e["enabled"]: self.set_collection_folder_enabled(p, not on),
                        check=e["enabled"])
            mi.set_tooltip_text(e["path"])
        if not entries:
            m.item("Keine Ordner in der Sammlung", sensitive=False)
        m.separator()
        m.item("Ordner hinzufügen…", self._defer(self.choose_folder), icon="folder_add")
        if entries:
            sub = WinMenu()
            for e in entries:
                mi = sub.item(folder_label(e["path"]),
                              lambda p=e["path"]: self.remove_collection_folder(p), icon="dismiss")
                mi.set_tooltip_text(e["path"])
            m.item("Ordner entfernen", icon="delete", submenu=sub)
        return m

    # -- Kontextmenü -----------------------------------------------------
    def _popup(self, menu, event):
        if event is not None:
            menu.popup_at_pointer(event, attach_to=self.canvas)
            return
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = 40, 40, 1, 1
        idx = self._index.get(self._cursor) if self._cursor else None
        if idx is not None and self._order[idx].path in self._sel:
            x, y, c = self._item_rect(idx)
            r.x, r.y = int(x + c / 2), int(y - self._scroll_y() + c / 2)
        win = self.canvas.get_window()
        if win is not None:
            menu.popup_at_rect(win, r, attach_to=self.canvas)

    def _popup_menu(self, event):
        ev = event.copy() if event is not None else None
        items = self._selected_items()
        if items:
            self._popup(self.build_item_menu(items, ev), ev)
        else:
            self._popup(self.build_background_menu(), ev)

    def build_item_menu(self, items=None, event=None):
        items = items if items is not None else self._selected_items()
        files = [it.gfile() for it in items]
        single = len(items) == 1
        m = WinMenu()
        m.icon_row([
            ("cut", "Ausschneiden", self._defer(self.cut), True),
            ("copy", "Kopieren", self._defer(self.copy), True),
            ("share", "Teilen", self._defer(self._share_menu, files, event), True),
            ("delete", "Löschen", self._defer(self.delete), True),
        ])
        m.item("Öffnen", self._defer(self.open_selected), icon="open", accel="Eingabe", bold=True)
        if single:
            m.item("Öffnen mit", icon="apps", submenu=self._open_with_menu(items[0]))
            m.item("Speicherort öffnen", self._defer(self.open_location), icon="folder_open")
        m.separator()
        m.item("Als Pfad kopieren", self._defer(self.copy_path), icon="copy_select",
               accel="Strg+Umschalt+C")
        m.item("Eigenschaften", self._defer(self.host.show_properties, files), icon="wrench",
               accel="Alt+Eingabe")
        return m

    def build_background_menu(self):
        from . import undo
        m = WinMenu()
        m.item("Anzeigen", icon="grid", submenu=self.build_size_menu())
        m.item("Sammlung", icon="image", submenu=self.build_collection_menu())
        m.separator()
        top = self.get_toplevel()
        if undo.can_undo():
            m.item(undo.undo_label(), self._defer(undo.undo, top), icon="arrow_undo", accel="Strg+Z")
        if undo.can_redo():
            m.item(undo.redo_label(), self._defer(undo.redo, top), icon="arrow_redo", accel="Strg+Y")
        if undo.can_undo() or undo.can_redo():
            m.separator()
        m.item("Alles auswählen", self._defer(self.select_all), icon="select_all_on",
               accel="Strg+A", sensitive=bool(self._order))
        m.item("Aktualisieren", self._defer(self.reload), icon="arrow_clockwise", accel="F5")
        return m

    def _open_with_menu(self, it):
        sub = WinMenu()
        ct = it.mime or "application/octet-stream"
        apps = []
        default = Gio.AppInfo.get_default_for_type(ct, False)
        if default is not None:
            apps.append(default)
        seen = {default.get_id()} if default is not None else set()
        for app in Gio.AppInfo.get_all_for_type(ct):
            if app.get_id() in seen or not app.should_show():
                continue
            seen.add(app.get_id())
            apps.append(app)
        gf = it.gfile()
        for app in apps[:12]:
            pb = icons.gicon_pixbuf(app.get_icon(), 16) if app.get_icon() else None
            sub.item(app.get_display_name(), self._defer(self._launch_with, app, [gf]), icon=pb)
        if apps:
            sub.separator()
        sub.item("Andere App auswählen", self._defer(self._choose_app, gf))
        return sub

    def _launch_with(self, app, gfiles):
        try:
            app.launch(gfiles, Gdk.Display.get_default().get_app_launch_context())
        except GLib.Error as exc:
            fileops.show_errors(self.get_toplevel(), "Die App konnte nicht gestartet werden.",
                                [(app.get_display_name(), exc.message)])

    def _choose_app(self, gf):
        top = self.get_toplevel()
        dlg = Gtk.AppChooserDialog.new(top if isinstance(top, Gtk.Window) else None,
                                       Gtk.DialogFlags.MODAL, gf)
        dlg.set_title("Öffnen mit")
        dlg.set_heading("Wählen Sie eine App zum Öffnen dieser Datei aus.")

        def resp(d, r):
            app = d.get_app_info() if r == Gtk.ResponseType.OK else None
            d.destroy()
            if app is not None:
                self._launch_with(app, [gf])
        dlg.connect("response", resp)
        dlg.show()

    def _share_menu(self, gfiles, event):
        m = WinMenu()
        m.item("Per E-Mail senden", lambda: self._send_mail(gfiles), icon="mail")
        m.item("Als Pfad kopieren", lambda: fileops.copy_paths(gfiles), icon="document_copy")
        self._popup(m, event)

    def _send_mail(self, gfiles):
        cmd = ["xdg-email"]
        for g in gfiles:
            if g.get_path():
                cmd += ["--attach", g.get_path()]
        try:
            subprocess.Popen(cmd, start_new_session=True)
        except OSError as exc:
            fileops.show_errors(self.get_toplevel(), "Das E-Mail-Programm konnte nicht gestartet werden.",
                                [("xdg-email", str(exc))])

    # ================================================================ Einstellungen
    def _on_config_changed(self, key, value):
        if self._destroyed:
            return
        if key == "gallery_folders":
            self._prune_roots()
            self._start_scan(restart=True)
        elif key == "gallery_size":
            self._apply_size(value)
            self.host.state_changed()

    def _on_destroy(self, *_a):
        self._destroyed = True
        self.config.disconnect_changed(self._on_config_changed)
        if self._scan is not None:
            self._scan.cancelled.set()
            self._scan = None
        self._thumbs.stop()
        for mon in self._monitors.values():
            mon.cancel()
        self._monitors.clear()
        for src in self._timers.values():
            if src:
                GLib.source_remove(src)
        self._timers.clear()
