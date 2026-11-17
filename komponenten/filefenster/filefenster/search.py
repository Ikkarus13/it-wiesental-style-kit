"""Dateisuche im Hintergrund (rekursiv ab einem Ordner).

    job = SearchJob(root_uri, "bericht", show_hidden=False,
                    on_batch=lambda hits: ..., on_done=lambda total, limited: ...)
    job.start()
    job.cancel()

Treffer: Name enthält den Suchbegriff (Gross/Klein egal). Mit * oder ? wird
der Begriff als Platzhaltermuster auf den ganzen Namen angewandt
("*.txt", "Bericht 202?"). Filter wie unter Windows im Suchtext:
Änderungsdatum:Heute, Art:Bild, Grösse:Gross, Erweiterung:pdf (Werte mit
Leerzeichen in Anführungszeichen: Änderungsdatum:"Letzte Woche").
Mit content=True wird zusätzlich in Textdateien gesucht („Dateiinhalte“).
hits ist eine Liste von (Gio.File, Gio.FileInfo)
mit places.FILE_ATTRS. Alle Rückrufe kommen im GTK-Hauptthread an,
nach cancel() keine mehr.
"""
import collections
import os
import re
import stat
import threading

from gi.repository import Gio, GLib

from .places import FILE_ATTRS, content_type

MAX_RESULTS = 5000
BATCH_SIZE = 64
BATCH_INTERVAL_US = 250_000
# virtuelle Dateisysteme bei Suche ab "/" auslassen
SKIP_DIRS = {"/proc", "/sys", "/dev", "/run", "/snap"}


# ----------------------------------------------------------------------
# Suchfilter („Suchoptionen“)
# ----------------------------------------------------------------------
# Schlüssel mit lower() vergleichen – casefold() machte aus „Grösse“ „grösse“
FILTER_KEYS = {"änderungsdatum": "date", "datum": "date", "geändert": "date", "date": "date",
               "art": "kind", "kind": "kind",
               "grösse": "size", "groesse": "size", "size": "size",
               "erweiterung": "ext", "dateierweiterung": "ext", "ext": "ext"}
# Menü: (Schlüssel im Suchtext, Anzeige, [Werte])
FILTER_MENU = [
    ("Änderungsdatum", "Änderungsdatum", ["Heute", "Gestern", "Diese Woche", "Letzte Woche",
                                          "Dieser Monat", "Letzter Monat", "Dieses Jahr", "Letztes Jahr"]),
    ("Art", "Art", ["Dokument", "Ordner", "Bild", "Musik", "Video", "Programm", "Archiv", "E-Mail"]),
    ("Grösse", "Grösse", ["Leer (0 KB)", "Winzig (0 – 16 KB)", "Klein (16 KB – 1 MB)",
                        "Mittel (1 – 128 MB)", "Gross (128 MB – 1 GB)", "Riesig (1 – 4 GB)",
                        "Gigantisch (> 4 GB)"]),
]
_TOKEN = re.compile(r'(?<!\S)([^\s:"]+):("[^"]*"|\S+)')
_SIZES = {"leer": (0, 0), "winzig": (0, 16 << 10), "klein": (16 << 10, 1 << 20),
          "mittel": (1 << 20, 128 << 20), "gross": (128 << 20, 1 << 30),
          "riesig": (1 << 30, 4 << 30), "gigantisch": (4 << 30, None)}
_DOC_TYPES = ("application/pdf", "application/msword", "application/rtf", "application/vnd.ms-",
              "application/vnd.openxmlformats", "application/vnd.oasis.opendocument",
              "application/x-tex", "text/")
_PROG_TYPES = ("application/x-executable", "application/x-sharedlib", "application/x-ms-dos-executable",
               "application/x-msdownload", "application/x-msi", "application/vnd.debian.binary-package",
               "application/x-shellscript", "application/x-desktop", "application/x-appimage")
_ARCH_TYPES = ("application/zip", "application/x-tar", "application/gzip", "application/x-7z",
               "application/x-rar", "application/vnd.rar", "application/x-xz", "application/x-bzip",
               "application/x-compressed-tar", "application/vnd.ms-cab-compressed", "application/x-cd-image")


