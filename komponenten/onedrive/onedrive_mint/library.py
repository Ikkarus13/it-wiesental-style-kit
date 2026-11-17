"""Teams-/SharePoint-Bibliotheken synchronisieren (wie „Synchronisieren“ unter Windows)."""

import os
import threading
import urllib.parse

from gi.repository import GLib, Gtk, Pango

from . import config, graph, states


def parse_odopen(uri):
    """odopen://sync/?siteId=...&listId=... -> (aktion, {param: wert})"""
    parsed = urllib.parse.urlparse(uri)
    action = (parsed.netloc or parsed.path.strip("/").split("/")[0] or "").lower()
    params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
    return action, params


def business_accounts(engine):
    return [rt for rt in engine.runtimes.values() if rt.account.kind == "business"]


def pick_account(engine, host="", email=""):
    """Passendes Geschaeftskonto zu einer Bibliothek finden (E-Mail, sonst Tenant)."""
    accs = business_accounts(engine)
    if email:
        for rt in accs:
            if rt.account.email and rt.account.email.lower() == email.lower():
                return rt
    tenant = graph.tenant_from_host(host)
    if tenant:
        for rt in accs:
            acc_host = urllib.parse.urlparse(rt.account.web_url).hostname or ""
            if graph.tenant_from_host(acc_host) == tenant:
                return rt
    return accs[0] if len(accs) == 1 else None


def target_folder(org, name, accounts):
    base = os.path.join(config.org_dir(org), name.replace("/", "-"))
    used = {a.mount_path for a in accounts}
    path, i = base, 2
    while path in used or (os.path.exists(path) and (not os.path.isdir(path) or os.listdir(path))):
        path, i = f"{base} ({i})", i + 1
    return path


