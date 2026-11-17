"""Drag & Drop für Dateien wie im Windows-11-Explorer.

Quelle: Dateiliste (alle Ansichten). Ziele: Ordner und freie Fläche der
Dateiliste, Navigationsbereich (Verweilen klappt auf), Brotkrumen der
Adressleiste, Tabs (Verweilen wechselt den Tab). Mit anderen Programmen geht
es in beide Richtungen über text/uri-list.

Aktion wie unter Windows: gleiches Laufwerk → verschieben, sonst kopieren;
Strg = kopieren, Umschalt = verschieben, Alt oder Strg+Umschalt =
Verknüpfung; mit der rechten Maustaste gezogen → Menü beim Ablegen. Auf den
Papierkorb gezogen = löschen. Unter dem Mauszeiger steht „→ Nach X verschieben“.
In eine geöffnete (lokale) ZIP-Datei wird kopiert, mit Umschalt verschoben.

    DropTarget(widget, resolve, highlight=None, autoscroll=None)
        resolve(x, y) -> Hit oder None   (Widget-Koordinaten)
    source_begin(context, files, pixbuf, button) / source_data_get / source_end
"""
import os

import cairo
from gi.repository import Gdk, Gio, GLib, Gtk

from . import fileops
from .menu import WinMenu

COPY, MOVE, LINK, ASK = (Gdk.DragAction.COPY, Gdk.DragAction.MOVE,
                         Gdk.DragAction.LINK, Gdk.DragAction.ASK)
ACTIONS = COPY | MOVE | LINK | ASK
URI_LIST = "text/uri-list"
VERB = {MOVE: "Nach {} verschieben", COPY: "Nach {} kopieren",
        LINK: "Verknüpfung in {} erstellen"}
GLYPH = {MOVE: "→", COPY: "+", LINK: "↗"}
_NO_DROP = ("recent:", "computer:", "network:", "burn:", "x-network-view:", "archive:", "volume:")


def targets():
    return [Gtk.TargetEntry.new(URI_LIST, 0, 0)]


def target_list():
    return Gtk.TargetList.new(targets())


def droppable(loc):
    """Kann man in diesen Ort etwas ablegen? (in Archive nur in eine lokale ZIP-Datei)"""
    if loc and loc.startswith("archive:"):
        from . import archive
        return archive.writable(loc)
    return bool(loc) and "://" in loc and not loc.startswith(_NO_DROP)


def _decide_zip(acts, files, dest):
    """Ablegen in eine geöffnete ZIP-Datei: wie Windows immer kopieren,
    Umschalt = verschieben (Quellen nach dem Hinzufügen löschen)."""
    from . import archive
    zf = archive.zip_file(dest.get_uri())
    if not archive.writable(dest.get_uri()) or any(_is_trash(f) for f in files) \
            or any(zf.equal(f) or zf.has_prefix(f) for f in files):
        return 0, 0
    if all(f.get_parent() is not None and f.get_parent().equal(dest) for f in files):
        return 0, 0          # liegt schon dort
    from_archive = any(f.get_uri().startswith("archive:") for f in files)
    if acts & MOVE and not acts & COPY and not from_archive:
        return MOVE, MOVE
    return (COPY, COPY) if acts & COPY else (0, 0)


class Hit:
    """Ablageziel unter dem Mauszeiger. dest=None: nur Verweilen (z. B. Tab
    mit „Start“), aber nichts ablegen."""

    def __init__(self, dest, name, key=None, on_hover=None, data=None):
        self.dest = dest
        self.name = name
        self.key = key if key is not None else (dest.get_uri() if dest else name)
        self.on_hover = on_hover
        self.data = data


# ======================================================================
# Quelle
# ======================================================================
_drag = None    # Ziehvorgang aus FileFenster: dict(files, icon, button)


