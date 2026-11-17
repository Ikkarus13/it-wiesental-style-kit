"""„Ordneroptionen“ wie im Windows-11-Explorer (··· → Optionen) und die
Hilfsfunktionen für die Einstellungen daraus.

    show_dialog(window)                 Dialog öffnen (nur ein Exemplar)
    start_location(config)              Start oder Dieser PC (neue Fenster/Tabs)
    open_folder(host, uri)              Ordner im selben oder in einem neuen Fenster
    full_path_text(loc)                 Pfad für Tab/Titelleiste
    record_visit(config, loc)           Ordnerbesuch zählen (häufig verwendete Ordner)
    frequent_folders(config, exclude)   häufig verwendete Ordner (URIs)
    clear_history(config)               FileFenster-Verlauf löschen
    apply_view_to_all(config, mode, uri) / reset_folders(config)

Alle Einstellungen liegen in config (DEFAULTS). Fenster und Ansichten
reagieren über config.connect_changed sofort – auch in anderen Fenstern.
"""
import os
import time

from gi.repository import Gdk, Gtk

from . import icons, places
from .config import DEFAULTS, get_config

FREQ_MIN = 2          # ab so vielen Besuchen „häufig verwendet“
FREQ_MAX = 10         # höchstens so viele Ordner im Schnellzugriff (wie Windows)
VISITS_MAX = 200      # gemerkte Ordner

GENERAL_KEYS = ("start_location", "open_new_window", "single_click", "show_recent", "show_frequent")
# Erweiterte Einstellungen (Text, Schlüssel, Häkchen = Wert umgekehrt)
ADVANCED = [
    ("Ausgeblendete Dateien, Ordner und Laufwerke anzeigen", "show_hidden", False),
    ("Erweiterungen bei bekannten Dateitypen ausblenden", "show_extensions", True),
    ("Kompaktansicht", "compact", False),
    ("Kontrollkästchen zur Auswahl von Elementen verwenden", "item_checkboxes", False),
    ("Statusleiste anzeigen", "status_bar", False),
    ("Vollständigen Pfad in der Titelleiste anzeigen", "full_path_title", False),
    ("Fensterhintergrund an Desktop-Hintergrund anpassen (Mica)", "mica", False),
]
VIEW_KEYS = tuple(k for _t, k, _i in ADVANCED)


# ----------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------
def start_location(config=None):
    """„Datei-Explorer öffnen für“: Start oder Dieser PC."""
    config = config or get_config()
    return places.THISPC if config.get("start_location") == "thispc" else places.HOME


def open_folder(host, uri):
    """Doppelklick/Eingabe auf einen Ordner: hier öffnen oder (Ordneroption
    „Jeden Ordner in einem eigenen Fenster öffnen“) in einem neuen Fenster."""
    if host.config.get("open_new_window", False):
        host.open_in_new_window(uri)
    else:
        host.navigate(uri)


def full_path_text(loc):
    """Vollständiger Pfad für Tab und Titelleiste (wie Windows: C:\\…)."""
    if not loc or places.is_virtual(loc) or loc.startswith(("trash:", "network:")):
        return places.display_name(loc)
    arc = places.archive_parts(loc)
    if arc:
        base = arc[0].get_path() or arc[0].get_parse_name()
        return os.path.join(base, *arc[2]) if arc[2] else base
    try:
        from .navrow import loc_to_text
        return loc_to_text(loc)
    except Exception:
        return places.display_name(loc)


def record_visit(config, loc):
    """Ordnerbesuch zählen – nur lokale Ordner und nur, wenn „Häufig verwendete
    Ordner anzeigen“ eingeschaltet ist."""
    if not loc or not loc.startswith("file://") or not config.get("show_frequent", True):
        return
    visits = dict(config.get("folder_visits") or {})
    old = visits.get(loc)
    count = _num(old[0]) if isinstance(old, (list, tuple)) and old else 0
    visits[loc] = [count + 1, int(time.time())]
    if len(visits) > VISITS_MAX:
        keep = sorted(visits.items(), key=lambda kv: _visit_key(kv[1]), reverse=True)
        visits = dict(keep[:VISITS_MAX])
    config.set("folder_visits", visits)


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return 0


def _visit_key(v):
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return (0, 0)
    return (_num(v[0]), _num(v[1]))


def frequent_folders(config, exclude=()):
    """Häufig verwendete Ordner (meiste Besuche zuerst), ohne exclude."""
    out = []
    for uri, v in (config.get("folder_visits") or {}).items():
        if uri in exclude or not isinstance(v, (list, tuple)) or len(v) != 2:
            continue
        if _num(v[0]) >= FREQ_MIN:
            out.append((_num(v[0]), _num(v[1]), uri))
    out.sort(reverse=True)
    return [u for _c, _t, u in out[:FREQ_MAX]]


