"""Einrichtungsassistent (angelehnt an das Windows-Setup)."""

import json
import os
import threading

from gi.repository import GLib, Gtk, Pango

from . import config, graph, rclone, states
from . import marke

CSS_TITLE = "<span size='x-large' weight='bold'>{}</span>"


def _page(title, text=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_border_width(24)
    t = Gtk.Label(xalign=0)
    t.set_markup(CSS_TITLE.format(GLib.markup_escape_text(title)))
    box.pack_start(t, False, False, 0)
    if text:
        lab = Gtk.Label(label=text, xalign=0)
        lab.set_line_wrap(True)
        lab.set_max_width_chars(70)
        box.pack_start(lab, False, False, 0)
    return box


def default_folder(kind, org, existing):
    home = os.path.expanduser("~")
    used = {a.mount_path for a in existing}
    names = ["OneDrive"] if kind == "personal" else []
    names.append("OneDrive - Persönlich" if kind == "personal" else f"OneDrive - {org or 'Geschäftlich'}")
    for n in names:
        p = os.path.join(home, n)
        if p in used:
            continue
        if not os.path.exists(p) or (os.path.isdir(p) and not os.listdir(p)):
            return p
    base, i = os.path.join(home, names[-1]), 2
    while os.path.exists(f"{base} ({i})") or f"{base} ({i})" in used:
        i += 1
    return f"{base} ({i})"


class SetupWizard(Gtk.Assistant):
    def __init__(self, app):
        super().__init__(title=f"{marke.NAME} einrichten")
        self.app = app
        self.set_default_size(680, 520)
        self.set_icon_name("onedrive-mint")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.binary = rclone.find_rclone(app.settings)
        self.token = None
        self.drive = None
        self.auth = None
        self.account = None

        self._build_intro()
        self._build_kind()
        self._build_signin()
        self._build_folder()
        self._build_ondemand()
        self._build_confirm()
        self._build_done()

        self.connect("cancel", self._on_cancel)
        self.connect("close", lambda *_: self.destroy())
        self.connect("prepare", self._on_prepare)
        self.connect("apply", self._on_apply)
        self.connect("destroy", lambda *_: self.auth and self.auth.cancel())

    # ------------------------------------------------------------ Seiten

    def _build_intro(self):
        box = _page(f"Willkommen bei {marke.NAME}",
                    "Speichern Sie Ihre Dateien in Microsoft OneDrive, um von überall darauf zuzugreifen. "
                    "Dieser Assistent verbindet ein persönliches oder ein Geschäfts-/Schulkonto "
                    "mit Ihrem Computer.\n\nDie Dateien erscheinen in einem Ordner in Ihrem "
                    "Persönlichen Ordner. Dank „Dateien bei Bedarf“ belegen sie erst Speicherplatz, "
                    "wenn Sie sie öffnen.")
        img = Gtk.Image.new_from_icon_name("onedrive-mint", Gtk.IconSize.DIALOG)
        img.set_pixel_size(128)
        box.pack_start(img, True, True, 0)
        complete = True
        if not self.binary:
            complete = False
            warn = Gtk.Label(xalign=0)
            warn.set_line_wrap(True)
            warn.set_markup("<b>rclone wurde nicht gefunden.</b>\nBitte zuerst installieren, z. B. mit\n"
                            "<tt>sudo apt install rclone fuse3</tt>\noder die aktuelle Version von "
                            "rclone.org (empfohlen, mindestens v1.63).")
            box.pack_start(warn, False, False, 0)
        else:
            ver = rclone.rclone_version(self.binary)
            if ver and ver[:2] < rclone.MIN_VERSION:
                warn = Gtk.Label(xalign=0)
                warn.set_line_wrap(True)
                warn.set_markup(f"<b>Hinweis:</b> rclone {'.'.join(map(str, ver))} ist alt. "
                                "Empfohlen ist mindestens v1.63 (Upload-Warteschlange, "
                                "Pausieren).")
                box.pack_start(warn, False, False, 0)
        self.append_page(box)
        self.set_page_type(box, Gtk.AssistantPageType.INTRO)
        self.set_page_title(box, "Willkommen")
        self.set_page_complete(box, complete)

    def _build_kind(self):
        box = _page("Welches Konto möchten Sie hinzufügen?")
        self.rb_personal = Gtk.RadioButton.new_with_label(None, "Persönliches Konto")
        self.rb_business = Gtk.RadioButton.new_with_label_from_widget(
            self.rb_personal, "Geschäfts-, Schul- oder Unikonto (Microsoft 365)")
        for rb, sub in ((self.rb_personal, "outlook.com, hotmail.com, live.com oder Microsoft-Konto"),
                        (self.rb_business, "Konto Ihrer Organisation, z. B. name@firma.ch")):
            box.pack_start(rb, False, False, 0)
            s = Gtk.Label(label=sub, xalign=0)
            s.set_margin_start(28)
            s.get_style_context().add_class("dim-label")
            box.pack_start(s, False, False, 0)

        exp = Gtk.Expander(label="Erweitert (eigene App-Registrierung)")
        grid = Gtk.Grid(row_spacing=6, column_spacing=8, margin_top=8)
        self.e_client_id = Gtk.Entry(hexpand=True, placeholder_text="leer = rclone-Standard")
        self.e_client_secret = Gtk.Entry(hexpand=True, visibility=False)
        self.e_tenant = Gtk.Entry(hexpand=True, placeholder_text="nur für Single-Tenant-Apps")
        # Vorgabe des Herausgebers (eigene App-Registrierung in branding.json)
        self.e_client_id.set_text(marke.CLIENT_ID)
        self.e_client_secret.set_text(marke.CLIENT_SECRET)
        self.e_tenant.set_text(marke.TENANT)
        for i, (lab, w) in enumerate((("Client-ID", self.e_client_id),
                                      ("Client-Secret", self.e_client_secret),
                                      ("Tenant-ID", self.e_tenant))):
            grid.attach(Gtk.Label(label=lab, xalign=1), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
        hint = Gtk.Label(xalign=0, label="Nur nötig, wenn Ihre Organisation Drittanbieter-Apps "
                                         "sperrt und Ihre IT eine eigene App bereitstellt.")
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("dim-label")
        grid.attach(hint, 0, 3, 2, 1)
        exp.add(grid)
        box.pack_start(exp, False, False, 12)
        self.append_page(box)
        self.set_page_title(box, "Konto")
        self.set_page_complete(box, True)
        self.page_kind = box

    def _build_signin(self):
        box = _page("Anmelden",
                    "Klicken Sie auf „Bei Microsoft anmelden“. Ihr Browser öffnet die "
                    "Microsoft-Anmeldeseite. Nach der Anmeldung kehren Sie hierher zurück.\n\n"
                    "Ihr Passwort wird nur von Microsoft verarbeitet; dieses Programm speichert "
                    "lediglich das Zugriffstoken.")
        hb = Gtk.Box(spacing=12)
        self.btn_signin = Gtk.Button(label="Bei Microsoft anmelden")
        self.btn_signin.get_style_context().add_class("suggested-action")
        self.btn_signin.connect("clicked", self._on_signin)
        self.spinner = Gtk.Spinner()
        hb.pack_start(self.btn_signin, False, False, 0)
        hb.pack_start(self.spinner, False, False, 0)
        box.pack_start(hb, False, False, 12)
        self.signin_status = Gtk.Label(xalign=0)
        self.signin_status.set_line_wrap(True)
        self.signin_status.set_selectable(True)
        box.pack_start(self.signin_status, False, False, 0)
        # Firmen, die Benutzerzustimmung fuer Apps sperren: Link fuer Entra-Admin erzeugen
        self.consent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12)
        clab = Gtk.Label(xalign=0)
        clab.set_line_wrap(True)
        clab.set_max_width_chars(70)
        clab.set_markup("Meldet Microsoft <i>„Genehmigung durch Administrator erforderlich“</i>? "
                        "Dann muss Ihre IT die App einmalig in Microsoft Entra ID freigeben.")
        self.consent_box.pack_start(clab, False, False, 0)
        cbtn = Gtk.Button(label="Link für Administrator erstellen …")
        cbtn.set_halign(Gtk.Align.START)
        cbtn.connect("clicked", self._on_admin_consent)
        self.consent_box.pack_start(cbtn, False, False, 0)
        box.pack_start(self.consent_box, False, False, 0)
        self.append_page(box)
        self.set_page_title(box, "Anmelden")
        self.page_signin = box

    def _build_folder(self):
        box = _page("Ihr OneDrive-Ordner",
                    "Ihre Dateien werden in diesem Ordner angezeigt. Der Ordner muss leer sein "
                    "oder darf noch nicht existieren.")
        hb = Gtk.Box(spacing=8)
        self.e_folder = Gtk.Entry(hexpand=True)
        self.e_folder.connect("changed", lambda *_: self._validate_folder())
        btn = Gtk.Button(label="Speicherort ändern …")
        btn.connect("clicked", self._choose_folder)
        hb.pack_start(self.e_folder, True, True, 0)
        hb.pack_start(btn, False, False, 0)
        box.pack_start(hb, False, False, 0)
        self.folder_msg = Gtk.Label(xalign=0)
        self.folder_msg.set_line_wrap(True)
        box.pack_start(self.folder_msg, False, False, 0)
        self.cb_bookmark = Gtk.CheckButton(label="In der Seitenleiste des Dateimanagers anzeigen")
        self.cb_bookmark.set_active(True)
        box.pack_start(self.cb_bookmark, False, False, 8)
        self.append_page(box)
        self.set_page_title(box, "Ordner")
        self.page_folder = box

    def _build_ondemand(self):
        box = _page("Alle Ihre Dateien, bereit und bei Bedarf verfügbar",
                    "Dateien bei Bedarf hilft Ihnen, auf alle Ihre Dateien in OneDrive "
                    "zuzugreifen, ohne den gesamten Speicherplatz zu belegen.")
        grid = Gtk.Grid(row_spacing=14, column_spacing=14, margin_top=8)
        rows = (
            (states.CLOUD, "Nur online verfügbar",
             "Belegen keinen Speicherplatz. Werden beim Öffnen automatisch heruntergeladen. "
             "Nur mit Internetverbindung nutzbar."),
            (states.LOCAL, "Auf diesem Gerät verfügbar",
             "Wurden geöffnet und liegen lokal vor. Per Rechtsklick → „Speicher freigeben“ "
             "wieder nur online."),
            (states.PINNED, "Immer auf diesem Gerät verfügbar",
             "Rechtsklick → „Immer auf diesem Gerät behalten“: wird vollständig heruntergeladen "
             "und bleibt auch offline verfügbar."),
        )
        for i, (st, title, text) in enumerate(rows):
            img = Gtk.Image.new_from_icon_name("emblem-" + states.EMBLEMS[st], Gtk.IconSize.DND)
            img.set_pixel_size(32)
            grid.attach(img, 0, i, 1, 1)
            lab = Gtk.Label(xalign=0)
            lab.set_line_wrap(True)
            lab.set_max_width_chars(60)
            lab.set_markup(f"<b>{title}</b>\n{GLib.markup_escape_text(text)}")
            grid.attach(lab, 1, i, 1, 1)
        box.pack_start(grid, False, False, 0)
        self.cb_pin_all = Gtk.CheckButton(
            label="Alle Dateien immer auf diesem Gerät behalten (benötigt viel Speicherplatz)")
        box.pack_start(self.cb_pin_all, False, False, 12)
        nemo = Gtk.Label(xalign=0)
        nemo.set_line_wrap(True)
        nemo.get_style_context().add_class("dim-label")
        nemo.set_text("Tipp: In Nemo/Nautilus können Sie in der Listenansicht über "
                      "Rechtsklick auf die Spaltenköpfe die Spalte „OneDrive-Status“ einblenden.")
        box.pack_start(nemo, False, False, 0)
        self.append_page(box)
        self.set_page_title(box, "Dateien bei Bedarf")
        self.set_page_complete(box, True)

    def _build_confirm(self):
        box = _page("Bereit zur Einrichtung")
        self.confirm_label = Gtk.Label(xalign=0)
        self.confirm_label.set_line_wrap(True)
        box.pack_start(self.confirm_label, False, False, 0)
        self.append_page(box)
        self.set_page_type(box, Gtk.AssistantPageType.CONFIRM)
        self.set_page_title(box, "Bestätigen")
        self.set_page_complete(box, True)
        self.page_confirm = box

    def _build_done(self):
        box = _page("Ihr OneDrive ist bereit",
                    f"{marke.NAME} läuft jetzt im Hintergrund. Das Symbol in der Leiste zeigt den "
                    "Synchronisierungsstatus. Ein Klick darauf öffnet das Aktivitätscenter, ein "
                    "Rechtsklick das Menü.")
        btn = Gtk.Button(label="Meinen OneDrive-Ordner öffnen")
        btn.set_halign(Gtk.Align.START)
        btn.get_style_context().add_class("suggested-action")
        btn.connect("clicked", lambda *_: self.account and self.app.open_folder(self.account.id))
        box.pack_start(btn, False, False, 12)
        self.append_page(box)
        self.set_page_type(box, Gtk.AssistantPageType.SUMMARY)
        self.set_page_title(box, "Fertig")
        self.set_page_complete(box, True)

    # ------------------------------------------------------------ Logik

    @property
    def kind(self):
        return "business" if self.rb_business.get_active() else "personal"

    def _on_prepare(self, _a, page):
        if page is self.page_signin and self.drive is None:
            self.set_page_complete(page, False)
            if self.kind == "business":
                self.consent_box.show_all()
            else:
                self.consent_box.hide()
        elif page is self.page_folder:
            if not self.e_folder.get_text():
                org = graph.org_from_web_url(self.drive.get("webUrl", "")) if self.drive else ""
                self.e_folder.set_text(default_folder(self.kind, org, config.load_accounts()))
            self._validate_folder()
        elif page is self.page_confirm:
            owner = (self.drive or {}).get("owner", {}).get("user", {})
            txt = (f"<b>Konto:</b> {GLib.markup_escape_text(owner.get('displayName', '?'))}"
                   f" ({'Geschäftlich' if self.kind == 'business' else 'Persönlich'})\n"
                   f"<b>Ordner:</b> {GLib.markup_escape_text(self.e_folder.get_text())}\n"
                   f"<b>Dateien bei Bedarf:</b> "
                   f"{'alle Dateien immer behalten' if self.cb_pin_all.get_active() else 'aktiv'}")
            self.confirm_label.set_markup(txt)

    def _on_signin(self, _btn):
        if not self.binary:
            return
        self.btn_signin.set_sensitive(False)
        self.spinner.start()
        self.signin_status.set_text("Browser wurde geöffnet – bitte dort das "
                                    + ("Geschäfts- oder Schulkonto" if self.kind == "business"
                                       else "persönliche Microsoft-Konto")
                                    + " auswählen bzw. „Anderes Konto verwenden“ wählen …")
        self.auth = rclone.AuthorizeProcess(self.binary, self.e_client_id.get_text().strip(),
                                            self.e_client_secret.get_text().strip(),
                                            self.e_tenant.get_text().strip(), kind=self.kind)

        def work():
            token, err = self.auth.run()
            drive = None
            if token:
                try:
                    drive = graph.my_drive(json.loads(token)["access_token"])
                except (graph.GraphError, ValueError, KeyError) as e:
                    err = f"Anmeldung erfolgreich, aber OneDrive nicht erreichbar: {e}"
            GLib.idle_add(self._signin_done, token, drive, err)

        threading.Thread(target=work, daemon=True).start()

    def _signin_done(self, token, drive, err):
        self.spinner.stop()
        self.btn_signin.set_sensitive(True)
        if err or not drive:
            from .consent import needs_admin_consent
            err = err or ""
            if "access_denied" in err:
                msg = ("Microsoft hat den Zugriff verweigert (access_denied).\n\n"
                       "Das passiert, wenn die Anmeldung abgebrochen wurde – oder wenn Ihre "
                       "Organisation verlangt, dass ein Administrator die App zuerst freigibt "
                       "(Meldung „Genehmigung erforderlich“ im Browser).")
            else:
                lines = [l for l in err.splitlines()
                         if l.strip() and "rclone help backends" not in l and l.strip() != "Help:"]
                msg = "\n".join(lines)
            if needs_admin_consent(err) or "access_denied" in err:
                msg += ("\n\n→ Erstellen Sie unten einen Link für Ihre IT-Administration."
                        if self.kind == "business" else "")
                if self.kind == "business":
                    self.consent_box.show_all()
            self.signin_status.set_markup(
                f"<b>Anmeldung fehlgeschlagen.</b>\n{GLib.markup_escape_text(msg)}")
            return False
        dtype = drive.get("driveType", "")
        if (dtype == "personal") != (self.kind == "personal"):
            self.signin_status.set_markup(
                "<b>Kontotyp passt nicht.</b>\nSie haben sich mit einem "
                f"{'persönlichen' if dtype == 'personal' else 'Geschäfts'}konto angemeldet. "
                "Bitte zurückgehen und den Kontotyp anpassen oder erneut anmelden.")
            return False
        if any(a.drive_id == drive.get("id") for a in config.load_accounts()):
            self.signin_status.set_markup("<b>Dieses Konto ist bereits eingerichtet.</b>")
            return False
        self.token, self.drive = token, drive
        owner = drive.get("owner", {}).get("user", {})
        quota = drive.get("quota", {})
        from .engine import human_size
        self.signin_status.set_markup(
            f"✔ Angemeldet als <b>{GLib.markup_escape_text(owner.get('displayName', ''))}</b>"
            f"{' (' + GLib.markup_escape_text(owner['email']) + ')' if owner.get('email') else ''}"
            f"\nSpeicher: {human_size(quota.get('used'))} von {human_size(quota.get('total'))} "
            "verwendet")
        self.set_page_complete(self.page_signin, True)
        return False

    def _on_admin_consent(self, _btn):
        from .consent import AdminConsentDialog
        # eine evtl. haengende Anmeldung (Browser zeigt Genehmigungsseite) abbrechen
        if self.auth:
            self.auth.cancel()
        AdminConsentDialog(self, self.e_tenant.get_text().strip(),
                           self.e_client_id.get_text().strip())

    def _choose_folder(self, _btn):
        dlg = Gtk.FileChooserDialog(title="Übergeordneten Ordner wählen", transient_for=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Auswählen", Gtk.ResponseType.OK)
        dlg.set_current_folder(os.path.dirname(self.e_folder.get_text()) or os.path.expanduser("~"))
        if dlg.run() == Gtk.ResponseType.OK:
            name = os.path.basename(self.e_folder.get_text()) or "OneDrive"
            self.e_folder.set_text(os.path.join(dlg.get_filename(), name))
        dlg.destroy()

    def _validate_folder(self):
        p = os.path.expanduser(self.e_folder.get_text().strip())
        ok, msg = True, ""
        if not p or not os.path.isabs(p):
            ok, msg = False, "Bitte einen absoluten Pfad angeben."
        elif any(a.mount_path == p for a in config.load_accounts()):
            ok, msg = False, "Dieser Ordner wird bereits von einem anderen Konto verwendet."
        elif os.path.exists(p) and (not os.path.isdir(p) or os.listdir(p)):
            ok, msg = False, "Der Ordner existiert bereits und ist nicht leer."
        elif not os.access(os.path.dirname(p) if not os.path.exists(p) else p, os.W_OK):
            ok, msg = False, "Keine Schreibrechte an diesem Ort."
        self.folder_msg.set_markup(f"<span foreground='#c01c28'>{msg}</span>" if msg else "")
        self.set_page_complete(self.page_folder, ok)

    def _on_apply(self, _a):
        drive = self.drive
        owner = drive.get("owner", {}).get("user", {})
        org = graph.org_from_web_url(drive.get("webUrl", "")) if self.kind == "business" else ""
        existing = config.load_accounts()
        acc_id = config.new_account_id(self.kind, org or owner.get("displayName", ""), existing)
        mount = os.path.expanduser(self.e_folder.get_text().strip())
        rclone.write_remote_config(acc_id, self.token, drive["id"], drive.get("driveType", ""),
                                   self.e_client_id.get_text().strip(),
                                   self.e_client_secret.get_text().strip(),
                                   self.e_tenant.get_text().strip())
        acc = config.Account(id=acc_id, kind=self.kind, display_name=owner.get("displayName", ""),
                             email=owner.get("email", ""), org=_org_label(org),
                             mount_path=mount, drive_id=drive["id"],
                             drive_type=drive.get("driveType", ""),
                             web_url=drive.get("webUrl", ""))
        os.makedirs(mount, exist_ok=True)
        if self.cb_pin_all.get_active():
            states.PinStore(acc_id).set("", states.PIN)
        if self.cb_bookmark.get_active():
            self.app.add_bookmark(mount, acc.label)
        self.app.set_folder_icon(mount)
        self.account = acc
        self.app.engine.add_account(acc)
        self.app.on_accounts_changed()

    def _on_cancel(self, *_):
        if self.auth:
            self.auth.cancel()
        self.destroy()


def _org_label(org):
    return org[:1].upper() + org[1:] if org else ""


class ReauthDialog:
    """Erneute Anmeldung fuer ein bestehendes Konto (z. B. Token abgelaufen)."""

    def __init__(self, app, account, parent=None, app_reg=None):
        """app_reg: (client_id, client_secret, tenant) – mit einer anderen App-Registrierung
        anmelden und sie bei Erfolg fuer das Konto und seine Teams-/SharePoint-Ordner uebernehmen."""
        self.app, self.account = app, account
        self.app_reg = app_reg
        self.dlg = Gtk.MessageDialog(transient_for=parent, modal=True,
                                     message_type=Gtk.MessageType.INFO,
                                     buttons=Gtk.ButtonsType.CANCEL,
                                     text=f"Erneut anmelden: {account.label}")
        self.dlg.format_secondary_text("Ihr Browser wurde geöffnet. Bitte melden Sie sich mit "
                                       f"dem Konto {account.display_name or ''} an.")
        binary = rclone.find_rclone(app.settings)
        cid, secret, tenant = app_reg or (rclone.read_remote_option(account.id, "client_id"),
                                          rclone.read_remote_option(account.id, "client_secret"),
                                          rclone.read_remote_option(account.id, "tenant"))
        self.auth = rclone.AuthorizeProcess(
            binary, cid, secret, tenant,
            kind=account.kind if account.kind != "library" else "business",
            login_hint=account.email)
        self.dlg.connect("response", lambda *_: (self.auth.cancel(), self.dlg.destroy()))
        self.dlg.show_all()
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        token, err = self.auth.run()
        GLib.idle_add(self._done, token, err)

    def _done(self, token, err):
        if self.auth.cancelled:
            return False
        self.dlg.destroy()
        if token:
            try:
                drive = graph.my_drive(json.loads(token)["access_token"])
                if drive.get("id") != self.account.drive_id:
                    raise graph.GraphError("Sie haben sich mit einem anderen Konto angemeldet.")
            except (graph.GraphError, ValueError, KeyError) as e:
                self.app.error_dialog("Anmeldung fehlgeschlagen", str(e))
                return False
            if self.app_reg:
                rclone.set_app(self.account.id, *self.app_reg)
            rclone.update_token(self.account.id, token)
            self.app.engine.restart_account(self.account.id)
            # Teams-/SharePoint-Ordner nutzen dieselbe Anmeldung
            for rt in list(self.app.engine.runtimes.values()):
                if rt.account.parent_id == self.account.id:
                    if self.app_reg:
                        rclone.set_app(rt.account.id, *self.app_reg)
                    rclone.update_token(rt.account.id, token)
                    self.app.engine.restart_account(rt.account.id)
            self.app.notify(self.account.label, "Erneut angemeldet.")
        else:
            from .consent import needs_admin_consent, AdminConsentDialog, domain_of
            if needs_admin_consent(err):
                cid, _s, tenant = self.app_reg or (
                    rclone.read_remote_option(self.account.id, "client_id"), "",
                    rclone.read_remote_option(self.account.id, "tenant"))
                AdminConsentDialog(None, tenant or domain_of(self.account.email), cid)
            else:
                self.app.error_dialog("Anmeldung fehlgeschlagen", err or "")
        return False
