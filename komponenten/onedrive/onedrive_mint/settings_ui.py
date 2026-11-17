"""Einstellungsfenster (Konten, Synchronisierung, Netzwerk, Info)."""

import os
import subprocess
import threading

from gi.repository import GLib, Gtk

from . import config, rclone
from .engine import human_size
from . import marke

SENSE_CHOICES = [(0, "Nie"), (1, "1 Tag"), (14, "14 Tage"), (30, "30 Tage"), (60, "60 Tage")]


def _section(title):
    lab = Gtk.Label(xalign=0, margin_top=12)
    lab.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
    return lab


def _hint(text):
    lab = Gtk.Label(label=text, xalign=0)
    lab.set_line_wrap(True)
    lab.set_max_width_chars(70)
    lab.get_style_context().add_class("dim-label")
    return lab


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title=f"{marke.NAME} – Einstellungen")
        self.app = app
        self.set_default_size(640, 520)
        self.set_icon_name("onedrive-mint")
        self.set_position(Gtk.WindowPosition.CENTER)
        nb = Gtk.Notebook()
        nb.set_border_width(8)
        self.add(nb)
        self.accounts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                                    border_width=12)
        nb.append_page(self._scroll(self.accounts_box), Gtk.Label(label="Konto"))
        nb.append_page(self._scroll(self._sync_page()), Gtk.Label(label="Synchronisierung"))
        from .backup_ui import BackupPage
        self.backup_page = BackupPage(app, self)
        nb.append_page(self._scroll(self.backup_page), Gtk.Label(label="Sicherung"))
        nb.append_page(self._scroll(self._network_page()), Gtk.Label(label="Netzwerk"))
        nb.append_page(self._scroll(self._advanced_page()), Gtk.Label(label="Erweitert"))
        nb.append_page(self._scroll(self._about_page()), Gtk.Label(label="Info"))
        self.refresh_accounts()
        self.connect("delete-event", lambda *_: self.hide() or True)

    @staticmethod
    def _scroll(child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(child)
        return sw

    # ------------------------------------------------------------ Konten

    _STATUS = {"ok": "Verbunden", "syncing": "Synchronisiert …", "starting": "Startet …",
               "error": "Fehler", "offline": "Offline", "stopped": "Gestoppt"}

    def _status_text(self, rt):
        return self._STATUS.get(rt.status, rt.status) + (f": {rt.error}" if rt.error else "")

    def _buttons(self, entries):
        btns = Gtk.Box(spacing=6, margin_top=6)
        for label, cb in entries:
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            btns.pack_start(b, False, False, 0)
        return btns

    def _signature(self):
        return tuple((rt.account.id, rt.status, rt.error, rt.quota,
                      rclone.read_remote_option(rt.account.id, "client_id"))
                     for rt in self.app.engine.runtimes.values())

    def refresh_if_changed(self):
        """Bei Statuswechseln (z. B. nach „Neu anmelden“) neu aufbauen – aber nur dann,
        sonst wuerde die Seite waehrend der Synchronisierung staendig flackern."""
        if self._signature() != getattr(self, "_shown", None):
            self.refresh_accounts()

    def refresh_accounts(self):
        self._shown = self._signature()
        if hasattr(self, "backup_page"):
            self.backup_page.refresh()
        box = self.accounts_box
        for c in box.get_children():
            box.remove(c)
        rts = list(self.app.engine.runtimes.values())
        if not rts:
            box.pack_start(_hint("Noch kein Konto eingerichtet."), False, False, 0)
        for rt in rts:
            acc = rt.account
            if acc.is_library:
                continue
            frame = Gtk.Frame()
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, border_width=10)
            frame.add(inner)
            title = Gtk.Label(xalign=0)
            title.set_markup(f"<b>{GLib.markup_escape_text(acc.label)}</b>")
            inner.pack_start(title, False, False, 0)
            who = acc.display_name + (f" ({acc.email})" if acc.email else "")
            inner.pack_start(_hint(who), False, False, 0)
            inner.pack_start(_hint(f"Ordner: {acc.mount_path}"), False, False, 0)
            if rt.quota and rt.quota[1]:
                inner.pack_start(_hint(f"{human_size(rt.quota[0])} von {human_size(rt.quota[1])} "
                                       "Cloudspeicher verwendet"), False, False, 0)
            inner.pack_start(_hint("Status: " + self._status_text(rt)), False, False, 0)
            inner.pack_start(self._buttons((
                ("Ordner öffnen", lambda _b, a=acc: self.app.open_folder(a.id)),
                ("Online anzeigen", lambda _b, a=acc: self.app.open_web(a.id)),
                ("Neu anmelden", lambda _b, a=acc: self.app.reauth(a, self)),
                ("Neu verbinden", lambda _b, a=acc: self.app.engine.restart_account(a.id)),
                ("Verknüpfung aufheben", lambda _b, a=acc: self._unlink(a)))), False, False, 0)
            cid = rclone.read_remote_option(acc.id, "client_id")
            if marke.EIGENE_APP and cid == marke.CLIENT_ID:
                app_text = f"{marke.NAME} ({marke.FIRMA or 'Herausgeber'})"
            elif cid:
                app_text = f"eigene App-Registrierung ({cid})"
            else:
                app_text = ("rclone-Standard-App (von EDR-Lösungen wie Huntress "
                            "als verdächtig gemeldet)")
            if acc.kind == "business" or marke.EIGENE_APP:
                inner.pack_start(_hint("Anmelde-App: " + app_text), False, False, 0)
            knoepfe = []
            if marke.EIGENE_APP and cid != marke.CLIENT_ID:
                # Konto meldet noch über rclone/andere App an → einmal neu anmelden
                knoepfe.append((f"Auf {marke.NAME}-Anmeldung umstellen …",
                                lambda _b, a=acc: self._publisher_app(a)))
            if acc.kind == "business":
                knoepfe.append(("App-Registrierung ändern …", lambda _b, a=acc: self._app_reg(a)))
            if knoepfe:
                inner.pack_start(self._buttons(knoepfe), False, False, 0)

            if acc.kind == "business":
                libs = [r for r in rts if r.account.parent_id == acc.id]
                head = Gtk.Label(xalign=0, margin_top=10)
                head.set_markup("<b>Synchronisierte Teams-/SharePoint-Ordner</b>")
                inner.pack_start(head, False, False, 0)
                if not libs:
                    inner.pack_start(_hint("Keine."), False, False, 0)
                for lrt in libs:
                    la = lrt.account
                    lbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=12,
                                   margin_top=6)
                    t = Gtk.Label(xalign=0)
                    t.set_markup(f"{GLib.markup_escape_text(la.label)}")
                    lbox.pack_start(t, False, False, 0)
                    crumbs = " › ".join([la.site_name, la.library_name] +
                                        ([la.remote_path] if la.remote_path else []))
                    lbox.pack_start(_hint(f"{crumbs}\n{la.mount_path}\nStatus: "
                                          f"{self._status_text(lrt)}"), False, False, 0)
                    lbox.pack_start(self._buttons((
                        ("Ordner öffnen", lambda _b, a=la: self.app.open_folder(a.id)),
                        ("Online anzeigen", lambda _b, a=la: self.app.open_web(a.id)),
                        ("Synchronisierung beenden", lambda _b, a=la: self._unlink(a)))),
                        False, False, 0)
                    inner.pack_start(lbox, False, False, 0)
                addlib = Gtk.Button(label="Teams-/SharePoint-Ordner synchronisieren …")
                addlib.set_halign(Gtk.Align.START)
                addlib.set_margin_top(6)
                addlib.connect("clicked", lambda *_: self.app.show_library_dialog())
                inner.pack_start(addlib, False, False, 0)
            box.pack_start(frame, False, False, 0)
        add = Gtk.Button(label="Konto hinzufügen")
        add.set_halign(Gtk.Align.START)
        add.get_style_context().add_class("suggested-action")
        add.connect("clicked", lambda *_: self.app.show_wizard())
        box.pack_start(add, False, False, 8)
        box.show_all()

    def _unlink(self, acc):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.NONE,
                                text=(f"Synchronisierung von „{acc.label}“ beenden?"
                                      if acc.is_library else
                                      f"Verknüpfung von „{acc.label}“ aufheben?"))
        pending = sum(len(rt.queue) for rt in self.app.engine.runtimes.values()
                      if rt.account.id == acc.id)
        msg = ("Die Synchronisierung wird beendet. Ihre Dateien bleiben online "
               "erhalten; lokal zwischengespeicherte Kopien werden entfernt.")
        if not acc.is_library and any(r.account.parent_id == acc.id
                                      for r in self.app.engine.runtimes.values()):
            msg += "\n\nAuch alle synchronisierten Teams-/SharePoint-Ordner dieses Kontos " \
                   "werden entfernt."
        if pending:
            msg += f"\n\nAchtung: {pending} Datei(en) wurden noch nicht hochgeladen und gehen " \
                   "verloren!"
        dlg.format_secondary_text(msg)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                        "Synchronisierung beenden" if acc.is_library else "Verknüpfung aufheben",
                        Gtk.ResponseType.OK)
        if dlg.run() == Gtk.ResponseType.OK:
            dlg.destroy()

            def work():
                children = [r.account for r in self.app.engine.runtimes.values()
                            if r.account.parent_id == acc.id]
                self.app.engine.remove_account(acc.id)
                GLib.idle_add(self.app.remove_bookmark, acc.mount_path)
                for a in children + [acc]:
                    if a.is_library:
                        org = os.path.dirname(a.mount_path)
                        if not any(r.account.is_library and r.account.mount_path.startswith(org + "/")
                                   for r in self.app.engine.runtimes.values()):
                            GLib.idle_add(self.app.remove_bookmark, org)
                GLib.idle_add(self.app.on_accounts_changed)
            threading.Thread(target=work, daemon=True).start()
        else:
            dlg.destroy()

    # ------------------------------------------------------------ Synchronisierung

    def _switch_row(self, box, label, key, on_change=None, hint=None):
        row = Gtk.Box(spacing=12)
        lab = Gtk.Label(label=label, xalign=0)
        lab.set_line_wrap(True)
        sw = Gtk.Switch(active=bool(self.app.settings[key]), valign=Gtk.Align.CENTER)

        def changed(s, _p):
            self.app.settings[key] = s.get_active()
            if on_change:
                on_change(s.get_active())
        sw.connect("notify::active", changed)
        row.pack_start(lab, True, True, 0)
        row.pack_start(sw, False, False, 0)
        box.pack_start(row, False, False, 0)
        if hint:
            box.pack_start(_hint(hint), False, False, 0)

    def _sync_page(self):
        s = self.app.settings
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=16)
        box.pack_start(_section("Allgemein"), False, False, 0)
        self._switch_row(box, f"{marke.NAME} beim Anmelden automatisch starten", "autostart",
                         self.app.set_autostart)
        self._switch_row(box, "Benachrichtigungen anzeigen", "notifications")
        self._switch_row(box, "Gelöschte Dateien in den OneDrive-Papierkorb verschieben",
                         "redirect_trash",
                         hint="„In den Papierkorb verschieben“ in Nemo/Nautilus löscht die Datei in "
                              "OneDrive. Sie liegt dann im OneDrive-Papierkorb (online) und kann "
                              "dort 30 Tage (Geschäftlich: 93 Tage) wiederhergestellt werden. "
                              "Ausgeschaltet bleibt der Linux-Papierkorb als Ordner .Trash-UID "
                              "in OneDrive liegen.")

        box.pack_start(_section("Dateien bei Bedarf"), False, False, 0)
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Label(label="Nicht verwendete Dateien nur online verfügbar machen nach",
                                 xalign=0), True, True, 0)
        combo = Gtk.ComboBoxText()
        for days, text in SENSE_CHOICES:
            combo.append(str(days), text)
        combo.set_active_id(str(s["storage_sense_days"]))
        if combo.get_active_id() is None:
            combo.set_active(0)
        combo.connect("changed", lambda c: s.__setitem__("storage_sense_days",
                                                          int(c.get_active_id())))
        row.pack_start(combo, False, False, 0)
        box.pack_start(row, False, False, 0)
        box.pack_start(_hint("Wie die Speicheroptimierung unter Windows: Dateien, die Sie so lange "
                             "nicht geöffnet haben, werden wieder „nur online“. Dateien mit "
                             "„Immer auf diesem Gerät behalten“ sind ausgenommen."), False, False, 0)
        btn = Gtk.Button(label="Jetzt Speicherplatz freigeben")
        btn.set_halign(Gtk.Align.START)
        btn.connect("clicked", self._free_now)
        box.pack_start(btn, False, False, 4)
        box.pack_start(_hint("Macht alle lokal verfügbaren Dateien (außer „Immer behalten“ und "
                             "noch nicht hochgeladene) wieder „nur online“."), False, False, 0)
        self.cache_label = _hint("")
        box.pack_start(self.cache_label, False, False, 0)
        self._update_cache_label()
        return box

    def _update_cache_label(self):
        def work():
            total = 0
            for root, _, files in os.walk(config.CACHE_DIR):
                for f in files:
                    try:
                        total += os.stat(os.path.join(root, f)).st_blocks * 512
                    except OSError:
                        pass
            GLib.idle_add(self.cache_label.set_text,
                          f"Lokal belegt: {human_size(total)} in {config.CACHE_DIR}")
        threading.Thread(target=work, daemon=True).start()

    def _free_now(self, btn):
        btn.set_sensitive(False)

        def work():
            freed = self.app.engine.free_up_all_unused()
            GLib.idle_add(btn.set_sensitive, True)
            GLib.idle_add(self.app.notify, "Speicher freigegeben",
                          f"{human_size(freed)} freigegeben.")
            self._update_cache_label()
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ Netzwerk

    def _network_page(self):
        s = self.app.settings
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=16)
        self.spins = {}
        for key, title in (("upload_limit_kbps", "Uploadrate"),
                           ("download_limit_kbps", "Downloadrate")):
            box.pack_start(_section(title), False, False, 0)
            row = Gtk.Box(spacing=12)
            rb_off = Gtk.RadioButton.new_with_label(None, "Nicht begrenzen")
            rb_on = Gtk.RadioButton.new_with_label_from_widget(rb_off, "Begrenzen auf")
            spin = Gtk.SpinButton.new_with_range(10, 1000000, 50)
            spin.set_value(s[key] or 1000)
            (rb_on if s[key] else rb_off).set_active(True)
            spin.set_sensitive(bool(s[key]))
            rb_on.connect("toggled", lambda rb, sp=spin: sp.set_sensitive(rb.get_active()))
            row.pack_start(rb_off, False, False, 0)
            row.pack_start(rb_on, False, False, 0)
            row.pack_start(spin, False, False, 0)
            row.pack_start(Gtk.Label(label="KB/s"), False, False, 0)
            box.pack_start(row, False, False, 0)
            self.spins[key] = (rb_on, spin)
        apply = Gtk.Button(label="Übernehmen (verbindet neu)")
        apply.set_halign(Gtk.Align.START)
        apply.connect("clicked", self._apply_network)
        box.pack_start(apply, False, False, 12)
        return box

    def _apply_network(self, _b):
        for key, (rb_on, spin) in self.spins.items():
            self.app.settings.data[key] = int(spin.get_value()) if rb_on.get_active() else 0
        self.app.settings.save()
        self.app.engine.restart_all()

    # ------------------------------------------------------------ Erweitert

    def _advanced_page(self):
        s = self.app.settings
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=16)
        box.pack_start(_section("Cache"), False, False, 0)
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Label(label="Maximale Cachegröße (GB, 0 = unbegrenzt)", xalign=0),
                       True, True, 0)
        spin = Gtk.SpinButton.new_with_range(0, 100000, 1)
        spin.set_value(s["cache_max_gb"])
        row.pack_start(spin, False, False, 0)
        box.pack_start(row, False, False, 0)
        box.pack_start(_hint("Harte Grenze von rclone. Wird sie überschritten, verdrängt rclone "
                             "die ältesten Dateien – auch „Immer behalten“-Dateien, die danach "
                             "neu geladen werden. Besser die Speicheroptimierung nutzen."),
                       False, False, 0)

        box.pack_start(_section("rclone"), False, False, 0)
        row = Gtk.Box(spacing=8)
        entry = Gtk.Entry(hexpand=True, text=s["rclone_path"],
                          placeholder_text=rclone.find_rclone() or "nicht gefunden")
        row.pack_start(Gtk.Label(label="Pfad"), False, False, 0)
        row.pack_start(entry, True, True, 0)
        box.pack_start(row, False, False, 0)
        binary = rclone.find_rclone(s)
        ver = rclone.rclone_version(binary) if binary else None
        box.pack_start(_hint(f"Verwendet: {binary or '–'}  "
                             f"(Version {'.'.join(map(str, ver)) if ver else 'unbekannt'})"),
                       False, False, 0)

        def apply(_b):
            s.data["cache_max_gb"] = int(spin.get_value())
            s.data["rclone_path"] = entry.get_text().strip()
            s.save()
            self.app.engine.restart_all()
        b = Gtk.Button(label="Übernehmen (verbindet neu)")
        b.set_halign(Gtk.Align.START)
        b.connect("clicked", apply)
        box.pack_start(b, False, False, 8)

        box.pack_start(_section("Microsoft Entra ID"), False, False, 0)
        box.pack_start(_hint("Wenn Ihre Organisation Benutzerzustimmung für Apps sperrt, kann ein "
                             "Entra-Administrator die App über einen Link für alle freigeben."),
                       False, False, 0)
        consent = Gtk.Button(label="Link für Administrator erstellen …")
        consent.set_halign(Gtk.Align.START)
        consent.connect("clicked", self._admin_consent)
        box.pack_start(consent, False, False, 0)

        box.pack_start(_section("Fehlersuche"), False, False, 0)
        logs = Gtk.Button(label="Protokollordner öffnen")
        logs.set_halign(Gtk.Align.START)
        logs.connect("clicked", lambda *_: subprocess.Popen(["xdg-open", config.LOG_DIR]))
        box.pack_start(logs, False, False, 0)
        return box

    def _publisher_app(self, acc):
        """Konto mit der App-Registrierung des Herausgebers neu anmelden."""
        from .wizard import ReauthDialog
        ReauthDialog(self.app, acc, self, app_reg=marke.app_reg())

    def _app_reg(self, acc):
        """Eigene Entra-App statt der rclone-Standard-App verwenden (mit neuer Anmeldung)."""
        dlg = Gtk.Dialog(title="App-Registrierung ändern", transient_for=self, modal=True)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL,
                        "Anmelden und übernehmen", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        box.pack_start(_hint(
            "Eigene App-Registrierung in Microsoft Entra ID für dieses Konto und seine Teams-/"
            "SharePoint-Ordner. Einrichtung: Plattform „Mobile und Desktop“ mit Umleitungs-URI "
            "http://localhost:53682/, „Öffentliche Clientflows zulassen“ = Ja, delegierte "
            "Graph-Berechtigungen Files.Read, Files.ReadWrite, Files.Read.All, "
            "Files.ReadWrite.All, Sites.Read.All, offline_access, User.Read, "
            "Administratorzustimmung erteilen. Ein Client-Secret ist dann nicht nötig."),
            False, False, 0)
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        fields = []
        for i, (lab, key, vis) in enumerate((("Client-ID", "client_id", True),
                                             ("Client-Secret (optional)", "client_secret", False),
                                             ("Tenant-ID", "tenant", True))):
            grid.attach(Gtk.Label(label=lab, xalign=0), 0, i, 1, 1)
            e = Gtk.Entry(hexpand=True, visibility=vis)
            e.set_text(rclone.read_remote_option(acc.id, key))
            grid.attach(e, 1, i, 1, 1)
            fields.append(e)
        box.pack_start(grid, False, False, 0)
        dlg.show_all()
        res = dlg.run()
        reg = tuple(e.get_text().strip() for e in fields)
        dlg.destroy()
        if res != Gtk.ResponseType.OK:
            return
        if not reg[0] or not reg[2]:
            self.app.error_dialog("Angaben fehlen", "Client-ID und Tenant-ID werden benötigt.",
                                  self)
            return
        from .wizard import ReauthDialog
        ReauthDialog(self.app, acc, self, app_reg=reg)

    def _admin_consent(self, _b):
        from .consent import AdminConsentDialog, domain_of
        biz = [rt.account for rt in self.app.engine.runtimes.values()
               if rt.account.kind == "business"]
        tenant = client = ""
        if biz:
            tenant = rclone.read_remote_option(biz[0].id, "tenant") or domain_of(biz[0].email)
            client = rclone.read_remote_option(biz[0].id, "client_id")
        AdminConsentDialog(self, tenant, client)

    # ------------------------------------------------------------ Info

    def _about_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=16)
        img = Gtk.Image.new_from_icon_name("onedrive-mint", Gtk.IconSize.DIALOG)
        img.set_pixel_size(96)
        box.pack_start(img, False, False, 0)
        t = Gtk.Label()
        esc = GLib.markup_escape_text
        t.set_markup(f"<big><b>{esc(marke.NAME)}</b></big>\n{esc(marke.B['zusatz'])}\n"
                     f"Version {config.VERSION}")
        t.set_justify(Gtk.Justification.CENTER)
        box.pack_start(t, False, False, 0)
        box.pack_start(_hint(marke.HINWEIS + " Synchronisiert über rclone "
                             "(mount --vfs-cache-mode full)."), False, False, 0)
        box.pack_start(_hint(marke.MARKEN), False, False, 0)
        if marke.KONTAKT or marke.WEB:
            kontakt = Gtk.Label(xalign=0)
            teile = []
            if marke.FIRMA:
                teile.append(f"<b>{esc(marke.FIRMA)}</b>")
            if marke.KONTAKT:
                teile.append(f'<a href="mailto:{esc(marke.KONTAKT)}">{esc(marke.KONTAKT)}</a>')
            if marke.WEB:
                teile.append(f'<a href="{esc(marke.WEB)}">{esc(marke.WEB)}</a>')
            kontakt.set_markup(" · ".join(teile))
            box.pack_start(kontakt, False, False, 0)

        box.pack_start(_section("Datenschutz"), False, False, 0)
        box.pack_start(_hint(marke.DATENSCHUTZ), False, False, 0)

        box.pack_start(_section("Updates"), False, False, 0)
        auto = Gtk.CheckButton(label="Automatisch nach Updates suchen (täglich)")
        auto.set_active(self.app.settings["update_check"])
        auto.connect("toggled", lambda b: self.app.settings.__setitem__("update_check",
                                                                        b.get_active()))
        box.pack_start(auto, False, False, 0)
        row = Gtk.Box(spacing=10)
        check = Gtk.Button(label="Nach Updates suchen")
        row.pack_start(check, False, False, 0)
        self.update_status = Gtk.Label(xalign=0)
        self.update_status.set_line_wrap(True)
        row.pack_start(self.update_status, True, True, 0)
        box.pack_start(row, False, False, 0)
        from . import updater
        kit = updater.kit_verwaltet()
        if kit:
            auto.set_no_show_all(True)
            auto.hide()
            box.pack_start(_hint(f"Updates kommen über das {kit}."), False, False, 0)
        elif config.UPDATE_REPO:
            box.pack_start(_hint(f"Quelle: github.com/{config.UPDATE_REPO} (Releases)"), False, False, 0)

        def done(rel, newer, err):
            check.set_sensitive(True)
            if err:
                self.update_status.set_text(err)
            elif newer:
                self.update_status.set_text(f"Version {rel.version} ist verfügbar.")
                self.app.show_update()
            elif rel:
                self.update_status.set_text(f"{marke.NAME} ist auf dem neuesten Stand.")
            else:
                self.update_status.set_text("Noch keine Version veröffentlicht.")

        def search(_b):
            check.set_sensitive(False)
            self.update_status.set_text("Suche …")
            self.app.check_updates(manual=True, done=done)
        check.connect("clicked", search)
        return box
