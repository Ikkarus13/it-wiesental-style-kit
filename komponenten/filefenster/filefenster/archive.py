"""ZIP und Archive wie im Windows-11-Explorer.

    compress(files, parent, on_done)   „In ZIP-Datei komprimieren“: Name.zip
                                       neben dem ersten Element, danach
                                       Umbenennen (macht die Dateiliste)
    extract_dialog(archive, parent, host)
                                       „Alle extrahieren…“: Dialog mit Ziel-
                                       ordner, danach Ordner anzeigen
    is_archive(name)                   kann „Alle extrahieren“?

Beide laufen als Hintergrundvorgang mit dem Fortschrittsfenster (Anhalten,
Abbrechen, Konflikt-Rückfrage) und lassen sich rückgängig machen. ZIP und
TAR (gz/bz2/xz) mit Python selbst, 7z/RAR über das Programm 7z, falls
installiert. Pfade aus dem Archiv werden geprüft (kein „../“ – Zip-Slip).

In eine geöffnete ZIP-Datei schreiben (wie Windows; nur eine lokale .zip,
nicht verschachtelt, TAR/7z/RAR/CAB bleiben schreibgeschützt):
    writable(loc)                      darf man an diesem archive://-Ort schreiben?
    zip_add / zip_delete / zip_rename / zip_mkdir
                                       Hintergrundvorgang (ZipEditJob), danach
                                       hängt remount() das Archiv neu ein und
                                       lädt die Ansichten neu (gvfs merkt sich
                                       sonst den alten Inhalt). Ohne Rückgängig.
"""
import bisect
import errno
import os
import secrets
import shutil
import stat
import struct
import subprocess
import tarfile
import threading
import time
import weakref
import zipfile

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import fileops, places

_TAR = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
_7Z = (".7z", ".rar", ".cab")      # über 7z bzw. gvfs (libarchive)
CHUNK = 1 << 20


def _ext(name):
    low = name.lower()
    for e in sorted(_TAR + _7Z + (".zip",), key=len, reverse=True):
        if low.endswith(e):
            return e
    return ""


# ======================================================================
# Geöffnete Archive (gvfs archive://): aushängen, wenn keine Ansicht sie zeigt
# ======================================================================
_shown = {}      # id(Ansicht) -> Wurzel-URIs der gezeigten Archive (innen → aussen)
_views = {}      # id(Ansicht) -> weakref der Ansicht (neu laden nach dem Schreiben)


def _roots(uri):
    """Wurzeln aller Archive, die für uri eingehängt sein müssen – bei einem
    ZIP in einer CAB auch die äussere CAB, sonst fehlen beim Lesen die Daten."""
    roots, arc = [], places.archive_parts(uri)
    while arc:
        roots.append(arc[1])
        arc = places.archive_parts(arc[0].get_uri())
    return roots


_seen = set()    # Archive, die dieser Prozess schon gezeigt hat


def _mounted(root):
    try:
        Gio.File.new_for_uri(root).find_enclosing_mount(None)
        return True
    except GLib.Error:
        return False


def track(view, uri):
    old = _shown.pop(id(view), [])
    new = _roots(uri)
    # Ein Archiv-Mount, den dieser Prozess nicht angelegt hat (z. B. übrig, weil
    # FileFenster beim letzten Mal mit offenem ZIP beendet wurde), kann eine
    # inzwischen ersetzte Datei zeigen – gvfs liefert dann alten Inhalt bzw.
    # Lesefehler. Darum einmal frisch einhängen (äusserstes Archiv genügt).
    fresh = [r for r in new if r not in _seen]
    _seen.update(new)
    stale = [r for r in fresh if _mounted(r)]
    if stale:
        GLib.idle_add(lambda r=stale[-1]: remount(r) and False)
    if new:
        _shown[id(view)] = new
        _views[id(view)] = weakref.ref(view)
    else:
        _views.pop(id(view), None)
    for i, root in enumerate(r for r in old if r not in new):   # innen zuerst
        GLib.timeout_add(1500 + 600 * i, _maybe_unmount, root)


def unmount_all(timeout=3.0):
    """Beim Beenden: alle Archive aushängen, die dieser Prozess gezeigt hat –
    sonst bleiben gvfs-Mounts übrig und zeigen nach einem Ersetzen der Datei
    alten Inhalt (Lesefehler). Wartet höchstens timeout Sekunden."""
    import time
    pending = []
    for root in sorted(_seen, key=len, reverse=True):     # innere zuerst
        try:
            mount = Gio.File.new_for_uri(root).find_enclosing_mount(None)
        except GLib.Error:
            continue
        box = {"done": False}

        def done(m, res, box=box):
            try:
                m.unmount_with_operation_finish(res)
            except GLib.Error:
                pass
            box["done"] = True
        mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, done)
        pending.append(box)
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while any(not b["done"] for b in pending) and time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.01)


def untrack(view):
    _views.pop(id(view), None)
    for i, root in enumerate(_shown.pop(id(view), [])):
        GLib.timeout_add(100 + 600 * i, _maybe_unmount, root)


def _maybe_unmount(root):
    if any(root in roots for roots in _shown.values()):
        return False
    try:
        mount = Gio.File.new_for_uri(root).find_enclosing_mount(None)
    except GLib.Error:
        return False

    def done(m, res):
        try:
            m.unmount_with_operation_finish(res)
        except GLib.Error:
            pass
    mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, done)
    return False


def is_archive(name):
    e = _ext(name)
    return bool(e) and (e not in _7Z or shutil.which("7z") is not None)


def _stem(name):
    e = _ext(name)
    return name[:-len(e)] if e else os.path.splitext(name)[0]


def _part_file(directory, name):
    """Neue, leere Hilfsdatei „.Name.xxxxxxxx.part“ in directory – exklusiv
    angelegt (überschreibt nie eine vorhandene Datei, folgt keinem Link,
    zwei Vorgänge kommen sich nicht in die Quere). → (Dateiobjekt, Pfad)"""
    stem = os.fsdecode(os.fsencode(name)[:120])
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    for _i in range(100):
        path = os.path.join(directory, f".{stem}.{secrets.token_hex(4)}.part")
        try:
            fd = os.open(path, flags, 0o666)
        except FileExistsError:
            continue
        return os.fdopen(fd, "w+b"), path
    raise FileExistsError(f"Keine Hilfsdatei in {directory} möglich.")


def _is_root(name):
    """Eintrag „./“ bzw. „.“ (Wurzel des Archivs, z. B. bei tar -C ordner .)."""
    return all(p in ("", ".") for p in name.replace("\\", "/").split("/"))


