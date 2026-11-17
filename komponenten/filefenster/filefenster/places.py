"""Orte und Pfade: virtuelle Orte (Start, Dieser PC), XDG-Spezialordner,
OneDrive-Ordner, Laufwerke, Brotkrumen für die Adressleiste.

Ein "Ort" (loc) ist immer ein String:
    "home:"    -> Start (Schnellzugriff + zuletzt verwendet)
    "gallery:" -> Katalog (Bilder und Videos der Sammlung, gallery.py)
    "thispc:"  -> Dieser PC (Laufwerke)
    sonst eine Gio-URI: file:///..., smb://..., trash:///, network:///
"""
import json
import os
import pwd
import re

from gi.repository import Gio, GLib

HOME = "home:"
THISPC = "thispc:"
GALLERY = "gallery:"
VIRTUAL = (HOME, THISPC, GALLERY)

# Attribute für Ordnerlisten und Suche (überall gleich verwenden)
FILE_ATTRS = ",".join([
    "standard::name", "standard::display-name", "standard::edit-name",
    "standard::type", "standard::size", "standard::icon", "standard::symbolic-icon",
    "standard::content-type", "standard::is-hidden", "standard::is-backup",
    "standard::is-symlink", "standard::symlink-target", "standard::target-uri",
    "time::modified", "time::created", "time::access", "owner::user",
    "access::can-read", "access::can-write", "access::can-rename",
    "access::can-delete", "access::can-trash",
    "thumbnail::path", "thumbnail::failed",
    "unix::mode", "owner::user",
    "trash::orig-path", "trash::deletion-date",
])

_SPECIAL = [
    # key, Windows-Bezeichnung, XDG-Verzeichnis
    ("desktop", "Desktop", GLib.UserDirectory.DIRECTORY_DESKTOP),
    ("downloads", "Downloads", GLib.UserDirectory.DIRECTORY_DOWNLOAD),
    ("documents", "Dokumente", GLib.UserDirectory.DIRECTORY_DOCUMENTS),
    ("pictures", "Bilder", GLib.UserDirectory.DIRECTORY_PICTURES),
    ("music", "Musik", GLib.UserDirectory.DIRECTORY_MUSIC),
    ("videos", "Videos", GLib.UserDirectory.DIRECTORY_VIDEOS),
]


def home_dir():
    return GLib.get_home_dir()


def user_display_name():
    real = GLib.get_real_name()
    if real and real != "Unknown":
        return real.split(",")[0]
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return GLib.get_user_name()


def special_dirs():
    """Liste der vorhandenen Spezialordner: dicts mit key, label, path, uri."""
    out = []
    home = home_dir()
    for key, label, which in _SPECIAL:
        path = GLib.get_user_special_dir(which)
        if not path or os.path.normpath(path) == os.path.normpath(home):
            continue
        if os.path.isdir(path):
            out.append({"key": key, "label": label, "path": path,
                        "uri": Gio.File.new_for_path(path).get_uri()})
    return out


def onedrive_dirs():
    """~/OneDrive* Ordner: dicts mit key='onedrive', label, path, uri."""
    out = []
    home = home_dir()
    try:
        names = sorted(os.listdir(home))
    except OSError:
        names = []
    for name in names:
        if name.lower().startswith("onedrive") and os.path.isdir(os.path.join(home, name)):
            path = os.path.join(home, name)
            out.append({"key": "onedrive", "label": name, "path": path,
                        "uri": Gio.File.new_for_path(path).get_uri()})
    return out


ODM_ACCOUNTS = os.path.expanduser("~/.config/onedrive-mint/accounts.json")


