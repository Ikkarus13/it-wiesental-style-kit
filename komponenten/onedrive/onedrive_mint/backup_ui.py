"""Reiter „Sicherung“ in den Einstellungen – wie „Sicherung verwalten“ unter Windows."""

import threading
import time

from gi.repository import GLib, Gtk, Pango

from .engine import human_size
from .folders import KNOWN, BackupError

DESCRIPTIONS = {
    "desktop": "Die Dateien bleiben lokal auf diesem PC und werden in beide Richtungen mit "
               "OneDrive abgeglichen. Sie funktionieren also auch, wenn OneDrive nicht läuft. "
               "Ordner auf dem Desktop werden ins OneDrive verschoben und erscheinen als "
               "Verknüpfung; ihr Inhalt ist verfügbar, solange OneDrive läuft.",
    "documents": "Der Ordner wird ins OneDrive verschoben. Es gilt „Dateien bei Bedarf“. "
                 "Ohne laufendes OneDrive ist der Ordner leer.",
    "pictures": "Der Ordner wird ins OneDrive verschoben. Es gilt „Dateien bei Bedarf“. "
                "Ohne laufendes OneDrive ist der Ordner leer.",
}


def _hint(text):
    lab = Gtk.Label(label=text, xalign=0)
    lab.set_line_wrap(True)
    lab.set_max_width_chars(72)
    lab.get_style_context().add_class("dim-label")
    return lab


