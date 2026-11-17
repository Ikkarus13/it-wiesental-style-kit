#!/usr/bin/env python3
"""Angriffs-Prüfung: feindliche Dateien, Namen und Server-Antworten.

Nichts wird wirklich ausgeführt oder geöffnet: host._ask/_run_program/
_start_app/_launch_default werden ersetzt; Marker-Skripte würden beim
Ausführen eine Datei anlegen – die darf es am Ende nicht geben.
Arbeitet nur unter ~/.cache/filefenster-angriff. FILEFENSTER_CONFIG_DIR setzen!

    FILEFENSTER_CONFIG_DIR=… python3 dev/test_angriff.py
"""
import os
import shutil
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FILEFENSTER_NO_UNIQUE", "1")
BASE = os.path.expanduser("~/.cache/filefenster-angriff")
os.environ["XDG_DATA_HOME"] = os.path.join(BASE, "xdg")     # vor dem ersten GLib-Aufruf

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

from filefenster import cloudstatus, dnd, fileops, host, places  # noqa: E402

ok = total = 0


def check(name, cond):
    global ok, total
    total += 1
    ok += bool(cond)
    print(("OK   " if cond else "FEHL ") + name)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)
    return path


# ---------------------------------------------------------------- Aufzeichnung
log = []
answer = {"r": 2}


def fake_ask(parent, text, title, buttons, default=0):
    log.append(("ask", text, title, list(buttons)))
    r = answer["r"]
    return r if r < len(buttons) else len(buttons) - 1


host._ask = fake_ask
host._run_program = lambda path: log.append(("run", path)) or True
host._start_app = lambda app, files: log.append(("app", app, [f.get_uri() for f in files])) or True
host._launch_default = lambda gf: log.append(("default", gf.get_uri())) or True


def launch(path_or_uri, r):
    log.clear()
    answer["r"] = r
    gf = (Gio.File.new_for_uri(path_or_uri) if "://" in path_or_uri
          else Gio.File.new_for_path(path_or_uri))
    host.launch_file(gf, None)
    return [e[0] for e in log]


def kinds(*k):
    return [e for e in log if e[0] in k]


shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE)
DL = os.path.join(BASE, "Downloads")
MARK = os.path.join(BASE, "AUSGEFUEHRT")
marker_cmd = f"touch '{MARK}'"

# 1. ELF-Programm aus den Downloads
elf = os.path.join(DL, "rechnung")
os.makedirs(DL)
shutil.copy("/bin/true", elf)
os.chmod(elf, 0o755)
check("ELF wird als Programm erkannt", host._probe(Gio.File.new_for_path(elf))["kind"] == "native")
check("ELF: Rückfrage, Abbrechen startet nichts", launch(elf, 1) == ["ask"])
check("ELF: erst „Ausführen“ startet", launch(elf, 0) == ["ask", "run"])
os.chmod(elf, 0o644)
ev = launch(elf, 0)
check("ELF ohne x-Bit: nur Hinweis, kein Start", ev == ["ask"] and kinds("ask")[0][3] == ["OK"])

# 2. Skript mit Shebang und x-Bit, Name mit Richtungszeichen (RLO)
spoof = os.path.join(DL, "rechnung‮fdp.sh")
write(spoof, f"#!/bin/sh\n{marker_cmd}\n", 0o755)
ev = launch(spoof, 2)
asks = kinds("ask")
check("Skript: Rückfrage mit Ausführen/Anzeigen/Abbrechen",
      asks and asks[0][3] == ["Ausführen", "Anzeigen", "Abbrechen"] and ev == ["ask"])
check("Skript: RLO im Dialog unschädlich gemacht",
      asks and "‮" not in asks[0][1] and "�" in asks[0][1])
ev = launch(spoof, 1)
check("Skript: „Anzeigen“ öffnet nur einen Editor (oder Hinweis)",
      "run" not in ev and "default" not in ev)

# 3. .desktop aus einem unsicheren Ort
evil = write(os.path.join(DL, "Rechnung.pdf.desktop"),
             "[Desktop Entry]\nType=Application\nName=Rechnung.pdf\n"
             f"Exec=sh -c \"{marker_cmd}\"\nIcon=x-office-document\n", 0o755)
ev = launch(evil, 2)
asks = kinds("ask")
check("App-Starter (unsicher): Vertrauens-Rückfrage, nichts gestartet",
      ev == ["ask"] and asks[0][2] == "Nicht vertrauenswürdiger App-Starter")
