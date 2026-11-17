"""Dateioperationen für die Dateiliste.

- Zwischenablage kompatibel zu Nemo/Nautilus ("x-special/gnome-copied-files",
  "text/uri-list", Text mit Pfaden) – schreiben und lesen.
- Einfügen (Kopieren/Verschieben) in einem Hintergrund-Thread. Verschieben
  über Dateisystemgrenzen = kopieren und Quelle NUR nach vollständig
  Kopie der jeweiligen Datei löschen. Kopie in denselben Ordner bekommt
  "Name - Kopie.ext" (wie Windows); sonst fragt das Fortschrittsfenster
  (progress.py) nach Ersetzen / Überspringen / Beide behalten.
- Löschen: Papierkorb; endgültig nur nach Bestätigung.
- Neuer Ordner / neue Datei mit "Neuer Ordner (2)"-Schema.

Alle Rückmeldungen (on_done …) kommen im GTK-Hauptthread an.
"""
import errno
import os
import threading

from gi.repository import Gdk, Gio, GLib, Gtk

GNOME_COPIED = "x-special/gnome-copied-files"
URI_LIST = "text/uri-list"
_T_GNOME, _T_URIS, _T_TEXT = 0, 1, 2

NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
COPY_FLAGS = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA


# ---------------------------------------------------------------------------
# Namen
# ---------------------------------------------------------------------------
def split_ext(name, is_dir=False):
    """("Bericht", ".txt"); Ordner und Punktdateien ohne Erweiterung."""
    if is_dir:
        return name, ""
    stem, ext = os.path.splitext(name)
    if not stem or not ext or ext == ".":
        return name, ""
    return stem, ext


def _exists(gfile):
    """Existiert (auch als defekter Link)?"""
    try:
        gfile.query_info("standard::type", NOFOLLOW, None)
        return True
    except GLib.Error:
        return False


def numbered_child(dir_gfile, name, is_dir=False):
    """Freier Name nach dem Schema "Neuer Ordner", "Neuer Ordner (2)" …"""
    stem, ext = split_ext(name, is_dir)
    candidate = dir_gfile.get_child(name)
    n = 2
    while _exists(candidate):
        candidate = dir_gfile.get_child(f"{stem} ({n}){ext}")
        n += 1
    return candidate


def copy_child(dir_gfile, name, is_dir=False):
    """Freier Name nach Windows-Schema "Name - Kopie.ext", "Name - Kopie (2).ext"."""
    candidate = dir_gfile.get_child(name)
    if not _exists(candidate):
        return candidate
    stem, ext = split_ext(name, is_dir)
    candidate = dir_gfile.get_child(f"{stem} - Kopie{ext}")
    n = 2
    while _exists(candidate):
        candidate = dir_gfile.get_child(f"{stem} - Kopie ({n}){ext}")
        n += 1
    return candidate


def _display(gfile):
    uri = gfile.get_uri()
    if uri.startswith("archive:"):      # lesbar statt /run/user/…/gvfs/archive:host=…
        from . import places
        arc = places.archive_parts(uri)
        if arc:
            outer = _display(arc[0])
            return "/".join([outer] + arc[2]) if arc[2] else outer
    path = gfile.get_path()
    return path if path else uri


def _basename(gfile):
    from .places import safe_text
    try:
        info = gfile.query_info("standard::display-name", NOFOLLOW, None)
        return safe_text(info.get_display_name())
    except GLib.Error:
        return safe_text(gfile.get_basename() or gfile.get_uri())


def valid_name(name):
    """Ein einzelner Name? Fremde Server/Archive können „..“ oder „a/b“ liefern –
    get_child() löst das auf und landet ausserhalb des Ordners."""
    return bool(name) and name not in (".", "..") and "/" not in name and "\0" not in name


def _io_error(code, msg):
    return GLib.Error.new_literal(Gio.io_error_quark(), msg, int(code))


def safe_child(parent, name):
    if not valid_name(name):
        from .places import safe_text
        raise _io_error(Gio.IOErrorEnum.INVALID_FILENAME, f"Unzulässiger Name „{safe_text(name)}“")
    return parent.get_child(name)


def is_inside(dest, f):
    """Liegt dest in f (oder ist f) – auch über einen verknüpften Pfad?"""
    if dest.equal(f) or dest.has_prefix(f):
        return True
    path = dest.get_path() if dest.get_uri().startswith("file:") else None
    if path is None or not f.get_uri().startswith("file:") or f.get_path() is None:
        return False
    rd = os.path.realpath(path)
    head, tail = os.path.split(os.path.normpath(f.get_path()))
    rf = os.path.join(os.path.realpath(head), tail) if tail else None   # f selbst nicht auflösen
    if rf is None or not os.path.isdir(rf) or os.path.islink(rf):
        return False
    return rd == rf or rd.startswith(rf.rstrip("/") + "/")


