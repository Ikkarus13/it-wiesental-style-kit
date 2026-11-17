"""Admin-Consent-Link fuer Microsoft Entra ID.

Viele Firmen erlauben Benutzern nicht, Drittanbieter-Apps selbst zuzustimmen
("Genehmigung durch Administrator erforderlich", AADSTS65001/AADSTS90094).
Ein Entra-Administrator kann die App dann ueber diesen Link einmalig fuer die
ganze Organisation freigeben (Unternehmensanwendungen → Berechtigungen).
"""

import subprocess
import urllib.parse

from gi.repository import Gdk, GLib, Gtk
from . import marke

# Standard-App von rclone fuer OneDrive (multi-tenant) und ihre Weiterleitungsadresse
RCLONE_CLIENT_ID = "b15665d9-eda6-4092-8539-0eec376afd59"
REDIRECT_URI = "http://localhost:53682/"
SCOPES = ("Files.Read", "Files.ReadWrite", "Files.Read.All", "Files.ReadWrite.All",
          "Sites.Read.All")

SCOPE_TEXT = {
    "Files.Read": "Benutzerdateien lesen",
    "Files.ReadWrite": "Benutzerdateien lesen und schreiben",
    "Files.Read.All": "Alle Dateien lesen, auf die der Benutzer Zugriff hat",
    "Files.ReadWrite.All": "Alle Dateien bearbeiten, auf die der Benutzer Zugriff hat",
    "Sites.Read.All": "Elemente in allen Websitesammlungen lesen (Teams/SharePoint)",
    "offline_access": "Zugriff beibehalten (angemeldet bleiben)",
}

CONSENT_ERRORS = ("AADSTS65001", "AADSTS90094", "AADSTS90095", "AADSTS900941",
                  "admin_consent", "Administratorgenehmigung", "admin approval",
                  "Genehmigung erforderlich", "Need admin approval")


def needs_admin_consent(text):
    t = (text or "").lower()
    return any(e.lower() in t for e in CONSENT_ERRORS)


def admin_consent_url(tenant="", client_id=""):
    """Link, den ein Entra-Administrator oeffnet, um die App fuer die Organisation freizugeben."""
    tenant = (tenant or "").strip() or "organizations"
    scope = " ".join("https://graph.microsoft.com/" + s for s in SCOPES) + " offline_access"
    query = urllib.parse.urlencode({
        "client_id": (client_id or "").strip() or marke.CLIENT_ID or RCLONE_CLIENT_ID,
        "scope": scope,
        "redirect_uri": REDIRECT_URI,
        "state": "onedrive-mint",
    }, quote_via=urllib.parse.quote)
    return f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/v2.0/adminconsent?{query}"


def domain_of(email):
    return email.split("@", 1)[1].strip() if email and "@" in email else ""


