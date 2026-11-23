"""Freigabedialog – wie „Freigeben“ im OneDrive-Client unter Windows.

Link erstellen (jeder / Personen in der Organisation, ansehen / bearbeiten, optional Ablaufdatum
und Kennwort), bestimmte Personen einladen und bestehende Freigaben verwalten. Alle Aufrufe an
Microsoft laufen im Hintergrund; das Fenster bleibt bedienbar.
"""

import datetime
import os
import re
import threading

from gi.repository import Gdk, GLib, Gtk, Pango

from . import graph

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _hint(text):
    lab = Gtk.Label(label=text, xalign=0)
    lab.set_line_wrap(True)
    lab.set_max_width_chars(60)
    lab.get_style_context().add_class("dim-label")
    return lab


def _section(text):
    lab = Gtk.Label(xalign=0, margin_top=6)
    lab.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
    return lab


def friendly(msg, what=""):
    """Graph-Fehler in einen verstaendlichen Satz uebersetzen."""
    low = (msg or "").lower()
    if "password" in low or "expiration" in low or "premium" in low or "not supported" in low:
        return ("Kennwort oder Ablaufdatum sind für dieses Konto nicht verfügbar "
                "(bei Privatkonten nur mit Microsoft 365 Personal/Family, bei Firmen nur, wenn "
                "der Administrator es erlaubt). Bitte ohne diese Angaben erneut versuchen.")
    if "anonymous" in low or "sharing" in low and "disabled" in low or "policy" in low:
        return ("Diese Art der Freigabe ist in Ihrer Organisation nicht erlaubt. Bitte eine "
                "andere Option wählen (z. B. „Personen in der Organisation“).")
    if "accessdenied" in low or "access denied" in low or "forbidden" in low or "403" in low:
        return "Sie haben keine Berechtigung, dieses Element freizugeben."
    if "invalid" in low and "recipient" in low:
        return "Mindestens eine E-Mail-Adresse wurde nicht erkannt."
    if "itemnotfound" in low or "not found" in low or "404" in low:
        return ("Das Element wurde in OneDrive nicht gefunden – vielleicht ist es noch nicht "
                "hochgeladen. Bitte kurz warten und erneut versuchen.")
    if "name or service" in low or "timed out" in low or "network" in low or "urlopen" in low:
        return "Keine Verbindung zu Microsoft. Bitte die Internetverbindung prüfen."
    return (f"{what} hat nicht geklappt: {msg}" if what else msg) or "Unbekannter Fehler."


