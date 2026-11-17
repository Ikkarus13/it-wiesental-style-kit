"""Gemeinsame Dateimanager-Erweiterung fuer Nemo (Mint/Cinnamon) und Nautilus.

Liefert:
  * Overlay-Symbole (Embleme) fuer nur online / lokal / immer behalten / Sync / Fehler
  * die Spalte "OneDrive-Status" in der Listenansicht
  * Kontextmenue: Immer auf diesem Geraet behalten, Speicher freigeben, Freigeben, Online anzeigen

Der Zustand wird direkt aus dem rclone-Cache gelesen (schnell, kein Netz). Aenderungen
meldet der Hintergrunddienst per D-Bus-Signal, dann werden die Symbole aktualisiert.
"""

import os
import shutil
import subprocess
import collections

from gi.repository import Gio, GLib, GObject

from . import config, states, thumbs
from . import marke

ATTR = "onedrive_status"
MAX_TRACKED = 20000


class _NoLock:
    """Alles laeuft im Hauptthread des Dateimanagers – kein Lock noetig."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
_DEBUG = os.environ.get("ODM_DEBUG")


def _log(*a):
    if _DEBUG:
        with open(_DEBUG, "a") as f:
            f.write(" ".join(str(x) for x in a) + "\n")


def build(fm_name):
    if fm_name == "Nemo":
        from gi.repository import Nemo as FM
    else:
        from gi.repository import Nautilus as FM

    bases = (GObject.GObject, FM.InfoProvider, FM.ColumnProvider, FM.MenuProvider)
    if fm_name == "Nemo" and hasattr(FM, "NameAndDescProvider"):
        bases += (FM.NameAndDescProvider,)

    class OneDriveExtension(*bases):
        def __init__(self):
            super().__init__()
            self.runtime = states.RuntimeState()
            # Pfad -> FileInfo. Starke Referenzen (begrenzt), denn die Python-Wrapper werden
            # sonst sofort freigegeben und spaetere Aktualisierungen erreichen die Datei nicht.
            self.files = collections.OrderedDict()
            self.dir_cache = {}                          # Pfad -> Zustand
            self.dir_pending = set()
            self.lock = _NoLock()
            self.bus = None
            try:
                self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                self.bus.signal_subscribe(None, config.DBUS_IFACE, "Changed", config.DBUS_PATH,
                                          None, Gio.DBusSignalFlags.NONE, self._on_changed)
            except GLib.Error:
                pass

        # -------------------------------------------------- Hilfen

        @staticmethod
        def _path(fileinfo):
            if fileinfo.get_uri_scheme() != "file":
                return None
            loc = fileinfo.get_location()
            return loc.get_path() if loc else None

        def _locate(self, path):
            if not path:
                return None, None
            view, rel = self.runtime.find(path)
            if view is None:
                # Verknuepfungen der Ordnersicherung (~/Dokumente, Ordner auf dem Desktop)
                real = os.path.realpath(path)
                if real != path:
                    view, rel = self.runtime.find(real)
            if view is None or (rel and (rel.startswith(".Trash-") or "/.Trash-" in rel)):
                return None, None
            if not view.meta_dir:            # Konto nicht verbunden
                return None, None
            return view, rel

        def _remember(self, path, fileinfo):
            self.files[path] = fileinfo
            self.files.move_to_end(path)
            while len(self.files) > MAX_TRACKED:
                self.files.popitem(last=False)

        def _on_changed(self, _conn, _sender, _path, _iface, _signal, params):
            paths = params.unpack()[0]
            with self.lock:
                for p in paths:
                    self.dir_cache.pop(p, None)
            for p in paths:
                fi = self.files.get(p)
                if fi is not None:
                    fi.invalidate_extension_info()

        def _dir_done(self, path, state):
            _log("dir_done", path, state, path in self.files)
            if state is None:
                # Dienst nicht erreichbar: nicht dauerhaft "unbekannt" cachen
                self.dir_pending.discard(path)
                return False
            with self.lock:
                self.dir_cache[path] = state
                self.dir_pending.discard(path)
            fi = self.files.get(path)
            if fi is not None:
                fi.invalidate_extension_info()
            return False

        def _query_dir(self, path):
            """Ordnerstatus asynchron beim Dienst erfragen.

            Keine Python-Threads hier: nemo-/nautilus-python geben den GIL ausserhalb der
            Erweiterungsaufrufe nicht frei, Threads wuerden haengen bleiben. Die D-Bus-
            Antwort kommt ueber die Hauptschleife zurueck."""
            if self.bus is None:
                self._dir_done(path, None)
                return

            def done(conn, res):
                try:
                    st = conn.call_finish(res).unpack()[0] or None
                except GLib.Error as e:
                    _log("dir_error", path, e.message)
                    st = None
                self._dir_done(path, st)
            self.bus.call(config.DBUS_NAME, config.DBUS_PATH, config.DBUS_IFACE, "GetState",
                          GLib.Variant("(s)", (path,)), GLib.VariantType("(s)"),
                          Gio.DBusCallFlags.NO_AUTO_START, 30000, None, done)

        # -------------------------------------------------- InfoProvider

        def update_file_info(self, fileinfo):
            path = self._path(fileinfo)
            view, rel = self._locate(path)
            if view is None:
                self._desktop_info(fileinfo, path)
                return FM.OperationResult.COMPLETE
            self._remember(path, fileinfo)
            vis = view.path(rel)
            if vis != path:
                # Aenderungssignale kommen mit dem Pfad im OneDrive (~/OneDrive - …/…)
                self._remember(vis, fileinfo)
            busy = self.runtime.busy(view.id)
            if fileinfo.is_directory():
                st = view.dir_state_quick(rel, busy)
                if st is None:
                    with self.lock:
                        st = self.dir_cache.get(path)
                        start = path not in self.dir_cache and path not in self.dir_pending
                        if start:
                            self.dir_pending.add(path)
                    _log("dir_query", path, "start" if start else "skip",
                         path in self.dir_pending, path in self.dir_cache)
                    if start:
                        self._query_dir(path)
            else:
                st = view.file_state(rel, busy, self.runtime.errors(view.id))
                self._thumbnail_policy(fileinfo, path, st)
            _log("update", path, st, fileinfo.is_directory())
            if st:
                if rel:   # der Wurzelordner bekommt das Ordnersymbol statt Emblem
                    fileinfo.add_emblem(states.EMBLEMS[st])
                fileinfo.add_string_attribute(ATTR, states.LABELS[st])
            return FM.OperationResult.COMPLETE

        def _desktop_info(self, fileinfo, path):
            """Dateien auf dem gesicherten Desktop liegen lokal und werden abgeglichen."""
            desktop = self.runtime.raw().get("desktop") if path else None
            if not desktop:
                return
            if path == desktop:
                # Der Ordner selbst: komplett lokal und abgeglichen wie unter Windows
                fileinfo.add_emblem(states.EMBLEMS[states.PINNED])
                fileinfo.add_string_attribute(ATTR, states.LABELS[states.PINNED])
                return
            if os.path.dirname(path) != desktop or fileinfo.is_directory():
                return
            name = os.path.basename(path)
            if name.startswith(".") or name.lower() in ("desktop.ini", "thumbs.db"):
                return
            fileinfo.add_emblem(states.EMBLEMS[states.PINNED])
            fileinfo.add_string_attribute(ATTR, states.LABELS[states.PINNED])

        @staticmethod
        def _thumbnail_policy(fileinfo, path, st):
            """Keine Vorschau fuer Nur-online-Dateien (wuerde den Download ausloesen)."""
            uri = fileinfo.get_uri()
            if st == states.CLOUD:
                try:
                    thumbs.block(uri, os.stat(path).st_mtime)
                except OSError:
                    pass
            elif st in (states.LOCAL, states.PINNED):
                thumbs.unblock(uri)

        # -------------------------------------------------- ColumnProvider

        def get_columns(self):
            return [FM.Column(name="OneDriveMint::status_column", attribute=ATTR,
                              label="OneDrive-Status",
                              description="Verfügbarkeit der Datei in OneDrive")]

        # -------------------------------------------------- MenuProvider

        def get_file_items(self, *args):
            files = args[-1]
            selected = []
            for f in files:
                p = self._path(f)
                view, rel = self._locate(p)
                if view is None:
                    return []
                selected.append((p, view, rel, f.is_directory()))
            if not selected:
                return []
            paths = [s[0] for s in selected]
            all_pinned = all(v.pins.is_pinned(r) for _, v, r, _ in selected)
            any_local = False
            for p, v, r, is_dir in selected:
                if is_dir:
                    any_local |= v.dir_state_quick(r) != states.CLOUD
                else:
                    any_local |= v.file_state(r) != states.CLOUD
            items = []
            pin = FM.MenuItem(name="OneDriveMint::pin",
                              label=("✓ " if all_pinned else "") +
                              "Immer auf diesem Gerät behalten",
                              tip="Dateien herunterladen und offline verfügbar halten",
                              icon="emblem-odm-pinned")
            pin.connect("activate", lambda *_: self._call("SetPinned",
                                                          GLib.Variant("(asb)",
                                                                       (paths, not all_pinned))))
            items.append(pin)
            if any_local or all_pinned:
                free = FM.MenuItem(name="OneDriveMint::free", label="Speicher freigeben",
                                   tip="Nur online verfügbar machen", icon="emblem-odm-cloud")
                free.connect("activate", lambda *_: self._call("FreeUp",
                                                               GLib.Variant("(as)", (paths,))))
                items.append(free)
            if len(paths) == 1:
                share = FM.MenuItem(name="OneDriveMint::share", label="OneDrive-Link kopieren",
                                    tip="Freigabelink erstellen und in die Zwischenablage kopieren",
                                    icon="onedrive-mint")
                share.connect("activate", lambda *_: self._call("Share",
                                                                GLib.Variant("(s)", (paths[0],))))
                items.append(share)
                web = FM.MenuItem(name="OneDriveMint::web", label="Online anzeigen",
                                  tip="Im Browser auf OneDrive öffnen", icon="web-browser")
                web.connect("activate", lambda *_: self._call("OpenOnline",
                                                              GLib.Variant("(s)", (paths[0],))))
                items.append(web)
            return items

        def get_background_items(self, *args):
            folder = args[-1]
            p = self._path(folder)
            view, rel = self._locate(p)
            if view is None:
                return []
            web = FM.MenuItem(name="OneDriveMint::bg_web", label="Online anzeigen",
                              tip="Diesen Ordner auf OneDrive öffnen", icon="web-browser")
            web.connect("activate", lambda *_: self._call("OpenOnline", GLib.Variant("(s)", (p,))))
            sync = FM.MenuItem(name="OneDriveMint::bg_sync", label="OneDrive: Jetzt synchronisieren",
                               tip="Änderungen sofort abrufen", icon="view-refresh")
            sync.connect("activate", lambda *_: self._call("SyncNow", None))
            return [web, sync]

        if fm_name == "Nemo":
            def get_name_and_desc(self):
                return [f"{marke.NAME}:::Status-Symbole, Spalte und Dateien bei Bedarf"]

        # -------------------------------------------------- D-Bus

        def _call(self, method, params):
            if self.bus is None:
                return

            def done(conn, res):
                try:
                    conn.call_finish(res)
                except GLib.Error as e:
                    _start_service()
                    _notify(marke.NAME, f"{marke.NAME} läuft nicht oder hat einen Fehler gemeldet:\n"
                            + e.message.split(":")[-1].strip())
            self.bus.call(config.DBUS_NAME, config.DBUS_PATH, config.DBUS_IFACE, method, params,
                          None, Gio.DBusCallFlags.NONE, 30000, None, done)

    return OneDriveExtension


def _start_service():
    exe = shutil.which("onedrive-mint") or os.path.expanduser("~/.local/bin/onedrive-mint")
    if os.path.exists(exe):
        subprocess.Popen([exe, "--background"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _notify(title, body):
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-i", "onedrive-mint", title, body])