def org_dirs():
    """Organisationsordner synchronisierter Teams-/SharePoint-Bibliotheken
    (unter Windows ~/<Organisation> mit Gebäude-Symbol): Elternordner der
    Bibliotheken aus onedrive-mint und Home-Ordner mit dessen Gebäude-Icon.
    dicts mit key='org', label, path, uri."""
    home = os.path.normpath(home_dir())
    found = set()
    try:
        with open(ODM_ACCOUNTS, encoding="utf-8") as fh:
            data = json.load(fh)
        accounts = data.get("accounts", data) if isinstance(data, dict) else data
        if isinstance(accounts, dict):
            accounts = list(accounts.values())
        for a in accounts:
            if isinstance(a, dict) and a.get("kind") == "library" and a.get("mount_path"):
                parent = os.path.dirname(os.path.normpath(a["mount_path"]))
                if os.path.dirname(parent) == home and os.path.isdir(parent):
                    found.add(parent)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):   # fremde/kaputte accounts.json
        pass
    try:
        names = os.listdir(home)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(home, name)
        if name.startswith(".") or path in found or not os.path.isdir(path):
            continue
        try:
            info = Gio.File.new_for_path(path).query_info(
                "metadata::custom-icon", Gio.FileQueryInfoFlags.NONE, None)
            icon = info.get_attribute_string("metadata::custom-icon") or ""
        except GLib.Error:
            icon = ""
        if "odm-org" in icon:
            found.add(path)
    return [{"key": "org", "label": os.path.basename(p), "path": p,
             "uri": Gio.File.new_for_path(p).get_uri()}
            for p in sorted(found, key=str.lower)]


def cloud_dirs():
    """OneDrive-Ordner und Organisationsordner (Reihenfolge wie Windows)."""
    return onedrive_dirs() + org_dirs()


_special_cache = None


def _special_map():
    global _special_cache
    if _special_cache is None:
        m = {os.path.normpath(home_dir()): ("user", user_display_name())}
        for d in special_dirs():
            m[os.path.normpath(d["path"])] = (d["key"], d["label"])
        for d in cloud_dirs():
            m[os.path.normpath(d["path"])] = (d["key"], d["label"])
        _special_cache = m
    return _special_cache


def special_key_for_path(path):
    """'documents', 'onedrive', ... für Spezialordner, sonst None.
    (Home-Ordner liefert None, damit er als normaler Ordner erscheint.)"""
    if not path:
        return None
    hit = _special_map().get(os.path.normpath(path))
    if hit and hit[0] != "user":
        return hit[0]
    return None


def default_pinned():
    return [d["uri"] for d in special_dirs()]


def to_gfile(loc):
    if loc in VIRTUAL:
        return None
    return Gio.File.new_for_uri(loc)


def uri_for_path(path):
    return Gio.File.new_for_path(path).get_uri()


def local_path(loc):
    f = to_gfile(loc)
    return f.get_path() if f is not None else None


def is_virtual(loc):
    return loc in VIRTUAL


def root_label():
    return "Systemlaufwerk (/)"


REMOTE_SCHEMES = ("smb", "sftp", "ftp", "ftps", "dav", "davs", "dav+sd", "davs+sd", "afp", "nfs", "network")


def is_remote(loc):
    """Netzwerkort? (gvfs hängt ihn per FUSE ein – trotzdem nie den FUSE-Pfad zeigen)"""
    return bool(loc) and loc.split(":", 1)[0].lower() in REMOTE_SCHEMES


def remote_parts(loc):
    """smb://server/freigabe/a%20b → ("smb", "server", ["freigabe", "a b"])."""
    try:
        u = GLib.Uri.parse(loc, GLib.UriFlags.NONE)
    except GLib.Error:
        return "", "", []
    parts = [GLib.Uri.unescape_string(p, None) or p for p in (u.get_path() or "").split("/") if p]
    return u.get_scheme() or "", u.get_host() or "", parts


def _remote_root(loc):
    u = GLib.Uri.parse(loc, GLib.UriFlags.NONE)
    return GLib.Uri.build(GLib.UriFlags.NONE, u.get_scheme(), u.get_userinfo(), u.get_host(),
                          u.get_port(), "/", None, None).to_string()


