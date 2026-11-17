#!/usr/bin/env python3
"""Native-Messaging-Host fuer die Edge-Erweiterung "PWA-Tray: Teams-Status".

Edge startet dieses Skript pro Nachricht, schickt JSON (4-Byte-Laenge + Text) ueber stdin.
Weitergereicht wird per D-Bus an die laufende PWA-Tray-Anwendung (Gio-Aktion "web-status").
Diagnose-Nachrichten (Seitentitel, Beschriftungen aus Teams) werden nur gespeichert, wenn die
Datei ~/.cache/pwa-tray/diagnose-an existiert: dann in ~/.cache/pwa-tray/diag.json (nur fuer den
Benutzer lesbar). Sonst werden sie verworfen und eine alte diag.json geloescht.
"""
import json
import os
import struct
import sys

from gi.repository import Gio, GLib

APP_ID = "ch.sascha.PwaTray"
APP_PATH = "/ch/sascha/PwaTray"


def read_message():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    (n,) = struct.unpack("=I", raw)
    if n > 1024 * 1024:
        return None
    return json.loads(sys.stdin.buffer.read(n).decode("utf-8"))


def reply(obj):
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(data)) + data)
    sys.stdout.buffer.flush()


def forward(msg):
    d = os.path.expanduser("~/.cache/pwa-tray")
    os.makedirs(d, exist_ok=True)
    if msg.get("type") == "status":
        with open(os.path.join(d, "last-status.json"), "w") as f:
            json.dump(msg, f, ensure_ascii=False)
    if msg.get("type") == "diag":
        pfad = os.path.join(d, "diag.json")
        if not os.path.exists(os.path.join(d, "diagnose-an")):
            try:
                os.unlink(pfad)          # enthaelt evtl. Namen/Nachrichten – nicht aufheben
            except OSError:
                pass
            return True
        fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(msg, f, indent=1, ensure_ascii=False)
        return True
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call_sync(APP_ID, APP_PATH, "org.gtk.Actions", "Activate",
                  GLib.Variant("(sava{sv})", ("web-status",
                                              [GLib.Variant("s", json.dumps(msg))], {})),
                  None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
    return True


def main():
    msg = read_message()
    if not isinstance(msg, dict):
        return
    try:
        ok = forward(msg)
    except Exception as e:  # PWA-Tray laeuft nicht o. ae.
        reply({"ok": False, "error": str(e)[:200]})
        return
    reply({"ok": ok})


if __name__ == "__main__":
    main()
