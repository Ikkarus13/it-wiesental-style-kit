#!/usr/bin/env python3
"""Regressionen aus der zweiten Code-Prüfung (ZIP schreiben, Rückgängig,
Ziehen, Bild-Bomben, ISO bereitstellen, OneDrive-Status) – ohne Fenster,
aber nur auf dem Test-Bildschirm gestartet:

    Xvfb :101 -screen 0 1600x1000x24 -nolisten tcp &
    DISPLAY=:101 FILEFENSTER_CONFIG_DIR=<scratch>/cfg python3 dev/test_review2.py

Testordner: ~/.cache/filefenster-review2. Nichts wird wirklich eingehängt
(UDisks wird simuliert), nichts landet im Papierkorb.
"""
import io
import os
import resource
import shutil
import struct
import sys
import threading
import time
import zipfile
import zlib

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (z. B. DISPLAY=:101)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

from filefenster import archive, places  # noqa: E402

T = os.path.expanduser("~/.cache/filefenster-review2")
shutil.rmtree(T, ignore_errors=True)
os.makedirs(T)
ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("  ok  ", name)
    else:
        fail += 1
        print("  FEHLER", name)


# ----------------------------------------------------------------------
# Bild-Bomben (zuerst: der Speicherverbrauch des Prozesses ist noch klein)
# ----------------------------------------------------------------------
def bomb_png(path, w, h):
    """1-Bit-PNG: wenige KB auf der Platte, w×h×3 Byte im Speicher."""
    row = b"\x00" * (1 + (w + 7) // 8)
    co = zlib.compressobj(9)
    data = b"".join(co.compress(row) for _ in range(h)) + co.flush()

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0))
                 + chunk(b"IDAT", data) + chunk(b"IEND", b""))


print("Bild-Bomben")
from filefenster import detailspane, gallery, previewpane  # noqa: E402
bomb = os.path.join(T, "bombe.png")
bomb_png(bomb, 12000, 12000)                 # 144 MPixel ≈ 430 MB, Datei ≈ 17 KB
small = os.path.join(T, "normal.png")
bomb_png(small, 3000, 2000)
gb = Gio.File.new_for_path(bomb)
check("Vorschau lädt die Bombe nicht",
      previewpane._load_image(gb, 2048, False, threading.Event()) is None)
check("Galerie lädt die Bombe nicht", gallery._image_thumb(bomb, 256) is None)
pb, wh = detailspane._load_image(gb, 256, threading.Event())
check("Detailbereich lädt die Bombe nicht (Grösse bleibt bekannt)", pb is None and wh == (12000, 12000))
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
check(f"Speicher bleibt klein ({rss} MB)", rss < 250)
pb = previewpane._load_image(Gio.File.new_for_path(small), 2048, False, threading.Event())
check("normales grosses PNG geht weiter (Vorschau)", pb is not None and pb.get_width() == 2048)
pb = gallery._image_thumb(small, 256)
check("normales grosses PNG geht weiter (Galerie)", pb is not None and pb.get_width() == 256)


# ----------------------------------------------------------------------
# In ZIP-Dateien schreiben
# ----------------------------------------------------------------------
def mkzip(path, entries, prefix=b""):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        for n, d in entries:
            zf.writestr(n, d, compress_type=zipfile.ZIP_DEFLATED)
    with open(path, "wb") as fh:
        fh.write(prefix + bio.getvalue())
    return path


def loc(path, inner=""):
    return Gio.File.new_for_uri(places.archive_uri(Gio.File.new_for_path(path)) + inner)


def run(op, folder, sources=(), **kw):
    job = archive.ZipEditJob(op, folder, sources, **kw)
    job.run()                                # synchron, ohne Fortschrittsfenster
    return job


def parts_left(d):
    return [n for n in os.listdir(d) if n.endswith(".part")]


print("ZIP schreiben")
stub = b"#!/bin/sh\necho Startskript\nexit 0\n"
z = mkzip(os.path.join(T, "sfx.zip"), [("x.txt", "x")], prefix=stub)
j = run("mkdir", loc(z), [loc(z, "Neu")])
with open(z, "rb") as fh:
    head = fh.read(len(stub))