def clear_history(config):
    """„Datei-Explorer-Verlauf löschen“: nur, was FileFenster selbst speichert
    (Adressverlauf, häufig verwendete Ordner) – nie die systemweite Liste
    zuletzt verwendeter Dateien (recently-used.xbel)."""
    config.set("address_history", [])
    config.set("folder_visits", {})


def apply_view_to_all(config, mode, uri=None):
    """„Für Ordner übernehmen“: Ansicht wird Standard für alle Ordner, eigene
    Ansichten einzelner Ordner entfallen. view_mode zuletzt setzen – darauf
    laden alle offenen Ordneransichten ihre Einstellungen neu."""
    # Der aktuelle Ordner behält die Ansicht auch dann, wenn er (Bilder/Videos)
    # sonst eine eigene Standardansicht hätte
    config.set("folder_views", {uri: mode} if uri else {})
    config.set("view_mode", mode)


def reset_folders(config):
    """„Ordner zurücksetzen“: Ansicht, Sortierung und Gruppierung aller Ordner
    auf den Standard."""
    config.set("folder_views", {})
    config.set("folder_sort", {})
    config.set("folder_group", {})
    config.set("view_mode", DEFAULTS["view_mode"])


# ----------------------------------------------------------------------
# Dialog
# ----------------------------------------------------------------------
_dialog = None


def show_dialog(window):
    global _dialog
    if _dialog is not None and _dialog.get_visible():
        _dialog.present()
        return _dialog
    _dialog = FolderOptionsDialog(window)
    _dialog.present()
    return _dialog


def _group(title, child):
    """Gruppenfeld mit Überschrift (wie die Rahmen im Windows-Dialog)."""
    frame = Gtk.Frame()
    frame.get_style_context().add_class("opt-group")
    lab = Gtk.Label(label=title)
    lab.get_style_context().add_class("opt-group-title")
    frame.set_label_widget(lab)
    frame.set_label_align(0.02, 0.5)
    for side in ("top", "bottom", "start", "end"):
        getattr(child, f"set_margin_{side}")(10 if side != "top" else 6)
    frame.add(child)
    return frame


