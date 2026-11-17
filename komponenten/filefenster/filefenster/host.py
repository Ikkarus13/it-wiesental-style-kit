"""Schnittstelle zwischen Ansichten (Dateiliste, Start, Dieser PC,
Navigationsbereich, Adresszeile …) und dem Fenster/Tab.

Ansichten bekommen beim Erzeugen ein `host`-Objekt mit diesen Methoden.
Im echten Programm ist das der Tab (bzw. für Navigationsbereich/Adresszeile
das Fenster, das an den aktuellen Tab weiterreicht). Für Tests gibt es
FakeHost, der alle Aufrufe nur protokolliert.

Methoden/Eigenschaften:
    host.window                  -> Gtk.Window (für Dialoge: transient_for)
    host.config                  -> config.Config
    host.location                -> aktueller Ort des Tabs ("home:", "thispc:", URI)
    host.navigate(loc)           -> im aktuellen Tab öffnen (mit Verlauf)
    host.open_in_new_tab(loc)
    host.open_in_new_window(loc)
    host.go_back() / host.go_forward() / host.go_up() / host.refresh()
    host.state_changed()         -> Auswahl/Anzahl/Ladezustand hat sich geändert
                                    (Fenster aktualisiert Befehlsleiste, Statusleiste,
                                    Detailbereich)
    host.show_properties(gfiles) -> Eigenschaften-Dialog für Liste von Gio.File
    host.is_pinned(uri) / host.pin(uri) / host.unpin(uri)   (Schnellzugriff)
    host.open_terminal(path)     -> Terminal in diesem Ordner öffnen
    host.start_search(query) / host.stop_search()          (nur Fenster)
"""
import os
import subprocess

from gi.repository import Gio, GLib

from . import places
from .config import get_config


def pinned_uris(config):
    pinned = config.get("pinned")
    if pinned is None:
        pinned = places.default_pinned()
    return list(pinned)


def open_terminal(path):
    for cmd in (["x-terminal-emulator"], ["gnome-terminal"], ["xfce4-terminal"], ["xterm"]):
        try:
            subprocess.Popen(cmd, cwd=path, start_new_session=True)
            return True
        except (FileNotFoundError, OSError):
            continue
    return False


def launch_file(gfile, parent_window=None):
    """Datei mit Standardanwendung öffnen (wie Doppelklick). Programme,
    ausführbare Skripte und App-Starter (.desktop) laufen nie stillschweigend:
    erst eine Rückfrage, aus Archiven/Netzwerkorten gar nicht (wie Nemo/Windows)."""
    probe = _probe(gfile)
    if probe["kind"] is None:
        return _launch_default(gfile)
    return _guarded(gfile, probe, parent_window)


# ---------------------------------------------------------------------------
# Schutz beim Starten (Tests ersetzen _ask/_run_program/_start_app/_launch_default)
# ---------------------------------------------------------------------------
_NATIVE = ("application/x-executable", "application/x-pie-executable",
           "application/x-sharedlib", "application/vnd.appimage",
           "application/x-iso9660-appimage")
_FOREIGN = ("application/x-ms-dos-executable", "application/x-msdownload",
            "application/x-msi", "application/x-ms-shortcut", "application/x-ms-ne-executable",
            "application/x-dosexec", "application/vnd.microsoft.portable-executable",
            "application/x-bat", "application/x-java-jnlp-file")
# Standard-Apps, die eine Datei nicht anzeigen, sondern ausführen
_RUNNERS = {"sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish", "python", "pypy",
            "perl", "ruby", "node", "nodejs", "php", "lua", "tclsh", "wish", "java", "javaw",
            "wine", "wine64", "mono", "pwsh", "gjs", "appimagelauncher", "bottles", "lutris"}
_RUNNER_IDS = ("wine", "bottles", "lutris", "playonlinux", "mono", "appimage", "java")


def _is_a(ct, types):
    return bool(ct) and any(Gio.content_type_is_a(ct, t) for t in types)


def _runs_code(app):
    if app is None:
        return False
    import re
    exe = os.path.basename(app.get_executable() or "").lower()
    if re.sub(r"[\d.\-]+$", "", exe) in _RUNNERS:
        return True
    aid = (app.get_id() or "").lower()
    return any(k in aid for k in _RUNNER_IDS)


def _trusted_desktop(path):
    """App-Starter aus den Anwendungsordnern (Startmenü) gelten als vertrauenswürdig."""
    real = os.path.realpath(path)
    dirs = [GLib.get_user_data_dir()] + list(GLib.get_system_data_dirs())
    for d in dirs:
        root = os.path.realpath(os.path.join(d, "applications"))
        if real.startswith(root.rstrip("/") + "/"):
            return True
    return False