zf = zipfile.ZipFile(z)
check("Vorspann (selbstentpackend) bleibt erhalten",
      j.committed and head == stub and zf.testzip() is None and "Neu/" in zf.namelist())

real = os.path.join(T, "echt")
os.makedirs(os.path.join(real, "Inhalt"))
with open(os.path.join(real, "Inhalt", "f.txt"), "w") as fh:
    fh.write("f")
z = mkzip(os.path.join(real, "selbst.zip"), [("alt.txt", "alt")])
os.symlink(real, os.path.join(T, "verkn"))
zl = os.path.join(T, "verkn", "selbst.zip")
j = run("add", loc(zl), [Gio.File.new_for_path(real)], move=True)
check("Ordner mit der ZIP (über Verknüpfung) verschieben: abgelehnt, ZIP bleibt",
      not j.committed and j.errors and os.path.isfile(z)
      and zipfile.ZipFile(z).namelist() == ["alt.txt"]
      and os.path.isfile(os.path.join(real, "Inhalt", "f.txt")))

hard = os.path.join(T, "harter-link.zip")
os.link(z, hard)
j = run("add", loc(z), [Gio.File.new_for_path(hard)], move=True)
check("ZIP über harten Link in sich selbst: abgelehnt, nichts gelöscht",
      not j.committed and any(e[1] == archive._SELF for e in j.errors)
      and os.path.isfile(z) and os.path.isfile(hard))

lnk = os.path.join(T, "auf-echt")
os.symlink(real, lnk)
j = run("add", loc(z), [Gio.File.new_for_path(lnk)], move=True)
names = zipfile.ZipFile(z).namelist()
check("Verknüpfung als Quelle bleibt Verknüpfung (kein Fehlalarm)",
      j.committed and not j.errors and "auf-echt" in names and not os.path.lexists(lnk))

z = mkzip(os.path.join(T, "fremd.zip"), [("a.txt", "a")])
os.chmod(z, 0o640)
with open(os.path.join(T, "neu.txt"), "w") as fh:
    fh.write("neu")
job = archive.ZipEditJob("add", loc(z), [Gio.File.new_for_path(os.path.join(T, "neu.txt"))])
orig_write = job._write


def write_and_meddle(zout, item):
    orig_write(zout, item)
    with open(z, "ab") as fh:                # anderes Programm ändert die ZIP gleichzeitig
        fh.write(b"fremd")
job._write = write_and_meddle
job.run()
with open(z, "rb") as fh:
    tail = fh.read()[-5:]
check("gleichzeitig fremd geänderte ZIP wird nicht überschrieben",
      not job.committed and any(archive._CHANGED in e[1] for e in job.errors) and tail == b"fremd"
      and os.path.isfile(os.path.join(T, "neu.txt")))

z = mkzip(os.path.join(T, "rechte.zip"), [("a/x.txt", "1"), ("ab/y.txt", "2")])
os.chmod(z, 0o640)
j = run("rename", loc(z), [loc(z, "a")], new_name="c")
zf = zipfile.ZipFile(z)
check("Ordner umbenennen trifft nur den Ordner (nicht „ab“), Rechte bleiben",
      sorted(zf.namelist()) == ["ab/y.txt", "c/x.txt"] and zf.testzip() is None
      and (os.stat(z).st_mode & 0o777) == 0o640)


class _Stream(io.RawIOBase):              # nicht spulbar → Einträge mit Datenbeschreibung
    def __init__(self):
        self.b = bytearray()

    def writable(self):
        return True

    def write(self, d):
        self.b += d
        return len(d)


s = _Stream()
with zipfile.ZipFile(s, "w") as zf:
    zf.writestr("dd/eins.txt", "eins" * 100, compress_type=zipfile.ZIP_DEFLATED)
    zf.writestr("dd/zwei.txt", "zwei")
z = os.path.join(T, "dd.zip")
with open(z, "wb") as fh:
    fh.write(bytes(s.b))
run("rename", loc(z), [loc(z, "dd")], new_name="ee")
run("delete", loc(z, "ee"), [loc(z, "ee/zwei.txt")])
zf = zipfile.ZipFile(z)
check("Einträge mit Datenbeschreibung: umbenennen + löschen bleibt lesbar",
      zf.namelist() == ["ee/eins.txt"] and zf.testzip() is None and zf.read("ee/eins.txt") == b"eins" * 100)
