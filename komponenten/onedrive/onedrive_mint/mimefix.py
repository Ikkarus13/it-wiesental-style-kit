"""Dateitypen fuer unbekannte/mehrdeutige Endungen registrieren.

Kann GNOME (shared-mime-info) eine Endung nicht eindeutig zuordnen – unbekannte wie `.snagx`
oder mehrdeutige wie `.exe` –, liest jeder Dateimanager beim Anzeigen den Dateianfang
(Content-Sniffing). Auf dem OneDrive-Mount heisst das: jede Datei wird heruntergeladen.

Fuer unbekannte Endungen legen wir eine Benutzer-MIME-Definition an
(~/.local/share/mime/packages): eigener Typ application/x-odm-<endung>. Danach ist die
Zuordnung eindeutig und es wird nichts mehr gelesen.
"""

import os
import re
import subprocess
import threading
from xml.sax.saxutils import escape

MIME_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "mime")
PACKAGE = os.path.join(MIME_DIR, "packages", "onedrive-mint-types.xml")
_EXT_RE = re.compile(r"^[a-z0-9_+-]{1,16}$")
_lock = threading.Lock()


def extension(name):
    if "." not in name.strip("."):
        return None
    ext = name.rsplit(".", 1)[1].lower()
    return ext if _EXT_RE.match(ext) else None


def uncertain_extension(name):
    """Unbekannte Endung, fuer die der Dateimanager sonst in die Datei schauen wuerde.

    Mehrdeutige bekannte Endungen (.exe, .html, …) bleiben unangetastet: GLib wertet bei
    Konkurrenz mehrerer Typen auch hoeher gewichtete Benutzer-Globs als unsicher."""
    ext = extension(name)
    if not ext:
        return None
    from gi.repository import Gio
    mime, uncertain = Gio.content_type_guess("x." + ext, None)
    return ext if uncertain and mime in ("application/octet-stream", "") else None


def _known():
    try:
        with open(PACKAGE) as f:
            return set(re.findall(r'pattern="\*\.([^"]+)"', f.read()))
    except OSError:
        return set()


def register(exts):
    """Endungen als eigene Typen registrieren. Gibt die neu registrierten zurueck."""
    with _lock:
        known = _known()
        new = {e for e in exts if e not in known}
        if not new:
            return set()
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 "<!-- Vom Cloud-Sync-Client (onedrive-mint) erzeugt: Typen fuer unbekannte Endungen, damit "
                 "Dateimanager Online-Dateien nicht zur Typ-Erkennung herunterladen. -->",
                 '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">']
        for ext in sorted(known | new):
            parts += [f'  <mime-type type="application/x-odm-{escape(ext)}">',
                      f"    <comment>{escape(ext.upper())}-Datei</comment>",
                      '    <generic-icon name="application-x-generic"/>',
                      f'    <glob pattern="*.{escape(ext)}" weight="50"/>',
                      "  </mime-type>"]
        parts.append("</mime-info>")
        os.makedirs(os.path.dirname(PACKAGE), exist_ok=True)
        tmp = PACKAGE + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(parts) + "\n")
        os.replace(tmp, PACKAGE)
        subprocess.run(["update-mime-database", MIME_DIR], capture_output=True, timeout=60)
        return new
