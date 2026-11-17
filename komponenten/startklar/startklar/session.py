"""Energie, Konto, Ordner öffnen – alles, was das Menü „nach aussen“ tut."""
import os
import shutil

from gi.repository import Gdk, Gio, GLib, Gtk

# Ordner-Schaltflächen der Fusszeile (wie „Ordner“ unter Windows-Personalisierung)
FOLDERS = {
    "documents": ("Dokumente", "document_20_regular", GLib.UserDirectory.DIRECTORY_DOCUMENTS),
    "downloads": ("Downloads", "arrow_download_20_regular", GLib.UserDirectory.DIRECTORY_DOWNLOAD),
    "pictures": ("Bilder", "image_20_regular", GLib.UserDirectory.DIRECTORY_PICTURES),
    "music": ("Musik", "music_note_2_20_filled", GLib.UserDirectory.DIRECTORY_MUSIC),
    "videos": ("Videos", "video_20_filled", GLib.UserDirectory.DIRECTORY_VIDEOS),
    "home": ("Persönlicher Ordner", "home_20_regular", None),
    "explorer": ("Explorer", "folder_20_regular", None),
    "settings": ("Einstellungen", "settings_20_regular", None),
}

# Für Tests: STARTKLAR_DRY_RUN=1 → nur protokollieren, nichts ausführen
DRY = bool(os.environ.get("STARTKLAR_DRY_RUN"))
log = []


def _ctx(timestamp=0):
    display = Gdk.Display.get_default()
    ctx = display.get_app_launch_context() if display else None
    if ctx is not None and timestamp:
        ctx.set_timestamp(timestamp)
    return ctx


def run(argv):
    if DRY:
        log.append(("run", list(argv)))
        return True
    if not shutil.which(argv[0]):
        print("Startklar: Programm fehlt:", argv[0])
        return False
    try:
        GLib.spawn_async(argv, flags=GLib.SpawnFlags.SEARCH_PATH)
        return True
    except GLib.Error as e:
        print("Startklar:", argv[0], e.message)
        return False


def shutdown():
    return run(["cinnamon-session-quit", "--power-off", "--no-prompt"])


def reboot():
    return run(["cinnamon-session-quit", "--reboot", "--no-prompt"])


def logout():
    return run(["cinnamon-session-quit", "--logout", "--no-prompt"])


def suspend():
    return run(["systemctl", "suspend"])


def lock():
    return run(["cinnamon-screensaver-command", "--lock"])


def settings(module=None):
    return run(["cinnamon-settings"] + ([module] if module else []))


def account_settings():
    return settings("user")


def open_uri(uri, timestamp=0):
    if DRY:
        log.append(("open", uri))
        return True
    try:
        Gio.AppInfo.launch_default_for_uri(uri, _ctx(timestamp))
        return True
    except GLib.Error as e:
        print("Startklar: Öffnen fehlgeschlagen:", uri, e.message)
        return False


def open_path(path, timestamp=0):
    return open_uri(Gio.File.new_for_path(path).get_uri(), timestamp)


def show_in_folder(path, timestamp=0):
    """Ordner öffnen und die Datei markieren (FileManager1 → Standard-Dateimanager)."""
    if DRY:
        log.append(("reveal", path))
        return True
    uri = Gio.File.new_for_path(path).get_uri()
    try:
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
            "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1", "ShowItems",
            GLib.Variant("(ass)", ([uri], "")), None, Gio.DBusCallFlags.NONE, 3000, None)
        return True
    except GLib.Error:
        return open_path(os.path.dirname(path) or "/", timestamp)


def open_folder(kind, timestamp=0):
    if kind == "settings":
        return settings()
    if kind == "explorer":
        info = Gio.AppInfo.get_default_for_type("inode/directory", False)
        if DRY:
            log.append(("explorer", info.get_id() if info else None))
            return True
        if info is not None:
            try:
                return info.launch([], _ctx(timestamp))
            except GLib.Error:
                pass
        return open_path(GLib.get_home_dir(), timestamp)
    entry = FOLDERS.get(kind)
    if entry is None:
        return False
    path = GLib.get_user_special_dir(entry[2]) if entry[2] is not None else None
    return open_path(path or GLib.get_home_dir(), timestamp)


def web_search(template, query, timestamp=0):
    q = GLib.uri_escape_string(query, None, False)
    url = template.replace("{}", q) if "{}" in template else template + q
    if not url.startswith(("https://", "http://")):
        return False
    return open_uri(url, timestamp)


def copy_text(text):
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).store()


def can_uninstall():
    return shutil.which("mint-remove-application") is not None


def uninstall(entry):
    fn = entry.info.get_filename()
    if fn:
        return run(["mint-remove-application", fn])
    return False


def user_name():
    return GLib.get_real_name() if GLib.get_real_name() not in ("", "Unknown") else GLib.get_user_name()