def _probe(gfile):
    """Was ist die Datei? kind: None (normal), "desktop", "native", "script", "foreign"."""
    uri = gfile.get_uri()
    name = gfile.get_basename() or ""
    path = gfile.get_path() if uri.startswith("file:") else None
    ct, exe, local = None, False, False
    if path:
        try:
            info = gfile.query_info("standard::content-type,standard::type,access::can-execute",
                                    Gio.FileQueryInfoFlags.NONE, None)
            ct = info.get_content_type()
            exe = (info.get_file_type() == Gio.FileType.REGULAR
                   and info.get_attribute_boolean("access::can-execute"))
        except GLib.Error:
            pass
        fuse = os.path.join(GLib.get_user_runtime_dir(), "gvfs") + "/"
        local = not (path.startswith(fuse) or "/.gvfs/" in path)
        if local:
            try:   # Kernel-Netzwerkfreigabe (cifs/nfs) wie einen Netzwerkort behandeln
                fsi = gfile.query_filesystem_info("filesystem::remote", None)
                local = not fsi.get_attribute_boolean("filesystem::remote")
            except GLib.Error:
                pass
    if not ct:   # Netzwerk/Archiv: nur am Namen raten (keine blockierende Abfrage)
        ct = Gio.content_type_guess(name, None)[0]
    app = Gio.AppInfo.get_default_for_type(ct, False) if ct else None
    kind = None
    if _is_a(ct, ("application/x-desktop",)) or name.lower().endswith(".desktop"):
        kind = "desktop"
    elif exe and local and _shebang(path):    # „#!“ – egal, was der Name behauptet
        kind = "script"
    elif _is_a(ct, _NATIVE) and not _is_a(ct, ("text/plain",)):
        kind = "native"         # Binärprogramm (Skripte sind auch x-executable, aber Text)
    elif _is_a(ct, _FOREIGN) or (ct or "").startswith("application/x-wine-extension"):
        kind = "foreign"
    elif _runs_code(app):       # Standard-App führt die Datei aus (Wine, Interpreter …)
        kind = "foreign"
    return {"kind": kind, "ct": ct, "path": path, "local": local and bool(path),
            "exe": exe, "app": app}


