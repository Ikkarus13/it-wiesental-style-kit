"""Menüs im Windows-11-Stil (abgerundet, Icons links, Tastenkürzel rechts,
optional eine Icon-Reihe oben wie im Win-11-Kontextmenü).

Beispiel:
    m = WinMenu()
    m.icon_row([("cut", "Ausschneiden", on_cut, True), ("copy", "Kopieren", on_copy, True)])
    m.item("Öffnen", on_open, icon="open", accel="Eingabe")
    m.separator()
    sub = WinMenu()
    sub.item("Details", cb, radio=True)          # Punkt links = aktiv
    sub.item("Liste", cb, radio=False)
    m.item("Anzeigen", icon="grid", submenu=sub)
    m.item("Ausgeblendete Elemente", cb, check=True)  # Häkchen links
    m.popup_at_pointer(event)   # oder m.popup_below(widget)

Callbacks bekommen keine Argumente (lambda verwenden).
"""
from gi.repository import Gdk, Gtk

from . import icons, theme


class WinMenu:
    def __init__(self):
        self.menu = Gtk.Menu()
        self.menu.get_style_context().add_class("win-menu")
        self.menu.set_reserve_toggle_size(False)
        self._row = 0
        self._has_marks = False
        self._has_icons = False
        self._items = []
        self.menu.connect("deactivate", self._on_deactivate)
        self._keepalive = None

    def _on_deactivate(self, *_):
        pass

    # -- Bausteine -------------------------------------------------------
    def icon_row(self, entries, columns=None):
        """entries: Liste von (fluent_icon, tooltip, callback, sensitive)."""
        n = columns or max(5, len(entries))
        for i, (icon, tip, cb, sensitive) in enumerate(entries):
            mi = Gtk.MenuItem()
            mi.get_style_context().add_class("icon-row-item")
            img = icons.fluent(icon, 16)
            mi.add(img)
            mi.set_tooltip_text(tip)
            mi.set_sensitive(bool(sensitive))
            if cb:
                mi.connect("activate", lambda _w, c=cb: c())
            self.menu.attach(mi, i, i + 1, self._row, self._row + 1)
            mi.show_all()
        self._icon_cols = n
        self._row += 1
        self.separator()

    def item(self, label, callback=None, icon=None, accel=None, sensitive=True,
             radio=None, check=None, submenu=None, bold=False):
        mi = Gtk.MenuItem()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box._has_mark = radio is not None or check is not None
        box._icon_spacer = None
        self._items.append(box)
        if icon is not None:
            self._has_icons = True
        # Markierungsspalte (Punkt/Häkchen)
        if radio is not None or check is not None:
            self._has_marks = True
            mark = Gtk.Box()
            mark.set_size_request(20, -1)
            mark.get_style_context().add_class("mark")
            if radio:
                mark.add(icons.fluent("circle", 6, filled=True))
            elif check:
                mark.add(icons.fluent("checkmark", 16))
            box.pack_start(mark, False, False, 0)
        if icon is not None:
            img = icons.fluent(icon, 16) if isinstance(icon, str) else Gtk.Image.new_from_pixbuf(icon)
            img.get_style_context().add_class("menu-icon")
            box.pack_start(img, False, False, 0)
        else:
            spacer = Gtk.Box()
            spacer.set_size_request(16, -1)
            spacer.get_style_context().add_class("menu-icon")
            box.pack_start(spacer, False, False, 0)
            box._icon_spacer = spacer
        lab = Gtk.Label(label=label, xalign=0)
        lab.get_style_context().add_class("menu-label")
        if bold:
            lab.get_style_context().add_class("bold")
        box.pack_start(lab, True, True, 0)
        if accel:
            al = Gtk.Label(label=accel, xalign=1)
            al.get_style_context().add_class("menu-accel")
            box.pack_end(al, False, False, 0)
        if submenu is not None:
            arrow = icons.fluent("chevron_right", 12)
            arrow.get_style_context().add_class("menu-arrow")
            box.pack_end(arrow, False, False, 0)
            mi.set_submenu(submenu.menu)
            submenu._parent = self
        mi.add(box)
        mi.set_sensitive(sensitive)
        if callback and submenu is None:
            mi.connect("activate", lambda _w, c=callback: c())
        self._attach(mi)
        return mi

    def separator(self):
        sep = Gtk.SeparatorMenuItem()
        self._attach(sep)

    def _attach(self, widget):
        cols = getattr(self, "_icon_cols", 1)
        self.menu.attach(widget, 0, cols, self._row, self._row + 1)
        self._row += 1
        widget.show_all()

    # -- Anzeigen -------------------------------------------------------
    def _prepare(self, attach_to=None):
        # Hat irgendein Eintrag eine Markierung, bekommen alle die Spalte
        # Kein Eintrag mit Icon → leere Icon-Spalte weglassen (z. B. Sortieren-Menü)
        if not self._has_icons:
            for box in self._items:
                if box._icon_spacer is not None:
                    box.remove(box._icon_spacer)
                    box._icon_spacer = None
        if self._has_marks:
            for box in self._items:
                if not box._has_mark and not getattr(box, "_mark_pad", False):
                    pad = Gtk.Box()
                    pad.set_size_request(20, -1)
                    pad.get_style_context().add_class("mark")
                    box.pack_start(pad, False, False, 0)
                    box.reorder_child(pad, 0)
                    box._mark_pad = True
        self.menu.show_all()
        if attach_to is not None and self.menu.get_attach_widget() is None:
            self.menu.attach_to_widget(attach_to, None)

    def popup_at_pointer(self, event=None, attach_to=None):
        self._prepare(attach_to)
        self.menu.popup_at_pointer(event)

    def popup_below(self, widget):
        self._prepare(widget)
        self.menu.popup_at_widget(widget, Gdk.Gravity.SOUTH_WEST,
                                  Gdk.Gravity.NORTH_WEST, None)

    def popup_at_rect(self, gdk_window, rect, attach_to=None):
        self._prepare(attach_to)
        self.menu.popup_at_rect(gdk_window, rect, Gdk.Gravity.SOUTH_WEST,
                                Gdk.Gravity.NORTH_WEST, None)