class BackupPage(Gtk.Box):
    def __init__(self, app, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, border_width=16)
        self.app = app
        self.window = window
        self.backup = app.engine.backup
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>Wichtige Ordner mit OneDrive sichern</b>")
        self.pack_start(title, False, False, 0)
        self.pack_start(_hint("Wie unter Windows: Desktop, Dokumente und Bilder werden mit OneDrive "
                              "verbunden und sind auf allen Geräten verfügbar. Ordner, die es im "
                              "OneDrive schon gibt (z. B. vom Windows-PC), werden weiterverwendet."),
                        False, False, 0)

        row = Gtk.Box(spacing=10)
        row.pack_start(Gtk.Label(label="Mit diesem OneDrive verbinden:", xalign=0), False, False, 0)
        self.combo = Gtk.ComboBoxText()
        row.pack_start(self.combo, False, False, 0)
        self.pack_start(row, False, False, 4)
        self.combo_hint = _hint("")
        self.pack_start(self.combo_hint, False, False, 0)

        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pack_start(self.rows, False, False, 6)
        self._timer = GLib.timeout_add_seconds(2, self._tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self._timer))
        self.refresh()

    # ------------------------------------------------------------------

    def _tick(self):
        if self.get_mapped() and any(self.backup.busy.values()):
            self.refresh()
        return True

    def refresh(self):
        active = self.combo.get_active_id()
        self.combo.remove_all()
        for rt in self.app.engine.runtimes.values():
            if not rt.account.is_library:
                self.combo.append(rt.account.id, rt.account.label)
        chosen = self.backup.account_id() or active
        if chosen:
            self.combo.set_active_id(chosen)
        if self.combo.get_active_id() is None:
            self.combo.set_active(0)
        locked = self.backup.any_enabled()
        self.combo.set_sensitive(not locked)
        self.combo_hint.set_text("Zum Wechseln des OneDrive zuerst alle Sicherungen beenden."
                                 if locked else "")
        for c in self.rows.get_children():
            self.rows.remove(c)
        for key in KNOWN:
            self.rows.pack_start(self._row(key), False, False, 0)
        self.rows.show_all()

    def _row(self, key):
        info = self.backup.info(key)
        frame = Gtk.Frame()
        box = Gtk.Box(spacing=12, border_width=10)
        frame.add(box)
        img = Gtk.Image.new_from_icon_name(KNOWN[key]["icon"], Gtk.IconSize.DIALOG)
        box.pack_start(img, False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        t = Gtk.Label(xalign=0)
        t.set_markup(f"<b>{KNOWN[key]['label']}</b>")
        v.pack_start(t, False, False, 0)
        if info.get("busy"):
            status = info["busy"]
        elif info["enabled"]:
            status = f"Gesichert in {info['target']}"
            if key == "desktop":
                last = self.backup.last_sync
                status += ("\nZuletzt abgeglichen " +
                           time.strftime("%H:%M:%S", time.localtime(last)) if last else
                           "\nAbgleich ausstehend")
        else:
            status = f"Nicht gesichert ({info['local']})"
        s = Gtk.Label(label=status, xalign=0)
        s.set_line_wrap(True)
        s.set_ellipsize(Pango.EllipsizeMode.NONE)
        v.pack_start(s, False, False, 0)
        if info.get("error"):
            e = Gtk.Label(xalign=0)
            e.set_line_wrap(True)
            e.set_markup(f"<span foreground='#c01c28'>{GLib.markup_escape_text(info['error'])}"
                         "</span>")
            v.pack_start(e, False, False, 0)
        v.pack_start(_hint(DESCRIPTIONS[key]), False, False, 0)
        box.pack_start(v, True, True, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        if info["enabled"]:
            b = Gtk.Button(label="Sicherung beenden")
            b.connect("clicked", lambda *_: self._disable(key))
            btns.pack_start(b, False, False, 0)
            if key == "desktop":
                s2 = Gtk.Button(label="Jetzt abgleichen")
                s2.connect("clicked", lambda *_: self.backup.sync_now())
                btns.pack_start(s2, False, False, 0)
        else:
            b = Gtk.Button(label="Sichern …")
            b.get_style_context().add_class("suggested-action")
            b.connect("clicked", lambda *_: self._enable(key))
            btns.pack_start(b, False, False, 0)
        b.set_sensitive(not info.get("busy"))
        box.pack_start(btns, False, False, 0)
        return frame

    # ------------------------------------------------------------------ Einschalten

    def _enable(self, key):
        acc_id = self.combo.get_active_id()
        if not acc_id:
            return
        self.backup.busy[key] = "Größe wird ermittelt …"
        self.refresh()

        def work():
            size, free = self.backup.check_space(acc_id, key)
            GLib.idle_add(self._confirm_enable, key, acc_id, size, free)
        threading.Thread(target=work, daemon=True).start()

    def _confirm_enable(self, key, acc_id, size, free):
        self.backup.busy.pop(key, None)
        self.refresh()
        rt = self.app.engine.runtimes[acc_id]
        label = KNOWN[key]["label"]
        if free is not None and size > free:
            self._message(Gtk.MessageType.ERROR, f"Nicht genug Speicher in {rt.account.label}",
                          f"{label} belegt {human_size(size)}, im OneDrive sind nur noch "
                          f"{human_size(free)} frei.")
            return False
        if key == "desktop":
            text = (f"Der Desktop wird mit {rt.account.label} verbunden.\n\n"
                    "• Dateien auf dem Desktop bleiben lokal und werden in beide Richtungen "
                    "abgeglichen. Dateien, die schon im OneDrive-Desktop liegen (z. B. vom "
                    "Windows-PC), erscheinen auf diesem Desktop.\n"
                    "• Ordner auf dem Desktop werden ins OneDrive verschoben und durch "
                    "Verknüpfungen ersetzt.\n"
                    "• Werden beide Seiten gleichzeitig geändert, bleiben beide Fassungen "
                    "erhalten (Kopie mit Rechnernamen).\n"
                    "• Windows-Verknüpfungen (.lnk) werden ausgeblendet.")
        else:
            text = (f"{label} ({human_size(size)}) wird nach {rt.account.label} verschoben und "
                    "hochgeladen.\n\n"
                    "• Programme finden den Ordner weiterhin unter dem bisherigen Pfad.\n"
                    "• Es gilt „Dateien bei Bedarf“: Dateien belegen erst beim Öffnen wieder "
                    "Platz auf diesem PC.\n"
                    "• Ohne laufendes OneDrive ist der Ordner leer.\n"
                    "• Ausführungsrechte und symbolische Links speichert OneDrive nicht.")
        if free is not None:
            text += f"\n\nFreier Speicher im OneDrive: {human_size(free)}"
        dlg = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text=f"{label} sichern?")
        dlg.format_secondary_text(text)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Sicherung starten",
                        Gtk.ResponseType.OK)
        res = dlg.run()
        dlg.destroy()
        if res != Gtk.ResponseType.OK:
            return False
        self._run(key, lambda progress: self.backup.enable(key, acc_id, progress),
                  f"{label} wird mit OneDrive verbunden",
                  f"{label} ist jetzt mit {rt.account.label} verbunden.")
        return False

    # ------------------------------------------------------------------ Ausschalten

    def _disable(self, key):
        label = KNOWN[key]["label"]
        dlg = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.NONE,
                                text=f"Sicherung von „{label}“ beenden?")
        if key == "desktop":
            dlg.format_secondary_text(
                "Die Dateien auf dem Desktop bleiben auf diesem PC und im OneDrive, werden aber "
                "nicht mehr abgeglichen. Die Ordner-Verknüpfungen werden entfernt; die Ordner "
                "bleiben im OneDrive.")
            copy = Gtk.CheckButton(label="Inhalt der Ordner auf diesen PC kopieren")
        else:
            dlg.format_secondary_text(
                f"Neue Dateien in „{label}“ werden dann nur noch auf diesem PC gespeichert. "
                "Die bisherigen Dateien bleiben im OneDrive.")
            copy = Gtk.CheckButton(label="Dateien aus OneDrive auf diesen PC kopieren "
                                         "(lädt alles herunter)")
        dlg.get_message_area().pack_start(copy, False, False, 6)
        copy.show()
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Sicherung beenden",
                        Gtk.ResponseType.OK)
        res = dlg.run()
        copy_back = copy.get_active()
        dlg.destroy()
        if res != Gtk.ResponseType.OK:
            return
        self._run(key, lambda progress: self.backup.disable(key, copy_back, progress),
                  f"Sicherung von {label} wird beendet", f"Sicherung von {label} beendet.")

    # ------------------------------------------------------------------

    def _run(self, key, fn, busy_text, done_text):
        self.backup.busy[key] = busy_text + " …"
        self.refresh()

        def progress(name):
            self.backup.busy[key] = f"{busy_text}: {name}"

        def work():
            err = None
            try:
                fn(progress)
            except BackupError as e:
                err = str(e)
            except Exception as e:  # noqa: BLE001
                err = f"Unerwarteter Fehler: {e}"
            finally:
                self.backup.busy.pop(key, None)
            GLib.idle_add(self._done, err, done_text)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, err, done_text):
        self.refresh()
        if err:
            self._message(Gtk.MessageType.ERROR, "Das hat nicht geklappt", err)
        else:
            self.app.notify("Ordnersicherung", done_text)
        return False

    def _message(self, kind, title, text):
        dlg = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=kind,
                                buttons=Gtk.ButtonsType.OK, text=title)
        dlg.format_secondary_text(text)
        dlg.run()
        dlg.destroy()
