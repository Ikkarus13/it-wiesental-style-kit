"""„An Taskleiste anheften“ für die Cinnamon-Fensterliste
(grouped-window-list@cinnamon.org, eine Instanz je Monitor).

Angeheftet wird über die Fensterliste selbst (org.Cinnamon.Eval → ihre
Methoden addFavorite/removeFavorite): So speichert sie wie gewohnt, und die
Leiste muss nicht neu geladen werden. Klappt Eval nicht, schreibt Startklar die
Einstellungsdateien und lädt die Fensterliste neu.
"""
import glob
import json
import os
import shutil
import tempfile

from gi.repository import Gio, GLib

from . import session

UUID = "grouped-window-list@cinnamon.org"


def _dir():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "cinnamon", "spices", UUID)


def _files():
    return sorted(glob.glob(os.path.join(_dir(), "*.json")))


def available():
    return bool(_files())


def pinned_ids():
    ids = set()
    for fn in _files():
        try:
            with open(fn, encoding="utf-8") as f:
                ids.update(json.load(f).get("pinned-apps", {}).get("value", []))
        except (OSError, ValueError, AttributeError):
            pass
    return ids


def is_pinned(app_id):
    return app_id in pinned_ids()


def _eval(code):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    res = bus.call_sync("org.Cinnamon", "/org/Cinnamon", "org.Cinnamon", "Eval",
                        GLib.Variant("(s)", (code,)), None, Gio.DBusCallFlags.NONE, 3000, None)
    ok, value = res.unpack()
    return ok, value


def _script(app_id, pin):
    aid = json.dumps(app_id)
    if pin:
        body = (f"if(a.pinnedFavorites){{a.pinnedFavorites.addFavorite({{appId:{aid},pos:-1}});n++;}}")
    else:
        body = (f"var p=a.pinnedFavorites;if(p&&p._favorites.some(function(f){{return f.id==={aid};}}))"
                f"{{p.removeFavorite({aid});n++;}}")
    return ("(function(){var n=0;imports.ui.appletManager.getRunningInstancesForUuid("
            f"{json.dumps(UUID)}).forEach(function(a){{{body}}});return n;}})()")


def set_pinned(app_id, pin):
    if session.DRY:
        session.log.append(("taskbar", app_id, pin))
        return True
    try:
        ok, value = _eval(_script(app_id, pin))
        if ok and value.strip() not in ("0", ""):
            return True
        if ok and not pin:
            return True          # war nirgends angeheftet
    except GLib.Error as e:
        print("Startklar: Eval nicht möglich:", e.message)
    return _write_files(app_id, pin)


def _write_files(app_id, pin):
    """Rückfall: Einstellungsdateien ändern und die Fensterliste neu laden."""
    backup = os.path.join(os.path.expanduser("~/.config/startklar"), "taskleiste-sicherung")
    changed = False
    for fn in _files():
        try:
            with open(fn, encoding="utf-8") as f:
                data = json.load(f)
            value = list(data["pinned-apps"]["value"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pin and app_id not in value:
            value.append(app_id)
        elif not pin and app_id in value:
            value.remove(app_id)
        else:
            continue
        os.makedirs(backup, exist_ok=True)
        dst = os.path.join(backup, os.path.basename(fn))
        if not os.path.exists(dst):
            shutil.copy2(fn, dst)
        data["pinned-apps"]["value"] = value
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(fn), prefix=".startklar-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, fn)
        changed = True
    if changed:
        try:
            Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
                "org.Cinnamon", "/org/Cinnamon", "org.Cinnamon", "ReloadXlet",
                GLib.Variant("(ss)", (UUID, "APPLET")), None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass
    return changed
