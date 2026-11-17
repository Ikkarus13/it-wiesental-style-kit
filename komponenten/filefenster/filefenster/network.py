"""Netzwerk wie im Windows-Explorer: Netzlaufwerke (SMB) verbinden, merken,
trennen. Einhängen über gvfs (Gio.File.mount_enclosing_volume) mit dem
GTK-Anmeldedialog (Benutzer, Domäne, Passwort, „Passwort merken“).

    connect_dialog(parent_window, host)   „Netzlaufwerk verbinden“
    mount(gfile, parent, callback)        callback(ok, fehlertext)
    saved_drives() / save_drive(uri) / forget_drive(uri) / is_saved(uri)
    drive_label(uri)                      „freigabe (\\\\server)“ wie Windows
    disconnect(host, uri, mount, on_changed)
"""
from gi.repository import Gdk, Gio, GLib, Gtk

from . import places
from .config import get_config


# ----------------------------------------------------------------------
# Gemerkte Netzlaufwerke („Verbindung bei Anmeldung wiederherstellen“)
# ----------------------------------------------------------------------
def _norm(uri):
    """Zum Vergleichen: SMB unterscheidet Gross/klein nicht (gvfs schreibt
    Freigaben klein: smb://nas/Daten → smb://nas/daten/)."""
    return ((uri or "").rstrip("/") + "/").lower()


def saved_drives():
    return [d for d in (get_config().get("network_drives") or [])
            if isinstance(d, dict) and d.get("uri")]


def is_saved(uri):
    return any(_norm(d["uri"]) == _norm(uri) for d in saved_drives())


def save_drive(uri):
    uri = places.without_password(uri)      # kein Passwort in settings.json
    if not is_saved(uri):
        get_config().set("network_drives", saved_drives() + [{"uri": uri.rstrip("/") + "/"}])


def forget_drive(uri):
    get_config().set("network_drives", [d for d in saved_drives() if _norm(d["uri"]) != _norm(uri)])


def drive_label(uri):
    """Windows: „freigabe (\\\\server)“."""
    _sch, host, parts = places.remote_parts(uri)
    if parts:
        return f"{parts[-1]} (\\\\{host})"
    return host or uri


# ----------------------------------------------------------------------
# Einhängen / Trennen
# ----------------------------------------------------------------------
def mount(gfile, parent, callback):
    def done(f, res):
        try:
            f.mount_enclosing_volume_finish(res)
        except GLib.Error as exc:
            if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                callback(True, None)
            elif exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                callback(False, None)            # Anmeldung abgebrochen
            else:
                callback(False, exc.message)
            return
        callback(True, None)
    gfile.mount_enclosing_volume(Gio.MountMountFlags.NONE, Gtk.MountOperation.new(parent),
                                 None, done)


def disconnect(host, uri, mount_obj=None, on_changed=None):
    """„Trennen“: aushängen und nicht mehr merken."""
    forget_drive(uri)
    if mount_obj is None:
        try:
            mount_obj = Gio.File.new_for_uri(uri).find_enclosing_mount(None)
        except GLib.Error:
            mount_obj = None
    if mount_obj is None:
        if on_changed:
            on_changed()
        return

    def done(m, res):
        try:
            m.unmount_with_operation_finish(res)
        except GLib.Error:
            pass
        if on_changed:
            on_changed()
    win = getattr(host, "win", host)
    mount_obj.unmount_with_operation(Gio.MountUnmountFlags.NONE,
                                     Gtk.MountOperation.new(win if isinstance(win, Gtk.Window) else None),
                                     None, done)


# ----------------------------------------------------------------------
# Dialog „Netzlaufwerk verbinden“
# ----------------------------------------------------------------------
class NetworkDriveDialog(Gtk.Window):
    def __init__(self, parent, host):
        super().__init__(title="Netzlaufwerk verbinden")
        self.host = host
        for c in ("filefenster", "win-dialog", "netdrive-dialog"):
            self.get_style_context().add_class(c)
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
            self.set_modal(True)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_skip_taskbar_hint(True)
        from .propsdialog import _fluent_button
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(24)
        head = Gtk.Label(xalign=0)
        head.set_markup("<span size='large'>Welcher Netzwerkordner soll zugeordnet werden?</span>")
        box.pack_start(head, False, False, 4)
        box.pack_start(Gtk.Label(label="Geben Sie den Ordner an, zu dem eine Verbindung "
                                       "hergestellt werden soll:", xalign=0), False, False, 0)
        row = Gtk.Box(spacing=8)
        row.pack_start(Gtk.Label(label="Ordner:", xalign=0), False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_width_chars(44)
        self.entry.set_placeholder_text("\\\\server\\freigabe")
        self.entry.connect("activate", lambda *_: self._go())
        row.pack_start(self.entry, True, True, 0)
        box.pack_start(row, False, False, 4)
        ex = Gtk.Label(xalign=0)
        ex.set_markup("<small>Beispiel: \\\\server\\freigabe</small>")
        ex.set_margin_start(58)
        box.pack_start(ex, False, False, 0)
        self.reconnect = Gtk.CheckButton(label="Verbindung bei Anmeldung wiederherstellen")
        self.reconnect.set_active(True)
        box.pack_start(self.reconnect, False, False, 6)
        self.error = Gtk.Label(xalign=0)
        self.error.set_line_wrap(True)
        self.error.set_max_width_chars(60)
        self.error.get_style_context().add_class("error-text")
        self.error.set_no_show_all(True)
        box.pack_start(self.error, False, False, 0)
        area.pack_start(box, True, True, 0)
        footer = Gtk.Box()
        footer.get_style_context().add_class("win-footer")
        btns = Gtk.Box(spacing=8)
        btns.set_halign(Gtk.Align.END)
        for side in ("top", "bottom", "start", "end"):
            getattr(btns, f"set_margin_{side}")(16 if side in ("top", "bottom") else 24)
        self.ok = _fluent_button("Fertig stellen", accent=True)
        self.ok.connect("clicked", lambda *_: self._go())
        cancel = _fluent_button("Abbrechen")
        cancel.connect("clicked", lambda *_: self.destroy())
        btns.pack_start(self.ok, False, False, 0)
        btns.pack_start(cancel, False, False, 0)
        footer.pack_start(btns, True, True, 0)
        area.pack_end(footer, False, False, 0)
        self.add(area)
        self.connect("key-press-event", self._on_key)
        self.show_all()
        self.entry.grab_focus()

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    def _fail(self, text):
        self.error.set_text(text)
        self.error.show()
        self.ok.set_sensitive(True)
        self.entry.set_sensitive(True)

    def _go(self):
        text = self.entry.get_text().strip()
        uri = places.resolve_input(text) if text else None
        if not uri or not places.is_remote(uri) or uri.startswith("network:") \
                or not places.remote_parts(uri)[1]:
            self._fail("Geben Sie einen Netzwerkpfad wie \\\\server\\freigabe ein.")
            return
        self.error.hide()
        self.ok.set_sensitive(False)
        self.entry.set_sensitive(False)
        keep = self.reconnect.get_active()

        def done(ok, err):
            if not ok:
                if err:
                    self._fail(f"Die Verbindung mit {text} konnte nicht hergestellt werden.\n{err}")
                else:
                    self._fail("Die Anmeldung wurde abgebrochen.")
                return
            if keep:
                save_drive(uri)
            host = self.host
            self.destroy()
            if host is not None:
                host.open_in_new_window(uri)     # Windows öffnet das neue Laufwerk
        mount(Gio.File.new_for_uri(uri), self, done)


def connect_dialog(parent, host):
    return NetworkDriveDialog(parent, host)
