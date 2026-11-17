"""Zuletzt verwendete Dateien („Empfohlen“) und ein kleiner Dateinamen-Index
für die Suche.

Der Index liest nur Verzeichnislisten im persönlichen Ordner (keine Inhalte)
und lässt FUSE-/Netzwerk-Einhängepunkte (z. B. OneDrive) bewusst aus: Hängt
deren Dienst, bliebe sonst ein Thread im Kernel stecken – und der verhindert
z. B. den Bereitschaftsmodus.
"""
import datetime
import os
import re
import threading
import time

from gi.repository import GLib, Gtk

from .apps import fold

SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages",
             "snap", "Trash", "lost+found"}
MAX_ENTRIES = 250_000
MAX_DEPTH = 9
TIME_BUDGET = 25.0
MAX_AGE = 15 * 60

_OCT = re.compile(r"\\([0-7]{3})")
_REMOTE_FS = ("fuse", "nfs", "cifs", "smb", "sshfs", "davfs", "9p", "afs")


def foreign_mounts():
    """Einhängepunkte von FUSE-/Netzwerk-Dateisystemen."""
    out = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                parts, rparts = left.split(), right.split()
                if len(parts) < 5 or not rparts:
                    continue
                fstype = rparts[0]
                if fstype.startswith(_REMOTE_FS) or fstype in ("fuseblk",):
                    out.add(_OCT.sub(lambda m: chr(int(m.group(1), 8)), parts[4]))
    except OSError:
        pass
    return out


def _under(path, mounts):
    for m in mounts:
        if path == m or path.startswith(m.rstrip("/") + "/"):
            return True
    return False


# ---------------------------------------------------------------- Empfohlen
class RecentFile:
    __slots__ = ("path", "uri", "name", "time", "mime", "is_dir")

    def __init__(self, path, uri, name, ts, mime, is_dir=False):
        self.path, self.uri, self.name, self.time, self.mime = path, uri, name, ts, mime
        self.is_dir = is_dir


def recent_files(limit=40):
    mgr = Gtk.RecentManager.get_default()
    mounts = foreign_mounts()
    out, seen = [], set()
    items = sorted(mgr.get_items(), key=lambda it: -max(it.get_modified(), it.get_visited()))
    for it in items:
        uri = it.get_uri()
        if not uri.startswith("file://"):
            continue
        try:
            path = GLib.filename_from_uri(uri)[0]
        except GLib.Error:
            continue
        if path in seen:
            continue
        seen.add(path)
        mime = it.get_mime_type() or ""
        is_dir = mime in ("inode/directory", "x-directory/normal")
        # Auf FUSE nicht nachsehen (könnte hängen) – lokal schon
        if not _under(path, mounts):
            if not os.path.exists(path):
                continue
            is_dir = os.path.isdir(path)
        out.append(RecentFile(path, uri, it.get_display_name() or os.path.basename(path),
                              max(it.get_modified(), it.get_visited()),
                              "inode/directory" if is_dir else mime, is_dir))
        if len(out) >= limit:
            break
    return out


def forget_recent(uri):
    try:
        Gtk.RecentManager.get_default().remove_item(uri)
    except GLib.Error:
        pass


_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONTHS = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.",
           "Okt.", "Nov.", "Dez."]


def relative_time(ts, now=None):
    """„Vor 5 Minuten“, „Gestern um 14:03“, „12. Nov.“ – wie Windows."""
    now = now or time.time()
    delta = now - ts
    if delta < 60:
        return "Gerade eben"
    if delta < 3600:
        m = int(delta // 60)
        return f"Vor {m} Minute" if m == 1 else f"Vor {m} Minuten"
    d = datetime.datetime.fromtimestamp(ts)
    n = datetime.datetime.fromtimestamp(now)
    days = (n.date() - d.date()).days
    if days == 0:
        h = int(delta // 3600)
        return f"Vor {h} Stunde" if h == 1 else f"Vor {h} Stunden"
    if days == 1:
        return f"Gestern um {d:%H:%M}"
    if 1 < days < 7:
        return f"{_WEEKDAYS[d.weekday()]} um {d:%H:%M}"
    if d.year == n.year:
        return f"{d.day}. {_MONTHS[d.month - 1]}"
    return f"{d:%d.%m.%Y}"


# ------------------------------------------------------------- Dateiindex
class FileIndex:
    def __init__(self, root=None):
        self.root = root or os.path.expanduser("~")
        self.items = []          # (fold_name, path, is_dir)
        self.stamp = 0.0
        self._running = False
        self._lock = threading.Lock()
        self._listeners = []

    def connect_updated(self, cb):
        self._listeners.append(cb)

    def refresh(self, force=False):
        if self._running or (not force and time.time() - self.stamp < MAX_AGE):
            return
        self._running = True
        threading.Thread(target=self._run, name="startklar-index", daemon=True).start()

    def _run(self):
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)
        except (OSError, AttributeError):
            pass
        try:
            items = self._scan()
            with self._lock:
                self.items = items
                self.stamp = time.time()
        finally:
            self._running = False
        GLib.idle_add(self._notify)

    def _notify(self):
        for cb in self._listeners:
            cb()
        return False

    def _scan(self):
        mounts = {m for m in foreign_mounts() if _under(m, {self.root})}
        out, stack = [], [(self.root, 0)]
        deadline = time.monotonic() + TIME_BUDGET
        while stack and len(out) < MAX_ENTRIES and time.monotonic() < deadline:
            path, depth = stack.pop()
            try:
                with os.scandir(path) as it:
                    entries = list(it)
            except OSError:
                continue
            for de in entries:
                name = de.name
                if name.startswith(".") or name.endswith("~"):
                    continue
                full = os.path.join(path, name)
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if name in SKIP_DIRS or full in mounts:
                        continue
                    if depth < MAX_DEPTH:
                        stack.append((full, depth + 1))
                out.append((fold(name), full, is_dir))
        return out

    def search(self, query, limit_files=6, limit_dirs=4):
        q = fold(query).strip()
        if len(q) < 2:
            return [], []
        files, dirs = [], []
        with self._lock:
            items = self.items
        for key, path, is_dir in items:
            pos = key.find(q)
            if pos < 0:
                continue
            rank = (0 if pos == 0 else 1, path.count("/"), len(key))
            (dirs if is_dir else files).append((rank, path))
        files.sort()
        dirs.sort()
        return [p for _r, p in files[:limit_files]], [p for _r, p in dirs[:limit_dirs]]
