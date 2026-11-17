"""Vorschaubilder fuer "nur online"-Dateien unterdruecken.

Nemo/Nautilus halten den rclone-Mount fuer ein lokales Dateisystem und erzeugen
Vorschauen fuer PDFs, Bilder, Office-Dateien … – dafuer lesen sie die ganze Datei,
und das loest den Download aus. Windows macht das bei Online-Dateien nicht.

Nach der freedesktop-Thumbnail-Spezifikation ueberspringt der Dateimanager Dateien,
fuer die unter ~/.cache/thumbnails/fail/<programm>/<md5(uri)>.png ein gueltiger
Fehleintrag (gleiche URI und MTime) liegt. Genau so einen Eintrag legen wir fuer
Nur-online-Dateien an und entfernen ihn wieder, sobald die Datei lokal ist.
"""

import hashlib
import os
import struct
import zlib

FAIL_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                        "thumbnails", "fail", "gnome-thumbnail-factory")
MARK = b"onedrive-mint"


def _chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data +
            struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def _png(uri, mtime):
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)          # 1x1 RGBA
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")
    text = [(b"Thumb::URI", uri.encode()), (b"Thumb::MTime", str(int(mtime)).encode()),
            (b"Software", MARK)]
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    for k, v in text:
        out += _chunk(b"tEXt", k + b"\x00" + v)
    return out + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _path(uri):
    return os.path.join(FAIL_DIR, hashlib.md5(uri.encode()).hexdigest() + ".png")


def block(uri, mtime):
    """Fehleintrag anlegen (falls noch nicht vorhanden bzw. MTime geaendert)."""
    path = _path(uri)
    data = _png(uri, mtime)
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return
    except OSError:
        pass
    try:
        os.makedirs(FAIL_DIR, exist_ok=True)
        tmp = path + ".odm-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        pass


def unblock(uri):
    """Nur eigene Fehleintraege entfernen – echte Fehlschlaege bleiben unangetastet."""
    path = _path(uri)
    try:
        with open(path, "rb") as f:
            if MARK not in f.read():
                return False
        os.unlink(path)
        return True
    except OSError:
        return False


_SUPPORTED = None


def thumbnailable_types():
    """MIME-Typen, fuer die auf diesem System Vorschauen erzeugt werden koennen."""
    global _SUPPORTED
    if _SUPPORTED is None:
        types = set()
        for d in ("/usr/share/thumbnailers", "/usr/local/share/thumbnailers",
                  os.path.expanduser("~/.local/share/thumbnailers")):
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                try:
                    with open(os.path.join(d, n)) as f:
                        for line in f:
                            if line.startswith("MimeType="):
                                types.update(t.strip() for t in line[9:].split(";") if t.strip())
                except OSError:
                    pass
        _SUPPORTED = types
    return _SUPPORTED


def is_thumbnailable(name):
    from gi.repository import Gio
    mime = Gio.content_type_guess(name, None)[0]
    return mime.startswith(("image/", "video/")) or mime in thumbnailable_types()


def file_uri(path):
    from gi.repository import Gio
    return Gio.File.new_for_path(path).get_uri()
