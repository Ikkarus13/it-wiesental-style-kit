"""Rückgängig/Wiederholen (Strg+Z / Strg+Y) für Dateivorgänge wie im
Windows-Explorer: Umbenennen, Verschieben, Kopieren, Löschen (Papierkorb),
Wiederherstellen und Neu. Endgültiges Löschen lässt sich nicht rückgängig
machen (wie Windows).

Prinzip: Was neu entstanden ist (Kopie, neuer Ordner, Wiederhergestelltes),
wandert beim Rückgängigmachen in den Papierkorb und kommt beim Wiederholen
von dort zurück – es geht also nie etwas endgültig verloren.

    record(Op(kind, pairs))   – fileops ruft das nach erfolgreichen Vorgängen auf
    undo(parent) / redo(parent)
    can_undo() / can_redo() / undo_label() / redo_label()
    connect(callback)         – callback() bei jeder Änderung der Stapel
"""
import os
import stat

from gi.repository import GLib

LABELS = {"rename": "Umbenennen", "move": "Verschieben", "copy": "Kopieren",
          "trash": "Löschen", "restore": "Wiederherstellen", "new": "Neu"}
MAX = 50

# Was neu entstanden ist: rückgängig = Papierkorb, wiederholen = zurückholen
_CREATED = ("copy", "new", "restore")
_FUSE = os.path.join(GLib.get_user_runtime_dir(), "gvfs") + "/"


def _ident(gfile):
    """Kennung einer lokalen Datei: Gerät, Inode und (ausser bei Ordnern) die
    Änderungszeit – ein Inode wird nach dem Löschen oft sofort wiederverwendet.
    None, wenn sie fehlt oder sich nicht prüfen lässt (Netz, FUSE)."""
    path = gfile.get_path() if gfile is not None and gfile.is_native() else None
    if not path or path.startswith(_FUSE):
        return None
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISDIR(st.st_mode):
        return st.st_dev, st.st_ino
    return st.st_dev, st.st_ino, st.st_mtime_ns


class Op:
    """pairs: rename/move/copy → (vorher, nachher); new → (None, neu);
    trash/restore → (Datei am ursprünglichen Ort, None)."""

    def __init__(self, kind, pairs):
        self.kind = kind
        self.pairs = list(pairs)
        self.idents = {}         # Pfad → _ident() der Dateien, die gerade dort sind

    @property
    def label(self):
        return LABELS.get(self.kind, "")

    def files(self):
        return [b if b is not None else a for a, b in self.pairs]

    def present(self, applied):
        """Dateien, die nach dem Vorgang (applied) bzw. nach dem Rückgängigmachen
        an ihrem Ort liegen (und auf die der nächste Schritt wirkt)."""
        if self.kind in ("rename", "move"):
            return [b if applied else a for a, b in self.pairs]
        if self.kind in ("copy", "new"):
            return [b for _a, b in self.pairs] if applied else []
        if self.kind == "restore":
            return [a for a, _b in self.pairs] if applied else []
        return [] if applied else [a for a, _b in self.pairs]     # trash

    def remember(self, applied):
        self.idents = {}
        for f in self.present(applied):
            ident = _ident(f)
            if ident is not None:
                self.idents[f.get_path()] = ident

    def changed(self, applied):
        """Liegt an einem der Orte inzwischen etwas anderes (umbenannt, ersetzt,
        neu angelegt)? → Liste der Pfade."""
        out = []
        for f in self.present(applied):
            path = f.get_path() if f is not None else None
            want = self.idents.get(path) if path else None
            if want is not None and _ident(f) != want:
                out.append(path)
        return out


_undo, _redo, _listeners = [], [], []


def record(op):
    op.remember(True)
    _undo.append(op)
    del _undo[:-MAX]
    _redo.clear()
    _notify()


def connect(callback):
    _listeners.append(callback)


def _notify():
    for cb in list(_listeners):
        try:
            cb()
        except Exception:
            pass


def can_undo():
    return bool(_undo)


def can_redo():
    return bool(_redo)


def undo_label():
    return f"Rückgängig: {_undo[-1].label}" if _undo else "Rückgängig"


def redo_label():
    return f"Wiederholen: {_redo[-1].label}" if _redo else "Wiederholen"


def undo(parent=None):
    if _undo:
        op = _undo.pop()
        _notify()
        _apply(op, True, parent, lambda ok: _finish(op, ok, _redo, _undo))


def redo(parent=None):
    if _redo:
        op = _redo.pop()
        _notify()
        _apply(op, False, parent, lambda ok: _finish(op, ok, _undo, _redo))


def _finish(op, ok, target, source):
    # Hat nichts geklappt, bleibt der Schritt auf seinem Stapel
    if ok:
        op.remember(target is _undo)       # wieder ausgeführt bzw. rückgängig gemacht
    (target if ok else source).append(op)
    _notify()


def _apply(op, reverse, parent, then):
    from . import fileops
    changed = op.changed(reverse)
    if changed:
        # Inzwischen umbenannt, ersetzt oder neu angelegt: nie die falsche Datei
        # verschieben oder löschen – der Schritt verfällt
        title = ("Rückgängig ist nicht möglich." if reverse
                 else "Wiederholen ist nicht möglich.")
        fileops.show_errors(parent, title,
                            [(p, "Das Element wurde inzwischen geändert, ersetzt oder verschoben.")
                             for p in changed])
        _notify()
        return
    files = op.files()
    if op.kind == "rename":
        try:
            for old, new in op.pairs:
                a, b = (new, old) if reverse else (old, new)
                fileops.rename(a, b.get_basename(), record=False)
            then(True)
        except GLib.Error as exc:
            fileops.show_errors(parent, "Das Umbenennen konnte nicht rückgängig gemacht werden.",
                                [(fileops._display(op.pairs[0][1]), exc.message)])
            then(False)
        return
    if op.kind == "move":
        pairs = [(b, a) if reverse else (a, b) for a, b in op.pairs]
        fileops.transfer([p[0] for p in pairs], None, move=True, parent=parent,
                         targets=[p[1] for p in pairs], record=False,
                         on_done=lambda created: then(bool(created)))
        return
    to_trash = (op.kind in _CREATED) == reverse
    if to_trash:
        fileops.delete(files, parent=parent, record=False,
                       on_done=lambda deleted: then(bool(deleted)))
    else:
        _untrash(files, parent, then)


def _untrash(files, parent, then):
    """Die zuletzt gelöschten Papierkorb-Einträge dieser Pfade zurückholen."""
    from . import fileops
    wanted = {f.get_path() for f in files if f.get_path()}
    best = {}
    for g, info in fileops.trash_items():
        orig = info.get_attribute_byte_string("trash::orig-path")
        if orig in wanted:
            when = info.get_attribute_string("trash::deletion-date") or ""
            if orig not in best or when > best[orig][0]:
                best[orig] = (when, g)
    if not best:
        fileops.show_errors(parent, "Rückgängig ist nicht möglich.",
                            [(p, "Das Element befindet sich nicht mehr im Papierkorb.")
                             for p in sorted(wanted)])
        then(False)
        return
    fileops.restore([g for _w, g in best.values()], parent=parent, record=False,
                    on_done=lambda restored: then(bool(restored)))


def reset():
    """Nur für Tests."""
    _undo.clear()
    _redo.clear()
    _notify()