# ---------------------------------------------------------------------------
# Zwischenablage
# ---------------------------------------------------------------------------
class _Clipboard:
    """Besitzt die CLIPBOARD-Auswahl für Dateien (über ein unsichtbares
    Widget, da Gtk.Clipboard.set_with_data in PyGObject fehlt) und merkt
    sich, ob die Zwischenablage Dateien enthält und welche ausgeschnitten
    sind (auch wenn Nemo/Nautilus sie ausgeschnitten hat)."""

    def __init__(self):
        self._owner = Gtk.Invisible()
        self._owner.realize()
        names = [(GNOME_COPIED, _T_GNOME), (URI_LIST, _T_URIS),
                 ("UTF8_STRING", _T_TEXT), ("text/plain;charset=utf-8", _T_TEXT),
                 ("text/plain", _T_TEXT), ("STRING", _T_TEXT), ("TEXT", _T_TEXT)]
        targets = [Gtk.TargetEntry.new(n, 0, i) for n, i in names]
        Gtk.selection_add_targets(self._owner, Gdk.SELECTION_CLIPBOARD, targets)
        self._owner.connect("selection-get", self._on_get)
        self._owner.connect("selection-clear-event", self._on_clear)
        self._uris = []
        self._cut = False
        self._own = False
        self._can_paste = False
        self._cut_set = frozenset()
        self._listeners = []
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.clipboard.connect("owner-change", self._on_owner_change)
        self._refresh()

    # -- Beobachter ------------------------------------------------------
    def connect_changed(self, callback):
        """callback() bei Änderung von can_paste/cut_uris."""
        self._listeners.append(callback)

    def disconnect_changed(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception as exc:  # ein kaputter Beobachter darf nicht alles stören
                print(f"[filefenster] Zwischenablage-Beobachter: {exc}")

    # -- Schreiben -------------------------------------------------------
    def set_files(self, gfiles, cut):
        uris = [g.get_uri() for g in gfiles]
        if not uris:
            return False
        ok = Gtk.selection_owner_set(self._owner, Gdk.SELECTION_CLIPBOARD,
                                     Gtk.get_current_event_time())
        if not ok:
            return False
        self._own = True
        self._uris = uris
        self._cut = bool(cut)
        self._can_paste = True
        self._cut_set = frozenset(uris) if cut else frozenset()
        self.clipboard.set_can_store(None)
        self._notify()
        return True

    def clear_if_cut(self):
        """Nach Einfügen ausgeschnittener Elemente die Zwischenablage leeren."""
        if self._own and self._cut:
            Gtk.selection_owner_set(None, Gdk.SELECTION_CLIPBOARD, Gtk.get_current_event_time())
            self._own = False
            self._uris = []
            self._cut = False
            self._cut_set = frozenset()
            self._can_paste = False
            self._notify()

    def _on_get(self, _widget, sdata, info, _time):
        if info == _T_GNOME:
            payload = ("cut" if self._cut else "copy") + "\n" + "\n".join(self._uris)
            sdata.set(sdata.get_target(), 8, payload.encode("utf-8"))
        elif info == _T_URIS:
            payload = "\r\n".join(self._uris) + "\r\n"
            sdata.set(sdata.get_target(), 8, payload.encode("utf-8"))
        else:
            lines = []
            for u in self._uris:
                p = Gio.File.new_for_uri(u).get_path()
                lines.append(p if p else u)
            sdata.set_text("\n".join(lines), -1)

    def _on_clear(self, *_a):
        self._own = False
        self._uris = []
        self._cut = False
        self._cut_set = frozenset()
        self._refresh()
        return False

    # -- Lesen -----------------------------------------------------------
    def _on_owner_change(self, *_a):
        if not self._own:
            self._refresh()

    def _refresh(self):
        """Inhalt neu prüfen (asynchron), dann Beobachter informieren."""
        if self._own:
            self._notify()
            return

        def done(uris, cut):
            if self._own:  # inzwischen selbst übernommen
                return
            self._can_paste = bool(uris)
            self._cut_set = frozenset(uris) if cut else frozenset()
            self._notify()
        self.read_files(done)

    def read_files(self, callback, with_trust=False):
        """callback(uris: list[str], cut: bool) – leer, wenn keine Dateien.
        with_trust: callback(uris, cut, trusted) – trusted=False, wenn die Angaben
        nur als Text oder aus einem Webbrowser kommen (eine Webseite kann beliebige
        Pfade in die Zwischenablage legen)."""
        def give(uris, cut, trusted):
            if with_trust:
                callback(uris, cut, trusted)
            else:
                callback(uris, cut)

        if self._own:
            uris, cut = list(self._uris), self._cut

            def deliver():
                give(uris, cut, True)
                return False
            GLib.idle_add(deliver)
            return

        def got_gnome(_cb, sdata, *_a):
            uris, cut = _parse_gnome(sdata)     # setzen nur Dateimanager
            if uris:
                give(uris, cut, True)
            else:
                self.clipboard.request_contents(Gdk.Atom.intern(URI_LIST, False), got_urilist)

        def got_urilist(_cb, sdata, *_a):
            uris = _parse_urilist(sdata)
            if not uris:
                self.clipboard.request_text(got_text)
            elif not with_trust:
                callback(uris, False)
            else:
                def got_targets(_c, atoms, *_b):
                    names = [a.name() for a in (atoms or [])]
                    callback(uris, False, not browser_targets(names))
                self.clipboard.request_targets(got_targets)

        def got_text(_cb, text, *_a):
            give(_parse_text_paths(text), False, False)

        self.clipboard.request_contents(Gdk.Atom.intern(GNOME_COPIED, False), got_gnome)

    @property
    def can_paste(self):
        return self._can_paste

    @property
    def cut_uris(self):
        return self._cut_set


def _sdata_text(sdata):
    if sdata is None or sdata.get_length() <= 0:
        return ""
    data = sdata.get_data()
    if not data:
        return ""
    return bytes(data).decode("utf-8", "replace")


def _parse_gnome(sdata):
    lines = [ln.strip() for ln in _sdata_text(sdata).splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or lines[0] not in ("copy", "cut"):
        return [], False
    uris = [ln for ln in lines[1:] if not ln.startswith("#") and ":" in ln]
    return uris, lines[0] == "cut"


def _parse_urilist(sdata):
    out = []
    for ln in _sdata_text(sdata).splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and ":" in ln:
            out.append(ln)
    return out


_BROWSER_TARGETS = ("text/x-moz-url", "_NETSCAPE_URL", "text/x-moz-url-data",
                    "application/x-moz-file-promise-url", "text/x-moz-place")


def browser_targets(names):
    """Stammen diese Datenformate (Ziehen/Zwischenablage) von einem Webbrowser?"""
    return any(n in _BROWSER_TARGETS or n.startswith(("chromium/", "application/x-moz"))
               for n in names)


def confirm_foreign(uris, parent):
    """Rückfrage vor dem Einfügen von Dateiangaben aus Text oder einem Webbrowser."""
    from .places import safe_text
    names = [safe_text(Gio.File.new_for_uri(u).get_parse_name()) for u in uris[:6]]
    more = f"\n… und {len(uris) - 6} weitere" if len(uris) > 6 else ""
    text = ("Die Zwischenablage enthält Dateiangaben als Text oder aus einem Webbrowser. "
            "Eine Webseite kann so beliebige Dateien dieses PCs vorschlagen.\n\n"
            + "\n".join(names) + more + "\n\nDiese Elemente hierher kopieren?")
    return _message(parent, text, "warning", "Einfügen",
                    buttons=["Kopieren", "Abbrechen"], default=1) == 0


def _parse_text_paths(text):
    """Nur wenn JEDE Zeile ein vorhandener absoluter Pfad oder file://-URI ist."""
    if not text or len(text) > 64 * 1024:    # wird bei jedem Wechsel geprüft (Hauptthread)
        return []
    lines = text.splitlines()
    if len(lines) > 200:
        return []
    out = []
    for ln in lines:
        ln = ln.strip().strip('"')
        if not ln:
            continue
        if ln.startswith("file://"):
            f = Gio.File.new_for_uri(ln)
        elif ln.startswith("/"):
            f = Gio.File.new_for_path(ln)
        else:
            return []
        if not _exists(f):
            return []
        out.append(f.get_uri())
    return out


_clip = None


def clipboard():
    global _clip
    if _clip is None:
        _clip = _Clipboard()
    return _clip


def set_clipboard(gfiles, cut=False):
    """Dateien in die Zwischenablage (für Start/Dieser PC/Navigationsbereich)."""
    return clipboard().set_files(list(gfiles), cut=cut)


def copy_files(gfiles):
    return clipboard().set_files(gfiles, cut=False)


def cut_files(gfiles):
    return clipboard().set_files(gfiles, cut=True)


def can_paste():
    return clipboard().can_paste


def cut_uris():
    return clipboard().cut_uris


def copy_paths(gfiles):
    """ "Als Pfad kopieren": Pfade in Anführungszeichen, zeilenweise."""
    lines = [f'"{_display(g)}"' for g in gfiles]
    if lines:
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("\n".join(lines), -1)


# ---------------------------------------------------------------------------
# Dialoge
# ---------------------------------------------------------------------------
def _message(parent, text, kind, title, buttons=None, default=0):
    """Dialog im Windows-Stil (propsdialog.message), blockierend."""
    from . import propsdialog  # spät importieren (kein Zyklus beim Start)
    if parent is not None and not isinstance(parent, Gtk.Window):
        parent = parent.get_toplevel() if isinstance(parent, Gtk.Widget) else None
    if parent is not None and not parent.get_mapped():
        parent = None  # Fenster inzwischen geschlossen (Vorgang lief weiter)
    return propsdialog.message(parent, text, kind=kind, title=title,
                               buttons=buttons, default=default)


def show_errors(parent, title, errors):
    """errors: Liste von (Name/Pfad, Meldung)."""
    if not errors:
        return
    from .places import safe_text
    lines = [f"{safe_text(str(name))}: {safe_text(str(msg))}" for name, msg in errors[:12]]
    if len(errors) > 12:
        lines.append(f"… und {len(errors) - 12} weitere Fehler")
    _message(parent, title + "\n\n" + "\n".join(lines), "error", "FileFenster")


def info_dialog(parent, title, text, secondary=None):
    """Hinweisdialog (blockierend, kurz)."""
    _message(parent, text + ("\n\n" + secondary if secondary else ""), "warning", title)


def ask_yes_no(parent, title, text, secondary=None, default_yes=True):
    r = _message(parent, text + ("\n\n" + secondary if secondary else ""), "warning", title,
                 buttons=["Ja", "Nein"], default=0 if default_yes else 1)
    return r == 0


def confirm_permanent_delete(parent, gfiles, reason=None):
    """Windows-Wortlaut. True = löschen."""
    n = len(gfiles)
    if n == 1:
        is_dir = False
        try:
            info = gfiles[0].query_info("standard::type", NOFOLLOW, None)
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        except GLib.Error:
            pass
        title = "Ordner löschen" if is_dir else "Datei löschen"
        text = ("Möchten Sie diesen Ordner endgültig löschen?" if is_dir
                else "Möchten Sie diese Datei endgültig löschen?")
        secondary = _basename(gfiles[0])
    else:
        title = "Mehrere Elemente löschen"
        text = f"Möchten Sie diese {n} Elemente endgültig löschen?"
        secondary = None
    if reason:
        secondary = reason + ("\n\n" + secondary if secondary else "")
    return ask_yes_no(parent, title, text, secondary, default_yes=True)


# ---------------------------------------------------------------------------
# Hintergrund-Vorgänge (laufen im Thread; progress.py liest den Stand ab)
# ---------------------------------------------------------------------------
SCAN_ATTRS = ("standard::name,standard::display-name,standard::type,standard::size,"
              "time::modified")
MOVE_FLAGS = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE
OVERWRITE = Gio.FileCopyFlags.OVERWRITE


def _is_dir(info):
    return info.get_file_type() == Gio.FileType.DIRECTORY


def _io(exc, code):
    return isinstance(exc, GLib.Error) and exc.matches(Gio.io_error_quark(), code)


def _cancelled(exc):
    return _io(exc, Gio.IOErrorEnum.CANCELLED)


def _list_children(gfile, cancellable):
    en = gfile.enumerate_children(SCAN_ATTRS, NOFOLLOW, cancellable)
    out = []
    try:
        while True:
            ci = en.next_file(cancellable)
            if ci is None:
                return out
            out.append(ci)
    finally:
        en.close(None)


def _measure(gfile, info, cancellable):
    """(Bytes, Elemente) eines Eintrags samt Inhalt (Ordner zählen mit)."""
    if not _is_dir(info):
        return info.get_size(), 1
    total, items = 0, 1
    for ci in _list_children(gfile, cancellable):
        b, n = _measure(safe_child(gfile, ci.get_name()), ci, cancellable)
        total += b
        items += n
    return total, items


def _in_trash(gfile):
    return gfile.get_uri().startswith("trash:")


def _fs_id(gfile):
    try:
        return gfile.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE,
                                None).get_attribute_string("id::filesystem")
    except GLib.Error:
        return None


def _delete_recursive(gfile, cancellable=None, on_item=None):
    """Löscht gfile samt Inhalt. Symlinks werden NIE verfolgt."""
    if gfile.get_uri().startswith("file:") and gfile.get_path():
        return _delete_local(gfile.get_path(), cancellable, on_item)
    info = gfile.query_info("standard::type", NOFOLLOW, cancellable)
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        for ci in _list_children(gfile, cancellable):
            _delete_recursive(safe_child(gfile, ci.get_name()), cancellable, on_item)
    gfile.delete(cancellable)
    if on_item:
        on_item()


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _delete_local(path, cancellable=None, on_item=None):
    """Lokal über Verzeichnis-Deskriptoren löschen: tauscht jemand (z. B. in /tmp
    oder auf einer Freigabe) während des Löschens einen Ordner gegen einen
    Symlink aus, wird nie dessen Ziel gelöscht – nur, was wirklich darunter liegt."""
    head, name = os.path.split(os.path.normpath(path))
    if not name or name in (".", ".."):
        raise _io_error(Gio.IOErrorEnum.INVALID_FILENAME, f"Unzulässiger Pfad: {path}")
    try:
        pfd = os.open(head or "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            _rm_at(pfd, name, cancellable, on_item)
        finally:
            os.close(pfd)
    except OSError as exc:
        code = Gio.io_error_from_errno(exc.errno) if exc.errno else Gio.IOErrorEnum.FAILED
        raise _io_error(code, exc.strerror or str(exc))
    except RecursionError:
        raise _io_error(Gio.IOErrorEnum.FAILED, "Der Ordner ist zu tief verschachtelt.")


def _rm_at(dfd, name, cancellable, on_item):
    if cancellable is not None:
        cancellable.set_error_if_cancelled()
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=dfd)
    except OSError as exc:
        if exc.errno not in (errno.ENOTDIR, errno.ELOOP):
            raise
        os.unlink(name, dir_fd=dfd)        # Datei oder der Symlink selbst – nie sein Ziel
    else:
        try:
            for n in os.listdir(fd):
                _rm_at(fd, n, cancellable, on_item)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=dfd)
    if on_item:
        on_item()


