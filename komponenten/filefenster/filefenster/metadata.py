"""Zusatzinfos für die Spalten der Detailansicht (wie Windows):
Abmessungen, Länge, Autoren, Titel – im Hintergrund gelesen und gecacht.

    meta = get(path, mtime, content_type)   → dict oder None (wird geladen)
    connect(callback)                       callback(path), wenn Infos da sind

Quellen: Bilder über GdkPixbuf, Audio/Video über GStreamer (Discoverer),
Office (docx/xlsx/pptx) und OpenDocument über die eingebetteten
Metadaten, PDF über Info-Wörterbuch bzw. XMP (ohne Poppler).
"""
import collections
import html
import os
import re
import threading
import weakref
import zipfile

from gi.repository import GLib

MAX_CACHE = 20000
_cache = collections.OrderedDict()      # (path, mtime) → dict
_pending = set()
_queue = collections.deque()
_cond = threading.Condition()
_thread = None
_listeners = []
_gst = None


def connect(callback):
    try:
        ref = weakref.WeakMethod(callback)
    except TypeError:
        ref = (lambda cb=callback: cb)
    _listeners.append(ref)


def _notify(path):
    for ref in list(_listeners):
        cb = ref()
        if cb is None:
            _listeners.remove(ref)
            continue
        try:
            cb(path)
        except Exception as exc:
            print("[filefenster] Metadaten:", exc)
    return False


def get(path, mtime, ctype):
    """Infos aus dem Cache oder None (dann wird im Hintergrund geladen)."""
    if not path:
        return {}
    key = (path, mtime)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    if not _interesting(path, ctype):
        return {}
    try:    # OneDrive „nur online“: Lesen würde die Datei herunterladen
        from . import cloudstatus
        if cloudstatus.online_only(path):
            return {}
    except ImportError:
        pass
    with _cond:
        if key not in _pending:
            _pending.add(key)
            _queue.append((key, ctype))      # zuletzt angefragt = zuerst (sichtbar)
            _cond.notify()
    _start()
    return None


def _interesting(path, ctype):
    ct = ctype or ""
    low = path.lower()
    return (ct.startswith(("image/", "audio/", "video/")) or low.endswith(_DOC_EXT)
            or low.endswith(".pdf"))


def _start():
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_worker, daemon=True)
        _thread.start()


def _worker():
    while True:
        with _cond:
            while not _queue:
                _cond.wait()
            key, ctype = _queue.pop()
        path = key[0]
        try:
            info = _read(path, ctype)
        except Exception:
            info = {}
        with _cond:
            _pending.discard(key)
        _cache[key] = info
        while len(_cache) > MAX_CACHE:
            _cache.popitem(last=False)
        GLib.idle_add(_notify, path)


# ----------------------------------------------------------------------
# Lesen
# ----------------------------------------------------------------------
_DOC_EXT = (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm",
            ".odt", ".ods", ".odp", ".odg")


def _read(path, ctype):
    import stat
    try:    # nur normale Dateien: eine Pipe namens „x.pdf“ blockierte sonst den Arbeiter
        if not stat.S_ISREG(os.stat(path).st_mode):
            return {}
    except OSError:
        return {}
    ct = ctype or ""
    low = path.lower()
    if low.endswith(_DOC_EXT):
        return _office(path)
    if low.endswith(".pdf"):
        return _pdf(path)
    if ct.startswith("image/"):
        out = _image(path)
        if out:
            return out
    if ct.startswith(("audio/", "video/", "image/")):
        return _media(path)
    return {}


def _image(path):
    from gi.repository import GdkPixbuf
    try:
        fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
    except Exception:
        return {}
    return {"dims": (w, h)} if fmt is not None and w > 0 else {}