class ShareDialog(Gtk.Window):
    def __init__(self, app, path):
        super().__init__(title="Freigeben")
        self.app = app
        self.path = path
        self.target = None
        self.set_default_size(520, -1)
        self.set_icon_name("onedrive-mint")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.connect("key-press-event", self._on_key)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=16)
        self.add(outer)
        name = os.path.basename(path.rstrip("/")) or path
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<big><b>„{GLib.markup_escape_text(name)}“ freigeben</b></big>")
        title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        outer.pack_start(title, False, False, 0)
        self.sub = _hint("Wird geladen …")
        outer.pack_start(self.sub, False, False, 0)

        self.stack = Gtk.Notebook()
        outer.pack_start(self.stack, True, True, 4)
        self.stack.append_page(self._link_page(), Gtk.Label(label="Link"))
        self.stack.append_page(self._invite_page(), Gtk.Label(label="Personen einladen"))
        self.stack.append_page(self._manage_page(), Gtk.Label(label="Zugriff verwalten"))
        self.stack.connect("switch-page", self._on_page)

        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(60)
        self.status.set_selectable(True)
        outer.pack_start(self.status, False, False, 0)

        close = Gtk.Button(label="Schliessen")
        close.connect("clicked", lambda *_: self.destroy())
        bb = Gtk.Box()
        bb.pack_end(close, False, False, 0)
        outer.pack_start(bb, False, False, 0)
        self._set_sensitive(False)
        self.show_all()
        self._run(lambda: self.app.engine.share_target(path), self._loaded, "Laden")

    # ------------------------------------------------------------------ Hilfen

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.destroy()

    def _run(self, fn, done, what):
        """fn im Hintergrund, done(ergebnis) im Hauptthread; Fehler landen in der Statuszeile."""
        def work():
            try:
                res = fn()
            except graph.GraphError as e:
                GLib.idle_add(self._failed, str(e), what)
                return
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self._failed, str(e), what)
                return
            GLib.idle_add(done, res)
        threading.Thread(target=work, daemon=True).start()

    def _failed(self, msg, what):
        self._busy(False)
        self.status.set_markup(f"<span foreground='#c01c28'>"
                               f"{GLib.markup_escape_text(friendly(msg, what))}</span>")
        return False

    def _ok(self, text):
        self.status.set_markup(f"<span foreground='#26a269'>✓</span> "
                               f"{GLib.markup_escape_text(text)}")

    def _busy(self, busy, text=""):
        self._set_sensitive(not busy and self.target is not None)
        if busy:
            self.status.set_text(text)

    def _set_sensitive(self, on):
        for w in (self.b_link, self.b_invite, self.c_who, self.c_role, self.c_irole):
            w.set_sensitive(on)

    def _copy(self, url):
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.set_text(url, -1)
        cb.store()

    # ------------------------------------------------------------------ Laden

    def _loaded(self, target):
        self.target = target
        if target.personal:
            self.c_who.remove(1)       # „Personen in der Organisation“ gibt es nicht
        else:
            self.c_who.remove(1)
            self.c_who.insert(1, "organization",
                              f"Personen in {target.org}" if target.org
                              else "Personen in der Organisation")
            self.c_who.set_active_id("organization")
        self.sub.set_text(target.account_label)
        self._busy(False)
        self.status.set_text("")
        return False

    # ------------------------------------------------------------------ Link

    def _link_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=12)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        grid.attach(Gtk.Label(label="Wer:", xalign=0), 0, 0, 1, 1)
        self.c_who = Gtk.ComboBoxText(hexpand=True)
        self.c_who.append("anonymous", "Jeder mit dem Link")
        self.c_who.append("organization", "Personen in der Organisation")
        self.c_who.set_active_id("anonymous")
        grid.attach(self.c_who, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Recht:", xalign=0), 0, 1, 1, 1)
        self.c_role = Gtk.ComboBoxText()
        self.c_role.append("view", "Kann ansehen")
        self.c_role.append("edit", "Kann bearbeiten")
        self.c_role.set_active_id("view")
        grid.attach(self.c_role, 1, 1, 1, 1)

        self.x_expire = Gtk.CheckButton(label="Ablaufdatum:")
        grid.attach(self.x_expire, 0, 2, 1, 1)
        self.e_expire = Gtk.Entry(placeholder_text="TT.MM.JJJJ", sensitive=False)
        in7 = datetime.date.today() + datetime.timedelta(days=7)
        self.e_expire.set_text(in7.strftime("%d.%m.%Y"))
        self.x_expire.connect("toggled", lambda b: self.e_expire.set_sensitive(b.get_active()))
        grid.attach(self.e_expire, 1, 2, 1, 1)

        self.x_pw = Gtk.CheckButton(label="Kennwort:")
        grid.attach(self.x_pw, 0, 3, 1, 1)
        self.e_pw = Gtk.Entry(visibility=False, sensitive=False)
        self.x_pw.connect("toggled", lambda b: self.e_pw.set_sensitive(b.get_active()))
        grid.attach(self.e_pw, 1, 3, 1, 1)
        # Microsoft erlaubt ein Kennwort nur bei „Jeder mit dem Link“
        self.c_who.connect("changed", self._who_changed)
        box.pack_start(grid, False, False, 0)

        self.b_link = Gtk.Button(label="Link erstellen und kopieren")
        self.b_link.get_style_context().add_class("suggested-action")
        self.b_link.set_halign(Gtk.Align.START)
        self.b_link.connect("clicked", self._make_link)
        box.pack_start(self.b_link, False, False, 4)
        self.link_lbl = Gtk.Label(xalign=0, selectable=True)
        self.link_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(self.link_lbl, False, False, 0)
        return box

    def _who_changed(self, combo):
        anyone = combo.get_active_id() == "anonymous"
        if not anyone:
            self.x_pw.set_active(False)
        self.x_pw.set_sensitive(anyone)
        self.x_pw.set_tooltip_text(None if anyone else
                                   "Ein Kennwort gibt es nur bei „Jeder mit dem Link“.")

    def _expires(self):
        if not self.x_expire.get_active():
            return ""
        txt = self.e_expire.get_text().strip()
        try:
            d = datetime.datetime.strptime(txt, "%d.%m.%Y").date()
        except ValueError:
            raise ValueError("Bitte das Ablaufdatum als TT.MM.JJJJ eingeben.") from None
        if d <= datetime.date.today():
            raise ValueError("Das Ablaufdatum muss in der Zukunft liegen.")
        return d.isoformat()

    def _make_link(self, _b):
        try:
            expires = self._expires()
        except ValueError as e:
            self._failed(str(e), "")
            return
        pw = self.e_pw.get_text() if self.x_pw.get_active() else ""
        if self.x_pw.get_active() and not pw:
            self._failed("Bitte ein Kennwort eingeben oder das Häkchen entfernen.", "")
            return
        kind, scope = self.c_role.get_active_id(), self.c_who.get_active_id()
        self._busy(True, "Link wird erstellt …")
        self._run(lambda: self.target.create_link(kind, scope, pw, expires), self._link_done,
                  "Link erstellen")

    def _link_done(self, url):
        self._busy(False)
        if not url:
            self._failed("Microsoft hat keinen Link zurückgegeben.", "")
            return False
        self._copy(url)
        self.link_lbl.set_text(url)
        self._ok("Link in die Zwischenablage kopiert.")
        return False

    # ------------------------------------------------------------------ Einladen

    def _invite_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=12)
        box.pack_start(_hint("E-Mail-Adressen, durch Komma oder Leerzeichen getrennt. Nur diese "
                             "Personen können den Link öffnen (Anmeldung erforderlich)."),
                       False, False, 0)
        self.e_mails = Gtk.Entry(placeholder_text="name@firma.ch, …", hexpand=True)
        box.pack_start(self.e_mails, False, False, 0)
        row = Gtk.Box(spacing=10)
        row.pack_start(Gtk.Label(label="Recht:"), False, False, 0)
        self.c_irole = Gtk.ComboBoxText()
        self.c_irole.append("read", "Kann ansehen")
        self.c_irole.append("write", "Kann bearbeiten")
        self.c_irole.set_active_id("read")
        row.pack_start(self.c_irole, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.x_mail = Gtk.CheckButton(label="Einladung per E-Mail senden (von Microsoft)")
        self.x_mail.set_active(True)
        box.pack_start(self.x_mail, False, False, 0)
        self.e_msg = Gtk.Entry(placeholder_text="Nachricht (optional)")
        self.x_mail.connect("toggled", lambda b: self.e_msg.set_sensitive(b.get_active()))
        box.pack_start(self.e_msg, False, False, 0)
        self.b_invite = Gtk.Button(label="Einladen")
        self.b_invite.get_style_context().add_class("suggested-action")
        self.b_invite.set_halign(Gtk.Align.START)
        self.b_invite.connect("clicked", self._invite)
        box.pack_start(self.b_invite, False, False, 4)
        return box

    def _invite(self, _b):
        mails = [m for m in re.split(r"[\s,;]+", self.e_mails.get_text().strip()) if m]
        bad = [m for m in mails if not EMAIL.match(m)]
        if not mails or bad:
            self._failed("Bitte gültige E-Mail-Adressen eingeben" +
                         (f" (nicht erkannt: {', '.join(bad)})." if bad else "."), "")
            return
        role, send = self.c_irole.get_active_id(), self.x_mail.get_active()
        msg = self.e_msg.get_text().strip()
        self._busy(True, "Einladung wird verschickt …" if send else "Freigabe wird erstellt …")
        self._run(lambda: self.target.invite(mails, role, send, msg),
                  lambda url: self._invite_done(url, mails, send), "Einladen")

    def _invite_done(self, url, mails, sent):
        self._busy(False)
        who = ", ".join(mails)
        if sent:
            self._ok(f"Freigegeben für {who}. Microsoft schickt die Einladung per E-Mail.")
        elif url:
            self._copy(url)
            self._ok(f"Freigegeben für {who}. Link in die Zwischenablage kopiert.")
        else:
            self._ok(f"Freigegeben für {who}.")
        self.e_mails.set_text("")
        return False

    # ------------------------------------------------------------------ Verwalten

    def _manage_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=12)
        self.perm_list = Gtk.ListBox()
        self.perm_list.set_selection_mode(Gtk.SelectionMode.NONE)
        ph = Gtk.Label(label="Keine Freigaben.")
        ph.set_margin_top(20)
        ph.show()
        self.perm_list.set_placeholder(ph)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(220)
        sw.add(self.perm_list)
        box.pack_start(sw, True, True, 0)
        return box

    def _on_page(self, _nb, _page, num):
        if num == 2 and self.target is not None:
            self._load_perms()

    def _load_perms(self, then=""):
        self._busy(True, "Freigaben werden geladen …")
        self._run(self.target.permissions, lambda perms: self._show_perms(perms, then),
                  "Freigaben laden")

    def _show_perms(self, perms, then=""):
        self._busy(False)
        if then:
            self._ok(then)
        else:
            self.status.set_text("")
        for c in self.perm_list.get_children():
            self.perm_list.remove(c)
        for p in sorted(perms, key=lambda p: (not p["besitzer"], p["geerbt"], p["wer"])):
            row = Gtk.Box(spacing=8, border_width=6)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            who = Gtk.Label(label=p["wer"], xalign=0)
            who.set_ellipsize(Pango.EllipsizeMode.END)
            v.pack_start(who, False, False, 0)
            ablauf = ".".join(reversed(p["ablauf"].split("-"))) if p["ablauf"] else ""
            extra = p["recht"] + (f" · läuft ab am {ablauf}" if ablauf else "") + \
                (" · vom übergeordneten Ordner" if p["geerbt"] else "")
            v.pack_start(_hint(extra), False, False, 0)
            row.pack_start(v, True, True, 0)
            if p["link"]:
                cp = Gtk.Button.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.BUTTON)
                cp.set_tooltip_text("Link kopieren")
                cp.connect("clicked", lambda _b, u=p["link"]: (self._copy(u),
                                                                self._ok("Link kopiert.")))
                row.pack_start(cp, False, False, 0)
            if not p["besitzer"] and not p["geerbt"]:
                rm = Gtk.Button(label="Entfernen")
                rm.connect("clicked", lambda _b, perm=p: self._remove(perm))
                row.pack_start(rm, False, False, 0)
            self.perm_list.add(row)
        self.perm_list.show_all()
        return False

    def _remove(self, perm):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text="Freigabe entfernen?")
        dlg.format_secondary_text(f"{perm['wer']} ({perm['recht']}) verliert den Zugriff. "
                                  "Ein bereits verschickter Link funktioniert danach nicht mehr.")
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Entfernen", Gtk.ResponseType.OK)
        res = dlg.run()
        dlg.destroy()
        if res != Gtk.ResponseType.OK:
            return
        self._busy(True, "Freigabe wird entfernt …")
        self._run(lambda: self.target.remove(perm["id"]),
                  lambda _r: self._load_perms("Freigabe entfernt.") or False,
                  "Entfernen")