check("keine .part-Hilfsdateien liegen geblieben", not parts_left(T) and not parts_left(real))


# ----------------------------------------------------------------------
# Rückgängig / Wiederholen
# ----------------------------------------------------------------------
print("Rückgängig")
from filefenster import fileops, undo  # noqa: E402
ERR, DELETED = [], []
fileops.show_errors = lambda parent, title, errors: ERR.append((title, errors))
real_delete = fileops.delete
fileops.delete = lambda files, **kw: DELETED.append([f.get_path() for f in files])
U = os.path.join(T, "undo")
os.makedirs(U)


def put(name, text, old=False):
    with open(os.path.join(U, name), "w") as fh:
        fh.write(text)
    if old:                                   # vorhandene Dateien sind älter als neu angelegte
        os.utime(os.path.join(U, name), (time.time() - 3600,) * 2)
    return Gio.File.new_for_path(os.path.join(U, name))


undo.reset()
a = put("A.txt", "original", old=True)
b = fileops.rename(a, "B.txt")
undo.undo()
check("Umbenennen rückgängig (unverändert) klappt",
      os.path.isfile(os.path.join(U, "A.txt")) and not os.path.exists(os.path.join(U, "B.txt")))
undo.redo()
os.remove(os.path.join(U, "B.txt"))          # anderes Programm: B gelöscht …
put("B.txt", "andere Datei")                  # … und gleichnamig neu angelegt
ERR.clear()
undo.undo()
check("Umbenennen rückgängig auf neu angelegte gleichnamige Datei: verweigert",
      os.path.isfile(os.path.join(U, "B.txt")) and not os.path.exists(os.path.join(U, "A.txt"))
      and ERR and not undo.can_undo() and not undo.can_redo())

undo.reset()
c = put("C.txt", "c", old=True)
undo.record(undo.Op("rename", [(Gio.File.new_for_path(os.path.join(U, "X.txt")), c)]))
undo.undo()                                   # → X.txt
os.rename(os.path.join(U, "X.txt"), os.path.join(U, "Y.txt"))
put("X.txt", "neu")
ERR.clear()
undo.redo()
check("Wiederholen nach Ersetzen der Datei: verweigert",
      ERR and os.path.isfile(os.path.join(U, "X.txt")) and not os.path.exists(os.path.join(U, "C.txt")))

undo.reset()
src = put("quelle.txt", "q")
dst = put("kopie.txt", "q", old=True)
undo.record(undo.Op("copy", [(src, dst)]))
os.remove(dst.get_path())
put("kopie.txt", "eigene neue Arbeit")
DELETED.clear()
ERR.clear()
undo.undo()
check("Kopieren rückgängig, Kopie inzwischen ersetzt: nichts gelöscht",
      not DELETED and ERR and os.path.isfile(dst.get_path()))
undo.record(undo.Op("copy", [(src, dst)]))
undo.undo()
check("Kopieren rückgängig (unverändert): Kopie in den Papierkorb",
      DELETED == [[dst.get_path()]])
fileops.delete = real_delete
undo.reset()


# ----------------------------------------------------------------------
# Ziehen und Ablegen
# ----------------------------------------------------------------------
print("Ziehen")
from filefenster import dnd  # noqa: E402


class Ctx:
    def __init__(self, acts):
        self.acts = acts

    def get_actions(self):
        return self.acts

    def get_suggested_action(self):
        return Gdk.DragAction.COPY


D = os.path.join(T, "dnd")
os.makedirs(os.path.join(D, "daten", "unter"))
os.symlink(os.path.join(D, "daten"), os.path.join(D, "verkn"))
acts = Gdk.DragAction.COPY | Gdk.DragAction.MOVE
dest = Gio.File.new_for_path(os.path.join(D, "verkn", "unter"))
check("Ordner in eigenen Unterordner (über Verknüpfung): nicht ablegbar",
      dnd.decide(Ctx(acts), [Gio.File.new_for_path(os.path.join(D, "daten"))], dest) == (0, 0))
os.symlink(os.path.join(D, "daten"), os.path.join(D, "link-auf-daten"))
check("Verknüpfung auf den Ordner darf hinein (bleibt Verknüpfung)",
      dnd.decide(Ctx(acts), [Gio.File.new_for_path(os.path.join(D, "link-auf-daten"))], dest)[0] != 0)