class FolderOptionsDialog(Gtk.Window):
    """Ordneroptionen: Registerkarten Allgemein und Ansicht, OK/Abbrechen/
    Übernehmen. Änderungen gelten erst mit OK oder Übernehmen; Löschen, Für
    Ordner übernehmen und Ordner zurücksetzen wirken sofort (wie Windows)."""

    def __init__(self, parent):
        super().__init__(title="Ordneroptionen")
        self.win = parent
        self.config = get_config()
        self._block = False
        self._values = {}
        self._setters = {}
        self._touched = set()       # vom Nutzer hier geänderte Schlüssel
        from .propsdialog import _fluent_button
        self._button = _fluent_button
        for c in ("filefenster", "win-dialog", "props-dialog", "options-dialog"):
            self.get_style_context().add_class(c)
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
            self.set_modal(True)
            self.set_destroy_with_parent(True)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_skip_taskbar_hint(True)
        self.set_resizable(False)
        self.set_default_size(440, -1)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for side in ("top", "start", "end"):
            getattr(outer, f"set_margin_{side}")(12)
        outer.set_margin_bottom(14)
        self.add(outer)
        self.notebook = Gtk.Notebook()
        self.notebook.set_show_border(False)
        self.notebook.append_page(self._general_page(), Gtk.Label(label="Allgemein"))
        self.notebook.append_page(self._view_page(), Gtk.Label(label="Ansicht"))
        outer.pack_start(self.notebook, True, True, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_halign(Gtk.Align.END)
        btns.set_margin_top(14)
        self.btn_ok = _fluent_button("OK", accent=True)
        self.btn_cancel = _fluent_button("Abbrechen")
        self.btn_apply = _fluent_button("Übernehmen")
        for b in (self.btn_ok, self.btn_cancel, self.btn_apply):
            btns.pack_start(b, False, False, 0)
        outer.pack_start(btns, False, False, 0)
        self.btn_ok.connect("clicked", lambda *_: (self.apply(), self.destroy()))
        self.btn_cancel.connect("clicked", lambda *_: self.destroy())
        self.btn_apply.connect("clicked", lambda *_: self.apply())
        self.btn_ok.set_can_default(True)
        self.set_default(self.btn_ok)

        self.connect("key-press-event", self._on_key)
        self.connect("destroy", self._on_destroy)
        self.config.connect_changed(self._on_config_changed)
        for key in GENERAL_KEYS + VIEW_KEYS:
            self._show_value(key, self.config.get(key, DEFAULTS[key]))
        self._update_apply()
        self.show_all()
        self.btn_ok.grab_focus()

    # -- Werte --------------------------------------------------------------
    def _show_value(self, key, value):
        """Wert in die Bedienelemente übernehmen (ohne als Änderung zu zählen)."""
        self._values[key] = value
        self._block = True
        try:
            self._setters[key](value)
        finally:
            self._block = False

    def _changed(self, key, value):
        if self._block:
            return
        self._values[key] = value
        self._touched.add(key)
        self._update_apply()

    def pending(self):
        """Schlüssel, deren Wert im Dialog von der Einstellung abweicht."""
        return [k for k, v in self._values.items() if self.config.get(k, DEFAULTS[k]) != v]

    def _update_apply(self):
        self.btn_apply.set_sensitive(bool(self.pending()))

    def apply(self):
        for key in self.pending():
            self.config.set(key, self._values[key])
        self._touched.clear()
        self._update_apply()

    def _on_config_changed(self, key, value):
        # Umschalter an anderer Stelle (Anzeigen-Menü, Strg+H, anderes Fenster):
        # Dialog nachführen, solange der Nutzer diesen Wert hier nicht geändert hat
        if key not in self._setters:
            return
        if key not in self._touched:
            self._show_value(key, value)
        self._update_apply()

    def restore_defaults(self, keys):
        """„Standardwerte wiederherstellen“: nur die Bedienelemente; gespeichert
        wird erst mit OK/Übernehmen (wie Windows)."""
        for key in keys:
            self._show_value(key, DEFAULTS[key])
        self._touched.update(keys)
        self._update_apply()

    # -- Registerkarte Allgemein -------------------------------------------
    @staticmethod
    def _page_box():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("props-page")
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(14)
        return box

    def _radios(self, key, choices):
        """Optionsfelder für einen Schlüssel; choices: [(Text, Wert)]."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        buttons = []
        group = None
        for text, value in choices:
            rb = Gtk.RadioButton.new_with_label_from_widget(group, text)
            group = group or rb
            rb.get_child().set_line_wrap(True)
            rb.get_child().set_xalign(0)
            rb.connect("toggled", lambda b, v=value: b.get_active() and self._changed(key, v))
            box.pack_start(rb, False, False, 0)
            buttons.append((rb, value))

        def setter(val):
            for rb, v in buttons:
                if v == val:
                    rb.set_active(True)
        self._setters[key] = setter
        box.radios = buttons
        return box

    def _check(self, text, key, invert=False):
        cb = Gtk.CheckButton(label=text)
        cb.get_child().set_line_wrap(True)
        cb.get_child().set_xalign(0)
        cb.connect("toggled", lambda b: self._changed(key, b.get_active() != invert))
        self._setters[key] = lambda val: cb.set_active(bool(val) != invert)
        return cb

    def _general_page(self):
        page = self._page_box()
        # Datei-Explorer öffnen für:
        row = Gtk.Box(spacing=10)
        row.pack_start(Gtk.Label(label="Datei-Explorer öffnen für:", xalign=0), False, False, 0)
        self.combo_start = Gtk.ComboBoxText()
        self.combo_start.append("home", "Start")
        self.combo_start.append("thispc", "Dieser PC")
        self.combo_start.connect("changed", lambda c: self._changed("start_location", c.get_active_id()))
        self._setters["start_location"] = lambda v: self.combo_start.set_active_id(
            "thispc" if v == "thispc" else "home")
        row.pack_start(self.combo_start, True, True, 0)
        page.pack_start(row, False, False, 0)

        self.rb_window = self._radios("open_new_window", [
            ("Jeden Ordner im selben Fenster öffnen", False),
            ("Jeden Ordner in einem eigenen Fenster öffnen", True)])
        page.pack_start(_group("Ordner durchsuchen", self.rb_window), False, False, 0)

        self.rb_click = self._radios("single_click", [
            ("Öffnen durch einfachen Klick (Auswahl durch Zeigen)", True),
            ("Öffnen durch Doppelklick (Auswahl durch einfachen Klick)", False)])
        page.pack_start(_group("Klickoptionen", self.rb_click), False, False, 0)

        priv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.chk_recent = self._check("Zuletzt verwendete Dateien anzeigen", "show_recent")
        self.chk_frequent = self._check("Häufig verwendete Ordner anzeigen", "show_frequent")
        priv.pack_start(self.chk_recent, False, False, 0)
        priv.pack_start(self.chk_frequent, False, False, 0)
        clear = Gtk.Box(spacing=10)
        clear.set_margin_top(4)
        clear.pack_start(Gtk.Label(label="Datei-Explorer-Verlauf löschen", xalign=0), True, True, 0)
        self.btn_clear = self._button("Löschen")
        self.btn_clear.get_style_context().add_class("small")
        self.btn_clear.set_tooltip_text("Löscht den Adressverlauf und die häufig verwendeten "
                                        "Ordner von FileFenster.")
        self.btn_clear.connect("clicked", lambda *_: clear_history(self.config))
        clear.pack_end(self.btn_clear, False, False, 0)
        priv.pack_start(clear, False, False, 0)
        page.pack_start(_group("Datenschutz", priv), False, False, 0)

        self.btn_defaults_general = self._button("Standardwerte wiederherstellen")
        self.btn_defaults_general.set_halign(Gtk.Align.END)
        self.btn_defaults_general.connect("clicked", lambda *_: self.restore_defaults(GENERAL_KEYS))
        page.pack_end(self.btn_defaults_general, False, False, 0)
        return page

    # -- Registerkarte Ansicht ---------------------------------------------
    def _folder_view(self):
        tab = getattr(self.win, "current", None)
        return tab.folder_view() if tab is not None and hasattr(tab, "folder_view") else None

    def _view_page(self):
        page = self._page_box()
        fv_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        img = Gtk.Image.new_from_pixbuf(icons.folder_pixbuf(32))
        img.set_valign(Gtk.Align.START)
        fv_box.pack_start(img, False, False, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        note = Gtk.Label(label="Sie können die Ansicht für diesen Ordner (z. B. Details oder "
                               "Symbole) auf alle Ordner anwenden.", xalign=0)
        note.set_line_wrap(True)
        note.set_max_width_chars(46)
        right.pack_start(note, False, False, 0)
        brow = Gtk.Box(spacing=8)
        self.btn_apply_folders = self._button("Für Ordner übernehmen")
        self.btn_reset_folders = self._button("Ordner zurücksetzen")
        self.btn_apply_folders.connect("clicked", lambda *_: self.apply_to_folders())
        self.btn_reset_folders.connect("clicked", lambda *_: self.reset_folders())
        # wie Windows: nur aus einem Ordner heraus (nicht aus Start/Dieser PC)
        self.btn_apply_folders.set_sensitive(self._folder_view() is not None)
        brow.pack_start(self.btn_apply_folders, False, False, 0)
        brow.pack_start(self.btn_reset_folders, False, False, 0)
        right.pack_start(brow, False, False, 0)
        fv_box.pack_start(right, True, True, 0)
        page.pack_start(_group("Ordneransichten", fv_box), False, False, 0)

        page.pack_start(Gtk.Label(label="Erweiterte Einstellungen:", xalign=0), False, False, 0)
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lst.get_style_context().add_class("opt-list-body")
        for side in ("top", "bottom", "start", "end"):
            getattr(lst, f"set_margin_{side}")(8)
        head = Gtk.Box(spacing=6)
        head.pack_start(Gtk.Image.new_from_pixbuf(icons.folder_pixbuf(16)), False, False, 0)
        head.pack_start(Gtk.Label(label="Dateien und Ordner", xalign=0), False, False, 0)
        lst.pack_start(head, False, False, 0)
        self.adv_checks = {}
        for text, key, invert in ADVANCED:
            cb = self._check(text, key, invert)
            cb.set_margin_start(22)
            lst.pack_start(cb, False, False, 0)
            self.adv_checks[key] = cb
        sw = Gtk.ScrolledWindow()
        sw.get_style_context().add_class("opt-list")
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(216)    # alle Einträge ohne Bildlauf (Mica zweizeilig)
        sw.add(lst)
        page.pack_start(sw, True, True, 0)

        self.btn_defaults_view = self._button("Standardwerte wiederherstellen")
        self.btn_defaults_view.set_halign(Gtk.Align.END)
        self.btn_defaults_view.connect("clicked", lambda *_: self.restore_defaults(VIEW_KEYS))
        page.pack_start(self.btn_defaults_view, False, False, 0)
        return page

    def _confirm(self, text):
        from .propsdialog import message
        return message(self, text, kind="question", title="Ordneransichten",
                       buttons=["Ja", "Nein"], default=0) == 0

    def apply_to_folders(self):
        fv = self._folder_view()
        if fv is None or not getattr(fv, "uri", None):
            return
        if self._confirm("Sollen alle Ordner mit den Ansichtseinstellungen dieses Ordners "
                         "übereinstimmen?"):
            apply_view_to_all(self.config, fv.view_mode, fv.uri)

    def reset_folders(self):
        if self._confirm("Sollen alle Ordner auf die Standardansichtseinstellungen "
                         "zurückgesetzt werden?"):
            reset_folders(self.config)

    # -- Fenster ------------------------------------------------------------
    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    def _on_destroy(self, *_):
        global _dialog
        self.config.disconnect_changed(self._on_config_changed)
        if _dialog is self:
            _dialog = None