check("App-Starter: Befehl wird angezeigt", asks and "touch" in asks[0][1])
ev = launch(evil, 1)
check("App-Starter: „Anzeigen“ startet ihn nicht",
      not [e for e in log if e[0] == "app" and not e[2]] and "default" not in ev)

# 4. .desktop aus dem Anwendungsordner (Startmenü) startet direkt
good = write(os.path.join(BASE, "xdg", "applications", "gut.desktop"),
             "[Desktop Entry]\nType=Application\nName=Gut\nExec=true\n")
ev = launch(good, 2)
check("App-Starter aus ~/.local/share/applications startet ohne Rückfrage", ev == ["app"])

# 5. Netzwerk/Archiv: nie ausführen
ev = launch("smb://server/freigabe/werkzeug.AppImage", 0)
check("AppImage von SMB: nur Hinweis", ev == ["ask"] and kinds("ask")[0][3] == ["OK"])
ev = launch("smb://server/freigabe/start.desktop", 1)
check("App-Starter von SMB: nur Anzeigen/Abbrechen", ev == ["ask"] and
      kinds("ask")[0][3] == ["Anzeigen", "Abbrechen"])


class FakeApp:
    def get_display_name(self): return "Wine"
    def get_executable(self): return "wine"
    def get_id(self): return "wine.desktop"


log.clear()
answer["r"] = 0
host._guarded(Gio.File.new_for_uri("archive://x/setup.exe"),
              {"kind": "foreign", "ct": "application/x-ms-dos-executable", "path": None,
               "local": False, "exe": False, "app": FakeApp()}, None)
check(".exe aus einem Archiv: nie an Wine übergeben", [e[0] for e in log] == ["ask"])
log.clear()
host._guarded(Gio.File.new_for_path(os.path.join(DL, "setup.exe")),
              {"kind": "foreign", "ct": "application/x-ms-dos-executable", "path": "x",
               "local": True, "exe": False, "app": FakeApp()}, None)
check(".exe lokal: „Ausführen“ erst nach Rückfrage", [e[0] for e in log] == ["ask", "default"])


class App:
    def __init__(self, exe, aid):
        self.e, self.i = exe, aid
    def get_executable(self): return self.e
    def get_id(self): return self.i


check("Interpreter als Standard-App erkannt",
      host._runs_code(App("/usr/bin/python3.12", "py.desktop"))
      and host._runs_code(App("flatpak", "com.usebottles.bottles.desktop"))
      and not host._runs_code(App("xed", "org.x.editor.desktop")))

# 6. Normale Datei: wie bisher ohne Rückfrage
txt = write(os.path.join(DL, "notiz.txt"), "hallo\n")
check("Textdatei öffnet ohne Rückfrage", launch(txt, 2) == ["default"])
check("Kein Marker-Skript wurde ausgeführt", not os.path.exists(MARK))

# ---------------------------------------------------------------- Namen/Pfade
check("valid_name lehnt .., ., a/b und leer ab",
      not any(fileops.valid_name(n) for n in ("..", ".", "a/b", "", "x\0y"))
      and fileops.valid_name("ok.txt"))
src = write(os.path.join(BASE, "quelle", "datei.txt"), "x")
dest = os.path.join(BASE, "ziel")
os.makedirs(dest)
job = fileops.TransferJob([Gio.File.new_for_path(src)], Gio.File.new_for_path(dest))
bad = Gio.FileInfo()
bad.set_name("../ausbruch")
bad.set_display_name("../ausbruch")
bad.set_file_type(Gio.FileType.REGULAR)
job._top(Gio.File.new_for_path(src), bad)
check("Servername „../ausbruch“ beim Kopieren abgelehnt",
      job.errors and not os.path.exists(os.path.join(BASE, "ausbruch")))

# Ordner mit einem Kind „..“ (feindlicher Server) – kein Weg nach oben
sdir = os.path.join(BASE, "qordner")
os.makedirs(sdir)
dinfo = Gio.File.new_for_path(sdir).query_info(fileops.SCAN_ATTRS, fileops.NOFOLLOW, None)
orig_list = fileops._list_children
up = Gio.FileInfo()
up.set_name("..")
up.set_file_type(Gio.FileType.DIRECTORY)
fileops._list_children = lambda g, c: [up]
job = fileops.TransferJob([], Gio.File.new_for_path(dest))
try:
    job._transfer(Gio.File.new_for_path(sdir), dinfo,
                  Gio.File.new_for_path(os.path.join(dest, "qordner")), "new")
    raised = False
except Exception:
    raised = True