def _shebang(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            return os.read(fd, 2) == b"#!"
        finally:
            os.close(fd)
    except OSError:
        return False


def _ask(parent, text, title, buttons, default=0):
    from . import propsdialog
    return propsdialog.message(parent, text, "warning", title, buttons=buttons, default=default)


def _launch_default(gfile):
    try:
        Gio.AppInfo.launch_default_for_uri(gfile.get_uri(), None)
        return True
    except GLib.Error as exc:
        print(f"[filefenster] Öffnen fehlgeschlagen: {exc.message}")
        return False


def _start_app(app, gfiles):
    try:
        from gi.repository import Gdk
        disp = Gdk.Display.get_default()
        app.launch(list(gfiles), disp.get_app_launch_context() if disp else None)
        return True
    except GLib.Error as exc:
        print(f"[filefenster] Starten fehlgeschlagen: {exc.message}")
        return False


def _run_program(path):
    try:
        subprocess.Popen([path], cwd=os.path.dirname(path) or "/", stdin=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError as exc:
        print(f"[filefenster] Ausführen fehlgeschlagen: {exc}")
        return False


def _show_text(gfile, parent):
    """„Anzeigen“: im Texteditor öffnen – nie mit einer App, die Code ausführt."""
    app = Gio.AppInfo.get_default_for_type("text/plain", False)
    if app is None or _runs_code(app):
        _ask(parent, "Es ist kein Texteditor eingerichtet.", "FileFenster", ["OK"])
        return False
    return _start_app(app, [gfile])


_REMOTE_NOTE = ("Programme aus Archiven oder von Netzwerkorten werden nie direkt ausgeführt. "
                "Kopieren Sie die Datei zuerst auf diesen PC.")
_TRUST_NOTE = "Führen Sie nur Programme aus, deren Herkunft Sie vertrauen."


def _guarded(gfile, probe, parent):
    from gi.repository import Gtk
    parent = parent if isinstance(parent, Gtk.Window) else None
    name = places.safe_text(gfile.get_basename() or gfile.get_uri())
    kind, ct, path, local = probe["kind"], probe["ct"], probe["path"], probe["local"]
    desc = places.safe_text(Gio.content_type_get_description(ct)) if ct else "Programm"
    title = "Sicherheitswarnung"
    if kind == "desktop":
        app = None
        if local and path:
            try:
                app = Gio.DesktopAppInfo.new_from_filename(path)
            except (TypeError, GLib.Error):
                app = None
        if app is not None and _trusted_desktop(path):
            return _start_app(app, [])      # wie aus dem Startmenü
        if app is not None:
            cmd = places.safe_text(app.get_commandline() or "")
            r = _ask(parent, f"„{name}“ ist ein App-Starter aus einem nicht vertrauenswürdigen "
                     f"Ort. Er würde diesen Befehl ausführen:\n\n{cmd}\n\n{_TRUST_NOTE}",
                     "Nicht vertrauenswürdiger App-Starter",
                     ["Trotzdem starten", "Anzeigen", "Abbrechen"], default=1)
            if r == 0:
                return _start_app(app, [])
        else:
            note = ("App-Starter aus Archiven oder von Netzwerkorten werden nie gestartet."
                    if not local else "Er kann nicht gestartet werden.")
            r = _ask(parent, f"„{name}“ ist ein App-Starter. {note}", title,
                     ["Anzeigen", "Abbrechen"], default=0) + 1
        return _show_text(gfile, parent) if r == 1 else False
    if kind in ("native", "script"):
        texty = kind == "script"
        if not (local and probe["exe"]):
            note = _REMOTE_NOTE if not local else "Die Datei ist nicht als ausführbar markiert."
            _ask(parent, f"„{name}“ ist ein Programm ({desc}).\n\n{note}", title, ["OK"])
            return False
        what = "ein ausführbares Skript" if texty else "ein Programm"
        more = " oder seinen Inhalt anzeigen" if texty else ""
        buttons = ["Ausführen", "Anzeigen", "Abbrechen"] if texty else ["Ausführen", "Abbrechen"]
        r = _ask(parent, f"„{name}“ ist {what} ({desc}).\n\nMöchten Sie es ausführen{more}? "
                 f"{_TRUST_NOTE}", title, buttons, default=1)
        if r == 0:
            return _run_program(path)
        return _show_text(gfile, parent) if texty and r == 1 else False
    # foreign: Windows-/Java-Programm oder Skript, das die Standard-App ausführt
    app = probe["app"]
    if app is None:
        return _launch_default(gfile)       # meldet „keine App“
    texty = _is_a(ct, ("text/plain",))
    appname = places.safe_text(app.get_display_name() or "")
    if not local:
        buttons = ["Anzeigen", "Abbrechen"] if texty else ["OK"]
        r = _ask(parent, f"„{name}“ ist ein Programm ({desc}) und würde mit „{appname}“ "
                 f"ausgeführt.\n\n{_REMOTE_NOTE}", title, buttons, default=0)
        return _show_text(gfile, parent) if texty and r == 0 else False
    buttons = ["Ausführen", "Anzeigen", "Abbrechen"] if texty else ["Ausführen", "Abbrechen"]
    r = _ask(parent, f"„{name}“ ist ein Programm ({desc}) und wird mit „{appname}“ "
             f"ausgeführt.\n\n{_TRUST_NOTE}", title, buttons, default=1)
    if r == 0:
        return _launch_default(gfile)
    return _show_text(gfile, parent) if texty and r == 1 else False


class FakeHost:
    def __init__(self, window=None, location=places.HOME):
        self.window = window
        self.config = get_config()
        self.location = location
        self.log = []

    def _rec(self, *args):
        self.log.append(args)
        print("[host]", *args)

    def navigate(self, loc):
        self._rec("navigate", loc)
        self.location = loc

    def open_in_new_tab(self, loc): self._rec("open_in_new_tab", loc)
    def open_in_new_window(self, loc): self._rec("open_in_new_window", loc)
    def go_back(self): self._rec("go_back")
    def go_forward(self): self._rec("go_forward")
    def go_up(self): self._rec("go_up")
    def refresh(self): self._rec("refresh")
    def state_changed(self): self._rec("state_changed")
    def show_properties(self, gfiles): self._rec("show_properties", [g.get_uri() for g in gfiles])
    def start_search(self, query): self._rec("start_search", query)
    def stop_search(self): self._rec("stop_search")
    def open_terminal(self, path): self._rec("open_terminal", path)

    def is_pinned(self, uri):
        return uri in pinned_uris(self.config)

    def pin(self, uri):
        lst = pinned_uris(self.config)
        if uri not in lst:
            lst.append(uri)
            self.config.set("pinned", lst)

    def unpin(self, uri):
        lst = [u for u in pinned_uris(self.config) if u != uri]
        self.config.set("pinned", lst)