class DragIcon(Gtk.Window):
    """Bild am Mauszeiger: Symbol mit Anzahl und darunter der Aktionstext."""
    HOT = (32, 34)

    def __init__(self, pixbuf, count):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.get_style_context().add_class("drag-icon")
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
            self.set_app_paintable(True)
            self.connect("draw", self._clear)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        ov = Gtk.Overlay()
        img = Gtk.Image.new_from_pixbuf(pixbuf) if pixbuf is not None else Gtk.Image()
        img.set_size_request(64, 64)
        img.set_opacity(0.85)
        ov.add(img)
        ov.set_halign(Gtk.Align.START)
        if count > 1:
            badge = Gtk.Label(label=str(count))
            badge.get_style_context().add_class("drag-count")
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.START)
            ov.add_overlay(badge)
        box.pack_start(ov, False, False, 0)
        self.label = Gtk.Label()
        self.label.get_style_context().add_class("drag-label")
        self.label.set_halign(Gtk.Align.START)
        self.label.set_margin_start(self.HOT[0] - 4)
        box.pack_start(self.label, False, False, 0)
        self.add(box)
        box.show_all()
        self.label.hide()

    @staticmethod
    def _clear(_w, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    def set_action(self, action, name):
        if action not in VERB or not name:
            self.label.hide()
            return
        pre, post = VERB[action].split("{}")
        esc = GLib.markup_escape_text
        self.label.set_markup(f"{GLYPH[action]}  {esc(pre)}<b>{esc(name)}</b>{esc(post)}")
        self.label.show()


def source_begin(context, files, pixbuf, button=1):
    global _drag
    source_end()
    icon = DragIcon(pixbuf, len(files))
    _drag = {"files": list(files), "icon": icon, "button": button}
    Gtk.drag_set_icon_widget(context, icon, *DragIcon.HOT)


def source_data_get(selection):
    if _drag:
        selection.set_uris([f.get_uri() for f in _drag["files"]])


def source_end():
    global _drag
    if _drag:
        _drag["icon"].destroy()
    _drag = None


def dragging():
    return _drag is not None


# ======================================================================
# Aktion festlegen und ausführen
# ======================================================================
_fs_cache = {}
_WEB = ("http:", "https:")


def _real(gfile):
    """Lokaler Pfad mit aufgelösten Verknüpfungen (Ordner einer Verknüpfung
    zählt als Ordner) oder None."""
    path = gfile.get_path() if gfile.get_uri().startswith("file:") else None
    return os.path.realpath(path) if path else None


def _inside(dest, f):
    """Liegt dest in f (oder ist f) – auch über einen verknüpften Pfad?"""
    return fileops.is_inside(dest, f)


def _fs(gfile):
    uri = gfile.get_uri()
    if uri.startswith(_WEB):
        return None      # Link aus dem Browser: nie das gleiche Laufwerk (und keine
                         # blockierende Netzabfrage bei jeder Mausbewegung)
    if uri not in _fs_cache:
        if len(_fs_cache) > 200:
            _fs_cache.clear()
        _fs_cache[uri] = fileops._fs_id(gfile)
    return _fs_cache[uri]


def _is_trash(gfile):
    return gfile.get_uri().startswith("trash:")


def default_action(files, dest):
    """Windows: gleiches Laufwerk → verschieben, sonst kopieren."""
    if any(_is_trash(f) for f in files):
        return MOVE
    a, b = _fs(files[0]), _fs(dest)
    return MOVE if a is not None and a == b else COPY


def from_browser(context):
    """Kommt das Ziehen aus einem Webbrowser? Eine Webseite kann beliebige
    file://-Adressen ins Ziehen legen – solche Dateien nie verschieben/löschen."""
    try:
        names = {a.name() for a in context.list_targets()}
    except Exception:
        return False
    return fileops.browser_targets(names)


def decide(context, files, dest):
    """→ (Aktion, angezeigte Aktion). 0 = hier nicht ablegen."""
    if not files or dest is None:
        return 0, 0
    for f in files:
        if _inside(dest, f):
            return 0, 0      # in sich selbst oder einen eigenen Unterordner
    acts = context.get_actions()
    if from_browser(context):            # nur kopieren (oder verknüpfen)
        acts = Gdk.DragAction(int(acts) & ~int(MOVE | ASK))
    if dest.get_uri().startswith("archive:"):
        return _decide_zip(acts, files, dest)
    if any(f.get_uri().startswith("archive:") for f in files):
        # aus einer geöffneten ZIP-Datei kann man nur herauskopieren
        return (COPY, COPY) if acts & COPY and not _is_trash(dest) else (0, 0)
    if _is_trash(dest):      # Papierkorb: immer löschen
        ok = acts & MOVE and not all(_is_trash(f) for f in files)
        return (MOVE, MOVE) if ok else (0, 0)
    default = default_action(files, dest)
    if _drag and _drag["button"] == 3 and acts & ASK:
        return ASK, default          # mit rechts gezogen → Menü
    if context.get_suggested_action() == ASK and acts & ASK:
        if _drag and _drag["button"] == 1 and acts & LINK:
            return LINK, LINK        # Alt + linke Maustaste = Verknüpfung (Windows)
        return ASK, default
    basic = [a for a in (COPY, MOVE, LINK) if acts & a]
    if not basic:
        return 0, 0
    action = basic[0] if len(basic) == 1 else (default if acts & default else basic[0])
    if action == MOVE and all(f.get_parent() is not None and f.get_parent().equal(dest)
                              for f in files):
        return 0, 0          # liegt schon dort
    return action, action


def perform(files, dest, action, widget, x=0, y=0):
    parent = widget.get_toplevel()
    if not isinstance(parent, Gtk.Window):
        parent = None
    if dest.get_uri().startswith("archive:"):
        from . import archive    # in eine geöffnete ZIP-Datei aufnehmen
        archive.zip_add(files, dest, move=action == MOVE, parent=parent)
    elif action == ASK:
        _ask(files, dest, widget, x, y)
    elif _is_trash(dest):
        fileops.delete(files, parent=parent)
    elif action == LINK:
        fileops.make_links(files, dest, parent=parent)
    elif action in (COPY, MOVE):
        fileops.transfer(files, dest, move=action == MOVE, parent=parent,
                         record=not any(_is_trash(f) for f in files))
    return False


def _ask(files, dest, widget, x, y):
    """Menü nach Ziehen mit der rechten Maustaste (wie Windows)."""
    default = default_action(files, dest)
    m = WinMenu()
    m.item("Hierher kopieren", lambda *_: perform(files, dest, COPY, widget), bold=default == COPY)
    m.item("Hierher verschieben", lambda *_: perform(files, dest, MOVE, widget), bold=default == MOVE)
    m.item("Verknüpfungen hier erstellen", lambda *_: perform(files, dest, LINK, widget))
    m.separator()
    m.item("Abbrechen")
    top = widget.get_toplevel()
    tx, ty = widget.translate_coordinates(top, int(x), int(y)) or (0, 0)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = tx, ty, 1, 1
    m.popup_at_rect(top.get_window(), rect, attach_to=widget)
    widget._dnd_menu = m      # am Leben halten


# ======================================================================
# Ziel
# ======================================================================
def paint_highlight(cr, x, y, w, h):
    """Ablageziel einfärben (wie Hover/Auswahl unter Windows)."""
    from . import theme
    sel = Gdk.RGBA()
    sel.parse(theme.P["selected"])
    border = Gdk.RGBA()
    border.parse(theme.P["selected_border"])
    cr.save()
    cr.set_source_rgba(sel.red, sel.green, sel.blue, 0.55)
    cr.rectangle(x, y, w, h)
    cr.fill()
    cr.set_source_rgba(border.red, border.green, border.blue, 1.0)
    cr.set_line_width(1)
    cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
    cr.stroke()
    cr.restore()


class DropTarget:
    HOVER_MS = 700
    EDGE = 28

    def __init__(self, widget, resolve, highlight=None, autoscroll=None):
        """highlight(hit oder None); autoscroll(x, y) -> (adjustment, Richtung)."""
        self.widget = widget
        self.resolve = resolve
        self.highlight = highlight
        self.autoscroll = autoscroll
        self._ext = None          # (context, files) bei fremden Programmen
        self._pending = None      # "peek" oder (x, y) beim Ablegen
        self._last = (0, 0)
        self._hit_key = None
        self._hover_id = 0
        self._scroll = None
        self._scroll_id = 0
        widget.drag_dest_set(0, targets(), ACTIONS)
        widget.connect("drag-motion", self._on_motion)
        widget.connect("drag-leave", self._on_leave)
        widget.connect("drag-drop", self._on_drop)
        widget.connect("drag-data-received", self._on_received)

    def _files(self, context):
        if _drag and Gtk.drag_get_source_widget(context) is not None:
            return _drag["files"]
        if self._ext and self._ext[0] is context:
            return self._ext[1]
        return None

    def _request(self, context, time):
        self.widget.drag_get_data(context, Gdk.Atom.intern(URI_LIST, False), time)

    def _on_motion(self, widget, context, x, y, time):
        self._last = (x, y)
        files = self._files(context)
        if files is None:
            if self._pending is None:
                self._pending = "peek"
                self._request(context, time)
            return True
        self._update(context, files, x, y, time)
        return True

    def _update(self, context, files, x, y, time):
        hit = self.resolve(x, y)
        self._set_hit(hit)
        self._update_scroll(x, y)
        action, shown = decide(context, files, hit.dest) if hit else (0, 0)
        Gdk.drag_status(context, action, time)
        if _drag:
            _drag["icon"].set_action(shown if action else 0, hit.name if hit else None)

    def _set_hit(self, hit):
        key = hit.key if hit else None
        if key == self._hit_key:
            return
        self._hit_key = key
        if self.highlight:
            self.highlight(hit)
        if self._hover_id:
            GLib.source_remove(self._hover_id)
            self._hover_id = 0
        if hit is not None and hit.on_hover is not None:
            self._hover_id = GLib.timeout_add(self.HOVER_MS, self._fire_hover, hit)

    def _fire_hover(self, hit):
        self._hover_id = 0
        try:
            hit.on_hover()
        except Exception as exc:  # Ziel kann inzwischen weg sein
            print("[filefenster] Ziehen/Verweilen:", exc)
        return False

    def _update_scroll(self, x, y):
        self._scroll = self.autoscroll(x, y) if self.autoscroll else None
        if self._scroll and self._scroll[1] and not self._scroll_id:
            self._scroll_id = GLib.timeout_add(40, self._scroll_tick)

    def _scroll_tick(self):
        if not self._scroll or not self._scroll[1]:
            self._scroll_id = 0
            return False
        adj, d = self._scroll
        top = adj.get_upper() - adj.get_page_size()
        adj.set_value(max(adj.get_lower(), min(top, adj.get_value() + d * 18)))
        return True

    def _stop(self):
        self._set_hit(None)
        self._scroll = None
        if _drag:
            _drag["icon"].set_action(0, None)

    def _on_leave(self, widget, context, time):
        self._stop()
        if self._pending == "peek":
            self._pending = None

    def _on_drop(self, widget, context, x, y, time):
        files = self._files(context)
        if files is None:
            self._pending = (x, y)
            self._request(context, time)
            return True
        self._finish(context, files, x, y, time)
        return True

    def _finish(self, context, files, x, y, time):
        hit = self.resolve(x, y)
        action = decide(context, files, hit.dest)[0] if hit else 0
        Gtk.drag_finish(context, bool(action), False, time)
        self._stop()
        self._ext = None
        if action:
            GLib.idle_add(perform, list(files), hit.dest, action, self.widget, x, y)

    def _on_received(self, widget, context, x, y, data, info, time):
        uris = data.get_uris() if data is not None else []
        files = [Gio.File.new_for_uri(u) for u in uris or [] if u]
        pending, self._pending = self._pending, None
        self._ext = (context, files)
        if isinstance(pending, tuple):
            if files:
                self._finish(context, files, x, y, time)
            else:
                Gtk.drag_finish(context, False, False, time)
            return
        lx, ly = self._last
        if files:
            self._update(context, files, lx, ly, time)
        else:
            Gdk.drag_status(context, 0, time)
