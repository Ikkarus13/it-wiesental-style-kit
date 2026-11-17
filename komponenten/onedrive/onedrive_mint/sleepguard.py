"""Standby-Schutz.

Haengt ein Prozess in einem Zugriff auf das rclone-Laufwerk (FUSE) fest, kann der Kernel ihn fuer
den Standby nicht einfrieren ("Freezing user space processes failed"). Das System versucht es
dann alle 20 s erneut – im schlimmsten Fall stundenlang, bis der Rechner haengt.

Darum meldet sich der Client bei logind mit einer "delay"-Sperre: Vor dem Standby bekommt er
einige Sekunden, um seine eigenen Laufwerkszugriffe (Herunterladen, Vorschau-Scan, Desktop-
Abgleich) anzuhalten. Erst dann gibt er die Sperre frei. Nach dem Aufwachen geht es weiter.
"""

import os
import threading

from gi.repository import Gio, GLib
from . import marke

LOGIN1 = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
LOGIN1_IFACE = "org.freedesktop.login1.Manager"
SETTLE_SECONDS = 4.0   # logind wartet standardmaessig hoechstens 5 s (InhibitDelayMaxSec)


class SleepGuard:
    def __init__(self, on_sleep, on_resume):
        """on_sleep(timeout) muss blockieren, bis die eigenen Zugriffe ruhen (oder timeout).
        on_resume() wird nach dem Aufwachen aufgerufen."""
        self.on_sleep = on_sleep
        self.on_resume = on_resume
        self._fd = -1
        self._sub = 0
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            self.bus = None
            return
        self._sub = self.bus.signal_subscribe(
            LOGIN1, LOGIN1_IFACE, "PrepareForSleep", LOGIN1_PATH, None,
            Gio.DBusSignalFlags.NONE, self._on_prepare)
        self._inhibit()

    def _inhibit(self):
        if not self.bus or self._fd >= 0:
            return
        try:
            res, fds = self.bus.call_with_unix_fd_list_sync(
                LOGIN1, LOGIN1_PATH, LOGIN1_IFACE, "Inhibit",
                GLib.Variant("(ssss)", ("sleep", marke.NAME,
                                        "Laufwerkszugriffe vor dem Standby anhalten", "delay")),
                GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
            handles = fds.steal_fds()
            self._fd = handles[res.unpack()[0]]
            for i, h in enumerate(handles):
                if h != self._fd:
                    os.close(h)
        except (GLib.Error, IndexError, OSError):
            self._fd = -1

    def _release(self):
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1

    def _on_prepare(self, _conn, _sender, _path, _iface, _signal, params):
        going_down = params.unpack()[0]
        if going_down:
            # Nicht im Hauptthread warten – der muss weiterlaufen (Tray, D-Bus).
            def settle():
                try:
                    self.on_sleep(SETTLE_SECONDS)
                finally:
                    GLib.idle_add(self._release)
            threading.Thread(target=settle, daemon=True, name="sleep-settle").start()
        else:
            self._inhibit()
            self.on_resume()

    def close(self):
        if self.bus and self._sub:
            self.bus.signal_unsubscribe(self._sub)
            self._sub = 0
        self._release()