finally:
    fileops._list_children = orig_list
check("Kind „..“ beim Kopieren übersprungen (Fehler statt Elternordner)",
      not raised and len(job.errors) == 1 and os.listdir(os.path.join(dest, "qordner")) == [])

# Einfügen eines Ordners in den eigenen Unterordner über einen Symlink
a = os.path.join(BASE, "A")
os.makedirs(os.path.join(a, "B"))
write(os.path.join(a, "B", "f.txt"), "x")
link = os.path.join(BASE, "L")
os.symlink(os.path.join(a, "B"), link)
job = fileops.TransferJob([Gio.File.new_for_path(a)], Gio.File.new_for_path(link))
job.run()
check("Ordner nicht über Symlink in eigenen Unterordner kopiert",
      job.errors and "Unterordner" in job.errors[0][1]
      and not os.path.exists(os.path.join(a, "B", "A")))
check("dnd erkennt dasselbe", dnd._inside(Gio.File.new_for_path(link), Gio.File.new_for_path(a)))

# TOCTOU beim endgültigen Löschen: Ordner wird während des Löschens gegen
# einen Symlink nach draussen getauscht
outside = os.path.join(BASE, "wertvoll")
write(os.path.join(outside, "behalten.txt"), "wichtig")
tree = os.path.join(BASE, "tmp", "baum")
write(os.path.join(tree, "sub", "a.txt"), "x")
os.symlink(outside, os.path.join(tree, "link"))   # vorhandener Symlink nach draussen
real_listdir = fileops.os.listdir
swapped = []


def racing_listdir(fd):
    names = real_listdir(fd)
    if "a.txt" in names and not swapped:    # Angreifer tauscht „sub“ aus
        os.rename(os.path.join(tree, "sub"), os.path.join(BASE, "tmp", "sub-echt"))
        os.symlink(outside, os.path.join(tree, "sub"))
        swapped.append(1)
    return names


fileops.os.listdir = racing_listdir
try:
    fileops._delete_recursive(Gio.File.new_for_path(tree))
except GLib.Error:
    pass
finally:
    fileops.os.listdir = real_listdir
check("Löschen folgt keinem untergeschobenen Symlink",
      swapped and os.path.exists(os.path.join(outside, "behalten.txt")))
plain = os.path.join(BASE, "tmp", "normal")
write(os.path.join(plain, "x", "y", "z.txt"), "x")
n = []
fileops._delete_recursive(Gio.File.new_for_path(plain), None, lambda: n.append(1))
check("Normales endgültiges Löschen funktioniert (4 Elemente)",
      not os.path.exists(plain) and len(n) == 4)

# ---------------------------------------------------------------- Fremde Eingaben
try:
    cloudstatus._on_changed(None, ":1.99", "/", "x", "Changed", GLib.Variant("(s)", ("/etc",)))
    cloudstatus._on_changed(None, ":1.99", "/", "x", "Changed", None)
    fine = True
except Exception:
    fine = False
check("OneDrive-Signal mit falschem Typ wird ignoriert", fine)

acc = write(os.path.join(BASE, "accounts.json"),
            '{"accounts": [{"kind": "library", "mount_path": 42}, "x", null]}')
places.ODM_ACCOUNTS = acc
try:
    places.org_dirs()
    fine = True
except Exception:
    fine = False
check("Kaputte accounts.json bringt die Orte nicht zum Absturz", fine)
check("Steuerzeichen im Namen werden sichtbar ersetzt",
      places.safe_text("a\nb‮c⁦") == "a�b�c�" and places.safe_text("Grösse") == "Grösse")

# ---------------------------------------------------------------- Ziehen aus dem Browser
class FakeDrag:
    def __init__(self, targets, acts):
        self.t, self.a = targets, acts
    def list_targets(self): return [Gdk.Atom.intern(t, False) for t in self.t]
    def get_actions(self): return self.a
    def get_suggested_action(self): return dnd.MOVE


src_dir = os.path.join(BASE, "quelle")
os.makedirs(src_dir, exist_ok=True)
dst_dir = os.path.join(BASE, "ziel")
os.makedirs(dst_dir, exist_ok=True)
secret = write(os.path.join(src_dir, "geheim.txt"), "x")
sf, df = [Gio.File.new_for_path(secret)], Gio.File.new_for_path(dst_dir)
browser = FakeDrag(["text/x-moz-url", "text/uri-list", "text/plain"], dnd.ACTIONS)
other = FakeDrag(["text/uri-list"], dnd.COPY | dnd.MOVE)
check("Firefox-Ziehen erkannt", dnd.from_browser(browser) and not dnd.from_browser(other))
check("Chrome-Ziehen erkannt",
      dnd.from_browser(FakeDrag(["chromium/x-renderer-taint", "text/uri-list"], dnd.COPY)))