class _Job(threading.Thread):
    """Gemeinsamer Stand für das Fortschrittsfenster. Die Felder werden im
    Thread geschrieben und vom Hauptthread nur gelesen."""
    kind = ""

    def __init__(self):
        super().__init__(daemon=True)
        self.cancellable = Gio.Cancellable()
        self.errors = []
        self.phase = "scan"          # scan → run → done
        self.total_bytes = self.done_bytes = 0
        self.total_items = self.done_items = 0
        self.current = ""            # Name des aktuellen Elements
        self.conflict = None         # offene Rückfrage, siehe _ask()
        self._go = threading.Event()  # gelöscht = angehalten
        self._go.set()
        self._app = None

    # -- Steuerung (Hauptthread) ----------------------------------------
    @property
    def paused(self):
        return not self._go.is_set()

    def pause(self):
        self._go.clear()

    def resume(self):
        self._go.set()

    def cancel(self):
        self.cancellable.cancel()
        self._go.set()

    def is_cancelled(self):
        return self.cancellable.is_cancelled()

    def answer(self, choice, for_all=False):
        """Antwort auf die offene Rückfrage: "replace", "skip" oder "keep"."""
        req = self.conflict
        if req is not None:
            req["answer"], req["all"] = choice, for_all
            req["event"].set()

    def fraction(self):
        if self.total_bytes > 0:
            f = self.done_bytes / self.total_bytes
        elif self.total_items > 0:
            f = self.done_items / self.total_items
        else:
            f = 0.0
        return max(0.0, min(1.0, f))

    # -- im Thread ------------------------------------------------------
    def _checkpoint(self):
        if not self._go.is_set():
            self._go.wait()
        self.cancellable.set_error_if_cancelled()

    def _ask(self, src, sinfo, dst, dinfo, can_replace):
        """Blockiert, bis das Fortschrittsfenster eine Antwort liefert."""
        req = {"src": src, "sinfo": sinfo, "dst": dst, "dinfo": dinfo,
               "can_replace": can_replace, "event": threading.Event(),
               "answer": "skip", "all": False}
        self.conflict = req
        while not req["event"].wait(0.2):
            if self.cancellable.is_cancelled():
                break
        self.conflict = None
        self.cancellable.set_error_if_cancelled()
        return req["answer"], req["all"]

    def _err(self, gfile, exc):
        msg = exc.message if isinstance(exc, GLib.Error) else str(exc)
        self.errors.append((_display(gfile), msg))

    def _finish(self):
        from . import progress
        progress.unwatch(self)
        try:
            self._call_done()
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            _running.discard(self)
            if self._app is not None:
                self._app.release()
        return False

    def _call_done(self):
        pass