def _safe_join(dest, name):
    """Zielpfad für einen Archiveintrag oder None (absolut, '..')."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return os.path.join(dest, *parts)


def _fake_info(name, size, mtime):
    """Gio.FileInfo für die Konflikt-Rückfrage (Quelle im Archiv)."""
    info = Gio.FileInfo()
    info.set_display_name(name)
    info.set_name(name)
    info.set_file_type(Gio.FileType.REGULAR)
    info.set_size(size)
    try:
        info.set_modification_date_time(GLib.DateTime.new_from_unix_local(int(mtime)))
    except (TypeError, ValueError, OverflowError):
        pass
    return info


# ======================================================================
# Komprimieren
# ======================================================================
class ZipJob(fileops._Job):
    kind = "zip"

    def __init__(self, sources, target, on_done):
        super().__init__()
        self.sources = list(sources)
        self.dest = target          # Gio.File der ZIP-Datei
        self.on_done = on_done
        self.created = []

    def _call_done(self):
        if self.on_done:
            self.on_done(self.created, self.errors)

    def _reserve(self):
        """Die ZIP-Datei gleich leer anlegen (wie Windows): ein zweiter Vorgang
        nimmt dann einen anderen Namen, und am Ende ersetzt os.replace nur
        diesen Platzhalter – nie eine inzwischen entstandene fremde Datei."""
        folder, name = self.dest.get_parent(), self.dest.get_basename()
        for _i in range(100):
            path = self.dest.get_path()
            if not path:
                raise ValueError("Nur lokale Dateien können komprimiert werden.")
            try:
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666))
                return path, os.lstat(path)
            except FileExistsError:
                self.dest = fileops.numbered_child(folder, name)
        raise FileExistsError(f"„{name}“ ist bereits vorhanden.")

    def _add_entry(self, zf, path, arc):
        if arc.endswith("/"):
            st = os.lstat(path)
            zi = zipfile.ZipInfo(arc, _dos_time(st.st_mtime))
            zi.external_attr = ((stat.S_IFDIR | (st.st_mode & 0o7777)) << 16) | 0x10
            zf.writestr(zi, b"")
            return
        # O_NOFOLLOW/O_NONBLOCK: zwischen Prüfung und Öffnen untergeschobene
        # Verknüpfung oder Pipe wird nicht gelesen (und blockiert nicht)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            st = os.lstat(path)
            zi = zipfile.ZipInfo(arc, _dos_time(st.st_mtime))
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(zi, os.fsencode(os.readlink(path)))
            return
        with os.fdopen(fd, "rb") as fin:
            st = os.fstat(fin.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise OSError(errno.EINVAL, "Keine normale Datei – übersprungen.")
            zi = zipfile.ZipInfo(arc, _dos_time(st.st_mtime))
            zi.external_attr = (stat.S_IFREG | (st.st_mode & 0o7777)) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.file_size = st.st_size
            with zf.open(zi, "w", force_zip64=st.st_size > 0x7FFFFFFF) as fout:
                while True:
                    self._checkpoint()
                    buf = fin.read(CHUNK)
                    if not buf:
                        break
                    fout.write(buf)
                    self.done_bytes += len(buf)

    def run(self):
        tmp = target = placeholder = None
        try:
            target, placeholder = self._reserve()
            plan = []      # (Pfad, Name im Archiv, Grösse)
            walk_err = (lambda exc: self.errors.append(
                (exc.filename or "", exc.strerror or str(exc))))

            def add(p, arc):
                """Verknüpfungen als Verknüpfung (nie den Inhalt ihres Ziels – sonst
                landet z. B. ~/.ssh/id_rsa im ZIP), Pipes/Geräte überspringen."""
                try:
                    st = os.lstat(p)
                except OSError as exc:
                    self.errors.append((p, exc.strerror or str(exc)))
                    return
                if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                    plan.append((p, arc, st.st_size if stat.S_ISREG(st.st_mode) else 0))
                else:
                    self.errors.append((p, "Keine normale Datei (z. B. Pipe oder Gerät) – übersprungen."))

            for src in self.sources:
                path = src.get_path()
                if not path:
                    self._err(src, GLib.Error("Nur lokale Dateien können komprimiert werden."))
                    continue
                base = os.path.dirname(path)
                if os.path.isdir(path) and not os.path.islink(path):
                    for root, dirs, files in os.walk(path, onerror=walk_err):
                        self.cancellable.set_error_if_cancelled()
                        dirs.sort()
                        rel = os.path.relpath(root, base)
                        if not files and not dirs:
                            plan.append((root, rel + "/", 0))
                        links = [d for d in dirs if os.path.islink(os.path.join(root, d))]
                        for f in sorted(files + links):
                            p = os.path.join(root, f)
                            if p != target:      # nie die eigene, gerade entstehende ZIP-Datei
                                add(p, os.path.join(rel, f))
                else:
                    add(path, os.path.basename(path))
            self.total_items = len(plan)
            self.total_bytes = sum(s for _p, _n, s in plan)
            self.phase = "run"
            fh, tmp = _part_file(os.path.dirname(target), os.path.basename(target))
            with fh, zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for path, arc, size in plan:
                    self._checkpoint()
                    self.current = os.path.basename(arc.rstrip("/"))
                    try:
                        self._add_entry(zf, path, arc)
                    except (OSError, ValueError) as exc:
                        self.errors.append((path, getattr(exc, "strerror", None) or str(exc)))
                    self.done_items += 1
            os.replace(tmp, target)
            self.created.append(self.dest)
        except GLib.Error as exc:
            if not fileops._cancelled(exc):
                self._err(self.dest, exc)
        except Exception as exc:
            self._err(self.dest, exc)
        finally:
            try:
                if tmp and os.path.lexists(tmp):
                    os.remove(tmp)
                if placeholder is not None and not self.created:
                    st = os.lstat(target)     # Platzhalter nur entfernen, wenn noch unverändert
                    if (st.st_ino, st.st_dev, st.st_size) == \
                            (placeholder.st_ino, placeholder.st_dev, 0):
                        os.remove(target)
            except OSError:
                pass
            self.phase = "done"
            GLib.idle_add(self._finish)


def zip_name(files):
    """Windows: Name des (ersten) Elements mit .zip."""
    first = files[0].get_basename()
    is_dir = os.path.isdir(files[0].get_path() or "")
    return (first if is_dir else fileops.split_ext(first)[0] or first) + ".zip"


def compress(files, parent=None, on_done=None):
    files = [f for f in files if f is not None]
    if not files:
        return None
    folder = files[0].get_parent()
    target = fileops.numbered_child(folder, zip_name(files))

    def done(created, errors):
        if created:
            from . import undo     # created[0]: der Name kann sich beim Anlegen geändert haben
            undo.record(undo.Op("new", [(None, created[0])]))
        if errors:
            fileops.show_errors(parent, "Einige Elemente konnten nicht komprimiert werden.", errors)
        if on_done:
            on_done(created)
    return fileops._start(ZipJob(files, target, done), parent)


# ======================================================================
# Extrahieren
# ======================================================================
class ExtractJob(fileops._Job):
    kind = "extract"

    def __init__(self, archive, dest_dir, on_done):
        super().__init__()
        self.archive = archive          # Gio.File
        self.sources = [archive]
        self.dest = dest_dir            # Gio.File
        self.on_done = on_done
        self.created = []               # neu entstandene Elemente oben im Ziel
        self._all_choice = None

    def _call_done(self):
        if self.on_done:
            self.on_done(self.created, self.errors)

    def _note_top(self, dest, path, existed_before):
        top = os.path.join(dest, os.path.relpath(path, dest).split(os.sep)[0])
        if top not in existed_before:
            g = Gio.File.new_for_path(top)
            if not any(g.equal(c) for c in self.created):
                self.created.append(g)

    def _decide(self, out, name, size, mtime):
        """→ Zielpfad oder None (überspringen) bei bestehender Datei."""
        if not os.path.lexists(out):
            return out
        dst = Gio.File.new_for_path(out)
        try:
            dinfo = dst.query_info(fileops.SCAN_ATTRS, fileops.NOFOLLOW, None)
        except GLib.Error:
            return out
        can_replace = dinfo.get_file_type() != Gio.FileType.DIRECTORY   # Ordner nie durch Datei
        choice = self._all_choice
        if choice is None or (choice == "replace" and not can_replace):
            src = Gio.File.new_for_path(os.path.join(self.archive.get_path() or "", name))
            choice, for_all = self._ask(src, _fake_info(os.path.basename(out), size, mtime),
                                        dst, dinfo, can_replace)
            if for_all:
                self._all_choice = choice
        if choice == "replace" and can_replace:
            return out
        if choice == "keep":
            return fileops.numbered_child(Gio.File.new_for_path(os.path.dirname(out)),
                                          os.path.basename(out)).get_path()
        return None

    def run(self):
        dest, fresh = None, False
        try:
            dest = self.dest.get_path()
            path = self.archive.get_path()
            if not dest or not path:
                raise ValueError("Nur lokale Archive können extrahiert werden.")
            fresh = not os.path.exists(dest)
            os.makedirs(dest, exist_ok=True)
            before = {os.path.join(dest, n) for n in os.listdir(dest)}
            ext = _ext(path)
            if ext == ".zip":
                self._zip(path, dest, before)
            elif ext in _TAR:
                self._tar(path, dest, before)
            else:
                self._7z(path, dest, before)
        except GLib.Error as exc:
            if not fileops._cancelled(exc):
                self._err(self.archive, exc)
        except Exception as exc:    # BadZipFile, TarError, zlib.error, EOFError, LZMAError …
            self.errors.append((self.archive.get_basename(), str(exc) or type(exc).__name__))
        finally:
            if fresh and os.path.isdir(dest):
                self.created = [self.dest]     # Rückgängig entfernt den ganzen neuen Ordner
            self.phase = "done"
            GLib.idle_add(self._finish)

    def _zip(self, path, dest, before):
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
            self.total_items = len(members)
            self.total_bytes = sum(m.file_size for m in members)
            self.phase = "run"
            for m in members:
                self._checkpoint()
                out = _safe_join(dest, m.filename)
                self.current = os.path.basename(m.filename.rstrip("/"))
                if out is None and m.is_dir() and _is_root(m.filename):
                    pass                     # „./“ = das Ziel selbst
                elif out is None:
                    self.errors.append((m.filename, "Unzulässiger Pfad im Archiv – übersprungen."))
                elif m.is_dir():
                    os.makedirs(out, exist_ok=True)
                    self._note_top(dest, out, before)
                else:
                    mtime = time.mktime(m.date_time + (0, 0, -1))
                    out = self._decide(out, m.filename, m.file_size, mtime)
                    if out is not None:
                        os.makedirs(os.path.dirname(out), exist_ok=True)
                        # eigene Hilfsdatei (nie „Name.part“: die könnte es geben)
                        fh, tmp = _part_file(os.path.dirname(out), os.path.basename(out))
                        try:
                            with fh as fout, zf.open(m) as fin:
                                while True:
                                    self._checkpoint()
                                    buf = fin.read(CHUNK)
                                    if not buf:
                                        break
                                    fout.write(buf)
                                    self.done_bytes += len(buf)
                            os.replace(tmp, out)
                        finally:
                            if os.path.lexists(tmp):
                                os.remove(tmp)
                        mode = (m.external_attr >> 16) & 0o777
                        if mode and m.create_system == 3:
                            os.chmod(out, mode | stat.S_IRUSR | stat.S_IWUSR)
                        os.utime(out, (mtime, mtime))
                        self._note_top(dest, out, before)
                self.done_items += 1

    def _tar(self, path, dest, before):
        with tarfile.open(path) as tf:
            members = tf.getmembers()
            self.total_items = len(members)
            self.total_bytes = sum(m.size for m in members if m.isfile())
            self.phase = "run"
            for m in members:
                self._checkpoint()
                self.current = os.path.basename(m.name)
                self._tar_member(tf, m, dest, before)
                if m.isfile():
                    self.done_bytes += m.size
                self.done_items += 1

    def _tar_member(self, tf, m, dest, before):
        out = _safe_join(dest, m.name)
        if out is None:
            if not (m.isdir() and _is_root(m.name)):     # „./“ = das Ziel selbst
                self.errors.append((m.name, "Unzulässiger Pfad im Archiv – übersprungen."))
            return
        try:   # Links/Pfade prüfen, BEVOR etwas Vorhandenes ersetzt wird
            tarfile.data_filter(m.replace(name=os.path.relpath(out, dest)), dest)
        except tarfile.FilterError as exc:
            self.errors.append((m.name, str(exc)))
            return
        final = out
        if not m.isdir():      # auch Links: tarfile ersetzte sonst ungefragt eine Datei
            final = self._decide(out, m.name, m.size, m.mtime)
            if final is None:
                return
        tmp = None
        if not m.isdir() and os.path.lexists(final):
            # Ersetzen: erst vollständig auspacken, dann austauschen
            tmp = os.path.join(os.path.dirname(final), ".{}.{}.part".format(
                os.fsdecode(os.fsencode(os.path.basename(final))[:120]), secrets.token_hex(4)))
        try:
            # genau dorthin, wo geprüft wurde (auch bei „\\“ im Namen)
            tf.extract(m.replace(name=os.path.relpath(tmp or final, dest)), dest, filter="data")
            if tmp:
                os.replace(tmp, final)
        except tarfile.FilterError as exc:
            self.errors.append((m.name, str(exc)))
            return
        finally:
            if tmp and os.path.lexists(tmp):
                os.remove(tmp)
        self._note_top(dest, final, before)

    def _7z(self, path, dest, before):
        self.total_bytes = 100
        self.phase = "run"
        self.current = os.path.basename(path)
        problem = sevenzip_precheck(path)
        if problem:
            self.errors.append((os.path.basename(path), problem))
            return
        # -spd: „*“/„?“ im Dateinamen wörtlich nehmen (sonst entpackt „a*.7z“ auch ab.7z)
        proc = subprocess.Popen(["7z", "x", "-y", "-aou", "-spd", "-bsp1", "-bso0", "-bse1",
                                 f"-o{dest}", "--", path], stdout=subprocess.PIPE,
                                stdin=subprocess.DEVNULL,
                                stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
        out = []
        try:
            buf = ""
            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    break
                if ch in "\r\n\b":
                    pct = buf.strip().split("%")[0].strip()
                    if pct.isdigit():
                        self.done_bytes = int(pct)
                    elif buf.strip():
                        out.append(buf.strip())
                    buf = ""
                else:
                    buf += ch
                if self.is_cancelled():
                    proc.terminate()
                    break
                self._go.wait()
        finally:
            proc.wait()
        if proc.returncode not in (0, None) and not self.is_cancelled():
            self.errors.append((os.path.basename(path), "\n".join(out[-3:]) or "7z-Fehler"))
        for n in os.listdir(dest):
            self._note_top(dest, os.path.join(dest, n), before)


_7Z_LINKS_SAFE = None


def sevenzip_links_safe():
    """Entpackt das installierte 7z Verknüpfungen (Symlinks) sicher? p7zip 16.02
    (Mint 21, Ubuntu 22.04) legt sie an und schreibt spätere Einträge durch sie
    hindurch; 7-Zip vor 25.01 hat noch eine Lücke dieser Art (CVE-2025-55188)."""
    global _7Z_LINKS_SAFE
    if _7Z_LINKS_SAFE is None:
        try:
            head = subprocess.run(["7z"], capture_output=True, text=True, errors="replace",
                                  stdin=subprocess.DEVNULL, timeout=15).stdout[:2000]
        except (OSError, subprocess.SubprocessError):
            head = ""
        import re
        m = re.search(r"7-Zip(?: \[\d+\])? (\d+)\.(\d+)", head)
        _7Z_LINKS_SAFE = bool(m) and "p7zip" not in head.lower() and (int(m.group(1)), int(m.group(2))) >= (25, 1)
    return _7Z_LINKS_SAFE


def sevenzip_precheck(path, links_safe=None):
    """Inhaltsverzeichnis prüfen, BEVOR 7z etwas schreibt → Fehlertext oder None.
    Abgewiesen: absolute Pfade, „..“, und Verknüpfungen, wenn dieses 7z sie
    nicht sicher behandelt."""
    try:
        r = subprocess.run(["7z", "l", "-slt", "-spd", "--", path], capture_output=True,
                           text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=300)
    except subprocess.TimeoutExpired:
        return "Das Inhaltsverzeichnis des Archivs liess sich nicht lesen (Zeitüberschreitung)."
    except OSError as exc:
        return str(exc)
    entries = r.stdout.split("\n----------\n", 1)
    if len(entries) < 2:
        return None                  # kein Verzeichnis (beschädigt/verschlüsselt): 7z meldet es selbst
    links, prev = False, None
    for line in entries[1].split("\n"):      # nicht splitlines(): trennt auch an U+2028 …
        key, sep, val = line.partition(" = ")
        if not sep:
            if line.strip() and prev == "Path":
                return "Ein Name im Archiv enthält einen Zeilenumbruch – nichts extrahiert."
            prev = None
            continue
        prev = key
        if key == "Path":
            parts = val.replace("\\", "/").split("/")
            if val.startswith(("/", "\\")) or ".." in parts or (len(val) > 2 and val[1] == ":" and val[2] in "/\\"):
                return f"Unzulässiger Pfad im Archiv: {places.safe_text(val)} – nichts extrahiert."
        elif key in ("Attributes", "Mode"):
            bits = val.split()
            if bits and bits[-1].startswith("l"):
                links = True
        elif key == "Symbolic Link" and val.strip():
            links = True
    if links and not (sevenzip_links_safe() if links_safe is None else links_safe):
        return ("Das Archiv enthält Verknüpfungen (Symlinks). Die installierte 7z-Version "
                "entpackt sie nicht sicher (erst ab 7-Zip 25.01). Nichts extrahiert – Sie können "
                "das Archiv aber als Ordner öffnen und die Dateien herauskopieren.")
    return None


def extract(archive, dest_dir, parent=None, on_done=None):
    def done(created, errors):
        if created:
            from . import undo
            undo.record(undo.Op("new", [(None, g) for g in created]))
        if errors:
            fileops.show_errors(parent, "Einige Elemente konnten nicht extrahiert werden.", errors)
        if on_done:
            on_done(created)
    return fileops._start(ExtractJob(archive, dest_dir, done), parent)


def default_dest(archive):
    return os.path.join(os.path.dirname(archive.get_path()), _stem(archive.get_basename()))


class ExtractDialog(Gtk.Window):
    """„ZIP-komprimierte Ordner extrahieren“ wie unter Windows."""

    def __init__(self, archive, parent, host):
        super().__init__(title="ZIP-komprimierte Ordner extrahieren")
        self.archive, self.host = archive, host
        for c in ("filefenster", "win-dialog", "extract-dialog"):
            self.get_style_context().add_class(c)
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
            self.set_modal(True)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_default_size(560, -1)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_skip_taskbar_hint(True)
        from .propsdialog import _fluent_button
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class("win-body")
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(24)
        head = Gtk.Label(xalign=0)
        head.set_markup("<span size='large'>Ziel auswählen und Dateien extrahieren</span>")
        box.pack_start(head, False, False, 4)
        box.pack_start(Gtk.Label(label="Dateien werden in diesen Ordner extrahiert:", xalign=0),
                       False, False, 0)
        row = Gtk.Box(spacing=8)
        self.entry = Gtk.Entry()
        self.entry.set_text(default_dest(archive))
        self.entry.set_width_chars(48)
        self.entry.connect("activate", lambda *_: self._go())
        browse = _fluent_button("Durchsuchen…")
        browse.connect("clicked", self._browse)
        row.pack_start(self.entry, True, True, 0)
        row.pack_start(browse, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.show_after = Gtk.CheckButton(label="Dateien nach Extrahierung anzeigen")
        self.show_after.set_active(True)
        box.pack_start(self.show_after, False, False, 4)
        area.pack_start(box, True, True, 0)
        footer = Gtk.Box()
        footer.get_style_context().add_class("win-footer")
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        for side in ("top", "bottom", "start", "end"):
            getattr(btns, f"set_margin_{side}")(16 if side in ("top", "bottom") else 24)
        ok = _fluent_button("Extrahieren", accent=True)
        ok.connect("clicked", lambda *_: self._go())
        cancel = _fluent_button("Abbrechen")
        cancel.connect("clicked", lambda *_: self.destroy())
        btns.pack_start(ok, False, False, 0)
        btns.pack_start(cancel, False, False, 0)
        footer.pack_start(btns, True, True, 0)
        area.pack_end(footer, False, False, 0)
        box = area
        self.add(box)
        self.connect("key-press-event", self._on_key)
        self.show_all()
        self.entry.grab_focus()
        self.entry.select_region(0, -1)
        self.entry.get_layout().set_ellipsize(Pango.EllipsizeMode.START)

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    def _browse(self, _btn):
        dlg = Gtk.FileChooserDialog(title="Ziel auswählen", transient_for=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                        "Ordner auswählen", Gtk.ResponseType.OK)
        start = os.path.dirname(self.entry.get_text().rstrip("/")) or "/"
        if os.path.isdir(start):
            dlg.set_current_folder(start)
        if dlg.run() == Gtk.ResponseType.OK and dlg.get_filename():
            self.entry.set_text(dlg.get_filename())
        dlg.destroy()

    def _go(self):
        path = os.path.expanduser(self.entry.get_text().strip())
        if not path:
            return
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(self.archive.get_path()), path)
        dest = Gio.File.new_for_path(path)
        show = self.show_after.get_active()
        parent = self.get_transient_for()
        host = self.host

        def done(_created):
            if show and os.path.isdir(path) and host is not None:
                host.open_in_new_window(dest.get_uri())
        self.destroy()
        extract(self.archive, dest, parent=parent, on_done=done)


def extract_dialog(archive, parent=None, host=None):
    return ExtractDialog(archive, parent, host)


# ======================================================================
# In eine geöffnete ZIP-Datei schreiben (wie Windows: nur ZIP-Dateien, die
# selbst lokal liegen – TAR/7z/RAR/CAB und ZIP im ZIP bleiben schreibgeschützt)
# ======================================================================
_LOCAL_SIG = b"PK\x03\x04"
_DD_SIG = b"PK\x07\x08"
_F_ENCRYPTED, _F_DD, _F_UTF8 = 0x1, 0x8, 0x800     # Bits in flag_bits
_X_ZIP64, _X_UPATH = 0x0001, 0x7075                # Extra-Felder
_ADD_ATTRS = fileops.SCAN_ATTRS + ",unix::mode,unix::device,unix::inode,standard::symlink-target"
_locks = {}
_locks_guard = threading.Lock()


def zip_file(loc):
    """Archivdatei (Gio.File) einer ZIP-Adresse, in die man schreiben könnte, sonst None."""
    arc = places.archive_parts(loc)
    if not arc:
        return None
    afile = arc[0]
    if not afile.get_uri().startswith("file:") or _ext(afile.get_basename() or "") != ".zip":
        return None
    return afile


def writable(loc):
    """Kann man an diesem archive://-Ort schreiben? Nur eine lokale .zip
    (nicht verschachtelt), die man ändern darf."""
    afile = zip_file(loc)
    path = afile.get_path() if afile is not None else None
    return bool(path) and os.access(path, os.W_OK) and os.access(os.path.dirname(path), os.W_OK)


def _norm(name):
    return "/".join(p for p in name.split("/") if p not in ("", "."))


def _join(folder, name):
    return f"{folder}/{name}" if folder else name


def _keys(zi):
    """Pfade, unter denen ein Eintrag erscheinen kann: so wie Python ihn liest
    und – bei alten Namen ohne UTF-8-Kennzeichen – als UTF-8 gelesen (so zeigt
    libarchive/gvfs sie unter Linux)."""
    keys = [_norm(zi.filename)]
    if not zi.flag_bits & _F_UTF8:
        try:
            alt = _norm(zi.filename.encode("cp437").decode("utf-8"))
            if alt not in keys:
                keys.append(alt)
        except UnicodeError:
            pass
    return [k for k in keys if k]


def _strip_extra(extra, ids):
    out, i = [], 0
    while i + 4 <= len(extra):
        xid, xlen = struct.unpack("<HH", extra[i:i + 4])
        if xid not in ids:
            out.append(extra[i:i + 4 + xlen])
        i += 4 + xlen
    return b"".join(out)


def _has_extra(extra, xid):
    i = 0
    while i + 4 <= len(extra):
        x, xlen = struct.unpack("<HH", extra[i:i + 4])
        if x == xid:
            return True
        i += 4 + xlen
    return False


def _dos_time(ts):
    t = time.localtime(ts)[:6]
    if t[0] < 1980:
        return (1980, 1, 1, 0, 0, 0)
    return min(t, (2107, 12, 31, 23, 59, 58))


def _bad_name(name):
    """Namen, die beim Entpacken (auch unter Windows) aus dem Ziel ausbrechen könnten."""
    return (not name or "/" in name or "\0" in name
            or any(p in ("", ".", "..") for p in name.split("\\")))


class _Entry(zipfile.ZipInfo):
    """ZipInfo, die ihren ursprünglichen Namen (Bytes) behält – sonst schreibt
    zipfile alte CP437-Namen als UTF-8 um und Kopf und Verzeichnis passen
    nicht mehr zusammen."""
    __slots__ = ("raw_name",)

    def _encodeFilenameFlags(self):
        if self.raw_name is not None:
            return self.raw_name, self.flag_bits
        return super()._encodeFilenameFlags()


def _clone(zi):
    new = _Entry.__new__(_Entry)
    for s in zipfile.ZipInfo.__slots__:
        try:
            setattr(new, s, getattr(zi, s))
        except AttributeError:
            pass
    new.raw_name = None
    return new


def _lock(path):
    with _locks_guard:
        return _locks.setdefault(os.path.realpath(path), threading.Lock())


_CHANGED = ("Die ZIP-Datei wurde inzwischen von einem anderen Programm geändert. "
            "Der Vorgang wurde nicht ausgeführt.")
_SELF = "Die ZIP-Datei kann nicht in sich selbst aufgenommen werden."


def _changed(a, b):
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns) != (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns)


def _contains_real(src, path):
    """Ist die (lokale) Quelle src die Datei path selbst oder ein Ordner darüber –
    auch über Verknüpfungen im Pfad?"""
    sp = src.get_path() if src.get_uri().startswith("file:") else None
    if not sp:
        return False
    # nur den Ordner auflösen: eine Verknüpfung als Quelle bleibt Verknüpfung
    head, tail = os.path.split(os.path.normpath(sp))
    real = os.path.join(os.path.realpath(head), tail) if tail else os.path.realpath(sp)
    return real == path or path.startswith(real.rstrip("/") + "/")


class ZipEditJob(fileops._Job):
    """Ändert eine ZIP-Datei im Hintergrund: op "add" (sources nach folder,
    move = Quellen danach löschen), "delete" (sources), "rename" (sources[0]
    → new_name), "mkdir" (sources[0] = neuer Ordner).

    Geschrieben wird in eine temporäre Datei neben der ZIP, die erst am Ende
    per os.replace das Original ersetzt – bei Fehler oder Abbruch bleibt die
    ZIP-Datei unverändert. Unveränderte Einträge werden roh kopiert:
    Kompression, Zeiten, Rechte und Verschlüsselung bleiben genau erhalten.
    Vorgänge auf dieselbe ZIP-Datei laufen nacheinander.

    on_done(job) im Hauptthread; job.committed, job.created (Gio.Files der
    neuen Elemente oben im Ordner), job.errors.
    """

    def __init__(self, op, folder, sources=(), move=False, new_name=None, on_done=None):
        super().__init__()
        self.op = op
        self.folder = folder                         # Ordner im Archiv (Gio.File)
        arc = places.archive_parts(folder.get_uri())
        self.zip, self.root, parts = arc[0], arc[1], arc[2]
        self.path = os.path.realpath(self.zip.get_path())   # Verknüpfung bleibt Verknüpfung
        self.base = "/".join(parts)
        self.sources = list(sources)
        self.files = self.sources                    # Fortschrittsfenster (Löschen)
        self.dest = folder
        self.move = move
        self.new_name = new_name
        self.on_done = on_done
        self.kind = {"add": "move" if move else "copy", "delete": "delete"}.get(op, "zipupdate")
        self.committed = False
        self._created = []                           # Pfade im Archiv
        self._drop, self._rename = set(), {}         # id(ZipInfo) → …
        self._moved_files, self._moved_dirs = [], []
        self._all_choice = None
        self._zip_id = None

    @property
    def created(self):
        return [self._gfile_for(p) for p in self._created]

    def _gfile_for(self, path):
        g = Gio.File.new_for_uri(self.root)
        for p in path.split("/") if path else []:
            g = g.get_child(p)
        return g

    def _inner(self, gfile):
        arc = places.archive_parts(gfile.get_uri())
        return "/".join(arc[2]) if arc and arc[1] == self.root else None

    def _call_done(self):
        if self.on_done:
            self.on_done(self)

    # -- Ablauf (Thread) ---------------------------------------------------
    def run(self):
        tmp = None
        lock, locked = _lock(self.path), False
        try:
            while not lock.acquire(timeout=0.2):     # gleiche ZIP: nacheinander
                self.cancellable.set_error_if_cancelled()
            locked = True
            with zipfile.ZipFile(self.path) as zin:
                orig = os.fstat(zin.fp.fileno())
                self._zip_id = (orig.st_dev & 0xFFFFFFFF, orig.st_ino)   # unix::device ist 32 Bit
                infos = zin.infolist()
                self._index(infos)
                adds = getattr(self, "_plan_" + self.op)(infos)
                keep = [zi for zi in infos if id(zi) not in self._drop]
                if not adds and len(keep) == len(infos) and not self._rename:
                    return                           # nichts zu tun (alles übersprungen)
                offsets = sorted({zi.header_offset for zi in infos})
                first = offsets[0] if offsets else zin.start_dir
                self.total_bytes += sum(zi.compress_size for zi in keep) + max(0, first)
                self.phase = "run"
                fh, tmp = _part_file(os.path.dirname(self.path), os.path.basename(self.path))
                with open(self.path, "rb") as src, fh, zipfile.ZipFile(fh, "w") as zout:
                    if _changed(orig, os.fstat(src.fileno())):
                        raise OSError(0, _CHANGED)
                    if zin.comment:
                        zout.comment = zin.comment
                    if first > 0:                    # Vorspann (selbstentpackende ZIP) behalten
                        self._raw(src, zout.fp, 0, first)
                        zout.start_dir = zout.fp.tell()
                    for zi in keep:
                        self._checkpoint()
                        i = bisect.bisect_right(offsets, zi.header_offset)
                        limit = offsets[i] if i < len(offsets) else zin.start_dir
                        self._copy(src, zout, zi, limit)
                    for item in adds:
                        self._checkpoint()
                        self._write(zout, item)
                self._checkpoint()
                fd = os.open(tmp, os.O_RDONLY)       # erst auf der Platte, dann ersetzen
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                now = os.stat(self.path)
                if _changed(orig, now):              # inzwischen von anderem Programm geändert
                    raise OSError(0, _CHANGED)
                os.chmod(tmp, stat.S_IMODE(now.st_mode))
                os.replace(tmp, self.path)
                self.committed = True
            self._remove_moved()
        except GLib.Error as exc:
            if not fileops._cancelled(exc):
                self._err(self.zip, exc)
        except Exception as exc:     # BadZipFile, OSError, ValueError … – Original bleibt
            msg = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
            self.errors.append((self.zip.get_basename(), msg))
        finally:
            if locked:
                lock.release()
            try:
                if tmp and os.path.lexists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            self.phase = "done"
            GLib.idle_add(self._finish)

    # -- Inhaltsverzeichnis ------------------------------------------------
    def _index(self, infos):
        self._keys = {id(zi): _keys(zi) for zi in infos}
        self._files, self._dirs = {}, set()     # Pfad → [ZipInfo | ("neu", Quelle)]; Ordner
        for zi in infos:
            for k in self._keys[id(zi)]:
                self._note(k, zi.is_dir(), zi)

    def _note(self, path, is_dir, what=None):
        parts = path.split("/")
        for i in range(1, len(parts)):
            self._dirs.add("/".join(parts[:i]))
        if is_dir:
            self._dirs.add(path)
        else:
            self._files.setdefault(path, []).append(what)

    def _kind(self, path):
        if path in self._files:
            return "file"
        return "dir" if path in self._dirs else None

    def _under(self, infos, path):
        """Einträge path und (bei Ordnern) alles darunter → [(ZipInfo, passender Pfad)]."""
        out = []
        for zi in infos:
            for k in self._keys[id(zi)]:
                if k == path or k.startswith(path + "/"):
                    out.append((zi, k))
                    break
        return out

    # -- Planen ------------------------------------------------------------
    def _plan_delete(self, infos):
        parents = set()
        for g in self.sources:
            path = self._inner(g)
            hit = self._under(infos, path) if path else []
            if not hit:
                self.errors.append((fileops._display(g), "Das Element wurde im Archiv nicht gefunden."))
                continue
            self._drop.update(id(zi) for zi, _k in hit)
            self.total_items += len(hit)
            parents.add(path.rpartition("/")[0])
        # Ordner, in dem gelöscht wurde, bleibt (wie Windows) – auch wenn er
        # nur implizit über seinen Inhalt existierte
        left = set()
        for zi in infos:
            if id(zi) not in self._drop:
                for k in self._keys[id(zi)]:
                    parts = k.split("/")
                    left.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        return [("dir", None, p, None) for p in sorted(parents) if p and p not in left]

    def _plan_rename(self, infos):
        old, new_name = self._inner(self.sources[0]), self.new_name
        if not old or _bad_name(new_name):
            raise ValueError(f"„{new_name}“ ist als Name nicht zulässig.")
        new = _join(old.rpartition("/")[0], new_name)
        if new == old:
            return []
        if self._kind(new) is not None:
            raise ValueError(f"Es ist bereits ein Element mit dem Namen „{new_name}“ vorhanden.")
        hit = self._under(infos, old)
        if not hit:
            raise ValueError(f"„{old.rpartition('/')[2]}“ wurde im Archiv nicht gefunden.")
        for zi, k in hit:
            self._rename[id(zi)] = new + k[len(old):] + ("/" if zi.is_dir() else "")
        self.total_items = len(hit)
        self._created = [new]
        return []

    def _plan_mkdir(self, infos):
        path = self._inner(self.sources[0])
        name = (path or "").rpartition("/")[2]
        if not path or _bad_name(name):
            raise ValueError(f"„{name}“ ist als Name nicht zulässig.")
        if self._kind(path) is not None:
            raise ValueError(f"Es ist bereits ein Element mit dem Namen „{name}“ vorhanden.")
        self.total_items = 1
        self._created = [path]
        return [("dir", None, path, None)]

    def _plan_add(self, infos):
        adds = []
        for src in self.sources:
            self.cancellable.set_error_if_cancelled()
            if self.zip.equal(src) or self.zip.has_prefix(src) or _contains_real(src, self.path) \
                    or self.folder.equal(src) or self.folder.has_prefix(src):
                self.errors.append((fileops._display(src),
                                    "Der Zielordner ist ein Unterordner des Quellordners."))
                continue
            try:
                info = src.query_info(_ADD_ATTRS, fileops.NOFOLLOW, self.cancellable)
            except GLib.Error as exc:
                if fileops._cancelled(exc):
                    raise
                self._err(src, exc)
                continue
            name = info.get_name()
            parent = src.get_parent()
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
            if parent is not None and parent.equal(self.folder):
                name, mode = self._copy_name(self.base, name, is_dir), "new"   # „Name - Kopie“
            else:
                name, mode = self._resolve(src, info, self.base, name)
            if mode == "skip":
                continue
            movable = not src.get_uri().startswith(("archive:", "trash:"))
            if self._plan(src, info, self.base, name, mode, adds, movable):
                self._created.append(_join(self.base, name))
        return adds

    def _plan(self, src, info, folder, name, mode, adds, movable):
        """Element samt Inhalt vormerken. → False, wenn etwas fehlte/übersprungen wurde."""
        self.cancellable.set_error_if_cancelled()
        path = _join(folder, name)
        if _bad_name(name):
            self.errors.append((fileops._display(src), "Der Name ist in einer ZIP-Datei nicht zulässig."))
            return False
        ftype = info.get_file_type()
        if ftype == Gio.FileType.DIRECTORY:
            if mode == "new":
                adds.append(("dir", src, path, info))
                self._note(path, True)
                self.total_items += 1
            try:
                en = src.enumerate_children(_ADD_ATTRS, fileops.NOFOLLOW, self.cancellable)
                try:
                    children = list(en)
                finally:
                    en.close(None)
            except GLib.Error as exc:
                if fileops._cancelled(exc):
                    raise
                self._err(src, exc)
                return False
            complete = True
            for ci in sorted(children, key=lambda i: i.get_name()):
                child = src.get_child(ci.get_name())
                cname, cmode = ci.get_name(), "new"
                if mode != "new":
                    cname, cmode = self._resolve(child, ci, path, cname)
                if cmode == "skip":
                    complete = False
                    continue
                complete = self._plan(child, ci, path, cname, cmode, adds, movable) and complete
            if movable:
                self._moved_dirs.append(src)     # nur, wenn danach leer
            return complete
        if ftype not in (Gio.FileType.REGULAR, Gio.FileType.SYMBOLIC_LINK):
            self.errors.append((fileops._display(src),
                                "Dieses Element kann nicht in eine ZIP-Datei aufgenommen werden."))
            return False
        if ftype == Gio.FileType.REGULAR and info.has_attribute("unix::inode") \
                and (info.get_attribute_uint32("unix::device"),
                     info.get_attribute_uint64("unix::inode")) == self._zip_id:
            # die ZIP-Datei selbst (über einen anderen Pfad) – beim Verschieben
            # würde sie danach gelöscht
            self.errors.append((fileops._display(src), _SELF))
            return False
        if mode == "replace":
            for old in self._files.pop(path, []):
                if isinstance(old, tuple):       # in diesem Vorgang schon vorgemerkt
                    adds[:] = [a for a in adds if a[2] != path]
                    if old[1] in self._moved_files:
                        self._moved_files.remove(old[1])
                else:
                    self._drop.add(id(old))
        kind = "link" if ftype == Gio.FileType.SYMBOLIC_LINK else "file"
        adds.append((kind, src, path, info))
        self._note(path, False, ("neu", src))
        self.total_items += 1
        if kind == "file":
            self.total_bytes += info.get_size()
        if movable:
            self._moved_files.append(src)
        return True

    def _resolve(self, src, sinfo, folder, name):
        """(Name, Modus) mit Modus "new", "replace", "merge" oder "skip" – wie
        TransferJob._resolve, nur gegen den Inhalt der ZIP-Datei."""
        path = _join(folder, name)
        kind = self._kind(path)
        if kind is None:
            return name, "new"
        sdir = sinfo.get_file_type() == Gio.FileType.DIRECTORY
        if sdir and kind == "dir":
            return name, "merge"
        can_replace = not sdir and kind == "file"
        choice = self._all_choice
        if choice is None or (choice == "replace" and not can_replace):
            choice, for_all = self._ask(src, sinfo, self._gfile_for(path),
                                        self._dinfo(path, kind), can_replace)
            if for_all:
                self._all_choice = choice
        if choice == "replace" and can_replace:
            return name, "replace"
        if choice == "keep":
            return self._free_name(folder, name, sdir, "{stem} ({n}){ext}", 2), "new"
        return name, "skip"

    def _dinfo(self, path, kind):
        name = path.rpartition("/")[2]
        zi = next((z for z in self._files.get(path, []) if isinstance(z, zipfile.ZipInfo)), None)
        if kind == "file" and zi is not None:
            return _fake_info(name, zi.file_size, time.mktime(zi.date_time + (0, 0, -1)))
        info = _fake_info(name, 0, time.time())
        if kind == "dir":
            info.set_file_type(Gio.FileType.DIRECTORY)
        return info

    def _free_name(self, folder, name, is_dir, pattern, n):
        stem, ext = fileops.split_ext(name, is_dir)
        while True:
            cand = pattern.format(stem=stem, n=n, ext=ext)
            if self._kind(_join(folder, cand)) is None:
                return cand
            n += 1

    def _copy_name(self, folder, name, is_dir):
        """Windows-Schema „Name - Kopie.ext“, „Name - Kopie (2).ext“."""
        if self._kind(_join(folder, name)) is None:
            return name
        stem, ext = fileops.split_ext(name, is_dir)
        if self._kind(_join(folder, f"{stem} - Kopie{ext}")) is None:
            return f"{stem} - Kopie{ext}"
        return self._free_name(folder, name, is_dir, "{stem} - Kopie ({n}){ext}", 2)

    # -- Schreiben ---------------------------------------------------------
    def _raw(self, src, out, start, end):
        src.seek(start)
        left = end - start
        while left > 0:
            self._checkpoint()
            buf = src.read(min(CHUNK, left))
            if not buf:
                raise zipfile.BadZipFile("Die ZIP-Datei ist unvollständig.")
            out.write(buf)
            left -= len(buf)
            self.done_bytes += len(buf)

    def _copy(self, src, zout, zi, limit):
        """Eintrag unverändert (roh, ohne neu zu komprimieren) übernehmen –
        auf Wunsch unter neuem Namen."""
        h = zi.header_offset
        src.seek(h)
        head = src.read(30)
        if len(head) < 30 or head[:4] != _LOCAL_SIG:
            raise zipfile.BadZipFile(f"Beschädigter Eintrag im Archiv: {zi.filename}")
        n, m = struct.unpack("<HH", head[26:30])
        src.seek(n, 1)
        lextra = src.read(m)
        start = h + 30 + n + m
        end = start + zi.compress_size
        tail = 0
        if zi.flag_bits & _F_DD:                 # Datenbeschreibung nach den Daten
            src.seek(end)
            tail = (4 if src.read(4) == _DD_SIG else 0) + (20 if _has_extra(lextra, _X_ZIP64) else 12)
        if end + tail > limit:
            raise zipfile.BadZipFile(f"Beschädigter Eintrag im Archiv: {zi.filename}")
        self.current = zi.filename.rstrip("/").rpartition("/")[2]
        out = zout.fp
        new = _clone(zi)
        new.header_offset = out.tell()
        renamed = self._rename.get(id(zi))
        if renamed is None:
            new.extra = _strip_extra(zi.extra, (_X_ZIP64,))
            new.raw_name = zi.orig_filename.encode("utf-8" if zi.flag_bits & _F_UTF8 else "cp437")
            self._raw(src, out, h, end + tail)
        else:
            new.filename = new.orig_filename = renamed
            if not zi.flag_bits & _F_ENCRYPTED:  # (Prüfbyte verschlüsselter Einträge hängt am Bit)
                new.flag_bits &= ~_F_DD
            zip64 = zi.file_size > zipfile.ZIP64_LIMIT or zi.compress_size > zipfile.ZIP64_LIMIT
            new.extra = _strip_extra(lextra, (_X_ZIP64, _X_UPATH))
            out.write(new.FileHeader(zip64))
            self._raw(src, out, start, end)
            if new.flag_bits & _F_DD:
                out.write(struct.pack("<4sLQQ" if zip64 else "<4sLLL", _DD_SIG, zi.CRC,
                                      zi.compress_size, zi.file_size))
            new.extra = _strip_extra(zi.extra, (_X_ZIP64, _X_UPATH))
            self.done_items += 1
        zout.filelist.append(new)
        zout.NameToInfo[new.filename] = new
        zout.start_dir = out.tell()
        zout._didModify = True

    def _write(self, zout, item):
        kind, src, path, info = item
        self.current = path.rpartition("/")[2]
        mtime = time.time()
        mode = 0
        if info is not None:
            dt = info.get_modification_date_time()
            mtime = dt.to_unix() if dt is not None else mtime
            mode = stat.S_IMODE(info.get_attribute_uint32("unix::mode")) \
                if info.has_attribute("unix::mode") else 0
        zi = zipfile.ZipInfo(path + ("/" if kind == "dir" else ""), _dos_time(mtime))
        if kind == "dir":
            zi.external_attr = ((stat.S_IFDIR | (mode or 0o755)) << 16) | 0x10
            zi.CRC = 0
            zout.mkdir(zi)
        elif kind == "link":
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            target = info.get_symlink_target() or ""
            zout.writestr(zi, target.encode("utf-8", "surrogateescape"))
        else:
            zi.external_attr = (stat.S_IFREG | (mode or 0o644)) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.file_size = info.get_size()       # entscheidet über ZIP64
            stream = src.read(self.cancellable)
            try:
                with zout.open(zi, "w") as fout:
                    while True:
                        self._checkpoint()
                        buf = stream.read_bytes(CHUNK, self.cancellable).get_data()
                        if not buf:
                            break
                        fout.write(buf)
                        self.done_bytes += len(buf)
            finally:
                stream.close(None)
        self.done_items += 1

    def _remove_moved(self):
        """Verschieben: Quellen erst löschen, wenn die ZIP-Datei ersetzt ist."""
        if not (self.move and self.committed):
            return
        for f in self._moved_files:
            try:
                f.delete(self.cancellable)
            except GLib.Error as exc:
                if fileops._cancelled(exc):
                    return
                self._err(f, exc)
        for d in self._moved_dirs:               # innen zuerst (nach dem Inhalt vorgemerkt); nur leere
            try:
                d.delete(self.cancellable)
            except GLib.Error as exc:
                if fileops._cancelled(exc):
                    return
                if not fileops._io(exc, Gio.IOErrorEnum.NOT_EMPTY):
                    self._err(d, exc)


# -- Neu einhängen: gvfsd-archive merkt sich den alten Inhalt -----------
def _dependents(root):
    """Eingehängte Archive, die an root hängen (root selbst und darin
    geöffnete Archive) – innere zuerst."""
    found = []
    for m in Gio.VolumeMonitor.get().get_mounts():
        uri = m.get_root().get_uri()
        chain = _roots(uri) if uri.startswith("archive:") else []
        if root in chain:
            found.append((chain.index(root), m))
    if not found:
        try:
            found.append((0, Gio.File.new_for_uri(root).find_enclosing_mount(None)))
        except GLib.Error:
            pass
    return [m for _d, m in sorted(found, key=lambda t: -t[0])]


def _busy(root):
    """Liest gerade ein anderer Vorgang aus diesem Archiv (Herauskopieren …)?"""
    for job in fileops.running_jobs():
        if job.phase == "done":
            continue
        files = list(getattr(job, "sources", None) or []) + [getattr(job, "archive", None)]
        if any(isinstance(g, Gio.File) and root in _roots(g.get_uri()) for g in files):
            return True
    return False


def _views_showing(root):
    out = []
    for vid, roots in list(_shown.items()):
        ref = _views.get(vid)
        view = ref() if ref is not None else None
        if view is not None and root in roots:
            out.append(view)
    return out


def remount(root, then=None):
    """Archiv (und darin geöffnete Archive) aushängen, wieder einhängen und
    alle Ansichten darin neu laden – sie bleiben im selben Ordner."""
    def start():
        if _busy(root):
            GLib.timeout_add(400, start)
            return False
        mounts = _dependents(root)

        def unmounted(m, res, force=False):
            try:
                m.unmount_with_operation_finish(res)
            except GLib.Error:
                if not force:        # z. B. „belegt“: dann eben erzwingen
                    m.unmount_with_operation(Gio.MountUnmountFlags.FORCE, None, None,
                                             lambda mm, r: unmounted(mm, r, True))
                    return
            step()

        def step():
            if mounts:
                mounts.pop(0).unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None,
                                                     unmounted)
                return
            views = _views_showing(root)
            if not views:
                done()
                return
            # erst das Archiv selbst einhängen – darin geöffnete Archive brauchen es
            Gio.File.new_for_uri(root).mount_enclosing_volume(
                Gio.MountMountFlags.NONE, None, None, mounted, views)

        def mounted(f, res, views):
            try:
                f.mount_enclosing_volume_finish(res)
            except GLib.Error:
                pass             # die Ansichten melden den Fehler selbst
            for v in views:
                try:
                    v.zip_reload()
                except Exception as exc:   # Ansicht inzwischen zerstört
                    print("[filefenster] ZIP neu laden:", exc)
            done()

        def done():
            if then:
                then()
        step()
        return False
    start()


# -- Öffentliche Vorgänge (Hauptthread) ----------------------------------
def _edit(job, parent, on_done, title, after=None):
    def done(j):
        if j.errors:
            fileops.show_errors(parent, title, j.errors)
        if after:
            after(j)

        def reloaded():
            if on_done:
                on_done(j.created if j.committed else [])
        if j.committed:
            remount(j.root, reloaded)
        else:
            reloaded()
    job.on_done = done
    return fileops._start(job, parent)


def zip_add(sources, dest, move=False, parent=None, on_done=None, from_clipboard=False):
    """Dateien/Ordner in einen Ordner einer geöffneten ZIP-Datei kopieren
    (move: danach die Quellen löschen). on_done(created: list[Gio.File])."""
    sources = [s for s in sources if s is not None]
    if not sources or not writable(dest.get_uri()):
        return None

    def after(j):
        if move and from_clipboard and j.committed and not j.errors:
            fileops.clipboard().clear_if_cut()
    title = ("Einige Elemente konnten nicht verschoben werden." if move
             else "Einige Elemente konnten nicht kopiert werden.")
    return _edit(ZipEditJob("add", dest, sources, move=move), parent, on_done, title, after)


def zip_delete(files, parent=None, on_done=None):
    """Elemente aus einer geöffneten ZIP-Datei endgültig löschen (die Rückfrage
    stellt der Aufrufer – für ZIP-Inhalte gibt es keinen Papierkorb)."""
    files = list(files)
    if not files or not writable(files[0].get_uri()):
        return None
    return _edit(ZipEditJob("delete", files[0].get_parent(), files), parent, on_done,
                 "Einige Elemente konnten nicht gelöscht werden.")


def zip_rename(gfile, new_name, parent=None, on_done=None):
    if not writable(gfile.get_uri()):
        return None
    return _edit(ZipEditJob("rename", gfile.get_parent(), [gfile], new_name=new_name),
                 parent, on_done, "Das Element konnte nicht umbenannt werden.")


def zip_mkdir(target, parent=None, on_done=None):
    """Ordner target (Gio.File im Archiv) anlegen."""
    if not writable(target.get_uri()):
        return None
    return _edit(ZipEditJob("mkdir", target.get_parent(), [target]), parent, on_done,
                 "Der Ordner konnte nicht erstellt werden.")
