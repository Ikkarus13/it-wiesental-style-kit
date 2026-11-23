"""OneDrive-Status wie im Windows-Explorer: Wolke (nur online), Häkchen
(lokal), grüner Kreis (immer behalten), Sync-Pfeile, Fehler.

Quelle ist onedrive-mint (~/.local/share/onedrive-mint): dieselbe
Zustandslogik wie dessen Nemo-Erweiterung (onedrive_mint.states liest den
rclone-Cache direkt, ohne Netz). Ordner, die sich nicht sofort bestimmen
lassen, fragt das Modul per D-Bus (GetState) ab; Änderungen meldet der Dienst
mit dem Signal "Changed". Ohne onedrive-mint bleibt alles still.

    state(path, is_dir) -> "cloud" | "local" | "pinned" | "sync" | "error" | None
    in_cloud(path), label(state), pixbuf(state, size), with_overlay(pb, state, size)
    connect(callback)  – callback(paths) bei Änderungen (schwache Referenz)
    menu_state(paths), set_pinned(paths, value), free_up(paths), open_online(path)
"""
import os
import sys
import weakref

from gi.repository import GdkPixbuf, Gio, GLib, Gtk

ODM_LIB = os.path.expanduser("~/.local/share/onedrive-mint")
try:
    if os.path.isdir(os.path.join(ODM_LIB, "onedrive_mint")) and ODM_LIB not in sys.path:
        sys.path.append(ODM_LIB)
    from onedrive_mint import config as _cfg, states as _st
except Exception:  # nicht installiert oder inkompatibel
    _cfg = _st = None

_runtime = None
_bus = None
_dirs = {}          # Ordnerpfad -> Zustand (vom Dienst)
_pending = set()
_listeners = []
_pix = {}
_over = {}


_FUSE = os.path.join(GLib.get_user_runtime_dir(), "gvfs") + "/"


def available():
    return _st is not None


def _rt():
    global _runtime
    if _runtime is None and _st is not None:
        _runtime = _st.RuntimeState()
        _subscribe()
    return _runtime


def _locate(path):
    rt = _rt()
    if rt is None or not path:
        return None, None
    try:
        view, rel = rt.find(path)
        # realpath fragt jede Pfadstufe ab – bei Netzwerkfreigaben (gvfs-FUSE)
        # blockiert das die Oberfläche; OneDrive liegt nie dort
        if view is None and not path.startswith(_FUSE):
            real = os.path.realpath(path)
            if real != path:
                view, rel = rt.find(real)
    except Exception:
        return None, None
    if view is None or not view.meta_dir:
        return None, None
    if rel and (rel.startswith(".Trash-") or "/.Trash-" in rel):
        return None, None
    return view, rel


def in_cloud(path):
    return _locate(path)[0] is not None


def state(path, is_dir):
    """Zustand oder None (kein OneDrive, Kontowurzel, Ordner wird noch erfragt)."""
    view, rel = _locate(path)
    if view is None or not rel:
        return None
    rt = _rt()
    try:
        busy = rt.busy(view.id)
        if not is_dir:
            return view.file_state(rel, busy, rt.errors(view.id))
        st = view.dir_state_quick(rel, busy)
    except Exception:
        return None
    if st is not None:
        return st
    if path in _dirs:
        return _dirs[path] or None
    _query_dir(path)
    return None


def online_only(path):
    """Nur in der Cloud (Lesen würde die Datei herunterladen)?"""
    try:
        return state(path, False) == "cloud"
    except Exception:
        return False


def label(st):
    return _st.LABELS.get(st, "") if _st and st else ""


# ----------------------------------------------------------------------
# Symbole (die Embleme von onedrive-mint, im Windows-11-Stil)
# ----------------------------------------------------------------------
def pixbuf(st, size):
    key = (st, size)
    if key not in _pix:
        pb = None
        if _st and st in _st.EMBLEMS:
            try:
                pb = Gtk.IconTheme.get_default().load_icon(
                    "emblem-" + _st.EMBLEMS[st], size, Gtk.IconLookupFlags.FORCE_SIZE)
            except GLib.Error:
                pb = None
        _pix[key] = pb
    return _pix[key]