def _remote_join(root, parts):
    """URI aus Wurzel und Ordnernamen – als Text, denn gvfs' get_child/get_parent
    liefern bei SMB interne Namen (smb://server/._Freigabe)."""
    esc = [GLib.Uri.escape_string(p, "!$&'()*+,;=:@", False) for p in parts]
    return root.rstrip("/") + "/" + "/".join(esc)


def without_password(loc):
    """smb://benutzer:geheim@server/… → smb://benutzer@server/… – Passwörter
    nie in Einstellungen (Adressverlauf, gemerkte Laufwerke) schreiben; beim
    Verbinden fragt der Anmeldedialog (mit Schlüsselbund) danach."""
    if not loc or "://" not in loc or "@" not in loc:
        return loc
    scheme, _sep, rest = loc.partition("://")
    auth, slash, path = rest.partition("/")
    userinfo, at, host = auth.rpartition("@")
    if not at or ":" not in userinfo:
        return loc
    return f"{scheme}://{userinfo.split(':', 1)[0]}@{host}{slash}{path}"


def unc_to_uri(text):
    """\\\\server\\freigabe\\Ordner (oder //server/…) → smb://server/freigabe/Ordner."""
    import re
    parts = [p for p in re.split(r"[\\/]+", text.strip()) if p]
    if not parts:
        return None
    return _remote_join(f"smb://{parts[0]}/", parts[1:])


def archive_uri(gfile):
    """archive://-Adresse (gvfs) einer ZIP-/Archivdatei – öffnet sie wie einen Ordner."""
    host = GLib.Uri.escape_string(GLib.Uri.escape_string(gfile.get_uri(), None, False), None, False)
    return f"archive://{host}/"


def archive_parts(loc):
    """archive://… → (Archivdatei als Gio.File, Wurzel-URI, innere Ordnernamen) oder None."""
    if not loc or not loc.startswith("archive://"):
        return None
    host = loc[len("archive://"):].split("/", 1)[0]
    furi = GLib.Uri.unescape_string(GLib.Uri.unescape_string(host, None) or "", None)
    if not furi:
        return None
    root = f"archive://{host}/"
    inner = Gio.File.new_for_uri(root).get_relative_path(Gio.File.new_for_uri(loc)) or ""
    return Gio.File.new_for_uri(furi), root, [p for p in inner.split("/") if p]


# auch einzelne Surrogate: so kommen Namen mit ungültigem UTF-8 an (GTK/GLib werfen dann)
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069\ud800-\udfff]")


def safe_text(text):
    """Fremde Namen für die Anzeige: Steuerzeichen (Zeilenumbruch …) und
    Richtungszeichen (U+202E macht aus „rechnung<RLO>fdp.exe“ scheinbar
    „rechnungexe.pdf“) werden zu „\ufffd“ – die echte Endung bleibt sichtbar."""
    return _UNSAFE.sub("\ufffd", text) if text else (text or "")


def display_name(loc):
    return safe_text(_display_name(loc))


def _display_name(loc):
    """Name für Tab-Titel, Fenstertitel, Suchfeld-Platzhalter."""
    if loc == HOME:
        return "Start"
    if loc == THISPC:
        return "Dieser PC"
    if loc == GALLERY:
        return "Katalog"
    arc = archive_parts(loc)
    if arc:
        return arc[2][-1] if arc[2] else arc[0].get_basename()
    if is_remote(loc):
        sch, host, parts = remote_parts(loc)
        if sch == "network":
            return "Netzwerk"
        return parts[-1] if parts else (host or loc)
    f = to_gfile(loc)
    path = f.get_path()
    if path is not None:
        norm = os.path.normpath(path)
        hit = _special_map().get(norm)
        if hit:
            return hit[1]
        if norm == "/":
            return root_label()
        return os.path.basename(norm)
    if loc.startswith("trash:"):
        return "Papierkorb"
    if loc.startswith("network:"):
        return "Netzwerk"
    try:
        info = f.query_info("standard::display-name", Gio.FileQueryInfoFlags.NONE, None)
        return info.get_display_name()
    except GLib.Error:
        base = f.get_basename()
        return base if base and base != "/" else (f.get_uri_scheme() or loc)


