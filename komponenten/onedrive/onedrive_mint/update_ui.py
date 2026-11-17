"""Dialog „Update verfügbar“: installieren, herunterladen oder überspringen."""

import os
import threading

from gi.repository import GLib, Gtk

from . import config, updater
from .engine import human_size
from . import marke


def download_dir():
    return (GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            or os.path.expanduser("~"))


class UpdateDialog(Gtk.Dialog):
    def __init__(self, app, rel):
        super().__init__(title=f"{marke.NAME} – Update", modal=False)
        self.app = app
        self.rel = rel
        self.busy = False
        self.set_default_size(560, 420)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("onedrive-mint")
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)

        head = Gtk.Box(spacing=12)
        img = Gtk.Image.new_from_icon_name("onedrive-mint", Gtk.IconSize.DIALOG)
        head.pack_start(img, False, False, 0)
        t = Gtk.Label(xalign=0)
        size = f" · {human_size(rel.size)}" if rel.size else ""
        t.set_markup(f"<big><b>Version {GLib.markup_escape_text(rel.version)} ist verfügbar</b>"
                     f"</big>\nInstalliert ist Version {config.VERSION}{size}")
        head.pack_start(t, True, True, 0)
        box.pack_start(head, False, False, 0)

        lab = Gtk.Label(xalign=0)
        lab.set_markup("<b>Neuerungen</b>")
        box.pack_start(lab, False, False, 4)
        sw = Gtk.ScrolledWindow()
        sw.set_shadow_type(Gtk.ShadowType.IN)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tv = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD,
                          left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        tv.get_buffer().set_text(rel.notes or "Keine Beschreibung vorhanden.")
        sw.add(tv)
        box.pack_start(sw, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)
        self.bar = Gtk.ProgressBar()
        self.bar.set_no_show_all(True)
        box.pack_start(self.bar, False, False, 0)

        area = self.get_action_area()
        self.b_page = Gtk.Button(label="Release-Seite")
        self.b_page.connect("clicked", lambda *_: app.open_uri(rel.page))
        self.b_download = Gtk.Button(label="Herunterladen …")
        self.b_download.set_tooltip_text("Paket speichern und später selbst mit install.sh "
                                         "installieren")
        self.b_download.connect("clicked", self._download)
        self.b_skip = Gtk.Button(label="Überspringen")
        self.b_skip.set_tooltip_text("Nicht mehr an diese Version erinnern")
        self.b_skip.connect("clicked", self._skip)
        self.b_later = Gtk.Button(label="Später")
        self.b_later.connect("clicked", lambda *_: self.destroy())
        self.b_install = Gtk.Button(label="Jetzt installieren")
        self.b_install.get_style_context().add_class("suggested-action")
        self.b_install.connect("clicked", self._install)
        for b in (self.b_page, self.b_download, self.b_skip, self.b_later, self.b_install):
            area.pack_start(b, False, False, 0)
        self.connect("delete-event", lambda *_: self.busy)
        self.show_all()

    def _set_busy(self, busy, text=""):
        self.busy = busy
        for b in (self.b_download, self.b_skip, self.b_later, self.b_install):
            b.set_sensitive(not busy)
        self.status.set_text(text)
        self.bar.set_visible(busy)
        self.bar.set_fraction(0)

    def _progress(self, done, total):
        def upd():
            if total:
                self.bar.set_fraction(min(done / total, 1))
                self.bar.set_text(f"{human_size(done)} von {human_size(total)}")
            else:
                self.bar.pulse()
                self.bar.set_text(human_size(done))
            self.bar.set_show_text(True)
            return False
        GLib.idle_add(upd)

    def _skip(self, _b):
        self.app.settings.data["update_skip"] = self.rel.version
        self.app.settings.save()
        self.app.set_update(None)
        self.destroy()

    # ------------------------------------------------------------------ Installieren

    def _install(self, _b):
        pending = self.app.engine.unsynced_count()
        text = f"{marke.NAME} wird dafür kurz beendet und startet danach von selbst neu."
        if pending:
            text += (f"\n\n{pending} Datei(en) sind noch nicht hochgeladen. Sie bleiben erhalten "
                     "und werden nach dem Neustart hochgeladen.")
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text=f"Version {self.rel.version} installieren?")
        dlg.format_secondary_text(text)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Installieren", Gtk.ResponseType.OK)
        res = dlg.run()
        dlg.destroy()
        if res != Gtk.ResponseType.OK:
            return
        self._set_busy(True, "Update wird heruntergeladen …")
        dest_dir = os.path.join(config.CACHE_DIR, "updates")

        def work():
            try:
                os.makedirs(dest_dir, exist_ok=True)
                archive = updater.download(self.rel, os.path.join(dest_dir, self.rel.filename),
                                           self._progress)
                ext = updater.install(
                    archive, lambda msg: GLib.idle_add(self.status.set_text, msg))
                try:
                    os.unlink(archive)
                except OSError:
                    pass
                GLib.idle_add(self._installed, ext)
            except updater.UpdateError as e:
                GLib.idle_add(self._failed, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _installed(self, extension_changed):
        self.status.set_text(f"Installiert. {marke.NAME} startet neu …")
        self.app.restart_after_update(self.rel.version, extension_changed)
        return False

    def _failed(self, msg):
        self._set_busy(False)
        self.status.set_markup(f"<span foreground='#c01c28'>{GLib.markup_escape_text(msg)}</span>"
                               f"\nDetails: {updater.UPDATE_LOG}")
        return False

    # ------------------------------------------------------------------ Herunterladen

    def _download(self, _b):
        fc = Gtk.FileChooserDialog(title="Update speichern", transient_for=self,
                                   action=Gtk.FileChooserAction.SAVE)
        fc.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Speichern", Gtk.ResponseType.OK)
        fc.set_do_overwrite_confirmation(True)
        fc.set_current_folder(download_dir())
        fc.set_current_name(self.rel.filename)
        res = fc.run()
        dest = fc.get_filename()
        fc.destroy()
        if res != Gtk.ResponseType.OK or not dest:
            return
        self._set_busy(True, "Wird heruntergeladen …")

        def work():
            try:
                updater.download(self.rel, dest, self._progress)
                GLib.idle_add(self._downloaded, dest)
            except updater.UpdateError as e:
                GLib.idle_add(self._failed, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _downloaded(self, dest):
        self._set_busy(False)
        name = os.path.basename(dest)
        self.status.set_text(f"Gespeichert: {dest}\nZum Installieren entpacken und im Ordner "
                             "./install.sh ausführen (tar xzf " + name + ").")
        self.app.reveal(dest)
        return False