class TransferJob(_Job):
    """Kopieren (move=False) oder Verschieben (move=True) nach dest_dir.

    Namenskonflikte: Kopie in denselben Ordner → "Name - Kopie" (wie
    Windows); sonst Rückfrage Ersetzen / Überspringen / Beide behalten,
    gleichnamige Ordner werden zusammengeführt. Verschieben über
    Dateisystemgrenzen löscht jede Quelldatei erst nach ihrer vollständigen
    Kopie; Übersprungenes bleibt in der Quelle.

    on_done(created: list[Gio.File], errors: list[(name, msg)]) im Hauptthread.
    """

    def __init__(self, sources, dest_dir, move=False, on_done=None, targets=None, kind=None):
        super().__init__()
        self.kind = kind or ("move" if move else "copy")
        self.sources = list(sources)
        self.targets = list(targets) if targets else None  # exakte Ziele (Wiederherstellen)
        self.dest = dest_dir
        self.move = move
        self.on_done = on_done
        self.created = []
        self.pairs = []          # (Quelle, neues Ziel) – für Rückgängig
        self._all_choice = None
        self._skipped = 0        # beim Zusammenführen übersprungene Elemente

    def _call_done(self):
        if self.on_done:
            self.on_done(self.created, self.errors)

    def run(self):
        try:
            plan = self._scan()
            self.phase = "run"
            done_b = done_n = 0
            for src, info, size, items, target in plan:
                if self.is_cancelled():
                    break
                self.current = info.get_display_name()
                try:
                    self._top(src, info, target)
                except GLib.Error as exc:
                    if _cancelled(exc):
                        break
                    self._err(src, exc)
                except Exception as exc:  # unerwartet, aber nicht abstürzen
                    self._err(src, exc)
                done_b += size
                done_n += items
                self.done_bytes, self.done_items = done_b, done_n
        except GLib.Error as exc:
            if not _cancelled(exc):
                self._err(self.dest, exc)
        finally:
            self.phase = "done"
            GLib.idle_add(self._finish)

    def _scan(self):
        plan = []
        dest_fs = _fs_id(self.dest) if self.move and self.dest is not None else None
        for i, src in enumerate(self.sources):
            target = self.targets[i] if self.targets else None
            self.cancellable.set_error_if_cancelled()
            try:
                info = src.query_info(SCAN_ATTRS + ",id::filesystem", NOFOLLOW, self.cancellable)
                if dest_fs and info.get_attribute_string("id::filesystem") == dest_fs:
                    size, items = 0, 1  # Umbenennen im selben Dateisystem: sofort
                else:
                    size, items = _measure(src, info, self.cancellable)
            except GLib.Error as exc:
                if _cancelled(exc):
                    raise
                self._err(src, exc)
                continue
            except RecursionError:
                self.errors.append((_display(src), "Der Ordner ist zu tief verschachtelt."))
                continue
            plan.append((src, info, size, items, target))
            self.total_bytes += size
            self.total_items += items
        return plan

    def _top(self, src, info, target=None):
        if target is not None:
            parent = target.get_parent()
            if parent is not None and not _exists(parent):
                parent.make_directory_with_parents(self.cancellable)  # wie Windows
            dst, mode = self._resolve(src, info, target)
            if mode != "skip":
                self._transfer_top(src, info, dst, mode)
            return
        is_dir = _is_dir(info)
        parent = src.get_parent()
        same_dir = parent is not None and parent.equal(self.dest)
        if self.move and same_dir:
            return  # Ausschneiden + Einfügen im selben Ordner: nichts tun (wie Windows)
        name = info.get_name()
        if parent is None:      # Laufwerks-/Freigabe-Wurzel („/“): unter ihrem Anzeigenamen
            name = (info.get_display_name() or "").replace("/", "_").strip() or "Laufwerk"
        if not valid_name(name):
            self._err(src, _io_error(Gio.IOErrorEnum.INVALID_FILENAME, "Unzulässiger Name"))
            return
        if src.equal(self.dest) or (is_dir and is_inside(self.dest, src)):
            self.errors.append((_display(src), "Der Zielordner ist ein Unterordner des Quellordners."))
            return
        if same_dir:
            dst, mode = copy_child(self.dest, name, is_dir), "new"
        else:
            dst, mode = self._resolve(src, info, self.dest.get_child(name))
            if mode == "skip":
                return
        self._transfer_top(src, info, dst, mode)

    def _transfer_top(self, src, info, dst, mode):
        errors_before, skipped_before = len(self.errors), self._skipped
        self._transfer(src, info, dst, mode)
        # Papierkorb-Eintrag nur als Ganzes entfernbar – also nur, wenn wirklich
        # alles herausgeholt wurde (Übersprungenes bleibt im Papierkorb)
        if self.move and _in_trash(src) and len(self.errors) == errors_before \
                and self._skipped == skipped_before and _exists(src):
            src.delete(self.cancellable)
        self.created.append(dst)
        if mode == "new":
            self.pairs.append((src, dst))

    def _resolve(self, src, sinfo, dst):
        """(Ziel, Modus) mit Modus "new", "replace", "merge" oder "skip"."""
        try:
            dinfo = dst.query_info(SCAN_ATTRS, NOFOLLOW, self.cancellable)
        except GLib.Error as exc:
            if _cancelled(exc):
                raise
            return dst, "new"
        if src.equal(dst):
            return dst, "skip"
        if _is_dir(sinfo) and _is_dir(dinfo):
            return dst, "merge"
        can_replace = not _is_dir(sinfo) and not _is_dir(dinfo)
        choice = self._all_choice
        if choice is None or (choice == "replace" and not can_replace):
            choice, for_all = self._ask(src, sinfo, dst, dinfo, can_replace)
            if for_all:
                self._all_choice = choice
        if choice == "replace" and can_replace:
            return dst, "replace"
        if choice == "keep":
            return numbered_child(dst.get_parent(), sinfo.get_name(), _is_dir(sinfo)), "new"
        return dst, "skip"

    def _count(self, info):
        if not _is_dir(info):
            self.done_bytes += info.get_size()
        self.done_items += 1

    def _transfer(self, src, info, dst, mode):
        self._checkpoint()
        if self.move and mode != "merge":
            flags = MOVE_FLAGS | (OVERWRITE if mode == "replace" else Gio.FileCopyFlags.NONE)
            try:
                src.move(dst, flags, self.cancellable, None, None)
                self._count(info)
                return
            except GLib.Error as exc:
                # Inhalte eines Papierkorb-Ordners lassen sich nicht einzeln
                # verschieben (PERMISSION_DENIED) – dann kopieren; der Eintrag
                # wird am Ende als Ganzes entfernt (_transfer_top)
                if not (_io(exc, Gio.IOErrorEnum.NOT_SUPPORTED)
                        or _io(exc, Gio.IOErrorEnum.WOULD_RECURSE)
                        or (_in_trash(src) and _io(exc, Gio.IOErrorEnum.PERMISSION_DENIED))):
                    raise
        if not _is_dir(info):
            self._copy_file(src, info, dst, mode == "replace")
            if self.move and not _in_trash(src):
                src.delete(self.cancellable)  # erst nach vollständiger Kopie
            return
        if mode == "new":
            dst.make_directory(self.cancellable)
        self.done_items += 1
        for ci in _list_children(src, self.cancellable):
            child = src
            try:
                child = safe_child(src, ci.get_name())
                cdst, cmode = safe_child(dst, ci.get_name()), "new"
                if mode == "merge":
                    cdst, cmode = self._resolve(child, ci, cdst)
                    if cmode == "skip":
                        self._skipped += 1
                        self._count(ci)
                        continue
                self._transfer(child, ci, cdst, cmode)
            except GLib.Error as exc:
                if _cancelled(exc):
                    raise
                self._err(child, exc)
        if mode == "new":
            try:  # Zeiten/Rechte des Ordners übernehmen (nach dem Inhalt)
                src.copy_attributes(dst, COPY_FLAGS, self.cancellable)
            except GLib.Error as exc:
                if _cancelled(exc):
                    raise
        if self.move and not _in_trash(src):
            try:  # nur möglich, wenn alles verschoben wurde
                src.delete(self.cancellable)
            except GLib.Error as exc:
                if _cancelled(exc):
                    raise

    def _copy_file(self, src, info, dst, overwrite):
        self.current = info.get_display_name()
        base = self.done_bytes
        go = self._go

        def progress_cb(current, _total, *_a):
            self.done_bytes = base + current
            if not go.is_set():
                go.wait()  # Anhalten mitten in einer grossen Datei
        flags = COPY_FLAGS | (OVERWRITE if overwrite else Gio.FileCopyFlags.NONE)
        try:
            src.copy(dst, flags, self.cancellable, progress_cb)
        except GLib.Error as exc:
            # Angefangene Kopie nicht liegen lassen. Ohne OVERWRITE legt GIO
            # das Ziel exklusiv an – ausser bei EXISTS stammt es also von uns.
            if not overwrite and not _io(exc, Gio.IOErrorEnum.EXISTS):
                try:
                    dst.delete(None)
                except GLib.Error:
                    pass
            raise
        self.done_bytes = base + info.get_size()
        self.done_items += 1


