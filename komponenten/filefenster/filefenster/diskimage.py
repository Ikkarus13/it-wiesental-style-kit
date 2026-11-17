"""ISO-/IMG-Abbilder bereitstellen wie im Windows-Explorer („Bereitstellen“,
Doppelklick): über UDisks2 als schreibgeschütztes Loop-Gerät einhängen und
das neue Laufwerk öffnen. Ist das Abbild schon eingehängt, wird nur geöffnet.
Auswerfen geht wie bei jedem Laufwerk (Dieser PC → Auswerfen); das
Loop-Gerät verschwindet dabei von selbst (Autoclear).

    is_image(name)
    mount(gfile, parent, on_mounted)   on_mounted(Pfad, vom_Desktop_geöffnet) im
                                       Hauptthread
"""
import os
import stat
import threading
import time

from gi.repository import Gio, GLib

UD = "org.freedesktop.UDisks2"
UD_PATH = "/org/freedesktop/UDisks2"
EXTS = (".iso", ".img")


def is_image(name):
    return name.lower().endswith(EXTS)


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _objects(bus):
    res = bus.call_sync(UD, UD_PATH, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
                        None, GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE,
                        10000, None)
    return res.unpack()[0]


def _bytes_to_str(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).rstrip(b"\0").decode("utf-8", "replace")
    if isinstance(v, (list, tuple)):
        return bytes(v).rstrip(b"\0").decode("utf-8", "replace")
    return v or ""


def _find_loop(objs, path):
    for obj, ifaces in objs.items():
        loop = ifaces.get(UD + ".Loop")
        if loop and _bytes_to_str(loop.get("BackingFile")) == path:
            return obj
    return None


def _filesystems(objs, loop_obj):
    """Dateisysteme auf dem Loop-Gerät (ganzes Gerät oder Partitionen)."""
    out = []
    for obj, ifaces in objs.items():
        if obj != loop_obj and not obj.startswith(loop_obj + "p"):
            continue
        fs = ifaces.get(UD + ".Filesystem")
        if fs is None:
            continue
        typ = (ifaces.get(UD + ".Block") or {}).get("IdType", "")
        mounts = [_bytes_to_str(m) for m in fs.get("MountPoints") or []]
        out.append((0 if typ in ("iso9660", "udf") else 1, obj, mounts))
    return [(o, m) for _p, o, m in sorted(out)]


def _desktop_automount(key="automount"):
    """Hängt der Desktop (Cinnamon/GNOME) neue Laufwerke selbst ein
    (key="automount") bzw. öffnet sie danach (key="automount-open")?"""
    src = Gio.SettingsSchemaSource.get_default()
    for schema in ("org.cinnamon.desktop.media-handling", "org.gnome.desktop.media-handling"):
        if src is not None and src.lookup(schema, True) is not None:
            try:
                return Gio.Settings.new(schema).get_boolean(key)
            except Exception:
                continue
    return False


def _mount_sync(path):
    bus = _bus()
    objs = _objects(bus)
    loop = _find_loop(objs, path)
    if loop is not None:
        return _mount_loop(bus, loop)
    # O_NONBLOCK: eine FIFO „x.iso“ blockiert sonst den Thread für immer
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("Das Abbild ist keine normale Datei.")
        os.set_blocking(fd, True)
        fdl = Gio.UnixFDList.new()
        idx = fdl.append(fd)
        res, _out = bus.call_with_unix_fd_list_sync(
            UD, UD_PATH + "/Manager", UD + ".Manager", "LoopSetup",
            GLib.Variant("(ha{sv})", (idx, {"read-only": GLib.Variant("b", True)})),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 30000, fdl, None)
    finally:
        os.close(fd)
    loop = res.unpack()[0]
    try:
        return _mount_loop(bus, loop)
    except Exception:
        # selbst angelegtes Loop-Gerät nicht als Leiche zurücklassen
        try:
            bus.call_sync(UD, loop, UD + ".Loop", "Delete", GLib.Variant("(a{sv})", ({},)),
                          None, Gio.DBusCallFlags.NONE, 10000, None)
        except GLib.Error:
            pass
        raise


def _mount_loop(bus, loop):
    # UDisks braucht einen Moment, bis das Dateisystem erkannt ist
    fss = []
    for _i in range(40):
        fss = _filesystems(_objects(bus), loop)
        if fss:
            break
        time.sleep(0.15)
    if not fss:
        raise RuntimeError("Auf dem Abbild wurde kein lesbares Dateisystem gefunden.")
    obj, mounts = fss[0]
    by_desktop = False
    # Ist der Automounter des Desktops an, hängt er das neue Gerät selbst ein.
    # Kämen wir ihm zuvor, meldet er „ist bereits eingehängt“ als Fehlerdialog –
    # also kurz auf ihn warten und nur einhängen, wenn er es nicht tut.
    if not mounts and _desktop_automount():
        for _i in range(20):
            time.sleep(0.15)
            mounts = dict(_filesystems(_objects(bus), loop)).get(obj) or []
            if mounts:
                by_desktop = True     # der Desktop hat eingehängt (und öffnet ggf. selbst)
                break
    if mounts:
        target = mounts[0]          # schon eingehängt (auch vom Automounter)
    else:
        try:
            res = bus.call_sync(UD, obj, UD + ".Filesystem", "Mount",
                                GLib.Variant("(a{sv})", ({"options": GLib.Variant("s", "ro")},)),
                                GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 30000, None)
            target = res.unpack()[0]
        except GLib.Error as exc:
            # doch gleichzeitig eingehängt worden → vorhandenen Einhängepunkt nehmen
            if "AlreadyMounted" not in (Gio.DBusError.get_remote_error(exc) or ""):
                raise
            target = (dict(_filesystems(_objects(bus), loop)).get(obj) or [None])[0]
            if not target:
                raise
    # Erst jetzt Autoclear: beim Aushängen verschwindet das Loop-Gerät von selbst.
    # (Vorher gesetzt, gäbe der Kernel es schon nach der Dateisystemerkennung frei.)
    try:
        bus.call_sync(UD, loop, UD + ".Loop", "SetAutoclear",
                      GLib.Variant("(ba{sv})", (True, {})), None,
                      Gio.DBusCallFlags.NONE, 10000, None)
    except GLib.Error:
        pass
    return target, by_desktop and _desktop_automount("automount-open")


def mount(gfile, parent=None, on_mounted=None):
    path = gfile.get_path()
    if not path:
        return

    def work():
        opened = False
        try:
            (target, opened), err = _mount_sync(path), None
        except GLib.Error as exc:
            Gio.DBusError.strip_remote_error(exc)
            target, err = None, exc.message
        except (OSError, RuntimeError) as exc:
            target, err = None, str(exc)
        GLib.idle_add(done, target, opened, err)

    def done(target, opened, err):
        if err:
            from . import fileops
            fileops.show_errors(parent, "Das Abbild konnte nicht bereitgestellt werden.",
                                [(gfile.get_basename(), err)])
        elif target and on_mounted:
            on_mounted(target, opened)
        return False
    threading.Thread(target=work, daemon=True).start()