def loc_icon_kind(loc):
    """Icon-Art (für icons.place_pixbuf) eines Ortes."""
    if loc == HOME:
        return "home"
    if loc == THISPC:
        return "thispc"
    if loc == GALLERY:
        return "gallery"
    if loc.startswith("trash:"):
        return "trash"
    if loc.startswith("archive:"):
        return "folder"
    if loc.startswith(("network:", "smb:", "sftp:", "ftp:", "dav")):
        return "network"
    path = local_path(loc)
    if path:
        norm = os.path.normpath(path)
        if norm == "/":
            return "drive-system"
        hit = _special_map().get(norm)
        if hit:
            return hit[0]
    return "folder"


def breadcrumbs(loc):
    """Liste von (label, loc) für die Adressleiste."""
    if loc == HOME:
        return [("Start", HOME)]
    if loc == THISPC:
        return [("Dieser PC", THISPC)]
    if loc == GALLERY:
        return [("Katalog", GALLERY)]
    if is_remote(loc):   # Netzwerk › SERVER › freigabe › Ordner (wie Windows)
        sch, host, parts = remote_parts(loc)
        crumbs = [("Netzwerk", "network:///")]
        if sch != "network" and host:
            root = _remote_root(loc)
            crumbs.append((host, root))
            for i, p in enumerate(parts):
                crumbs.append((p, _remote_join(root, parts[:i + 1])))
        return crumbs
    arc = archive_parts(loc)
    if arc:      # … › Ordner › Archiv.zip › Unterordner (wie Windows)
        afile, root, parts = arc
        par = afile.get_parent()
        crumbs = breadcrumbs(par.get_uri()) if par is not None else []
        crumbs.append((afile.get_basename(), root))
        cur = Gio.File.new_for_uri(root)
        for p in parts:
            cur = cur.get_child(p)
            crumbs.append((p, cur.get_uri()))
        return crumbs
    f = to_gfile(loc)
    path = f.get_path()
    if path is not None:
        norm = os.path.normpath(path)
        smap = _special_map()
        # nächsten "Anker" (Spezialordner/Home) suchen
        anchor = norm
        while True:
            if anchor in smap:
                break
            parent = os.path.dirname(anchor)
            if parent == anchor:
                anchor = None
                break
            anchor = parent
        crumbs = []
        if anchor is not None:
            crumbs.append((smap[anchor][1], uri_for_path(anchor)))
            rest = os.path.relpath(norm, anchor)
            cur = anchor
        else:
            crumbs.append(("Dieser PC", THISPC))
            crumbs.append((root_label(), uri_for_path("/")))
            rest = os.path.relpath(norm, "/")
            cur = "/"
        if rest != ".":
            for part in rest.split(os.sep):
                cur = os.path.join(cur, part)
                crumbs.append((safe_text(part), uri_for_path(cur)))
        return crumbs
    # Nicht-lokale URIs: an den Eltern entlang
    chain = []
    cur = f
    while cur is not None:
        chain.append(cur)
        cur = cur.get_parent()
    chain.reverse()
    crumbs = []
    if loc.startswith(("smb:", "network:", "sftp:", "ftp:", "dav")):
        crumbs.append(("Netzwerk", "network:///"))
    for g in chain:
        uri = g.get_uri()
        if uri.rstrip("/") in ("network:", "smb:", "network:/", "smb:/"):
            continue
        crumbs.append((display_name(uri), uri))
    return crumbs


def parent(loc):
    """Übergeordneter Ort (für den Pfeil nach oben) oder None."""
    if loc in (HOME, GALLERY):
        return None
    if loc == THISPC:
        return HOME
    if is_remote(loc):
        sch, host, parts = remote_parts(loc)
        if sch == "network" or not host:
            return None
        if not parts:
            return "network:///"            # Server → Netzwerk
        return _remote_join(_remote_root(loc), parts[:-1])
    arc = archive_parts(loc)
    if arc and not arc[2]:          # Wurzel des Archivs → Ordner mit der Archivdatei
        par = arc[0].get_parent()
        return par.get_uri() if par is not None else None
    f = to_gfile(loc)
    path = f.get_path()
    if path is not None and os.path.normpath(path) == "/":
        return THISPC
    p = f.get_parent()
    return p.get_uri() if p is not None else None