check("Aus dem Browser gleicher Datenträger: kopieren statt verschieben",
      dnd.decide(browser, sf, df)[0] == dnd.COPY and dnd.decide(other, sf, df)[0] == dnd.MOVE)
check("Aus dem Browser in den Papierkorb: nicht erlaubt",
      dnd.decide(browser, sf, Gio.File.new_for_uri("trash:///"))[0] == 0)
check("Browser bietet nur Verschieben: nichts ablegen",
      dnd.decide(FakeDrag(["text/x-moz-url", "text/uri-list"], dnd.MOVE), sf, df)[0] == 0)

# ---------------------------------------------------------------- 7z-Vorprüfung
import subprocess  # noqa: E402
import threading  # noqa: E402
import zipfile  # noqa: E402
from gi.repository import Gtk  # noqa: E402
from filefenster import archive, folderview  # noqa: E402

if shutil.which("7z"):
    zdir = os.path.join(BASE, "7z")
    os.makedirs(os.path.join(zdir, "src", "sub"), exist_ok=True)
    write(os.path.join(zdir, "src", "sub", "f.txt"), "hallo")
    os.symlink("/etc/hostname", os.path.join(zdir, "src", "aussen"))
    links7z = os.path.join(zdir, "links.7z")
    subprocess.run(["7z", "a", "-snl", "-bso0", "-bsp0", links7z, "."],
                   cwd=os.path.join(zdir, "src"), check=True)
    plain7z = os.path.join(zdir, "normal.7z")
    subprocess.run(["7z", "a", "-bso0", "-bsp0", plain7z, "sub"],
                   cwd=os.path.join(zdir, "src"), check=True)
    evil = os.path.join(zdir, "boese.zip")
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("ok.txt", "x")
        zf.writestr("../../ausbruch.txt", "x")
    check("7z-Vorprüfung: normales Archiv geht durch", archive.sevenzip_precheck(plain7z) is None)
    check("7z-Vorprüfung: Symlinks mit altem 7z (p7zip) abgewiesen",
          "Verknüpfungen" in (archive.sevenzip_precheck(links7z, links_safe=False) or ""))
    check("7z-Vorprüfung: Symlinks mit 7-Zip ab 21 erlaubt",
          archive.sevenzip_precheck(links7z, links_safe=True) is None)
    check("7z-Vorprüfung: „../“ im Archiv abgewiesen",
          "Unzulässiger Pfad" in (archive.sevenzip_precheck(evil, links_safe=True) or ""))
    archive._7Z_LINKS_SAFE = False          # p7zip simulieren: Entpacken bricht ab
    out = os.path.join(zdir, "ziel")
    job = archive.ExtractJob(Gio.File.new_for_path(links7z), Gio.File.new_for_path(out),
                             lambda *a: None)
    job.run()
    check("p7zip simuliert: nichts extrahiert, Fehler gemeldet",
          job.errors and not os.listdir(out) if os.path.isdir(out) else bool(job.errors))
    archive._7Z_LINKS_SAFE = None
else:
    print("Hinweis: 7z fehlt – 7z-Vorprüfung übersprungen")


def spin(cond, secs=5):
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + secs * 1_000_000
    while not cond() and GLib.get_monotonic_time() < end:
        ctx.iteration(False)
    return cond()


# ---------------------------------------------------------------- Einfügen von Text
cb = fileops.clipboard()
cb._own = False
Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(secret, -1)
spin(lambda: False, 0.3)
got = []
cb.read_files(lambda uris, cut, trusted: got.append((uris, cut, trusted)), with_trust=True)
spin(lambda: got)
check("Pfad als Text: erkannt, aber als nicht vertrauenswürdig markiert",
      got and got[0][0] == [Gio.File.new_for_path(secret).get_uri()] and got[0][2] is False)
asked = []
fileops.confirm_foreign = lambda uris, parent: asked.append(list(uris)) or False
dest_dir = os.path.join(BASE, "einfuegen")
os.makedirs(dest_dir, exist_ok=True)
fileops.paste_into(Gio.File.new_for_path(dest_dir))
spin(lambda: asked)
spin(lambda: False, 0.5)
check("Einfügen von Text-Pfaden fragt zuerst; „Abbrechen“ kopiert nichts",
      asked and not os.listdir(dest_dir))