class DeleteJob(_Job):
    """Papierkorb (permanent=False) oder endgültig löschen.

    on_done(deleted: list[Gio.File], unsupported: list[Gio.File], errors) im Hauptthread.
    unsupported = Papierkorb hier nicht möglich (nur bei permanent=False)."""

    def __init__(self, gfiles, permanent=False, on_done=None):
        super().__init__()
        self.kind = "delete" if permanent else "trash"
        self.files = list(gfiles)
        self.permanent = permanent
        self.on_done = on_done
        self.deleted = []
        self.unsupported = []

    def _call_done(self):
        if self.on_done:
            self.on_done(self.deleted, self.unsupported, self.errors)

    def _tick(self):
        self.done_items += 1
        self._checkpoint()

    def run(self):
        try:
            if self.permanent:
                for f in self.files:
                    try:
                        if _in_trash(f):
                            self.total_items += 1
                            continue
                        info = f.query_info(SCAN_ATTRS, NOFOLLOW, self.cancellable)
                        self.total_items += _measure(f, info, self.cancellable)[1]
                    except GLib.Error as exc:
                        if _cancelled(exc):
                            raise
                        self.total_items += 1
                    except RecursionError:      # Löschen selbst meldet es dann je Element
                        self.total_items += 1
            else:
                self.total_items = len(self.files)
            self.phase = "run"
            for f in self.files:
                self._checkpoint()
                self.current = _basename(f)
                try:
                    if self.permanent and _in_trash(f):
                        f.delete(self.cancellable)  # Kinder sind im Papierkorb gesperrt
                        self.done_items += 1
                    elif self.permanent:
                        _delete_recursive(f, self.cancellable, self._tick)
                    else:
                        f.trash(self.cancellable)
                        self.done_items += 1
                    self.deleted.append(f)
                except GLib.Error as exc:
                    if _cancelled(exc):
                        break
                    if not self.permanent and _io(exc, Gio.IOErrorEnum.NOT_SUPPORTED):
                        self.unsupported.append(f)
                    else:
                        self._err(f, exc)
                except RecursionError:
                    self.errors.append((_display(f), "Der Ordner ist zu tief verschachtelt."))
        except GLib.Error as exc:
            if not _cancelled(exc):
                self._err(self.files[0], exc)
        finally:
            self.phase = "done"
            GLib.idle_add(self._finish)


