"""Tray-Symbol.

* Cinnamon/MATE/Xfce (z. B. Linux Mint): XApp.StatusIcon – Linksklick oeffnet das Aktivitaetscenter
* GNOME (Ubuntu, Fedora), KDE, …: Ayatana-AppIndicator (StatusNotifierItem); unter GNOME
  braucht es die Shell-Erweiterung „AppIndicator Support“ (Ubuntu hat sie vorinstalliert)
* sonst: altes Gtk.StatusIcon
"""

import os

import gi
from gi.repository import Gio, Gtk
from . import marke

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp
except (ValueError, ImportError):
    XApp = None

AppIndicator = None
for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(ns, "0.1")
        AppIndicator = getattr(__import__("gi.repository", fromlist=[ns]), ns)
        break
    except (ValueError, ImportError, AttributeError):
        continue

ICON_DIR = os.path.join(os.path.expanduser("~/.local/share/icons/hicolor/scalable/status"))


def _xapp_usable():
    """XApp nur, wenn ein XApp-Infobereich laeuft (sonst waere das Symbol unsichtbar)."""
    if XApp is None:
        return False
    try:
        if XApp.StatusIcon.any_monitors():
            return True
    except (AttributeError, TypeError):
        pass
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return AppIndicator is None or any(d in desktop for d in ("cinnamon", "mate", "xfce"))

ICONS = {
    "ok": "odm-tray-ok-symbolic",
    "syncing": "odm-tray-sync-symbolic",
    "paused": "odm-tray-paused-symbolic",
    "error": "odm-tray-error-symbolic",
    "warning": "odm-tray-warning-symbolic",
    "offline": "odm-tray-offline-symbolic",
    "setup": "odm-tray-setup-symbolic",
}


def _cinnamon_theme_settings():
    src = Gio.SettingsSchemaSource.get_default()
    if src is not None and src.lookup("org.cinnamon.theme", True) is not None:
        return Gio.Settings.new("org.cinnamon.theme")
    return None