check("Browser-Formate erkannt (auch für die Zwischenablage)",
      fileops.browser_targets(["text/uri-list", "text/x-moz-url"])
      and not fileops.browser_targets(["x-special/gnome-copied-files", "text/uri-list"]))

# ---------------------------------------------------------------- Netzwerkorte: kein Hängen
class SlowFolder:
    _gfile = Gio.File.new_for_path(src_dir)

    def _slow_location(self):
        return True


calls = []
r = folderview.FolderView._query_infos(
    SlowFolder(), [Gio.File.new_for_path(secret), Gio.File.new_for_path(src_dir + "/fehlt")],
    lambda res, failed: calls.append((threading.current_thread().name, len(res), len(failed))))
check("Netzwerkort: Abfrage läuft nicht im Hauptthread (kehrt sofort zurück)",
      r is False and not calls)
check("… Ergebnis kommt im Hauptthread an (1 gefunden, 1 fehlt)",
      spin(lambda: calls) and calls[0] == (threading.main_thread().name, 1, 1))

# ---------------------------------------------------------------- Funde der dritten Prüfung
import struct  # noqa: E402
import zlib  # noqa: E402
from filefenster import sandbox  # noqa: E402

pyfile = write(os.path.join(BASE, "werkzeug.py"), "print('hallo')\n")
check("Skript ohne x-Bit ist kein „Programm“ (öffnet normal bzw. mit Rückfrage)",
      host._probe(Gio.File.new_for_path(pyfile))["kind"] != "native")

zsrc = os.path.join(BASE, "zipquelle")
os.makedirs(zsrc, exist_ok=True)
write(os.path.join(zsrc, "alt.txt"), "alt")
os.utime(os.path.join(zsrc, "alt.txt"), (0, 0))         # vor 1980
os.symlink(secret, os.path.join(zsrc, "notizen.txt"))   # zeigt auf „geheim.txt“
os.mkfifo(os.path.join(zsrc, "pipe"))
zout = os.path.join(BASE, "paket.zip")
zjob = archive.ZipJob([Gio.File.new_for_path(zsrc)], Gio.File.new_for_path(zout), lambda *a: None)
zt = threading.Thread(target=zjob.run, daemon=True)
zt.start()
zt.join(20)
check("Komprimieren hängt nicht an einer Pipe", not zt.is_alive())
with zipfile.ZipFile(zout) as zf:
    link_info = zf.getinfo("zipquelle/notizen.txt")
    link_data = zf.read(link_info)
    names = zf.namelist()
check("Verknüpfung als Verknüpfung gespeichert, nie der Inhalt ihres Ziels",
      stat.S_ISLNK(link_info.external_attr >> 16) and link_data == os.fsencode(secret))
check("Datei von vor 1980 trotzdem im ZIP, Pipe übersprungen (mit Meldung)",
      "zipquelle/alt.txt" in names and "zipquelle/pipe" not in names
      and any("Pipe" in str(m) for _n, m in zjob.errors))


class TInfo:
    def __init__(self, uri):
        self.u = uri

    def get_attribute_string(self, _a):
        return self.u


home_t = GLib.filename_to_uri(os.path.join(GLib.get_user_data_dir(), "Trash", "files", "x"), None)
usb_t = "file:///media/anna/STICK/.Trash-1000/files/x"
check("Wiederherstellen: eigener Papierkorb vertraut",
      fileops._restore_target_ok(TInfo(home_t), "/home/anna/x.txt")[0])
check("Wiederherstellen: fremder Papierkorb darf nicht aus dem Datenträger heraus",
      not fileops._restore_target_ok(
          TInfo(usb_t), "/media/anna/STICK/../../../home/anna/.config/autostart/x.desktop")[0]
      and fileops._restore_target_ok(TInfo(usb_t), "/media/anna/STICK/Ordner/x.txt")[0])


def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)


bomb = os.path.join(BASE, "bombe.png")
with open(bomb, "wb") as fh:
    fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 6, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(b"\0" * 64)) + chunk(b"IEND", b""))
check("Dateiliste: Bildbombe (30000×30000) wird nicht dekodiert",
      folderview._load_scaled(bomb, 64) is None)