calls = []
real_fs_id = fileops._fs_id
fileops._fs_id = lambda g: calls.append(g.get_uri())
web = Gio.File.new_for_uri("https://beispiel.invalid/bild.png")
check("Link aus dem Browser: kopieren, ohne Netzabfrage",
      dnd.default_action([web], Gio.File.new_for_path(D)) == Gdk.DragAction.COPY
      and not any(u.startswith("https:") for u in calls))
fileops._fs_id = real_fs_id


# ----------------------------------------------------------------------
# ISO bereitstellen (UDisks simuliert)
# ----------------------------------------------------------------------
print("ISO bereitstellen")
from filefenster import diskimage  # noqa: E402


class FakeBus:
    def __init__(self, fs=False):
        self.calls, self.fs = [], fs

    def call_with_unix_fd_list_sync(self, *args):
        self.calls.append(args[3])
        return GLib.Variant("(o)", ("/org/freedesktop/UDisks2/block_devices/loop42",)), None

    def call_sync(self, name, obj, iface, method, params, *rest):
        self.calls.append((method, params.unpack() if params is not None else None))
        if method == "Mount":
            return GLib.Variant("(s)", ("/media/test/ISO",))
        return None


LOOP = "/org/freedesktop/UDisks2/block_devices/loop42"
iso = os.path.join(T, "abbild.iso")
with open(iso, "wb") as fh:
    fh.write(b"\0" * 4096)
saved = diskimage._bus, diskimage._objects, diskimage.time.sleep, diskimage._desktop_automount
diskimage.time.sleep = lambda s: None
diskimage._desktop_automount = lambda key="automount": False
bus = FakeBus()
diskimage._bus = lambda: bus
diskimage._objects = lambda b: {}
try:
    diskimage._mount_sync(iso)
    raised = False
except RuntimeError:
    raised = True
check("kein Dateisystem: Fehler und Loop-Gerät wieder gelöscht",
      raised and bus.calls[0] == "LoopSetup" and ("Delete", ({},)) in bus.calls)
bus = FakeBus()
diskimage._objects = lambda b: {LOOP: {diskimage.UD + ".Filesystem": {"MountPoints": []},
                                       diskimage.UD + ".Block": {"IdType": "iso9660"}}}
target, _opened = diskimage._mount_sync(iso)
mount = [c for c in bus.calls if c[0] == "Mount"]
check("schreibgeschützt eingehängt (ro), Autoclear danach",
      target == "/media/test/ISO" and mount and mount[0][1] == ({"options": "ro"},)
      and bus.calls[-1][0] == "SetAutoclear" and not any(c[0] == "Delete" for c in bus.calls))
fifo = os.path.join(T, "fifo.iso")
os.mkfifo(fifo)
res = []


def _try_fifo():
    try:
        diskimage._mount_sync(fifo)
        return "eingehängt"
    except RuntimeError as exc:
        return str(exc)


th = threading.Thread(target=lambda: res.append(_try_fifo()), daemon=True)
th.start()
th.join(3)
check("FIFO „x.iso“ blockiert nicht und wird abgelehnt",
      not th.is_alive() and res == ["Das Abbild ist keine normale Datei."])
diskimage._bus, diskimage._objects, diskimage.time.sleep, diskimage._desktop_automount = saved


# ----------------------------------------------------------------------
# OneDrive-Status: Netzwerkpfade nie über FUSE auflösen
# ----------------------------------------------------------------------
print("OneDrive-Status")
from filefenster import cloudstatus  # noqa: E402
seen = []
real_realpath = os.path.realpath
cloudstatus.os.path.realpath = lambda p: seen.append(p) or real_realpath(p)
try:
    cloudstatus._locate(cloudstatus._FUSE + "smb-share:server=x,share=y/datei.txt")
finally:
    cloudstatus.os.path.realpath = real_realpath
check("gvfs-FUSE-Pfad: kein realpath im GTK-Thread", not seen)

shutil.rmtree(T, ignore_errors=True)
print(f"{ok}/{ok + fail} Prüfungen bestanden")
sys.exit(1 if fail else 0)