class SyncLibraryDialog(Gtk.Dialog):
    def __init__(self, app, url="", odopen=None):
        super().__init__(title="Teams-/SharePoint-Ordner synchronisieren", modal=False)
        self.app = app
        self.info = None
        self.odopen = odopen or {}
        self.set_default_size(620, -1)
        self.set_icon_name("onedrive-mint")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(bool(odopen))
        self.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
        self.btn_sync = self.add_button("Synchronisieren", Gtk.ResponseType.OK)
        self.btn_sync.get_style_context().add_class("suggested-action")
        self.btn_sync.set_sensitive(False)
        self.connect("response", self._on_response)

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(18)
        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='large' weight='bold'>Ordner aus Teams oder SharePoint "
                         "synchronisieren</span>")
        box.pack_start(title, False, False, 0)

        accs = business_accounts(app.engine)
        if not accs:
            lab = Gtk.Label(xalign=0, label="Dafür wird ein Geschäfts- oder Schulkonto benötigt. "
                                            "Bitte zuerst ein solches Konto hinzufügen.")
            lab.set_line_wrap(True)
            box.pack_start(lab, False, False, 0)
            add = Gtk.Button(label="Geschäftskonto hinzufügen …")
            add.set_halign(Gtk.Align.START)
            add.connect("clicked", lambda *_: (self.destroy(), app.show_wizard()))
            box.pack_start(add, False, False, 0)
            self.show_all()
            return

        hint = Gtk.Label(xalign=0)
        hint.set_line_wrap(True)
        hint.set_max_width_chars(72)
        hint.set_markup(
            "Fügen Sie einen Link zum Ordner ein:\n"
            "• <b>Teams:</b> Kanal → <i>Dateien</i> → beim Ordner <i>…</i> → <i>Link kopieren</i>\n"
            "• <b>SharePoint:</b> Ordner im Browser öffnen und die Adresse kopieren\n"
            "Oder in SharePoint/Teams im Browser direkt auf <i>Synchronisieren</i> klicken.")
        box.pack_start(hint, False, False, 0)
        bm = Gtk.LinkButton.new_with_label("", "Lesezeichen „In OneDrive synchronisieren“ "
                                               "für den Browser einrichten …")
        bm.set_halign(Gtk.Align.START)
        bm.connect("activate-link", lambda *_: (BookmarkletDialog(self), True)[1])
        box.pack_start(bm, False, False, 0)

        grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        grid.attach(Gtk.Label(label="Konto", xalign=1), 0, 0, 1, 1)
        self.combo = Gtk.ComboBoxText(hexpand=True)
        for rt in accs:
            self.combo.append(rt.account.id, f"{rt.account.label} ({rt.account.display_name})")
        self.combo.set_active(0)
        grid.attach(self.combo, 1, 0, 2, 1)
        grid.attach(Gtk.Label(label="Link", xalign=1), 0, 1, 1, 1)
        self.entry = Gtk.Entry(hexpand=True, text=url,
                               placeholder_text="https://firma.sharepoint.com/…")
        self.entry.connect("activate", lambda *_: self._resolve())
        self.entry.connect("changed", lambda *_: self._reset())
        grid.attach(self.entry, 1, 1, 1, 1)
        self.btn_check = Gtk.Button(label="Prüfen")
        self.btn_check.connect("clicked", lambda *_: self._resolve())
        grid.attach(self.btn_check, 2, 1, 1, 1)
        box.pack_start(grid, False, False, 4)

        res = Gtk.Box(spacing=12)
        self.spinner = Gtk.Spinner()
        self.res_icon = Gtk.Image.new_from_icon_name("odm-org", Gtk.IconSize.DIALOG)
        self.res_icon.set_no_show_all(True)
        self.res_label = Gtk.Label(xalign=0)
        self.res_label.set_line_wrap(True)
        self.res_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.res_label.set_selectable(True)
        res.pack_start(self.spinner, False, False, 0)
        res.pack_start(self.res_icon, False, False, 0)
        res.pack_start(self.res_label, True, True, 0)
        box.pack_start(res, False, False, 4)

        self.cb_pin = Gtk.CheckButton(label="Alle Dateien immer auf diesem Gerät behalten")
        box.pack_start(self.cb_pin, False, False, 0)
        self.show_all()

        if odopen:
            self.entry.set_text(odopen.get("folderUrl") or odopen.get("webUrl", ""))
            host = urllib.parse.urlparse(odopen.get("webUrl", "")).hostname or ""
            rt = pick_account(app.engine, host, odopen.get("userEmail", ""))
            if rt:
                self.combo.set_active_id(rt.account.id)
            self._resolve()
        elif url:
            self._resolve()

    # ------------------------------------------------------------------

    def _reset(self):
        if not getattr(self, "_resolving", False):
            self.info = None
            self.btn_sync.set_sensitive(False)

    def _resolve(self):
        acc_id = self.combo.get_active_id()
        url = self.entry.get_text().strip()
        if not acc_id or not (url or self.odopen):
            return
        self.info = None
        self.btn_sync.set_sensitive(False)
        self.btn_check.set_sensitive(False)
        self.res_icon.hide()
        self.spinner.start()
        self.res_label.set_text("Ordner wird gesucht …")
        odopen = self.odopen if self.odopen and url in (self.odopen.get("folderUrl"),
                                                        self.odopen.get("webUrl")) else None

        def work():
            try:
                token = self.app.engine.token_for(acc_id)
                if not token:
                    raise graph.GraphError("Konto ist nicht verbunden oder die Anmeldung ist abgelaufen.")
                info = graph.resolve_odopen(token, odopen) if odopen else graph.resolve_link(token, url)
                GLib.idle_add(self._resolved, info, None)
            except graph.GraphError as e:
                GLib.idle_add(self._resolved, None, str(e))
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self._resolved, None, f"Unerwarteter Fehler: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _resolved(self, info, err):
        self.spinner.stop()
        self.btn_check.set_sensitive(True)
        if err:
            self.res_label.set_markup(f"<b>Nicht gefunden.</b>\n{GLib.markup_escape_text(err)}")
            return False
        rt_parent = self.app.engine.runtimes[self.combo.get_active_id()]
        existing = self.app.engine.find_library(info.drive_id, info.rel_path)
        if existing:
            self.res_label.set_markup(
                f"<b>{GLib.markup_escape_text(info.breadcrumb)}</b>\nWird bereits synchronisiert: "
                f"{GLib.markup_escape_text(existing.account.mount_path)}")
            return False
        self.info = info
        self.target = target_folder(rt_parent.account.org or graph.tenant_from_host(info.host),
                                    info.display_name, config.load_accounts())
        self.res_icon.show()
        self.res_label.set_markup(
            f"<b>{GLib.markup_escape_text(info.breadcrumb)}</b>\n"
            f"Wird synchronisiert nach: {GLib.markup_escape_text(self.target)}")
        self.btn_sync.set_sensitive(True)
        self.btn_sync.grab_focus()
        return False

    def _on_response(self, _d, resp):
        if resp != Gtk.ResponseType.OK or not self.info:
            self.destroy()
            return
        parent_id = self.combo.get_active_id()
        rt = self.app.engine.add_library(parent_id, self.info, self.target)
        if self.cb_pin.get_active():
            states.PinStore(rt.account.id).set("", states.PIN)
        org_path = os.path.dirname(self.target)
        org_name = os.path.basename(org_path)
        self.app.add_bookmark(org_path, org_name)
        self.app.set_folder_icon(org_path, "odm-org")
        self.app.notify("Synchronisierung eingerichtet",
                        f"„{rt.account.label}“ wird jetzt in {org_name} synchronisiert.")
        self.app.on_accounts_changed()
        GLib.timeout_add_seconds(3, lambda: self.app.open_folder(rt.account.id) and False)
        self.destroy()


