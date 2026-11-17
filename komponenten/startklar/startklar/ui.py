"""Bausteine der Oberfläche: Kacheln, Zeilen, Abschnittsköpfe und die
Überlagerungs-Ebene für Kontextmenüs, Kategorie-Ordner und Buchstaben.

Kontextmenüs sind bewusst keine eigenen Fenster (Gtk.Menu): So bleibt der
Fokus im Startmenü, und es schliesst sich nicht versehentlich.
"""
from gi.repository import Gdk, GLib, Gtk, Pango

from . import icons, theme


def label(text, cls=None, xalign=0.0, ellipsize=True, lines=1):
    lb = Gtk.Label(label=text)
    lb.set_xalign(xalign)
    if ellipsize:
        lb.set_ellipsize(Pango.EllipsizeMode.END)
    if lines > 1:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lb.set_lines(lines)
    if cls:
        for c in cls.split():
            lb.get_style_context().add_class(c)
    return lb


def add_class(widget, *classes):
    ctx = widget.get_style_context()
    for c in classes:
        ctx.add_class(c)
    return widget


def on_context(widget, callback):
    """Rechtsklick und Menütaste → callback(widget, x, y) (x/y im Widget)."""
    def press(w, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 3:
            callback(w, ev.x, ev.y)
            return True
        return False

    def popup(w):
        a = w.get_allocation()
        callback(w, a.width / 2, a.height / 2)
        return True
    widget.connect("button-press-event", press)
    widget.connect("popup-menu", popup)


DND_TARGET = "startklar/app"


def dnd_targets():
    return [Gtk.TargetEntry.new(DND_TARGET, Gtk.TargetFlags.SAME_APP, 0)]


def drag_source(widget, entry, win, move=False):
    """App-Kachel/Zeile ziehbar machen (nur innerhalb des Menüs)."""
    widget.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, dnd_targets(),
                           Gdk.DragAction.MOVE if move else Gdk.DragAction.COPY)
    if entry.gicon is not None:
        widget.drag_source_set_icon_gicon(entry.gicon)

    def begin(w, _ctx):
        w.set_opacity(0.35 if move else 1.0)
        win.drag_started(entry.id)

    def end(w, _ctx):
        w.set_opacity(1.0)
        win.drag_finished()
    widget.connect("drag-begin", begin)
    widget.connect("drag-data-get",
                   lambda _w, _c, data, _i, _t: data.set(data.get_target(), 8, entry.id.encode()))
    widget.connect("drag-end", end)
    widget.connect("drag-failed", lambda *_a: True)     # kein Zurückschnellen


def tile(entry, on_click, width=96, height=84, icon_size=32):
    """App-Kachel (Symbol über Name) wie bei „Angeheftet“."""
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_size_request(width, height)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    img = icons.app_image(entry.gicon, icon_size)
    img.set_margin_top(14)
    box.pack_start(img, False, False, 0)
    lb = label(entry.name, xalign=0.5)
    lb.set_margin_top(8)
    lb.set_margin_start(4)
    lb.set_margin_end(4)
    lb.set_width_chars(1)
    box.pack_start(lb, False, False, 0)
    btn.add(box)
    btn.set_tooltip_text(entry.name if len(entry.name) > 12 else None)
    btn.connect("clicked", lambda _b: on_click(entry))
    btn.entry = entry
    return btn


def row(icon_widget, title, subtitle=None, height=48, title_cls=None):
    """Zeile: Symbol links, Titel (und Untertitel) rechts davon."""
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.set_margin_start(8)
    box.set_margin_end(8)
    btn.set_size_request(-1, height)
    icon_widget.set_valign(Gtk.Align.CENTER)
    box.pack_start(icon_widget, False, False, 0)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    texts.set_valign(Gtk.Align.CENTER)
    t = label(title, title_cls)
    texts.pack_start(t, False, False, 0)
    if subtitle:
        texts.pack_start(label(subtitle, "sub"), False, False, 0)
    box.pack_start(texts, True, True, 0)
    btn.add(box)
    btn.title_label = t
    return btn


def section_header(title, button_text=None, on_button=None, chevron="chevron_right_12_regular"):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    box.pack_start(label(title, "h"), True, True, 0)
    btn = None
    if button_text:
        btn = Gtk.Button()
        add_class(btn, "ctrl")
        inner = Gtk.Box(spacing=6)
        btn.text_label = label(button_text, ellipsize=False)
        inner.pack_start(btn.text_label, False, False, 0)
        if chevron:
            btn.chevron = icons.fluent(chevron, 12)
            inner.pack_start(btn.chevron, False, False, 0)
        btn.add(inner)
        btn.set_valign(Gtk.Align.CENTER)
        if on_button:
            btn.connect("clicked", lambda b: on_button(b))
        box.pack_end(btn, False, False, 0)
    box.button = btn
    return box


