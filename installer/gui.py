"""Grafischer Installations-Assistent (GTK 3) mit Branding aus branding/."""
import json
import os
import re
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from . import ablauf, aktualisierung, entfernen, katalog, marke  # noqa: E402

BRANDING_ORDNER = os.path.join(ablauf.PAKET, "branding")


def branding():
    return marke.laden()


def schriften_laden():
    """Schriften aus branding/schriften/ nur für dieses Programm registrieren."""
    import ctypes
    import glob
    dateien = glob.glob(os.path.join(BRANDING_ORDNER, "schriften", "*.[ot]tf"))
    if not dateien:
        return
    try:
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        for pfad in dateien:
            fc.FcConfigAppFontAddFile(None, pfad.encode())
    except OSError:
        pass


def stil_css(st):
    """Feste Markenfarben/Schriften aus branding.json → "stil" (alles optional).
    Der Inhaltsbereich folgt weiter dem System-Design; gefärbt werden Seitenleiste,
    Fussleiste, Akzente, Ecken und Schriften."""
    if not st:
        return ""
    g = st.get
    css = []
    if g("leiste_bg"):
        t = g("leiste_text", "#ffffff")
        css.append(f""".wst-leiste {{ background-color: {g('leiste_bg')}; color: {t};
              border-right: none; }}
.wst-leiste .wst-titel {{ color: {t}; }}
.wst-leiste .wst-klein {{ color: alpha({t}, 0.6); }}
.wst-leiste .wst-schritt {{ color: alpha({t}, 0.6); }}
.wst-leiste .wst-schritt.aktiv {{ color: {t}; }}
.wst-leiste .wst-schritt.erledigt {{ color: alpha({t}, 0.85); }}""")
    if g("fuss_bg"):
        t = g("fuss_text", "#ffffff")
        css.append(f""".wst-fuss {{ background-color: {g('fuss_bg')}; color: {t}; border-top: none; }}
.wst-fuss .wst-klein {{ color: alpha({t}, 0.7); }}
.wst-fuss button:not(.wst-primaer) {{ background-image: none; background-color: transparent;
    color: {t}; border: 1px solid alpha({t}, 0.35); box-shadow: none; }}
.wst-fuss button:not(.wst-primaer):hover {{ border-color: alpha({t}, 0.7); }}""")
    if g("akzent_text"):
        css.append(f"button.wst-primaer {{ color: {g('akzent_text')}; font-weight: 600; }}\n"
                   f"button.wst-primaer:disabled {{ color: alpha({g('akzent_text')}, 0.5); }}")
    if g("radius") is not None and g("radius") != "":
        r = g("radius")
        css.append(f".wst-karte, button.wst-primaer, .wst-fuss button {{ border-radius: {r}; }}")
    if g("schrift_titel"):
        css.append(f".wst-titel {{ font-family: \"{g('schrift_titel')}\"; font-weight: 800; "
                   "letter-spacing: -0.3px; }")
    if g("schrift_mono"):
        css.append(f".wst-mono {{ font-family: \"{g('schrift_mono')}\"; font-size: 8.5pt; "
                   "letter-spacing: 0.4px; }")
    return "\n".join(css)


CSS = """
.wst-leiste { background-color: alpha(@theme_fg_color, 0.04);
              border-right: 1px solid alpha(@theme_fg_color, 0.10); }
.wst-schritt { padding: 7px 14px; color: alpha(@theme_fg_color, 0.55);
               border-left: 3px solid transparent; }
.wst-schritt.aktiv { color: @theme_fg_color; font-weight: bold; border-left-color: AKZENT; }
.wst-schritt.erledigt { color: alpha(@theme_fg_color, 0.8); }
.wst-titel { font-size: 18pt; font-weight: 600; }
.wst-klein { font-size: 9pt; color: alpha(@theme_fg_color, 0.65); }
.wst-karte { border: 1px solid alpha(@theme_fg_color, 0.12); border-radius: 6px;
             padding: 4px 0; background-color: alpha(@theme_fg_color, 0.02); }
.wst-zeile { padding: 8px 14px; }
.wst-zeile + .wst-zeile { border-top: 1px solid alpha(@theme_fg_color, 0.07); }
.wst-fuss { border-top: 1px solid alpha(@theme_fg_color, 0.10); padding: 10px 16px; }
button.wst-primaer { background-image: none; background-color: AKZENT; color: #ffffff;
                     border-color: AKZENT; border-radius: 4px; padding: 4px 18px; }
button.wst-primaer:hover { background-color: shade(AKZENT, 1.1); }
button.wst-primaer:disabled { background-color: alpha(AKZENT, 0.4); color: alpha(#ffffff, 0.7); }
progressbar progress { background-image: none; background-color: AKZENT; border-color: AKZENT; }
progressbar trough { min-height: 6px; }
progressbar progress { min-height: 6px; }
.wst-log { font-family: monospace; font-size: 9pt; }
.wst-ok { color: #2e9d48; }
.wst-fehler { color: #c42b1c; }
.wst-marke { font-weight: bold; color: AKZENT; }
"""