def parse_query(query):
    """→ (Suchtext ohne Filter, {"date": …, "kind": …, "size": …, "ext": …})."""
    filters = {}

    def take(m):
        key = FILTER_KEYS.get(m.group(1).lower())
        if key is None:
            return m.group(0)
        filters[key] = m.group(2).strip('"').strip().casefold()
        return ""
    text = _TOKEN.sub(take, query or "")
    return " ".join(text.split()), filters


def set_filter(query, label, value):
    """Suchtext mit gesetztem Filter (ersetzt einen vorhandenen gleicher Art)."""
    key = FILTER_KEYS.get(label.lower())
    rest = _TOKEN.sub(lambda m: "" if FILTER_KEYS.get(m.group(1).lower()) == key else m.group(0),
                      query or "")
    val = f'"{value}"' if " " in value else value
    return " ".join((rest.split() + [f"{label}:{val}"]))


def _date_range(word, now=None):
    import datetime
    now = now or datetime.datetime.now()
    today = datetime.datetime.combine(now.date(), datetime.time())
    week = today - datetime.timedelta(days=today.weekday())
    month = today.replace(day=1)
    year = today.replace(month=1, day=1)
    prev_month = (month - datetime.timedelta(days=1)).replace(day=1)
    ranges = {"heute": (today, None), "gestern": (today - datetime.timedelta(days=1), today),
              "diese woche": (week, None), "letzte woche": (week - datetime.timedelta(days=7), week),
              "dieser monat": (month, None), "letzter monat": (prev_month, month),
              "dieses jahr": (year, None), "letztes jahr": (year.replace(year=year.year - 1), year)}
    r = ranges.get(word)
    if r is None:
        return None
    return (r[0].timestamp(), r[1].timestamp() if r[1] else None)


def make_filter(filters):
    """Gio.FileInfo → passt es zu allen Filtern?"""
    if not filters:
        return lambda _info: True
    date = _date_range(filters["date"]) if "date" in filters else None
    size_word = filters.get("size", "").split(" (")[0].split()[0] if filters.get("size") else ""
    size = _SIZES.get(size_word)
    kind = filters.get("kind")
    ext = filters.get("ext", "").lstrip(".")

    def ok(info):
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        if "date" in filters:
            if date is None:
                return False
            ts = info.get_attribute_uint64("time::modified") if info.has_attribute("time::modified") else 0
            if ts < date[0] or (date[1] is not None and ts >= date[1]):
                return False
        if "size" in filters:
            if size is None or is_dir:
                return False
            n = info.get_size()
            lo, hi = size              # „leer“ = genau 0, sonst lo < n ≤ hi
            if (hi == 0 and n != 0) or (hi != 0 and (n <= lo or (hi is not None and n > hi))):
                return False
        if ext:
            name = (info.get_display_name() or "").casefold()
            if is_dir or not name.endswith("." + ext):
                return False
        if kind:
            ct = content_type(info)
            want = {"ordner": is_dir,
                    "bild": ct.startswith("image/"),
                    "musik": ct.startswith("audio/"),
                    "video": ct.startswith("video/"),
                    "dokument": not is_dir and ct.startswith(_DOC_TYPES),
                    "programm": not is_dir and ct.startswith(_PROG_TYPES),
                    "archiv": not is_dir and ct.startswith(_ARCH_TYPES),
                    "e-mail": ct in ("message/rfc822", "application/vnd.ms-outlook")}.get(kind)
            if not want:
                return False
        return True
    return ok


def _text_contains(path, needle, limit=5 << 20):
    """Dateiinhalt (Textdateien bis 5 MB) enthält den Suchbegriff?"""
    ct, _u = Gio.content_type_guess(path, None)
    if not ct or not Gio.content_type_is_a(ct, "text/plain"):
        return False
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return False          # FIFO/Gerät namens *.txt: open() blockierte die Suche
        from . import cloudstatus
        if cloudstatus.online_only(path):
            return False          # OneDrive „nur online“: nicht herunterladen
        with open(path, "rb") as fh:
            return needle in fh.read().decode("utf-8", "ignore").casefold()
    except OSError:
        return False