GALLERY_WORDS = ("katalog", "galerie", "gallery", "gallery:")


def resolve_input(text, current=None):
    """Text aus der Adressleiste in einen Ort umwandeln (oder None)."""
    t = text.strip()
    if not t:
        return None
    low = t.lower()
    if low in ("start", "home:", "schnellzugriff"):
        return HOME
    if low in ("dieser pc", "thispc:", "computer"):
        return THISPC
    if low in GALLERY_WORDS:
        return GALLERY
    if low in ("papierkorb", "trash", "trash:", "trash:///"):
        return "trash:///"
    if low in ("netzwerk", "network", "network:///"):
        return "network:///"
    if t.startswith("\\\\") or (t.startswith("//") and not t.startswith("///")):
        return unc_to_uri(t)          # \\\\server\\freigabe wie unter Windows
    if "://" in t or (t.endswith(":") and t[:-1].isalpha()):
        return t
    t = os.path.expanduser(t)
    if not os.path.isabs(t):
        base = local_path(current) if current and not is_virtual(current) else None
        t = os.path.join(base or home_dir(), t)
    return uri_for_path(os.path.normpath(t))


_volumes = {}     # "volume:///…" → Gio.Volume (nicht eingehängte Wechseldatenträger)


def natural_key_str(text):
    """sda2 vor sda10."""
    import re
    return [int(t) if i % 2 else t for i, t in enumerate(re.split(r"(\d+)", text))]


def volume_loc(vol):
    """Ort für ein nicht eingehängtes Laufwerk; Navigieren dorthin hängt es ein."""
    ident = (vol.get_identifier("unix-device") or vol.get_uuid() or vol.get_name() or "?")
    loc = "volume:///" + GLib.Uri.escape_string(ident, None, False)
    _volumes[loc] = vol
    return loc


def volume_for(loc):
    return _volumes.get(loc)


def _removable(vol):
    drive = vol.get_drive()
    return drive is not None and (drive.is_removable() or drive.is_media_removable()
                                  or drive.can_eject())