def label(text, klasse=None, wrap=True, xalign=0.0, markup=False, selectable=False):
    lb = Gtk.Label(xalign=xalign)
    if markup:
        lb.set_markup(text)
    else:
        lb.set_text(text)
    lb.set_line_wrap(wrap)
    lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    lb.set_selectable(selectable)
    if klasse:
        for k in klasse.split():
            lb.get_style_context().add_class(k)
    return lb


def karte():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.get_style_context().add_class("wst-karte")
    return box


def zeile(*widgets):
    box = Gtk.Box(spacing=12)
    box.get_style_context().add_class("wst-zeile")
    for w in widgets:
        box.pack_start(w, w is widgets[0], True, 0)
    return box


class Assistent(Gtk.Window):
    def __init__(self, verwalten=False):
        self.b = branding()
        super().__init__(title=f"{self.b['produkt']} – {self.b['firma']}")
        self.set_default_size(820, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        symbol = os.path.join(BRANDING_ORDNER, "symbol.svg")
        if os.path.exists(symbol):
            self.set_icon_from_file(symbol)
        self.connect("delete-event", self._schliessen_fragen)

        schriften_laden()
        st = self.b.get("stil") or {}
        if st.get("schrift_text"):
            Gtk.Settings.get_default().set_property(
                "gtk-font-name", f"{st['schrift_text']} {st.get('schrift_groesse', 10)}")
        prov = Gtk.CssProvider()
        prov.load_from_data((CSS.replace("AKZENT", self.b["farbe"]) + "\n"
                             + stil_css(st)).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.laeuft = False
        self.fertig = False
        self.auswahl_checks = {}
        self.verwalten = verwalten

        aussen = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(aussen)
        mitte = Gtk.Box()
        aussen.pack_start(mitte, True, True, 0)

        # ---------------------------------------------------------- Seitenleiste
        seiten = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        seiten.set_size_request(210, -1)
        seiten.get_style_context().add_class("wst-leiste")
        logo = self._text_logo() if self.b.get("logo_text") else self._logo(2)
        if logo:
            logo.set_halign(Gtk.Align.START)
            logo.set_margin_start(17)
            logo.set_margin_top(22)
            seiten.pack_start(logo, False, False, 0)
        # Titel in der Seitenleiste: eigener Kurztitel, falls das Logo den Namen schon zeigt
        produkt = label(self.b.get("leiste_titel") or self.b["produkt"], "wst-titel")
        produkt.set_max_width_chars(14)          # lange Namen umbrechen statt die Leiste zu verbreitern
        produkt.set_margin_start(17)
        produkt.set_margin_top(8)
        produkt.set_margin_bottom(18)
        seiten.pack_start(produkt, False, False, 0)
        self.schritt_labels = {}
        namen = ([("verwalten", "Komponenten"), ("installation", "Entfernen"),
                  ("fertig", "Fertig")] if verwalten else
                 [("willkommen", "Willkommen"), ("auswahl", "Komponenten"),
                  ("browser", "Browser und Apps"), ("zusammenfassung", "Zusammenfassung"),
                  ("installation", "Installation"), ("fertig", "Fertig")])
        for sid, text in namen:
            lb = label(text, "wst-schritt", wrap=False)
            self.schritt_labels[sid] = lb
            seiten.pack_start(lb, False, False, 0)
        ver = label(f"Version {ablauf.version()}", "wst-klein wst-mono")
        ver.set_margin_start(17)
        ver.set_margin_bottom(14)
        seiten.pack_end(ver, False, False, 0)
        mitte.pack_start(seiten, False, False, 0)

        # ---------------------------------------------------------- Inhalt
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=120)
        mitte.pack_start(self.stack, True, True, 0)

        # ---------------------------------------------------------- Fuss
        fuss = Gtk.Box(spacing=8)
        fuss.get_style_context().add_class("wst-fuss")
        kontakt = self.b.get("kontakt") or self.b.get("web", "").replace("https://", "")
        fusstext = label(f"Bereitgestellt von <span weight='bold'>{GLib.markup_escape_text(self.b['firma'])}</span>"
                         + (f" · {GLib.markup_escape_text(kontakt)}" if kontakt else ""),
                         "wst-klein", markup=True, wrap=False)
        fuss.pack_start(fusstext, False, False, 0)
        self.btn_abbrechen = Gtk.Button(label="Abbrechen")
        self.btn_abbrechen.connect("clicked", lambda *_: self.close())
        self.btn_weiter = Gtk.Button(label="Weiter")
        self.btn_weiter.get_style_context().add_class("wst-primaer")
        self.btn_weiter.connect("clicked", self._weiter)
        self.btn_zurueck = Gtk.Button(label="Zurück")
        self.btn_zurueck.connect("clicked", self._zurueck)
        fuss.pack_end(self.btn_abbrechen, False, False, 0)
        fuss.pack_end(self.btn_weiter, False, False, 0)
        fuss.pack_end(self.btn_zurueck, False, False, 0)
        aussen.pack_end(fuss, False, False, 0)

        if verwalten:
            self._seite_verwalten()
            self.reihenfolge = ["verwalten", "installation", "fertig"]
        else:
            self._seite_willkommen()
            self._seite_auswahl()
            self._seite_browser()
            self._seite_zusammenfassung()
            self.reihenfolge = ["willkommen", "auswahl", "browser", "zusammenfassung",
                                "installation", "fertig"]
        self._seite_installation()
        self._seite_fertig()
        self.zeige(self.reihenfolge[0])

    # ------------------------------------------------------------------ Bausteine
    def _text_logo(self):
        """Wortmarke aus Text, optional mit farbigem Punkt davor (wie auf der Website)."""
        st = self.b.get("stil") or {}
        schrift = st.get("schrift_titel", "")
        punkt = self.b.get("logo_punkt")
        esc = GLib.markup_escape_text
        teile = []
        if punkt:
            teile.append(f"<span foreground='{esc(punkt)}'>●</span> ")
        teile.append(esc(self.b["logo_text"]))
        lb = Gtk.Label()
        lb.set_markup(f"<span font_family='{esc(schrift)}' weight='800' size='large'>"
                      + "".join(teile) + "</span>" if schrift else
                      "<b>" + "".join(teile) + "</b>")
        lb.get_style_context().add_class("wst-logo")
        return lb

    def _logo(self, faktor):
        pfad = os.path.join(BRANDING_ORDNER, "logo.svg")
        if not os.path.exists(pfad):
            pfad = os.path.join(BRANDING_ORDNER, "logo.png")
        skala = max(1, self.get_scale_factor())
        hoehe = 34 * faktor
        try:
            # SVG direkt in Zielgrösse rendern (scharf, auch bei HiDPI)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(pfad, -1, hoehe * skala, True)
        except GLib.Error:
            return None
        flaeche = Gdk.cairo_surface_create_from_pixbuf(pb, skala, None)
        return Gtk.Image.new_from_surface(flaeche)

    def _seite(self, sid, titel, untertitel=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_start(32)
        box.set_margin_end(32)
        box.set_margin_top(28)
        box.set_margin_bottom(16)
        box.pack_start(label(titel, "wst-titel"), False, False, 0)
        if untertitel:
            box.pack_start(label(untertitel), False, False, 0)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add(box)
        self.stack.add_named(scroll, sid)
        return box

    # ------------------------------------------------------------------ Seiten
    def _seite_willkommen(self):
        box = self._seite("willkommen", f"Willkommen bei {self.b['produkt']}",
                          self.b["untertitel"])
        text = ("Dieser Assistent richtet Werkzeuge ein, mit denen Linux Mint im Stil von "
                "Windows 11 aussieht und arbeitet: Startmenü, Explorer, Infobereich, "
                "Benachrichtigungen, Emoji-Panel und mehr.\n\n"
                "Sie können alles auf einmal installieren oder nur einzelne Teile. "
                "Auf Wunsch werden Microsoft Edge und Google Chrome installiert und "
                "Outlook und Teams als Apps eingerichtet.\n\n"
                "Für Systempakete fragt der Assistent einmal nach dem Administrator-Passwort. "
                "Alles andere wird nur für Ihr Benutzerkonto installiert.")
        box.pack_start(label(text), False, False, 0)
        box.pack_start(label(
            f"{self.b['produkt']} ist ein unabhängiges Paket"
            + (f" von {self.b['firma']}" if self.b.get("firma") else "")
            + " und nicht von Microsoft entwickelt, unterstützt oder mit Microsoft verbunden. "
            "Microsoft, Windows, Edge, Outlook, Teams und OneDrive sind Marken der "
            "Microsoft-Unternehmensgruppe, Chrome ist eine Marke von Google LLC.",
            "wst-klein"), False, False, 0)
        if not ablauf.desktop_sitzung():
            box.pack_start(label("⚠ Diese Sitzung ist nicht Cinnamon. Die Werkzeuge sind für "
                                 "Linux Mint mit Cinnamon gebaut – manche Teile wirken "
                                 "hier nicht.", "wst-fehler"), False, False, 0)
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            box.pack_start(label("⚠ Wayland-Sitzung: Emoji-Panel und PWA-Tray brauchen X11.",
                                 "wst-fehler"), False, False, 0)
        vorher = entfernen.installiert()
        if vorher:
            box.pack_start(label("Bereits installiert: " + ", ".join(
                katalog.NACH_ID[k].name for k in vorher if k in katalog.NACH_ID)
                + ". Eine erneute Installation aktualisiert diese Teile.", "wst-klein"),
                False, False, 0)

    def _seite_auswahl(self):
        box = self._seite("auswahl", "Was soll installiert werden?")
        self.rb_alles = Gtk.RadioButton.new_with_label(None, "Alles installieren (empfohlen)")
        self.rb_teile = Gtk.RadioButton.new_with_label_from_widget(self.rb_alles,
                                                                   "Nur ausgewählte Teile")
        box.pack_start(self.rb_alles, False, False, 0)
        box.pack_start(self.rb_teile, False, False, 0)
        self.rb_alles.connect("toggled", lambda *_: self._auswahl_aktualisieren())

        k = karte()
        vorher = entfernen.installiert()
        man = ablauf.manifest()
        for komp in katalog.KOMPONENTEN:
            cb = Gtk.CheckButton()
            cb.set_active(komp.standard)
            cb.connect("toggled", lambda *_: self._auswahl_aktualisieren())
            self.auswahl_checks[komp.id] = cb
            titel = f"<b>{GLib.markup_escape_text(komp.name)}</b>"
            v = man.get(komp.id, {}).get("version")
            if v:
                titel += f"  <span size='small' alpha='60%'>{GLib.markup_escape_text(v)}</span>"
            if komp.id in vorher:
                titel += "  <span size='small' alpha='60%'>· installiert</span>"
            if komp.hinweis:
                titel += f"  <span size='small' alpha='60%'>· {GLib.markup_escape_text(komp.hinweis)}</span>"
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text.pack_start(label(titel, markup=True), False, False, 0)
            text.pack_start(label(komp.beschreibung, "wst-klein"), False, False, 0)
            if komp.id == "design":
                opt = Gtk.Box(spacing=10)
                opt.set_margin_top(6)
                self.cmb_design = Gtk.ComboBoxText()
                self.cmb_design.append("dunkel", "Dunkel")
                self.cmb_design.append("hell", "Hell")
                self.cmb_design.set_active_id("dunkel")
                self.cb_mittig = Gtk.CheckButton(label="Startknopf und Fensterliste mittig")
                self.cb_mittig.set_active(True)
                opt.pack_start(self.cmb_design, False, False, 0)
                opt.pack_start(self.cb_mittig, False, False, 0)
                text.pack_start(opt, False, False, 0)
            r = Gtk.Box(spacing=10)
            r.get_style_context().add_class("wst-zeile")
            cb.set_valign(Gtk.Align.START)
            r.pack_start(cb, False, False, 0)
            r.pack_start(text, True, True, 0)
            k.pack_start(r, False, False, 0)
        box.pack_start(k, False, False, 0)
        self._auswahl_aktualisieren()

    def _auswahl_aktualisieren(self):
        alles = self.rb_alles.get_active()
        for kid, cb in self.auswahl_checks.items():
            cb.set_sensitive(not alles)
        an = self.design_an = alles or self.auswahl_checks["design"].get_active()
        self.cmb_design.set_sensitive(an)
        self.cb_mittig.set_sensitive(an)
        if hasattr(self, "btn_weiter") and self.stack.get_visible_child_name() == "auswahl":
            self.btn_weiter.set_sensitive(bool(self.gewaehlt()))

    def gewaehlt(self):
        if self.rb_alles.get_active():
            return [k.id for k in katalog.KOMPONENTEN]
        return [k.id for k in katalog.KOMPONENTEN if self.auswahl_checks[k.id].get_active()]

    def browser_noetig(self):
        return any(katalog.NACH_ID[k].braucht_browser for k in self.gewaehlt())

    def _seite_browser(self):
        box = self._seite("browser", "Browser und Apps",
                          "Outlook und Teams laufen unter Linux am besten als Web-Apps "
                          "(PWA) in Edge oder Chrome.")
        box.pack_start(label("<b>Browser installieren</b>", markup=True), False, False, 0)
        k = karte()
        self.browser_checks = {}
        for b in katalog.BROWSER:
            cb = Gtk.CheckButton(label=b.name)
            da = ablauf.browser_installiert(b.id)
            cb.set_active(True)
            if da:
                cb.set_sensitive(False)
                cb.set_label(f"{b.name}  (bereits installiert)")
            cb.connect("toggled", lambda *_: self._browser_aktualisieren())
            self.browser_checks[b.id] = cb
            k.pack_start(zeile(cb), False, False, 0)
        box.pack_start(k, False, False, 0)
        box.pack_start(label("Die Browser kommen aus den offiziellen Paketquellen von "
                             "Microsoft und Google und aktualisieren sich danach über die "
                             "Aktualisierungsverwaltung.", "wst-klein"), False, False, 0)

        box.pack_start(label("<b>Outlook und Teams als Apps anlegen</b>", markup=True),
                       False, False, 0)
        k = karte()
        self.webapp_rows = {}
        for w in katalog.WEBAPPS:
            cb = Gtk.CheckButton(label=w.name)
            cb.set_active(True)
            cmb = Gtk.ComboBoxText()
            cb.connect("toggled", lambda *_: self._browser_aktualisieren())
            info = label(w.warum, "wst-klein")
            links = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            links.pack_start(cb, False, False, 0)
            links.pack_start(info, False, False, 0)
            r = zeile(links, label("in", wrap=False), cmb)
            cmb.set_valign(Gtk.Align.CENTER)
            k.pack_start(r, False, False, 0)
            self.webapp_rows[w.id] = (cb, cmb)
        box.pack_start(k, False, False, 0)
        self.lb_webapp_info = label(
            "Die Apps werden über eine Browser-Richtlinie eingerichtet und erscheinen "
            "beim nächsten Start des Browsers im Startmenü. Sie lassen sich danach nur "
            "über diesen Installer wieder entfernen.", "wst-klein")
        box.pack_start(self.lb_webapp_info, False, False, 0)
        self._browser_aktualisieren()

    def _verfuegbare_browser(self):
        return [b for b in katalog.BROWSER
                if ablauf.browser_installiert(b.id) or self.browser_checks[b.id].get_active()]

    def _browser_aktualisieren(self):
        verf = self._verfuegbare_browser()
        for wid, (cb, cmb) in self.webapp_rows.items():
            alt = cmb.get_active_id()
            cmb.remove_all()
            for b in verf:
                cmb.append(b.id, b.name)
            w = katalog.WEBAPP_NACH_ID[wid]
            ids = [b.id for b in verf]
            cmb.set_active_id(alt if alt in ids else
                              (w.browser_empfohlen if w.browser_empfohlen in ids else
                               (ids[0] if ids else None)))
            cb.set_sensitive(bool(verf))
            cmb.set_sensitive(bool(verf) and cb.get_active())

    def auswahl(self):
        gew = self.gewaehlt()
        a = ablauf.Auswahl(komponenten=gew)
        if "design" in gew:
            a.design = self.cmb_design.get_active_id() or "dunkel"
            a.leiste_mittig = self.cb_mittig.get_active()
        if self.browser_noetig():
            a.browser_seite = True
            a.browser = [b for b, cb in self.browser_checks.items()
                         if cb.get_active() and cb.get_sensitive()]
            for wid, (cb, cmb) in self.webapp_rows.items():
                if cb.get_active() and cb.get_sensitive() and cmb.get_active_id():
                    a.webapps[wid] = cmb.get_active_id()
        return a

    def _seite_zusammenfassung(self):
        self.box_zus = self._seite("zusammenfassung", "Bereit zur Installation")
        self.zus_inhalt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.box_zus.pack_start(self.zus_inhalt, False, False, 0)

    def _zusammenfassung_fuellen(self):
        for c in self.zus_inhalt.get_children():
            c.destroy()
        a = self.auswahl()
        inst = ablauf.Installation(a)
        k = karte()
        for kid in a.komponenten:
            extra = ""
            if kid == "design":
                extra = (" – " + ("dunkel" if a.design == "dunkel" else "hell")
                         + (", Leiste mittig" if a.leiste_mittig else ""))
            k.pack_start(zeile(label("• " + katalog.NACH_ID[kid].name + extra)), False, False, 0)
        for b in a.browser:
            k.pack_start(zeile(label("• " + katalog.BROWSER_NACH_ID[b].name + " installieren")),
                         False, False, 0)
        for w, b in a.webapps.items():
            k.pack_start(zeile(label(f"• {katalog.WEBAPP_NACH_ID[w].name} als App in "
                                     f"{katalog.BROWSER_NACH_ID[b].name}")), False, False, 0)
        self.zus_inhalt.pack_start(k, False, False, 0)
        if inst.braucht_root():
            self.zus_inhalt.pack_start(label(
                "Für Systempakete und Browser werden Sie gleich nach dem "
                "Administrator-Passwort gefragt.", "wst-klein"), False, False, 0)
        if "startklar" in a.komponenten:
            self.zus_inhalt.pack_start(label(
                "Startklar ersetzt das bisherige Menü in der Leiste; FileFenster wird "
                f"Standard-Dateimanager. Beides lässt sich über «{self.b['produkt']}» im "
                "Startmenü wieder entfernen.", "wst-klein"), False, False, 0)
        self.zus_inhalt.show_all()

    def _seite_installation(self):
        box = self._seite("installation", "Installation läuft …")
        self.inst_titel = box.get_children()[0]
        self.lb_schritt = label("", wrap=False)
        self.lb_schritt.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(self.lb_schritt, False, False, 0)
        self.fortschritt = Gtk.ProgressBar()
        box.pack_start(self.fortschritt, False, False, 0)
        self.ergebnis_box = karte()
        self.ergebnis_box.set_no_show_all(True)
        box.pack_start(self.ergebnis_box, False, False, 0)
        exp = Gtk.Expander(label="Details")
        self.log_buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=self.log_buf, editable=False, cursor_visible=False,
                          wrap_mode=Gtk.WrapMode.WORD_CHAR)
        tv.get_style_context().add_class("wst-log")
        self.log_view = tv
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(-1, 220)
        sw.add(tv)
        exp.add(sw)
        box.pack_start(exp, False, False, 0)
        self.log_datei = os.path.join(ablauf.ZIEL, "installation.log")

    def _seite_fertig(self):
        box = self._seite("fertig", "Fertig")
        self.fertig_titel = box.get_children()[0]
        self.fertig_inhalt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.pack_start(self.fertig_inhalt, False, False, 0)

    def _seite_verwalten(self):
        box = self._seite("verwalten", "Installierte Komponenten",
                          "Haken entfernen, um eine Komponente zu deinstallieren. "
                          "Zum Hinzufügen das Installationspaket erneut starten.")
        self._update_karte(box)
        k = karte()
        self.verwalten_checks = {}
        inst = entfernen.installiert()
        for komp in katalog.KOMPONENTEN:
            if komp.id not in inst:
                continue
            cb = Gtk.CheckButton()
            cb.set_active(True)
            cb.connect("toggled", lambda *_: self.btn_weiter.set_sensitive(
                any(not c.get_active() for c in self.verwalten_checks.values())))
            self.verwalten_checks[komp.id] = cb
            r = Gtk.Box(spacing=10)
            r.get_style_context().add_class("wst-zeile")
            r.pack_start(cb, False, False, 0)
            r.pack_start(label(f"<b>{GLib.markup_escape_text(komp.name)}</b>  "
                               f"<span size='small' alpha='60%'>{GLib.markup_escape_text(inst[komp.id])}</span>",
                               markup=True), True, True, 0)
            k.pack_start(r, False, False, 0)
        if not self.verwalten_checks:
            k.pack_start(zeile(label("Keine Komponenten installiert.")), False, False, 0)
        box.pack_start(k, False, False, 0)

    # ------------------------------------------------------------------ Updates des Kits
    def _update_karte(self, box):
        if not aktualisierung.REPO:
            return
        k = karte()
        r = Gtk.Box(spacing=10)
        r.get_style_context().add_class("wst-zeile")
        self.upd_label = label(f"Version {aktualisierung.installierte_version()} – "
                               "suche nach Updates …")
        r.pack_start(self.upd_label, True, True, 0)
        self.upd_btn = Gtk.Button(label="Jetzt aktualisieren")
        self.upd_btn.get_style_context().add_class("wst-primaer")
        self.upd_btn.set_valign(Gtk.Align.CENTER)
        self.upd_btn.set_no_show_all(True)
        r.pack_start(self.upd_btn, False, False, 0)
        k.pack_start(r, False, False, 0)
        box.pack_start(k, False, False, 0)

        def arbeit():
            try:
                info, fehler = aktualisierung.pruefen(), None
            except aktualisierung.Fehler as e:
                info, fehler = None, str(e)
            GLib.idle_add(self._update_gefunden, info, fehler)
        threading.Thread(target=arbeit, daemon=True).start()

    def _update_gefunden(self, info, fehler):
        v = aktualisierung.installierte_version()
        if fehler:
            self.upd_label.set_text(f"Version {v}. Update-Suche nicht möglich: {fehler}")
        elif not info:
            self.upd_label.set_text(f"Version {v} – auf dem neuesten Stand.")
        else:
            self.upd_label.set_text(f"Version {info['version']} ist verfügbar "
                                    f"(installiert: {v}).")
            self.upd_btn.connect("clicked", lambda *_: self._update_starten(info))
            self.upd_btn.show()
        return False

    def _update_starten(self, info):
        self.zeige("installation")
        self.inst_titel.set_text("Aktualisierung läuft …")
        self.laeuft = True
        os.makedirs(ablauf.ZIEL, exist_ok=True)
        with open(self.log_datei, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} – Update auf {info['version']}\n")

        def arbeit():
            hinweise, ok = [], False
            try:
                self._melde("schritt", f"Version {info['version']} laden")
                ordner = aktualisierung.holen(info, log=lambda t: self._melde("log", t))
                p = subprocess.Popen(aktualisierung.befehl(ordner), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, errors="replace")
                in_hinweisen = False
                for z in p.stdout:
                    z = re.sub(r"\x1b\[[0-9;]*m", "", z.rstrip("\n"))
                    if z.startswith("==> "):
                        self._melde("schritt", z[4:])
                    elif z.startswith("  ✓ ") or z.startswith("  ✗ "):
                        kid, _, text = z[4:].partition(": ")
                        self._melde("ergebnis", kid.strip(), z.startswith("  ✓"), text or "")
                    elif z.startswith("Noch zu tun:"):
                        in_hinweisen = True
                    elif in_hinweisen and z.startswith("  • "):
                        hinweise.append(z[4:])
                    else:
                        self._melde("log", z)
                ok = p.wait() == 0
            except Exception as e:  # noqa: BLE001
                self._melde("log", f"FEHLER: {e}")
                hinweise.insert(0, f"Aktualisierung fehlgeschlagen: {e}")
            finally:
                aktualisierung.aufraeumen()
            self._melde("fortschritt", 1.0)
            GLib.idle_add(self._update_fertig, ok, hinweise)
        threading.Thread(target=arbeit, daemon=True).start()

    def _update_fertig(self, ok, hinweise):
        self.laeuft = False
        self.inst_titel.set_text("Aktualisierung abgeschlossen" if ok
                                 else "Aktualisierung mit Fehlern abgeschlossen")
        self.lb_schritt.set_text(f"Protokoll: {self.log_datei}")
        self._fertig_fuellen(ok, hinweise)
        self.btn_weiter.set_sensitive(True)
        self.btn_abbrechen.set_sensitive(True)
        return False

    # ------------------------------------------------------------------ Navigation
    def zeige(self, sid):
        self.stack.set_visible_child_name(sid)
        idx = self.reihenfolge.index(sid)
        for s, lb in self.schritt_labels.items():
            ctx = lb.get_style_context()
            ctx.remove_class("aktiv")
            ctx.remove_class("erledigt")
            if s == sid:
                ctx.add_class("aktiv")
            elif s in self.reihenfolge and self.reihenfolge.index(s) < idx:
                ctx.add_class("erledigt")
        browser_lb = self.schritt_labels.get("browser")
        if browser_lb is not None:
            browser_lb.set_visible(not hasattr(self, "rb_alles") or self.browser_noetig())
        self.btn_zurueck.set_visible(sid not in ("willkommen", "verwalten", "installation", "fertig"))
        self.btn_weiter.set_sensitive(True)
        self.btn_abbrechen.set_sensitive(sid != "installation")
        if sid == "zusammenfassung":
            self._zusammenfassung_fuellen()
            self.btn_weiter.set_label("Installieren")
        elif sid == "verwalten":
            self.btn_weiter.set_label("Entfernen")
            self.btn_weiter.set_sensitive(False)
        elif sid == "installation":
            self.btn_weiter.set_label("Weiter")
            self.btn_weiter.set_sensitive(False)
        elif sid == "fertig":
            self.btn_weiter.set_label("Schliessen")
            self.btn_abbrechen.hide()
        else:
            self.btn_weiter.set_label("Weiter")
        if sid == "auswahl":
            self._auswahl_aktualisieren()

    def _naechste(self, sid, richtung):
        i = self.reihenfolge.index(sid) + richtung
        while 0 <= i < len(self.reihenfolge):
            if self.reihenfolge[i] == "browser" and not self.browser_noetig():
                i += richtung
                continue
            return self.reihenfolge[i]
        return None

    def _weiter(self, *_):
        sid = self.stack.get_visible_child_name()
        if sid == "fertig":
            self.fertig = True
            self.close()
            return
        if sid == "installation":
            self.zeige("fertig")
            return
        if sid == "browser":
            self._browser_aktualisieren()
        nxt = self._naechste(sid, +1)
        if nxt == "installation":
            self.zeige("installation")
            if self.verwalten:
                self._entfernen_starten()
            else:
                self._installation_starten()
        elif nxt:
            self.zeige(nxt)

    def _zurueck(self, *_):
        prev = self._naechste(self.stack.get_visible_child_name(), -1)
        if prev:
            self.zeige(prev)

    def _schliessen_fragen(self, *_):
        if self.laeuft:
            return True   # während der Installation nicht schliessen
        return False

    # ------------------------------------------------------------------ Installation
    def _log(self, text):
        it = self.log_buf.get_end_iter()
        self.log_buf.insert(it, text + "\n")
        self.log_view.scroll_to_iter(self.log_buf.get_end_iter(), 0, False, 0, 0)
        try:
            with open(self.log_datei, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass

    def _ergebnis(self, kid, ok, text):
        name = (katalog.NACH_ID[kid].name if kid in katalog.NACH_ID
                else "Systempakete und Browser")
        sym = "✓" if ok else "✗"
        lb = label(f"<span weight='bold'>{sym}</span>  {GLib.markup_escape_text(name)}"
                   + ("" if ok else f" – {GLib.markup_escape_text(text)}"),
                   "wst-ok" if ok else "wst-fehler", markup=True)
        self.ergebnis_box.pack_start(zeile(lb), False, False, 0)
        self.ergebnis_box.set_no_show_all(False)
        self.ergebnis_box.show_all()

    def _ereignis(self, art, *daten):
        if art == "log":
            self._log(daten[0])
        elif art == "schritt":
            self.lb_schritt.set_text(daten[0])
            self._log(f"== {daten[0]}")
        elif art == "fortschritt":
            self.fortschritt.set_fraction(daten[0])
        elif art == "ergebnis":
            self._ergebnis(*daten)
        return False

    def _melde(self, *e):
        GLib.idle_add(self._ereignis, *e)

    def _installation_starten(self):
        a = self.auswahl()
        os.makedirs(ablauf.ZIEL, exist_ok=True)
        with open(self.log_datei, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} – Paket {ablauf.version()}\n")
        self.inst = ablauf.Installation(a)
        self.laeuft = True

        def arbeit():
            ok = False
            try:
                ok = self.inst.ausfuehren(self._melde)
            except Exception as e:  # noqa: BLE001
                self._melde("log", f"FEHLER: {e}")
            GLib.idle_add(self._installation_fertig, ok)
        threading.Thread(target=arbeit, daemon=True).start()

    def _installation_fertig(self, ok):
        self.laeuft = False
        self.inst_titel.set_text("Installation abgeschlossen" if ok
                                 else "Installation mit Fehlern abgeschlossen")
        self.lb_schritt.set_text(f"Protokoll: {self.log_datei}")
        self._fertig_fuellen(ok, self.inst.hinweise)
        self.btn_weiter.set_sensitive(True)
        self.btn_abbrechen.set_sensitive(True)
        return False

    def _fertig_fuellen(self, ok, hinweise):
        for c in self.fertig_inhalt.get_children():
            c.destroy()
        self.fertig_titel.set_text("Fertig" if ok else "Fertig – mit Fehlern")
        if not ok:
            self.fertig_inhalt.pack_start(label(
                f"Einige Teile konnten nicht installiert werden. Details stehen im Protokoll "
                f"({self.log_datei}). Der Installer kann gefahrlos erneut gestartet werden.",
                "wst-fehler"), False, False, 0)
        if hinweise:
            self.fertig_inhalt.pack_start(label("<b>Noch zu tun</b>", markup=True), False, False, 0)
            k = karte()
            for h in hinweise:
                k.pack_start(zeile(label("• " + h, selectable=True)), False, False, 0)
            self.fertig_inhalt.pack_start(k, False, False, 0)
        if not self.verwalten:
            btn = Gtk.Button(label="Jetzt abmelden")
            btn.set_halign(Gtk.Align.START)
            btn.connect("clicked", self._abmelden)
            self.fertig_inhalt.pack_start(btn, False, False, 0)
        if self.b.get("kontakt") or self.b.get("web"):
            self.fertig_inhalt.pack_start(label(
                "Fragen? " + " · ".join(x for x in (self.b.get("kontakt"), self.b.get("web")) if x),
                "wst-klein", selectable=True), False, False, 0)
        self.fertig_inhalt.show_all()

    def _abmelden(self, *_):
        GLib.spawn_async(["cinnamon-session-quit", "--logout"],
                         flags=GLib.SpawnFlags.SEARCH_PATH)

    # ------------------------------------------------------------------ Entfernen
    def _entfernen_starten(self):
        weg = [k for k, cb in self.verwalten_checks.items() if not cb.get_active()]
        self.inst_titel.set_text("Komponenten werden entfernt …")
        self.laeuft = True

        def arbeit():
            for i, kid in enumerate(weg):
                self._melde("fortschritt", i / len(weg))
                self._melde("schritt", katalog.NACH_ID[kid].name)
                try:
                    self._melde("log", entfernen.entfernen(kid))
                    self._melde("ergebnis", kid, True, "entfernt")
                except Exception as e:  # noqa: BLE001
                    self._melde("ergebnis", kid, False, str(e))
            self._melde("fortschritt", 1.0)
            GLib.idle_add(self._entfernen_fertig)
        threading.Thread(target=arbeit, daemon=True).start()

    def _entfernen_fertig(self):
        self.laeuft = False
        self.inst_titel.set_text("Entfernen abgeschlossen")
        self._fertig_fuellen(True, ["Einmal ab- und wieder anmelden, damit die Leiste "
                                    "überall aktualisiert ist."])
        self.btn_weiter.set_sensitive(True)
        return False


def starten(verwalten=False):
    win = Assistent(verwalten=verwalten)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    win.zeige(win.reihenfolge[0])
    Gtk.main()
    return 0