# ---------------------------------------------------------------------------
# Öffentliche Operationen (aus dem Hauptthread aufrufen)
# ---------------------------------------------------------------------------
_running = set()


def _start(job, parent):
    """Vorgang starten. Die Anwendung bleibt bis zum Ende am Leben (auch wenn
    alle Explorer-Fenster geschlossen werden) – wie unter Windows."""
    _running.add(job)
    app = Gio.Application.get_default()
    if app is not None:
        app.hold()
        job._app = app
    from . import progress
    if parent is not None and not isinstance(parent, Gtk.Window):
        parent = parent.get_toplevel() if isinstance(parent, Gtk.Widget) else None
    progress.watch(job, parent)
    job.start()
    return job


def paste_into(dest_dir, parent=None, on_done=None, on_progress=None):
    """Inhalt der Zwischenablage nach dest_dir einfügen.
    on_done(created: list[Gio.File]) nach Abschluss (auch bei Fehlern)."""
    def got(uris, cut, trusted):
        if not uris or (not trusted and not confirm_foreign(uris, parent)):
            return
        sources = [Gio.File.new_for_uri(u) for u in uris]
        transfer(sources, dest_dir, move=cut and trusted, parent=parent, on_done=on_done,
                 from_clipboard=True)
    clipboard().read_files(got, with_trust=True)


