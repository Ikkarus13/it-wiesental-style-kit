#!/usr/bin/env python3
"""Startklar starten oder steuern.

    startklar                    Dienst starten (Menü bleibt verborgen)
    startklar --toggle [JSON]    Menü zeigen/verbergen
    startklar --show [JSON]      Menü zeigen
    startklar --search TEXT      Menü mit Suchtext zeigen
    startklar --settings         Einstellungen öffnen
    startklar --quit             Dienst beenden
    startklar --version
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib  # noqa: E402

from startklar import APP_ID, DBUS_PATH, VERSION  # noqa: E402


def running(bus):
    try:
        res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                            "org.freedesktop.DBus", "NameHasOwner",
                            GLib.Variant("(s)", (APP_ID,)), None,
                            Gio.DBusCallFlags.NONE, 2000, None)
        return res.unpack()[0]
    except GLib.Error:
        return False


def call(bus, action, arg):
    params = [GLib.Variant("s", arg)] if arg is not None else []
    bus.call_sync(APP_ID, DBUS_PATH, "org.gtk.Actions", "Activate",
                  GLib.Variant("(sava{sv})", (action, params, {})), None,
                  Gio.DBusCallFlags.NONE, 5000, None)


def main(argv):
    args = argv[1:]
    service = "--gapplication-service" in args
    args = [a for a in args if a != "--gapplication-service"]
    action, arg = None, None
    if args:
        opt = args[0]
        if opt in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        if opt == "--version":
            print("Startklar", VERSION)
            return 0
        if opt in ("--toggle", "--show"):
            action, arg = opt[2:], (args[1] if len(args) > 1 else "{}")
        elif opt == "--search":
            action, arg = "search", json.dumps({"text": " ".join(args[1:])})
        elif opt == "--quit":
            action = "quit"
        elif opt == "--settings":
            action = "settings"
        elif opt != "--hidden":
            print("Unbekannte Option:", opt, file=sys.stderr)
            return 2
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if running(bus):
        if action:
            call(bus, action, arg)
        return 0
    if action == "quit":
        return 0
    from startklar.app import App
    app = App(initial=(action, arg) if action else None)
    return app.run([argv[0]] + (["--gapplication-service"] if service else []))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