def icon_button(icon_name, tooltip, on_click, size=40, icon_size=16):
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_size_request(size, size)
    btn.add(icons.fluent(icon_name, icon_size))
    btn.set_tooltip_text(tooltip)
    btn.connect("clicked", lambda b: on_click(b))
    btn.set_valign(Gtk.Align.CENTER)
    return btn


# ---------------------------------------------------------------- Ebene
class Layer:
    """Eine Überlagerung zur Zeit im Gtk.Overlay des Menüs."""

    def __init__(self, overlay):
        self.overlay = overlay
        self.scrim = None
        self.child = None
        self.on_close = None
        self.dim = False
        self.restore_focus = None

    @property
    def is_open(self):
        return self.child is not None

    def open(self, child, anchor=None, placement="below", center=False, dim=False,
             on_close=None, focus=True):
        """anchor: (x, y, w, h) im Overlay; placement: below | above | point."""
        self.close()
        toplevel = self.overlay.get_toplevel()
        self.restore_focus = toplevel.get_focus() if toplevel else None
        self.dim = dim
        self.on_close = on_close
        scrim = Gtk.EventBox()
        scrim.set_above_child(False)
        scrim.set_app_paintable(True)
        scrim.connect("draw", self._draw_scrim)
        scrim.connect("button-press-event", self._scrim_press)
        self.scrim = scrim
        self.overlay.add_overlay(scrim)
        child.show_all()
        child.set_halign(Gtk.Align.START)
        child.set_valign(Gtk.Align.START)
        self.child = child
        self.overlay.add_overlay(child)
        alloc = self.overlay.get_allocation()
        _min, nat = child.get_preferred_size()
        w = min(nat.width, alloc.width - 16)
        h = min(nat.height, alloc.height - 16)
        if center or anchor is None:
            x = (alloc.width - w) / 2
            y = (alloc.height - h) / 2
        else:
            ax, ay, aw, ah = anchor
            if placement == "above":
                x, y = ax + aw - w, ay - h - 4
                if x < 8:
                    x = ax
            elif placement == "point":
                x, y = ax, ay
                if y + h > alloc.height - 8:
                    y = ay - h
            else:
                x, y = ax, ay + ah + 4
                if y + h > alloc.height - 8:
                    y = ay - h - 4
        x = max(8, min(x, alloc.width - w - 8))
        y = max(8, min(y, alloc.height - h - 8))
        child.set_margin_start(int(x))
        child.set_margin_top(int(y))
        child.set_size_request(int(w), -1 if h >= nat.height else int(h))
        scrim.show()
        if focus:
            GLib.idle_add(self._focus_first)

    def _focus_first(self):
        if self.child is not None:
            self.child.child_focus(Gtk.DirectionType.TAB_FORWARD)
        return False

    def _draw_scrim(self, w, cr):
        if self.dim:
            cr.set_source_rgba(*theme.P["scrim"])
            cr.paint()
        return False

    def _scrim_press(self, _w, _ev):
        self.close()
        return True

    def close(self):
        if self.child is None:
            return
        child, scrim, cb, focus = self.child, self.scrim, self.on_close, self.restore_focus
        self.child = self.scrim = self.on_close = self.restore_focus = None
        self.overlay.remove(child)
        self.overlay.remove(scrim)
        child.destroy()
        scrim.destroy()
        if focus is not None and focus.get_mapped():
            focus.grab_focus()
        if cb:
            cb()

    def anchor_of(self, widget, x=None, y=None):
        """Lage eines Widgets (oder eines Punkts darin) im Overlay."""
        a = widget.get_allocation()
        res = widget.translate_coordinates(self.overlay, 0, 0)
        if not res:
            return (0, 0, 0, 0)
        ox, oy = res
        if x is not None:
            return (ox + x, oy + y, 0, 0)
        return (ox, oy, a.width, a.height)


def popup_menu(layer, items, anchor, placement="point", min_width=220):
    """items: [(icon|None, text, callback, enabled=True)] oder None für Trenner."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    add_class(box, "popup")
    box.set_size_request(min_width, -1)
    has_icons = any(it and it[0] for it in items)
    for it in items:
        if it is None:
            box.pack_start(Gtk.Separator(), False, False, 0)
            continue
        icon, text, cb = it[0], it[1], it[2]
        enabled = it[3] if len(it) > 3 else True
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        add_class(btn, "item")
        inner = Gtk.Box(spacing=12)
        if has_icons:
            if icon:
                inner.pack_start(icons.fluent(icon, 16), False, False, 0)
            else:
                spacer = Gtk.Box()
                spacer.set_size_request(16, 16)
                inner.pack_start(spacer, False, False, 0)
        inner.pack_start(label(text, ellipsize=False), True, True, 0)
        btn.add(inner)
        btn.set_sensitive(enabled)

        def clicked(_b, cb=cb):
            layer.close()
            cb()
        btn.connect("clicked", clicked)
        box.pack_start(btn, False, False, 0)
    layer.open(box, anchor=anchor, placement=placement)
    return box