class Tray:
    def __init__(self, app):
        self.app = app
        # Cinnamon zeigt symbolische Symbole rechts in der Leiste kleiner (z. B. 16 statt 24 px).
        # Dort deshalb farbige Symbole in Leistengrösse, weiss oder schwarz je nach Design.
        self._theme = _cinnamon_theme_settings()
        if self._theme is not None:
            self._theme.connect("changed::name", lambda *_: self._neu_zeichnen())
        self.menu = Gtk.Menu()
        self._last_key = None
        if _xapp_usable():
            self.icon = XApp.StatusIcon()
            self.icon.set_name("onedrive-mint")
            self.icon.set_icon_name(ICONS["setup"])
            self.icon.set_tooltip_text(marke.NAME)
            self.icon.set_secondary_menu(self.menu)
            self.icon.connect("activate", self._on_activate)
            self.kind = "xapp"
        elif AppIndicator is not None:
            self.icon = AppIndicator.Indicator.new(
                "onedrive-mint", ICONS["setup"], AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            if os.path.isdir(ICON_DIR):
                self.icon.set_icon_theme_path(ICON_DIR)
            self.icon.set_title(marke.NAME)
            self.icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.icon.set_menu(self.menu)
            self.kind = "indicator"
        else:
            self.icon = Gtk.StatusIcon.new_from_icon_name(ICONS["setup"])
            self.icon.connect("activate", lambda *_: self.app.show_activity())
            self.icon.connect("popup-menu", lambda i, b, t: self.menu.popup(
                None, None, Gtk.StatusIcon.position_menu, i, b, t))
            self.kind = "gtk"
        self.update()

    def _icon_name(self, state):
        name = ICONS.get(state, ICONS["ok"])
        try:
            wahl = self.app.settings["tray_icon"]
        except (AttributeError, KeyError, TypeError):
            wahl = "auto"
        if wahl == "symbolisch" or (wahl == "auto" and (self.kind != "xapp" or self._theme is None)):
            return name
        if wahl in ("weiss", "schwarz"):
            farbe = wahl
        else:
            farbe = "weiss" if "dark" in self._theme.get_string("name").lower() else "schwarz"
        return name.replace("-symbolic", f"-{farbe}")

    def _neu_zeichnen(self):
        self._last_key = None
        try:
            self.update()
        except Exception:  # noqa: BLE001 – Symbol beim nächsten Status neu
            pass

    def _on_activate(self, icon, button, time):
        if button == 1:
            self.app.show_activity()

    def update(self):
        state, text = self.app.engine.summary()
        paused = self.app.settings["paused"]
        rts = list(self.app.engine.runtimes.values())
        accounts = [(rt.account.id, rt.account.label) for rt in rts if not rt.account.is_library]
        orgs = sorted({os.path.dirname(rt.account.mount_path) for rt in rts
                       if rt.account.is_library})
        accounts = (accounts, tuple(orgs))
        rel = self.app.update_release
        key = (state, text, paused, tuple(accounts), rel and rel.version)
        if key == self._last_key:
            return
        self._last_key = key
        icon = self._icon_name(state)
        tooltip = marke.NAME + "\n" + text
        if self.kind == "xapp":
            self.icon.set_icon_name(icon)
            self.icon.set_tooltip_text(tooltip)
        elif self.kind == "indicator":
            self.icon.set_icon_full(icon, text)
            self.icon.set_title(tooltip)
        else:
            self.icon.set_from_icon_name(icon)
            self.icon.set_tooltip_text(tooltip)
        # Menue nur neu aufbauen, wenn sich die Struktur aendert (sonst schliesst es sich)
        struct = (paused, accounts)
        if struct != getattr(self, "_struct", None):
            self._struct = struct
            self._build_menu(text, paused, accounts)
        else:
            self._status_item.set_label(text)
        if rel and self._update_item.get_label() != self._update_label(rel):
            self._update_item.set_label(self._update_label(rel))
        self._update_item.set_visible(bool(rel))
        n = len(self.app.engine.problems())
        if (n, struct) != getattr(self, "_problems_key", None):
            self._problems_key = (n, struct)
            self._problems_item.set_label(f"Synchronisierungsprobleme anzeigen ({n})")
            self._problems_item.set_visible(bool(n))

    @staticmethod
    def _update_label(rel):
        return f"Update auf Version {rel.version} installieren …"

    def _item(self, label, cb=None, sensitive=True):
        it = Gtk.MenuItem.new_with_label(label)
        it.set_sensitive(sensitive)
        if cb:
            it.connect("activate", lambda *_: cb())
        self.menu.append(it)
        return it

    def _build_menu(self, text, paused, accounts_orgs):
        accounts, orgs = accounts_orgs
        for child in self.menu.get_children():
            self.menu.remove(child)
        app = self.app
        self._status_item = self._item(text, sensitive=False)
        self.menu.append(Gtk.SeparatorMenuItem())
        if self.kind == "indicator":
            it = self._item("Aktivitätscenter öffnen", app.show_activity)
            # Mittelklick aufs Symbol oeffnet das Aktivitaetscenter direkt
            self.icon.set_secondary_activate_target(it)
        for acc_id, label in accounts:
            self._item(f"Ordner öffnen: {label}", lambda a=acc_id: app.open_folder(a))
        for org in orgs:
            self._item(f"Ordner öffnen: {os.path.basename(org)}", lambda o=org: app.open_path(o))
        if accounts:
            for acc_id, label in accounts:
                self._item(f"Online anzeigen: {label}", lambda a=acc_id: app.open_web(a))
            self._item("Papierkorb (online) öffnen",
                       lambda: app.open_recycle_bin(accounts[0][0]) if len(accounts) == 1
                       else app.show_activity())
            self.menu.append(Gtk.SeparatorMenuItem())
            if paused:
                self._item("Synchronisierung fortsetzen", lambda: app.engine.set_paused(False))
            else:
                self._item("Synchronisierung anhalten", lambda: app.engine.set_paused(True))
            self._item("Jetzt synchronisieren", app.engine.sync_now)
        self.menu.append(Gtk.SeparatorMenuItem())
        self._problems_item = self._item("Synchronisierungsprobleme anzeigen", app.show_problems)
        self._problems_item.set_no_show_all(True)
        self._update_item = self._item("Update installieren …", app.show_update)
        self._update_item.set_no_show_all(True)
        self._item("Konto hinzufügen …", app.show_wizard)
        self._item("Teams-/SharePoint-Ordner synchronisieren …", app.show_library_dialog)
        self._item("Einstellungen", app.show_settings)
        self._item(f"{marke.NAME} beenden", app.quit_with_confirm)
        self.menu.show_all()