def make_matcher(query):
    q = query.strip().casefold()
    if not q:
        return lambda _name: False
    if "*" in q or "?" in q:
        parts = []
        for ch in q:
            if ch == "*":
                parts.append(".*")
            elif ch == "?":
                parts.append(".")
            else:
                parts.append(re.escape(ch))
        rx = re.compile("".join(parts), re.S)
        return lambda name: rx.fullmatch(name.casefold()) is not None
    return lambda name: q in name.casefold()


class SearchJob:
    def __init__(self, root_uri, query, show_hidden, on_batch, on_done, content=False):
        self.root = Gio.File.new_for_uri(root_uri)
        self.query = query
        self.show_hidden = show_hidden
        self.on_batch = on_batch
        self.on_done = on_done
        text, filters = parse_query(query)
        self.text = text.casefold()
        # nur Filter, kein Suchtext: alle Namen prüfen (dann filtern)
        self._match = make_matcher(text) if text else (lambda _n: bool(filters))
        self._filter = make_filter(filters)
        self.content = content and bool(text) and "*" not in text and "?" not in text
        self._cancel = threading.Event()
        self._thread = None
        self.count = 0
        self.limited = False
        self.running = False

    # -- Steuerung -------------------------------------------------------
    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel.set()
        self.running = False

    @property
    def cancelled(self):
        return self._cancel.is_set()

    # -- Thread ----------------------------------------------------------
    def _run(self):
        self._batch = []
        self._last_flush = GLib.get_monotonic_time()
        try:
            root_path = self.root.get_path()
            if root_path:
                self._walk_local(root_path)
            else:
                self._walk_gio(self.root)
        except Exception as exc:
            print(f"[filefenster] Suche abgebrochen: {exc}")
        self._flush(force=True)
        if not self.cancelled:
            GLib.idle_add(self._deliver_done, self.count, self.limited)

    def _add(self, gfile, info=None):
        if info is None:
            try:
                info = gfile.query_info(FILE_ATTRS, Gio.FileQueryInfoFlags.NONE, None)
            except GLib.Error:
                try:
                    info = gfile.query_info(FILE_ATTRS, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
                except GLib.Error:
                    return
        if not self._filter(info):
            return
        self._batch.append((gfile, info))
        self.count += 1
        if self.count >= MAX_RESULTS:
            self.limited = True
        self._flush()

    def _flush(self, force=False):
        if not self._batch:
            return
        now = GLib.get_monotonic_time()
        if force or len(self._batch) >= BATCH_SIZE or now - self._last_flush > BATCH_INTERVAL_US:
            batch, self._batch = self._batch, []
            self._last_flush = now
            GLib.idle_add(self._deliver_batch, batch)

    def _walk_local(self, root):
        queue = collections.deque([root])
        while queue and not self.cancelled and not self.limited:
            d = queue.popleft()
            try:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: e.name.casefold())
            except OSError:
                continue
            for e in entries:
                if self.cancelled or self.limited:
                    return
                name = e.name
                if not self.show_hidden and name.startswith("."):
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                if self._match(name) or (self.content and not is_dir
                                         and _text_contains(e.path, self.text)):
                    self._add(Gio.File.new_for_path(e.path))
                if is_dir and e.path not in SKIP_DIRS:
                    queue.append(e.path)
            self._flush()

    def _walk_gio(self, root):
        attrs = "standard::name,standard::type,standard::is-hidden"
        queue = collections.deque([root])
        while queue and not self.cancelled and not self.limited:
            d = queue.popleft()
            try:
                en = d.enumerate_children(attrs, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
            except GLib.Error:
                continue
            try:
                while not self.cancelled and not self.limited:
                    try:
                        info = en.next_file(None)
                    except GLib.Error:
                        break
                    if info is None:
                        break
                    name = info.get_name()
                    if not self.show_hidden and (info.get_attribute_boolean("standard::is-hidden") or name.startswith(".")):
                        continue
                    child = d.get_child(name)
                    if self._match(name):
                        self._add(child)
                    if info.get_file_type() == Gio.FileType.DIRECTORY:
                        queue.append(child)
            finally:
                en.close(None)
            self._flush()

    # -- Hauptthread -----------------------------------------------------
    def _deliver_batch(self, batch):
        if not self.cancelled and self.on_batch:
            self.on_batch(batch)
        return False

    def _deliver_done(self, count, limited):
        self.running = False
        if not self.cancelled and self.on_done:
            self.on_done(count, limited)
        return False
