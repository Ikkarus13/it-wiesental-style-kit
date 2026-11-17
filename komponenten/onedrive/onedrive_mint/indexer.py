"""Datei-Indexer (GNOME/Cinnamon "tracker") vom OneDrive fernhalten.

Zeigen Dokumente oder Bilder nach der Ordnersicherung ins OneDrive, durchsucht tracker-miner-fs
dort rekursiv alles und liest Dateiinhalte – das laedt das halbe OneDrive herunter. Ohne Netz
bleibt der Indexer im Laufwerk haengen und verhindert den Standby (siehe sleepguard.py).
Darum werden Index-Ordner, die im OneDrive liegen, aus der Indexer-Liste genommen.
"""

import os

from gi.repository import Gio

SCHEMA = "org.freedesktop.Tracker3.Miner.Files"
KEYS = ("index-recursive-directories", "index-single-directories")
ALIASES = {"&DESKTOP": "DESKTOP", "&DOCUMENTS": "DOCUMENTS", "&DOWNLOAD": "DOWNLOAD",
           "&MUSIC": "MUSIC", "&PICTURES": "PICTURES", "&PUBLIC_SHARE": "PUBLICSHARE",
           "&TEMPLATES": "TEMPLATES", "&VIDEOS": "VIDEOS"}


def _inside(path, mounts):
    path = path.rstrip("/")
    return any(path == m or path.startswith(m + "/") for m in mounts)


def _resolve(entry, user_dirs):
    if entry in ALIASES:
        return user_dirs.get(ALIASES[entry], "")
    if entry.startswith("$HOME"):
        return os.path.expanduser("~") + entry[5:]
    return os.path.expanduser(entry)


def _points_into(path, mounts):
    """Ohne das FUSE-Laufwerk selbst anzufassen: nur Pfad-Vergleich und readlink ausserhalb."""
    if not path:
        return False
    if _inside(path, mounts):
        return True
    try:
        if os.path.islink(path):
            target = os.readlink(path)
            target = os.path.normpath(os.path.join(os.path.dirname(path), target))
            return _inside(target, mounts)
    except OSError:
        pass
    return False


def exclude_mounts(mount_paths, user_dirs):
    """Entfernt Index-Ordner, die ins OneDrive zeigen. Liefert die entfernten Eintraege."""
    src = Gio.SettingsSchemaSource.get_default()
    if not src or not src.lookup(SCHEMA, True):
        return []
    mounts = [m.rstrip("/") for m in mount_paths if m]
    if not mounts:
        return []
    settings = Gio.Settings.new(SCHEMA)
    removed = []
    for key in KEYS:
        entries = settings.get_strv(key)
        keep = []
        for e in entries:
            # $HOME (nicht rekursiv) bleibt – dort liegen nur die Einstiegsordner.
            if e != "$HOME" and _points_into(_resolve(e, user_dirs), mounts):
                removed.append(e)
            else:
                keep.append(e)
        if keep != entries:
            settings.set_strv(key, keep)
    if removed:
        Gio.Settings.sync()
    return removed