def drives():
    """Laufwerke für "Dieser PC": dicts mit label, uri, path, kind, total, free, mount."""
    out = []
    seen_devs = set()

    def add(label, path, kind, mount=None):
        try:
            st = os.statvfs(path)
            dev = os.stat(path).st_dev
        except OSError:
            return
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        # gleiche Partition (auch Btrfs-Subvolumes: andere st_dev, gleiche Werte) nur einmal
        key = (st.f_blocks, st.f_bfree, st.f_files)
        if dev in seen_devs or key in seen_devs:
            return
        seen_devs.add(dev)
        seen_devs.add(key)
        out.append({"label": label, "uri": uri_for_path(path), "path": path,
                    "kind": kind, "total": total, "free": free, "mount": mount})

    add(root_label(), "/", "drive-system")
    home = home_dir()
    add(f"Benutzerdaten ({home})", home, "drive")
    vm = Gio.VolumeMonitor.get()
    remote_seen = set()
    for mount in vm.get_mounts():
        root = mount.get_root()
        path = root.get_path()
        if mount.is_shadowed() or root.get_uri().startswith("archive:"):
            continue      # geöffnete ZIP-Dateien sind keine Laufwerke
        if path is None or is_remote(root.get_uri()):
            from . import network
            uri = root.get_uri()
            key = uri.rstrip("/").lower()
            remote_seen.add(key)
            # Schreibweise des gemerkten Laufwerks (gvfs schreibt Freigaben klein)
            saved = next((d["uri"] for d in network.saved_drives()
                          if d["uri"].rstrip("/").lower() == key), None)
            label = (network.drive_label(saved or uri) if uri.startswith("smb:")
                     else mount.get_name())
            out.append({"label": label, "uri": uri, "path": None,
                        "kind": "network", "total": 0, "free": 0, "mount": mount})
            continue
        if path.startswith(("/snap/", "/var/snap")):
            continue
        kind = "usb" if mount.can_eject() else "drive"
        add(mount.get_name(), path, kind, mount)
    # Eingesteckt, aber (noch) nicht eingehängt – Windows zeigt solche Laufwerke
    # trotzdem; ein Klick hängt sie ein (z. B. wenn der Automounter nicht wollte)
    # gemerkte Netzlaufwerke, auch wenn (noch) nicht verbunden – Klick verbindet
    from . import network
    for d in network.saved_drives():
        if d["uri"].rstrip("/").lower() not in remote_seen:
            out.append({"label": network.drive_label(d["uri"]), "uri": d["uri"], "path": None,
                        "kind": "network", "total": 0, "free": 0, "mount": None,
                        "tip": "Netzlaufwerk – zum Verbinden anklicken"})
    vols = sorted(vm.get_volumes(), key=lambda v: natural_key_str(
        v.get_identifier("unix-device") or v.get_name() or ""))
    for vol in vols:
        if vol.get_mount() is None and vol.can_mount() and _removable(vol):
            out.append({"label": vol.get_name(), "uri": volume_loc(vol), "path": None,
                        "kind": "usb", "total": 0, "free": 0, "mount": None, "volume": vol,
                        "tip": "Wechseldatenträger – zum Öffnen anklicken"})
    return out


def format_size(n):
    """Grösse wie Windows in der Statusleiste: 1,23 MB."""
    if n is None:
        return ""
    units = ["Bytes", "KB", "MB", "GB", "TB"]
    if n < 1024:
        return f"{n} Bytes"
    val = float(n)
    i = 0
    while val >= 1024 and i < len(units) - 1:
        val /= 1024
        i += 1
    if val >= 100:
        s = f"{val:.0f}"
    elif val >= 10:
        s = f"{val:.1f}"
    else:
        s = f"{val:.2f}"
    return s.replace(".", ",") + " " + units[i]


def format_size_kb(n):
    """Grössenspalte der Detailansicht: immer KB, aufgerundet, mit Tausenderpunkt."""
    if n is None:
        return ""
    kb = (n + 1023) // 1024
    if n > 0 and kb == 0:
        kb = 1
    return f"{kb:,}".replace(",", ".") + " KB"


def format_date(unix_ts):
    """08.11.2026 14:32"""
    if not unix_ts:
        return ""
    try:    # unsinnige Zeitstempel (Archive, FAT, „touch -d @…“): Konstruktor wirft
        dt = GLib.DateTime.new_from_unix_local(int(unix_ts))
    except (TypeError, ValueError, OverflowError):
        return ""
    return dt.format("%d.%m.%Y %H:%M") if dt else ""


def content_type(info):
    """MIME-Typ einer Gio.FileInfo oder "". Fehlt er (Dateien in ZIP-Archiven
    über gvfs), wird er aus dem Namen geraten – info.get_content_type() würde
    dann GLib-GIO-CRITICAL ins Protokoll schreiben."""
    if info is None:
        return ""
    ct = (info.get_attribute_string("standard::content-type")
          or info.get_attribute_string("standard::fast-content-type"))
    if ct:
        return ct
    name = (info.get_attribute_string("standard::display-name")
            or info.get_attribute_byte_string("standard::name"))
    if not name:
        return ""
    ct, uncertain = Gio.content_type_guess(name, None)
    return "" if uncertain and ct == "application/octet-stream" else (ct or "")


def type_description(info):
    """Typ-Spalte: 'Dateiordner' oder Beschreibung des MIME-Typs."""
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        return "Dateiordner"
    ct = content_type(info)
    if not ct:
        return "Datei"
    return Gio.content_type_get_description(ct)
