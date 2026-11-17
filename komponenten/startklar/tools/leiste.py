#!/usr/bin/env python3
"""Startknopf in der Cinnamon-Leiste einsetzen bzw. das alte Menü zurückholen.

    leiste.py ersetzen SICHERUNG   altes Menü-Applet durch startklar@sascha ersetzen
    leiste.py zurueck SICHERUNG    vorherige Belegung wiederherstellen
    leiste.py entfernen            startklar@sascha aus der Leiste nehmen
    leiste.py ist-drin             Exit 0, wenn der Startknopf in der Leiste ist

Arbeitet nur auf org.cinnamon enabled-applets (Cinnamon lädt das selbst neu).
Zum Testen: GSETTINGS_BACKEND=memory.
"""
import os
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

UUID = "startklar@sascha"
MENUS = ("Cinnamenu@json", "menu@cinnamon.org", "CinnVIIStarkMenu@NikoKrause")


def settings():
    src = Gio.SettingsSchemaSource.get_default()
    if src is None or src.lookup("org.cinnamon", True) is None:
        sys.exit("Keine Cinnamon-Einstellungen gefunden.")
    return Gio.Settings.new("org.cinnamon")


def parse(entry):
    # panel1:center:1:Cinnamenu@json:28
    parts = entry.split(":")
    return parts if len(parts) >= 5 else None


def replace(backup):
    s = settings()
    applets = list(s.get_strv("enabled-applets"))
    if any((parse(a) or [None] * 5)[3] == UUID for a in applets):
        print("Startknopf ist schon in der Leiste.")
        return
    ids = [int(p[4]) for p in map(parse, applets) if p and p[4].isdigit()]
    new_id = max(ids + [s.get_int("next-applet-id") - 1]) + 1
    out, done = [], False
    for a in applets:
        p = parse(a)
        if not done and p and p[3] in MENUS:
            out.append(":".join([p[0], p[1], p[2], UUID, str(new_id)]))
            done = True
        else:
            out.append(a)
    if not done:
        # kein Menü gefunden: links in die erste Leiste
        out.append(f"panel1:left:0:{UUID}:{new_id}")
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as f:
            f.write("\n".join(applets) + "\n")
    s.set_strv("enabled-applets", out)
    s.set_int("next-applet-id", new_id + 1)
    Gio.Settings.sync()
    print("Startknopf eingesetzt" + (" (statt des bisherigen Menüs)." if done else "."))


def restore(backup):
    s = settings()
    try:
        with open(backup, encoding="utf-8") as f:
            old = [line.strip() for line in f if line.strip()]
    except OSError:
        old = None
    if old:
        s.set_strv("enabled-applets", old)
        os.unlink(backup)
        print("Leiste wiederhergestellt.")
    else:
        remove()
    Gio.Settings.sync()


def remove():
    s = settings()
    applets = [a for a in s.get_strv("enabled-applets") if (parse(a) or [None] * 5)[3] != UUID]
    s.set_strv("enabled-applets", applets)
    Gio.Settings.sync()
    print("Startknopf aus der Leiste entfernt.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ersetzen" and len(sys.argv) > 2:
        replace(sys.argv[2])
    elif cmd == "zurueck" and len(sys.argv) > 2:
        restore(sys.argv[2])
    elif cmd == "entfernen":
        remove()
    elif cmd == "ist-drin":
        sys.exit(0 if any((parse(a) or [None] * 5)[3] == UUID
                          for a in settings().get_strv("enabled-applets")) else 1)
    else:
        sys.exit(__doc__.strip())