def transfer(sources, dest_dir, move=False, parent=None, on_done=None,
             on_progress=None, from_clipboard=False, targets=None, record=True, kind=None):
    def done(created, errors):
        if record and job.pairs:
            from . import undo
            undo.record(undo.Op("move" if move else "copy", job.pairs))
        if move and from_clipboard and not errors and not job.is_cancelled():
            clipboard().clear_if_cut()
        if errors:
            title = ("Einige Elemente konnten nicht verschoben werden." if move
                     else "Einige Elemente konnten nicht kopiert werden.")
            show_errors(parent, title, errors)
        if on_done:
            on_done(created)
    job = TransferJob(sources, dest_dir, move, done, targets=targets, kind=kind)
    return _start(job, parent)


def delete(gfiles, parent=None, permanent=False, on_done=None, record=True):
    """In den Papierkorb (bzw. endgültig nach Rückfrage) löschen.
    on_done(deleted: list[Gio.File])."""
    gfiles = list(gfiles)
    if not gfiles:
        return None
    if permanent:
        if not confirm_permanent_delete(parent, gfiles):
            return None
        return _delete_permanently(gfiles, parent, on_done)

    def done(deleted, unsupported, errors):
        if record and deleted:
            from . import undo
            undo.record(undo.Op("trash", [(g, None) for g in deleted]))
        if errors:
            show_errors(parent, "Einige Elemente konnten nicht gelöscht werden.", errors)
        if unsupported:
            if confirm_permanent_delete(
                    parent, unsupported,
                    reason="Der Papierkorb ist an diesem Speicherort nicht verfügbar."):
                def done2(deleted2):
                    if on_done:
                        on_done(deleted + deleted2)
                _delete_permanently(unsupported, parent, done2)
                return
        if on_done:
            on_done(deleted)
    return _start(DeleteJob(gfiles, False, done), parent)


def trash_files(parent_window, gfiles, on_done=None):
    """In den Papierkorb; wo das nicht geht, nach Rückfrage endgültig.
    on_done(deleted: list[Gio.File])."""
    return delete(gfiles, parent=parent_window, permanent=False, on_done=on_done)


def _delete_permanently(gfiles, parent, on_done):
    def done(deleted, _unsupported, errors):
        if errors:
            show_errors(parent, "Einige Elemente konnten nicht gelöscht werden.", errors)
        if on_done:
            on_done(deleted)
    return _start(DeleteJob(gfiles, True, done), parent)


TRASH_ATTRS = SCAN_ATTRS + ",trash::orig-path,trash::deletion-date"


def trash_items():
    """[(Gio.File im Papierkorb, FileInfo)] – leer, wenn kein Papierkorb."""
    trash = Gio.File.new_for_uri("trash:///")
    try:
        return [(trash.get_child(i.get_name()), i)
                for i in _list_children_attrs(trash, TRASH_ATTRS)]
    except GLib.Error:
        return []


def _list_children_attrs(gfile, attrs):
    en = gfile.enumerate_children(attrs, Gio.FileQueryInfoFlags.NONE, None)
    try:
        return list(en)
    finally:
        en.close(None)