# Lesezeichen-Skript: baut aus der geoeffneten SharePoint-Bibliothek denselben odopen://-Link,
# den die Windows-Schaltflaeche „Synchronisieren“ erzeugt.
BOOKMARKLET = (
    "javascript:(function(){var c=window._spPageContextInfo;"
    "if(!c){alert('Bitte in einer SharePoint-Dokumentbibliothek ausf\\u00fchren "
    "(in Teams: Dateien \\u2192 In SharePoint \\u00f6ffnen).');return;}"
    "var q=new URLSearchParams(location.search),f=q.get('id')||q.get('RootFolder'),"
    "e=encodeURIComponent;"
    "location.href='odopen://sync/?siteId='+e(c.siteId||'')+'&webId='+e(c.webId||'')"
    "+'&listId='+e(c.listId||'')+'&webUrl='+e(c.webAbsoluteUrl)+'&webTitle='+e(c.webTitle||'')"
    "+'&listTitle='+e(c.listTitle||'')+'&userEmail='+e(c.userEmail||c.userPrincipalName||'')"
    "+'&folderUrl='+e(f?location.origin+f:location.origin+location.pathname);})();"
)


class BookmarkletDialog(Gtk.Dialog):
    def __init__(self, parent=None):
        super().__init__(title="Lesezeichen „In OneDrive synchronisieren“", transient_for=parent,
                         modal=True)
        self.add_button("Schließen", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(18)
        lab = Gtk.Label(xalign=0)
        lab.set_line_wrap(True)
        lab.set_max_width_chars(70)
        lab.set_markup(
            "Damit funktioniert „Synchronisieren“ in jedem Browser, auch wenn SharePoint die "
            "Schaltfläche unter Linux ausblendet:\n\n"
            "1. Auf <b>Kopieren</b> klicken.\n"
            "2. Im Browser ein neues Lesezeichen anlegen (z. B. Rechtsklick auf die "
            "Lesezeichenleiste → <i>Lesezeichen hinzufügen</i>), Name: <i>In OneDrive "
            "synchronisieren</i>, als Adresse den kopierten Text einfügen.\n"
            "3. In SharePoint (oder Teams → <i>Dateien</i> → <i>In SharePoint öffnen</i>) den "
            "gewünschten Ordner öffnen und auf das Lesezeichen klicken.\n"
            "4. Den Browser-Dialog „odopen-Link öffnen?“ bestätigen.")
        box.pack_start(lab, False, False, 0)
        tv = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.CHAR, monospace=True)
        tv.get_buffer().set_text(BOOKMARKLET)
        sw = Gtk.ScrolledWindow(min_content_height=90)
        sw.add(tv)
        box.pack_start(sw, True, True, 0)
        btn = Gtk.Button(label="Kopieren")
        btn.set_halign(Gtk.Align.START)

        def copy(_b):
            from gi.repository import Gdk
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            cb.set_text(BOOKMARKLET, -1)
            cb.store()
            btn.set_label("Kopiert ✓")
        btn.connect("clicked", copy)
        box.pack_start(btn, False, False, 0)
        self.set_default_size(620, -1)
        self.show_all()