if shutil.which("7z"):
    nl = os.path.join(BASE, "zeilen.zip")
    with zipfile.ZipFile(nl, "w") as zf:
        zf.writestr("a\n../../x.txt", "x")
    check("7z-Vorprüfung: Zeilenumbruch im Namen abgewiesen",
          "Zeilenumbruch" in (archive.sevenzip_precheck(nl, links_safe=True) or "")
          or "Unzulässiger Pfad" in (archive.sevenzip_precheck(nl, links_safe=True) or ""))

old_max = sandbox.MAX_OUTPUT
sandbox.MAX_OUTPUT = 1 << 20
t0 = GLib.get_monotonic_time()
res = sandbox.run(["/usr/bin/yes"], timeout=15)
sandbox.MAX_OUTPUT = old_max
check("Hilfsprogramm mit endloser Ausgabe wird früh abgebrochen",
      res is None and GLib.get_monotonic_time() - t0 < 10_000_000)
check("Gross/klein-Umbenennen: dieselbe Datei erkannt, andere nicht",
      folderview._same_file(Gio.File.new_for_path(secret), Gio.File.new_for_path(secret))
      and not folderview._same_file(Gio.File.new_for_path(secret), Gio.File.new_for_path(pyfile)))

# ---------------------------------------------------------------- Funde der zweiten Fable-Runde
from filefenster import columns, config as cfgmod, folderoptions, propsdialog  # noqa: E402

bad = os.fsdecode(b"datei\xff.txt")      # ungültiges UTF-8 → Surrogat
check("Ungültiges UTF-8 im Namen wird anzeigbar (keine Surrogate mehr)",
      "�" in places.safe_text(bad) and GLib.markup_escape_text(places.safe_text(bad)))
check("Unsinnige Zeitstempel stürzen nicht ab",
      places.format_date(99999999999999) == "" and columns._date(99999999999999) == ""
      and propsdialog.format_long_date(99999999999999) == "")
check("Kaputte settings.json: falsche Typen werden ignoriert",
      not cfgmod._plausible("window_size", None) and not cfgmod._plausible("window_size", [1, 2, 3])
      and not cfgmod._plausible("nav_width", "240") and cfgmod._plausible("nav_width", 300)
      and cfgmod._plausible("columns", ["name", "size"]) and cfgmod._plausible("pinned", None))
check("Häufige Ordner: kaputte Einträge stören nicht",
      folderoptions._num("x") == 0 and folderoptions._visit_key(["a", None]) == (0, 0))

# ---------------------------------------------------------------- Funde der dritten Fable-Runde
import datetime  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from filefenster import metadata, navrow, update_ui  # noqa: E402

fifo_pdf = os.path.join(BASE, "falle.pdf")
os.mkfifo(fifo_pdf)
res3 = []
th = threading.Thread(target=lambda: res3.append(metadata._read(fifo_pdf, "application/pdf")),
                      daemon=True)
th.start()
th.join(5)
check("Metadaten: Pipe namens „.pdf“ blockiert den Leser nicht", res3 == [{}])
try:
    navrow.natural_key("Ordner 5²")
    places.natural_key_str("①")
    fine = True
except Exception:
    fine = False
check("Sortierung: hochgestellte/eingekreiste Ziffern stürzen nicht ab", fine)
check("Gruppieren nach Datum: unsinniger Zeitstempel landet in „Vor langer Zeit“",
      folderview._date_group(2 ** 40, datetime.date.today()) == 8)
badb = os.path.join(os.fsencode(BASE), b"ordner\xff")
os.makedirs(badb, exist_ok=True)
crumbs = places.breadcrumbs(GLib.filename_to_uri(os.fsdecode(badb), None))
check("Adressleiste: Ordnername mit ungültigem UTF-8 anzeigbar",
      crumbs and all(not any("\ud800" <= c <= "\udfff" for c in lab) for lab, _u in crumbs))
sess = os.path.join(BASE, "sitzung.json")
for bad_data in ({"time": "abc", "windows": []},
                 {"time": time.time(), "windows": "x"},
                 {"time": time.time(), "windows": [1, None, {"tabs": 5}]}):
    with open(sess, "w") as fh:
        json.dump(bad_data, fh)
    try:
        r = update_ui.restore_session(None, sess)
        fine = r is False
    except Exception:
        fine = False
    if not fine:
        break
check("Kaputte Sitzungsdatei nach Update: normaler Start statt Absturz", fine)
check("Sitzung: Passwort aus Netzwerkadresse entfernt",
      "geheim" not in places.without_password("smb://anna:geheim@server/freigabe/"))

shutil.rmtree(BASE, ignore_errors=True)
print(f"{ok}/{total} Prüfungen bestanden")
sys.exit(0 if ok == total else 1)