def with_overlay(pb, st, size):
    """Status unten links auf das Symbol (Symbolansichten, wie Windows)."""
    if pb is None or not st:
        return pb
    key = (pb, st, size)
    out = _over.get(key)
    if out is not None:
        return out
    if len(_over) > 3000:
        _over.clear()
    s = max(12, min(32, size // 3))
    em = pixbuf(st, s)
    if em is None:
        return pb
    out = pb.copy()
    if not out.get_has_alpha():
        out = out.add_alpha(False, 0, 0, 0)
    y = max(0, out.get_height() - s)
    em.composite(out, 0, y, s, s, 0, y, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
    _over[key] = out
    return out


# ----------------------------------------------------------------------
# D-Bus: Ordnerstatus, Änderungen, Aktionen
# ----------------------------------------------------------------------
def _get_bus():
    global _bus
    if _bus is None:
        try:
            _bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            _bus = False
    return _bus or None


def _subscribe():
    bus = _get_bus()
    if bus is not None and _cfg is not None:
        # nur vom Dienst selbst (sonst kann jedes Programm der Sitzung Abfragen auslösen)
        bus.signal_subscribe(_cfg.DBUS_NAME, _cfg.DBUS_IFACE, "Changed", _cfg.DBUS_PATH, None,
                             Gio.DBusSignalFlags.NONE, _on_changed)


def _query_dir(path):
    bus = _get_bus()
    if bus is None or path in _pending:
        return
    _pending.add(path)

    def done(conn, res):
        _pending.discard(path)
        try:
            st = conn.call_finish(res).unpack()[0] or ""
        except GLib.Error:
            return            # Dienst läuft nicht: nicht dauerhaft merken
        _dirs[path] = st
        _notify([path])
    bus.call(_cfg.DBUS_NAME, _cfg.DBUS_PATH, _cfg.DBUS_IFACE, "GetState",
             GLib.Variant("(s)", (path,)), GLib.VariantType("(s)"),
             Gio.DBusCallFlags.NO_AUTO_START, 30000, None, done)


def _on_changed(_conn, _sender, _path, _iface, _signal, params):
    if params is None or params.get_type_string() != "(as)":
        return
    paths = [p for p in params.unpack()[0][:10000] if isinstance(p, str)]
    for p in paths:
        _dirs.pop(p, None)
    _notify(paths)


def connect(callback):
    """callback(paths); gebundene Methoden werden schwach gehalten."""
    try:
        ref = weakref.WeakMethod(callback)
    except TypeError:
        ref = (lambda cb=callback: cb)
    _listeners.append(ref)


def _notify(paths):
    for ref in list(_listeners):
        cb = ref()
        if cb is None:
            _listeners.remove(ref)
            continue
        try:
            cb(paths)
        except Exception as exc:
            print("[filefenster] OneDrive-Status:", exc)


def menu_state(paths):
    """(alle immer behalten, etwas lokal) oder None, wenn nicht alle in OneDrive."""
    if not paths or _st is None:
        return None
    all_pinned, any_local = True, False
    for p in paths:
        view, rel = _locate(p)
        if view is None or not rel:
            return None
        try:
            all_pinned &= view.pins.is_pinned(rel)
            st = view.dir_state_quick(rel) if os.path.isdir(p) else view.file_state(rel)
        except Exception:
            return None
        any_local |= st != _st.CLOUD
    return all_pinned, any_local


def _call(method, params):
    bus = _get_bus()
    if bus is None:
        return

    def done(conn, res):
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            print(f"[filefenster] OneDrive {method}: {exc.message}")
    bus.call(_cfg.DBUS_NAME, _cfg.DBUS_PATH, _cfg.DBUS_IFACE, method, params,
             None, Gio.DBusCallFlags.NONE, 30000, None, done)


def set_pinned(paths, value):
    _call("SetPinned", GLib.Variant("(asb)", (list(paths), bool(value))))


def free_up(paths):
    _call("FreeUp", GLib.Variant("(as)", (list(paths),)))


def open_online(path):
    _call("OpenOnline", GLib.Variant("(s)", (path,)))


def share_dialog(path):
    """Freigabedialog des Cloud-Clients (Link, Personen einladen, Zugriff verwalten)."""
    _call("ShareDialog", GLib.Variant("(s)", (path,)))