def _media(path):
    """Länge, Bildgrösse, Interpret/Titel über GStreamer."""
    global _gst
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstPbutils", "1.0")
        from gi.repository import Gst, GstPbutils
    except (ImportError, ValueError):
        return {}
    if _gst is None:
        Gst.init(None)
        _gst = GstPbutils.Discoverer.new(5 * Gst.SECOND)
    try:
        di = _gst.discover_uri(GLib.filename_to_uri(path, None))
    except GLib.Error:
        return {}
    out = {}
    dur = di.get_duration()
    if dur and dur > 0:
        out["length"] = dur / Gst.SECOND
    for v in di.get_video_streams() or []:
        if v.get_width() > 0 and not v.is_image():
            out["dims"] = (v.get_width(), v.get_height())
            break
        if v.get_width() > 0:
            out.setdefault("dims", (v.get_width(), v.get_height()))
    tags = di.get_tags()
    if tags is not None:
        ok, artist = tags.get_string("artist")
        if ok and artist:
            out["authors"] = artist
        ok, title = tags.get_string("title")
        if ok and title:
            out["title"] = title
    return out


XML_MAX = 1 << 20        # Metadaten-XML: mehr liest niemand (ZIP-Bombe)
TEXT_MAX = 4096          # Länge eines Autors/Titels


def _xml_text(xml, tags):
    """Inhalt des ersten <tag …>…</tag> (wie re.search(r"<tag[^>]*>(.*?)</tag>")),
    aber mit str.find: bleibt bei feindlichen Dateien (tausende offene Tags,
    kein schliessendes) linear statt quadratisch."""
    for tag in tags:
        start = xml.find("<" + tag)
        gt = xml.find(">", start) if start >= 0 else -1
        end = xml.find("</%s>" % tag, gt + 1) if gt >= 0 else -1
        if end < 0:
            continue
        inner = xml[gt + 1:end][:4 * TEXT_MAX]
        if inner.strip():
            return html.unescape(re.sub(r"<[^<>]*>", "", inner)).strip()[:TEXT_MAX]
    return ""


def _office(path):
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            name = next((n for n in ("docProps/core.xml", "meta.xml") if n in names), None)
            if name is None:
                return {}
            with zf.open(name) as fh:        # begrenzt lesen: ZIP-Bombe
                xml = fh.read(XML_MAX).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, EOFError, ValueError):
        return {}
    out = {}
    a = _xml_text(xml, ("dc:creator", "meta:initial-creator"))
    t = _xml_text(xml, ("dc:title",))
    if a:
        out["authors"] = a
    if t:
        out["title"] = t
    return out


def _pdf_string(raw):
    """PDF-Zeichenkette (Literal oder <hex>) → Text."""
    if raw.startswith(b"<"):
        try:
            b = bytes.fromhex(raw[1:-1].decode("ascii", "ignore"))
        except ValueError:
            return ""
    else:
        b = raw[1:-1]
        b = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"",
                                                   b"f": b""}.get(m.group(1), m.group(1)), b)
        b = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), b)
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", "replace").strip()
    return b.decode("latin-1").strip()


_PDF_VALUE = re.compile(rb"\s{0,64}(\((?:\\.|[^\\)]){0,%d}\)|<[0-9A-Fa-f\s]{0,%d}>)"
                        % (TEXT_MAX, 4 * TEXT_MAX))


def _pdf_value(data, name):
    """Rohwert hinter /name – wie re.search(rb"/name\\s*(…)"), aber nur an den
    ersten 200 Fundstellen und mit begrenzter Länge (sonst quadratisch bei
    feindlichen Dateien mit vielen „/Author(“ ohne „)“)."""
    key, pos = b"/" + name, 0
    for _i in range(200):
        i = data.find(key, pos)
        if i < 0:
            return None
        m = _PDF_VALUE.match(data, i + len(key))
        if m:
            return m.group(1)
        pos = i + 1
    return None


def _pdf(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(256 * 1024)
            tail = b""
            if size > 256 * 1024:
                fh.seek(max(0, size - 256 * 1024))
                tail = fh.read()
    except OSError:
        return {}
    data = head + tail
    out = {}
    for key, name in (("authors", b"Author"), ("title", b"Title")):
        raw = _pdf_value(data, name)
        if raw:
            val = _pdf_string(raw)
            if val:
                out[key] = val
    if "authors" not in out or "title" not in out:      # XMP-Metadaten
        xmp = data.decode("utf-8", "ignore")
        if "authors" not in out:
            a = _xml_text(xmp, ("dc:creator",))
            if a:
                out["authors"] = a
        if "title" not in out:
            t = _xml_text(xmp, ("dc:title",))
            if t:
                out["title"] = t
    return out
