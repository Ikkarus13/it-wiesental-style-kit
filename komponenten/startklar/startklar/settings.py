"""Einstellungsfenster – wie „Personalisierung > Start“ bei Windows.
Jede Änderung wird sofort gespeichert und gilt beim nächsten Öffnen des Menüs."""
import os

from gi.repository import Gtk

from . import ASSETS, VERSION, icons, session, ui, updater
from .config import get_config

WEB = [("bing", "Bing", "https://www.bing.com/search?q={}"),
       ("google", "Google", "https://www.google.com/search?q={}"),
       ("ddg", "DuckDuckGo", "https://duckduckgo.com/?q={}"),
       ("startpage", "Startpage", "https://www.startpage.com/do/search?q={}"),
       ("off", "Aus", "")]
FOLDER_ORDER = ["settings", "explorer", "documents", "downloads", "music", "pictures",
                "videos", "home"]


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Startklar – Einstellungen")
        self.app = app
        app.add_window(self)
        self.set_wmclass("startklar-settings", "Startklar")
        self.set_default_size(760, 840)
        self.set_position(Gtk.WindowPosition.CENTER)
        try:
            self.set_icon_from_file(os.path.join(ASSETS, "start.svg"))
        except Exception:
            pass
        ui.add_class(self, "sk", "skset")
        self.cfg = get_config()
        self.dependent = []          # Zeilen, die von „Empfohlen“ abhängen

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for side in ("start", "end"):
            getattr(self.box, f"set_margin_{side}")(36)
        self.box.set_margin_top(28)
        self.box.set_margin_bottom(36)
        sw.add(self.box)
        self.add(sw)

        self.box.pack_start(ui.label("Start", "pagetitle"), False, False, 0)
        hint = ui.label("Änderungen gelten sofort beim nächsten Öffnen des Startmenüs.", "sub")
        hint.set_margin_bottom(8)
        self.box.pack_start(hint, False, False, 0)

        self._section("Layout")
        self._card("Layout", "Mehr Platz für angeheftete Apps oder für Empfehlungen",
                   self._choice("layout", [("more_pins", "Mehr Pins"), ("default", "Standard"),
                                           ("more_recs", "Mehr Empfehlungen")]),
                   "grid_20_regular")
        self._card("Symbolgrösse", "Grösse der App-Symbole und Kacheln",
                   self._choice("icon_size", [("small", "Klein"), ("medium", "Mittel"),
                                              ("large", "Gross")]), "apps_20_regular")
        self._card("Menübreite", None,
                   self._choice("width", [("narrow", "Schmal"), ("normal", "Normal"),
                                          ("wide", "Breit")]), "list_20_regular")

        self._section("Bereiche")
        self._card("Empfohlen anzeigen", "Zuletzt geöffnete Dateien und neue Apps",
                   self._switch("show_recommended"), "document_20_regular")
        self.dependent.append(self._card(
            "Kürzlich hinzugefügte Apps anzeigen", "in „Empfohlen“ (installiert in den letzten 7 Tagen)",
            self._switch("show_recent_apps"), None, indent=True))
        self.dependent.append(self._card(
            "Zuletzt geöffnete Dateien anzeigen", "in „Empfohlen“",
            self._switch("show_recent_files"), None, indent=True))
        self._card("Bereich „Alle“ anzeigen", "Alle Apps nach Kategorie, im Raster oder als Liste",
                   self._switch("show_all"), "apps_20_regular")

        self._section("Ordner neben Ein/Aus")
        self.folder_switches = {}
        current = set(self.cfg.get("folders"))
        for kind in FOLDER_ORDER:
            text, icon, _d = session.FOLDERS[kind]
            sw_ = Gtk.Switch()
            sw_.set_active(kind in current)
            sw_.connect("notify::active", lambda *_a: self._folders_changed())
            self.folder_switches[kind] = sw_
            self._card(text, None, sw_, icon)

        self._section("Aussehen")
        self._card("Design", None,
                   self._choice("theme", [("auto", "Wie System"), ("dark", "Dunkel"),
                                          ("light", "Hell")]), "eye_20_regular")
        self._card("Acryl-Hintergrund", "Durchscheinend und weichgezeichnet wie bei Windows",
                   self._switch("acrylic"), "image_20_regular")

        self._section("Suche")
        self._card("Dateien durchsuchen",
                   "Dateinamen im persönlichen Ordner (ohne OneDrive und Netzlaufwerke)",
                   self._switch("search_files"), "search_20_regular")
        self._card("Websuche", "Anbieter für „Im Web suchen“", self._web_combo(),
                   "globe_20_regular")

        self._section("Angeheftet")
        reset = Gtk.Button(label="Zurücksetzen")
        ui.add_class(reset, "ctrl")
        reset.connect("clicked", self._reset_pins)
        self._card("Angeheftete Apps zurücksetzen",
                   "Wieder deine Cinnamon-Favoriten und Taskleisten-Apps", reset, "pin_20_regular")

        self._section("Updates")
        kit = updater.kit_info()
        if kit:                             # Updates kommen über das Style Kit
            btn = None
            if kit["verwalten"]:
                btn = Gtk.Button(label=f"{kit['kit']} öffnen")
                ui.add_class(btn, "ctrl")
                btn.connect("clicked", lambda _b: updater.kit_oeffnen())
            self._card(f"Startklar {VERSION}", updater.kit_meldung(kit["kit"]), btn,
                       "arrow_clockwise_20_regular")
        else:
            self._card("Automatisch nach Updates suchen", "Einmal täglich, nur signierte Pakete",
                       self._switch("update_check"), "arrow_download_20_regular")
            check = Gtk.Button(label="Jetzt suchen")
            ui.add_class(check, "ctrl")
            check.connect("clicked", lambda _b: self.app.check_update(manual=True))
            self._card(f"Startklar {VERSION}", None, check, "arrow_clockwise_20_regular")
        self._update_dependent()

    # ------------------------------------------------------------ Bausteine
    def _section(self, title):
        self.box.pack_start(ui.label(title, "sectiontitle"), False, False, 0)

    def _card(self, title, desc, control, icon, indent=False):
        row = Gtk.Box(spacing=16)
        ui.add_class(row, "card")
        if indent:
            row.set_margin_start(36)
        if icon:
            img = icons.fluent(icon, 20)
            img.set_valign(Gtk.Align.CENTER)
            row.pack_start(img, False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_valign(Gtk.Align.CENTER)
        texts.pack_start(ui.label(title, "cardtitle"), False, False, 0)
        if desc:
            texts.pack_start(ui.label(desc, "sub"), False, False, 0)
        row.pack_start(texts, True, True, 0)
        control.set_valign(Gtk.Align.CENTER)
        row.pack_end(control, False, False, 0)
        self.box.pack_start(row, False, False, 0)
        return row

    def _switch(self, key):
        sw = Gtk.Switch()
        sw.set_active(bool(self.cfg.get(key)))
        sw.connect("notify::active", lambda s, _p: self._set(key, s.get_active()))
        return sw

    def _choice(self, key, options):
        box = Gtk.Box()
        ui.add_class(box, "seg")
        group = None
        for value, text in options:
            b = Gtk.RadioButton.new_with_label_from_widget(group, text)
            group = group or b
            b.set_mode(False)
            b.set_active(self.cfg.get(key) == value)
            b.connect("toggled", lambda b, v=value: b.get_active() and self._set(key, v))
            box.pack_start(b, False, False, 0)
        return box

    def _web_combo(self):
        combo = Gtk.ComboBoxText()
        current = self.cfg.get("web_search")
        active = "off" if not current else None
        for wid, text, url in WEB:
            combo.append(wid, text)
            if url and url == current:
                active = wid
        if active is None:                       # eigene Adresse aus der Datei
            combo.append("custom", "Eigene")
            active = "custom"
        combo.set_active_id(active)

        def changed(c):
            wid = c.get_active_id()
            for w, _t, url in WEB:
                if w == wid:
                    self._set("web_search", url)
        combo.connect("changed", changed)
        return combo

    # ------------------------------------------------------------ Speichern
    def _set(self, key, value):
        if self.cfg.get(key) == value:
            return
        self.cfg.set(key, value)
        self._update_dependent()
        self.app.win.reconfigure()

    def _update_dependent(self):
        on = bool(self.cfg.get("show_recommended"))
        for row in self.dependent:
            row.set_sensitive(on)

    def _folders_changed(self):
        self._set("folders", [k for k in FOLDER_ORDER if self.folder_switches[k].get_active()])

    def _reset_pins(self, _b):
        from .apps import default_pinned
        self.app.win.set_pinned(default_pinned(self.app.index))
        self.app.win.reconfigure()