class AdminConsentDialog(Gtk.Dialog):
    def __init__(self, parent=None, tenant="", client_id=""):
        super().__init__(title="Freigabe durch Administrator", transient_for=parent, modal=True)
        self.set_icon_name("onedrive-mint")
        self.set_default_size(640, -1)
        self.add_button("Schließen", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_border_width(18)
        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='large' weight='bold'>Link für Ihre IT-Administration "
                         "erstellen</span>")
        box.pack_start(title, False, False, 0)
        intro = Gtk.Label(xalign=0)
        intro.set_line_wrap(True)
        intro.set_max_width_chars(74)
        intro.set_text("Ihre Organisation erlaubt nicht, dass Benutzer dieser App selbst "
                       "zustimmen. Ein Administrator von Microsoft Entra ID (globaler "
                       "Administrator, Anwendungs- oder Cloudanwendungsadministrator) kann die "
                       "App mit dem folgenden Link einmalig für alle Benutzer freigeben. Danach "
                       "können Sie sich normal anmelden.")
        box.pack_start(intro, False, False, 0)

        grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        grid.attach(Gtk.Label(label="Firmendomäne", xalign=1), 0, 0, 1, 1)
        self.e_tenant = Gtk.Entry(hexpand=True, text=tenant,
                                  placeholder_text="z. B. firma.ch oder Tenant-ID (leer = beliebig)")
        grid.attach(self.e_tenant, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="App (Client-ID)", xalign=1), 0, 1, 1, 1)
        self.e_client = Gtk.Entry(hexpand=True, text=client_id,
                                  placeholder_text=f"{RCLONE_CLIENT_ID} (rclone)")
        grid.attach(self.e_client, 1, 1, 1, 1)
        box.pack_start(grid, False, False, 0)

        self.link = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.CHAR, monospace=True)
        sw = Gtk.ScrolledWindow(min_content_height=80)
        sw.add(self.link)
        box.pack_start(sw, True, True, 0)

        btns = Gtk.Box(spacing=6)
        self.btn_copy = Gtk.Button(label="Link kopieren")
        self.btn_copy.connect("clicked", self._copy)
        mail = Gtk.Button(label="Per E-Mail an IT senden …")
        mail.connect("clicked", self._mail)
        open_b = Gtk.Button(label="Selbst öffnen (ich bin Administrator)")
        open_b.connect("clicked", lambda *_: Gtk.show_uri_on_window(self, self.url,
                                                                     Gdk.CURRENT_TIME))
        for b in (self.btn_copy, mail, open_b):
            btns.pack_start(b, False, False, 0)
        box.pack_start(btns, False, False, 0)

        perms = Gtk.Expander(label="Welche Berechtigungen werden freigegeben?")
        plab = Gtk.Label(xalign=0)
        plab.set_line_wrap(True)
        plab.set_markup("Delegierte Microsoft-Graph-Berechtigungen – die App handelt immer nur "
                        "im Namen des angemeldeten Benutzers und sieht nur, was dieser sehen darf:\n"
                        + "\n".join(f"• <tt>{s}</tt> – {GLib.markup_escape_text(SCOPE_TEXT[s])}"
                                    for s in SCOPES + ("offline_access",)))
        plab.set_margin_top(6)
        perms.add(plab)
        box.pack_start(perms, False, False, 0)
        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_max_width_chars(74)
        note.get_style_context().add_class("dim-label")
        note.set_text("Hinweis für Administratoren: Nach der Zustimmung leitet Microsoft auf "
                      "„localhost:53682“ weiter. Die Fehlermeldung „Seite nicht erreichbar“ ist "
                      "dort normal – die Freigabe ist dann bereits erteilt (sichtbar unter Entra ID "
                      "→ Unternehmensanwendungen → „rclone“ → Berechtigungen).")
        box.pack_start(note, False, False, 0)

        self.e_tenant.connect("changed", lambda *_: self._update())
        self.e_client.connect("changed", lambda *_: self._update())
        self._update()
        self.show_all()

    def _update(self):
        self.url = admin_consent_url(self.e_tenant.get_text(), self.e_client.get_text())
        self.link.get_buffer().set_text(self.url)
        self.btn_copy.set_label("Link kopieren")

    def _copy(self, _b):
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.set_text(self.url, -1)
        cb.store()
        self.btn_copy.set_label("Kopiert ✓")

    def _mail(self, _b):
        subject = f"Bitte App-Freigabe für {marke.NAME} (Microsoft OneDrive unter Linux) erteilen"
        body = ("Hallo\n\nich möchte OneDrive und Teams-Dateien unter Linux synchronisieren. "
                f"Das Programm {marke.NAME} braucht dafür eine "
                "Administratorgenehmigung.\n\nBitte als Entra-Administrator diesen Link öffnen "
                "und zustimmen (delegierte Berechtigungen: Dateien lesen/schreiben, "
                "SharePoint-Websites lesen, angemeldet bleiben):\n\n"
                f"{self.url}\n\n"
                "Nach der Zustimmung erscheint eine Seite „localhost nicht erreichbar“ – das "
                "ist normal, die Freigabe ist dann erteilt.\n\nVielen Dank!")
        mailto = "mailto:?" + urllib.parse.urlencode({"subject": subject, "body": body},
                                                       quote_via=urllib.parse.quote)
        try:
            Gtk.show_uri_on_window(self, mailto, Gdk.CURRENT_TIME)
        except GLib.Error:
            subprocess.Popen(["xdg-open", mailto])