def _restore_target_ok(info, orig):
    """→ (ok, normalisierter Pfad). Im eigenen Papierkorb (~/.local/share/Trash) steht
    der Pfad fest; den Papierkorb eines USB-Sticks/einer Freigabe (.Trash-1000) kann
    ein Fremder beschreiben („Path=../../home/…/autostart/x.desktop“) – dort muss das
    Ziel auf demselben Datenträger bleiben."""
    norm = os.path.normpath(orig)
    tu = info.get_attribute_string("standard::target-uri") if info is not None else None
    tp = Gio.File.new_for_uri(tu).get_path() if tu else None
    if not tp or not os.path.isabs(norm):
        return False, norm
    home_trash = os.path.join(GLib.get_user_data_dir(), "Trash") + "/"
    if tp.startswith(home_trash):
        return True, norm
    parts = tp.split("/")
    top = next(("/".join(parts[:i]) or "/" for i, p in enumerate(parts)
                if p.startswith(".Trash-") or p == ".Trash"), None)
    inside = top is not None and (norm == top or norm.startswith(top.rstrip("/") + "/"))
    return inside and ".." not in orig.split("/"), norm


def restore(items, parent=None, on_done=None, record=True):
    """Papierkorb-Einträge an ihren ursprünglichen Ort zurück.
    items: Gio.Files aus trash:///; on_done(restored: list[Gio.File])."""
    sources, targets = [], []
    for g in items:
        try:
            info = g.query_info("trash::orig-path,standard::target-uri,standard::display-name",
                                Gio.FileQueryInfoFlags.NONE, None)
            orig = info.get_attribute_byte_string("trash::orig-path")
        except GLib.Error:
            info = orig = None
        if not orig:
            continue
        ok, orig = _restore_target_ok(info, orig)
        if not ok:
            from .places import safe_text
            name = safe_text(info.get_display_name() or g.get_basename() or "")
            if _message(parent, f"„{name}“ stammt aus dem Papierkorb eines anderen Datenträgers "
                        f"und würde hierhin zurückgelegt:\n\n{safe_text(orig)}\n\nDas liegt "
                        "ausserhalb dieses Datenträgers. Trotzdem wiederherstellen?",
                        "warning", "Wiederherstellen",
                        buttons=["Wiederherstellen", "Überspringen"], default=1) != 0:
                continue
        sources.append(g)
        targets.append(Gio.File.new_for_path(orig))
    if not sources:
        return None

    def done(created):
        if record and created:
            from . import undo
            undo.record(undo.Op("restore", [(c, None) for c in created]))
        if on_done:
            on_done(created)
    return transfer(sources, None, move=True, parent=parent, on_done=done,
                    targets=targets, record=False, kind="restore")


def empty_trash(parent=None, on_done=None):
    """Papierkorb leeren (nach Rückfrage wie Windows)."""
    files = [g for g, _i in trash_items()]
    if not files:
        return None
    if not confirm_permanent_delete(parent, files):
        return None
    return _delete_permanently(files, parent, on_done)


def busy():
    """Laufen noch Hintergrundoperationen?"""
    return bool(_running)


def running_jobs():
    return list(_running)


def new_folder(dir_gfile, name="Neuer Ordner"):
    """Legt den Ordner an und gibt das Gio.File zurück (wirft GLib.Error)."""
    target = numbered_child(dir_gfile, name, is_dir=True)
    target.make_directory(None)
    from . import undo
    undo.record(undo.Op("new", [(None, target)]))
    return target


def new_file(dir_gfile, template_path=None):
    """Neue Datei: leeres "Neues Textdokument.txt" oder Kopie einer Vorlage."""
    if template_path:
        name = os.path.basename(template_path)
        target = numbered_child(dir_gfile, name)
        Gio.File.new_for_path(template_path).copy(
            target, Gio.FileCopyFlags.NONE, None, None, None)
    else:
        target = numbered_child(dir_gfile, "Neues Textdokument.txt")
        stream = target.create(Gio.FileCreateFlags.NONE, None)
        stream.close(None)
    from . import undo
    undo.record(undo.Op("new", [(None, target)]))
    return target


def make_links(sources, dest_dir, parent=None):
    """Verknüpfungen (symbolische Links) in dest_dir anlegen, benannt wie
    unter Windows „Name - Verknüpfung“ (Endung bleibt erhalten)."""
    created, errors = [], []
    for src in sources:
        path = src.get_path()
        if not path:
            errors.append((_display(src), "Für dieses Element kann keine Verknüpfung erstellt werden."))
            continue
        is_dir = os.path.isdir(path)
        base = src.get_basename()
        if is_dir:
            name = f"{base} - Verknüpfung"
        else:
            stem, ext = split_ext(base)
            name = f"{stem} - Verknüpfung{ext}"
        target = numbered_child(dest_dir, name, is_dir=is_dir)
        try:
            target.make_symbolic_link(path, None)
            created.append(target)
        except GLib.Error as exc:
            errors.append((_display(src), exc.message))
    if created:
        from . import undo
        undo.record(undo.Op("new", [(None, t) for t in created]))
    if errors:
        show_errors(parent, "Einige Verknüpfungen konnten nicht erstellt werden.", errors)
    return created


def rename(gfile, new_name, record=True):
    """Umbenennen; gibt das neue Gio.File zurück (wirft GLib.Error)."""
    new = gfile.set_display_name(new_name, None)
    if record and not new.equal(gfile):
        from . import undo
        undo.record(undo.Op("rename", [(gfile, new)]))
    return new


def templates_dir():
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_TEMPLATES)
    if path and os.path.isdir(path) and os.path.normpath(path) != os.path.normpath(GLib.get_home_dir()):
        return path
    return None


def list_templates():
    """[(Anzeigename, Pfad)] aus dem XDG-Vorlagenordner (ohne Unterordner)."""
    d = templates_dir()
    if not d:
        return []
    out = []
    try:
        for e in sorted(os.scandir(d), key=lambda e: e.name.lower()):
            if e.name.startswith(".") or not e.is_file():
                continue
            out.append((split_ext(e.name)[0], e.path))
    except OSError:
        pass
    return out
