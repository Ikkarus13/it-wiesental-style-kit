"""Hauptfenster: Tabs in der Titelleiste, Adresszeile, Befehlsleiste,
Navigationsbereich, Inhalt (pro Tab), Detailbereich, Statusleiste,
Tastenkürzel. Ein Tab ist zugleich der `host` seiner Ansichten.
"""
import os

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import host as hostmod
from . import dnd, folderoptions, icons, places, theme, undo, update_ui
from .config import get_config
from .menu import WinMenu

# Module der Agenten (mit Platzhaltern, falls noch nicht vorhanden)
try:
    from .folderview import FolderView
except Exception as exc:  # pragma: no cover
    print("[filefenster] folderview fehlt:", exc)
    FolderView = None
try:
    from .homeview import HomeView
except Exception as exc:
    print("[filefenster] homeview fehlt:", exc)
    HomeView = None
try:
    from .thispcview import ThisPCView
except Exception as exc:
    print("[filefenster] thispcview fehlt:", exc)
    ThisPCView = None
try:
    from .gallery import GalleryView
except Exception as exc:
    print("[filefenster] gallery fehlt:", exc)
    GalleryView = None
try:
    from .navpane import NavPane
except Exception as exc:
    print("[filefenster] navpane fehlt:", exc)
    NavPane = None
try:
    from .navrow import NavRow
except Exception as exc:
    print("[filefenster] navrow fehlt:", exc)
    NavRow = None
try:
    from .detailspane import DetailsPane
except Exception as exc:
    print("[filefenster] detailspane fehlt:", exc)
    DetailsPane = None
try:
    from .previewpane import PreviewPane
except Exception as exc:
    print("[filefenster] previewpane fehlt:", exc)
    PreviewPane = None
try:
    from . import propsdialog
except Exception as exc:
    print("[filefenster] propsdialog fehlt:", exc)
    propsdialog = None


VIEW_MODES = [
    # mode, Bezeichnung, Icon, Strg+Umschalt+<n>
    ("xl", "Extra grosse Symbole", "grid", 1),
    ("large", "Grosse Symbole", "grid", 2),
    ("medium", "Mittelgrosse Symbole", "grid_dots", 3),
    ("small", "Kleine Symbole", "apps_list", 4),
    ("list", "Liste", "list", 5),
    ("details", "Details", "apps_list_detail", 6),
    ("tiles", "Kacheln", "square_multiple", 7),
    ("content", "Inhalt", "content_view", 8),
]
VIEW_ICON = {m: ic for m, _l, ic, _n in VIEW_MODES}


def _placeholder(text):
    lab = Gtk.Label(label=text)
    lab.get_style_context().add_class("placeholder")
    return lab


# ---------------------------------------------------------------------------
class Tab:
    """Ein Tab: Verlauf, eigene Ansichten. Dient als `host` (siehe host.py)."""

    def __init__(self, win, loc):
        self.win = win
        self.window = win
        self.config = win.config
        self.location = None
        self.back = []
        self.forward = []
        self.search_text = ""
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.get_style_context().add_class("content-stack")
        self.views = {}
        self.widget = TabWidget(self)
        self.navigate(loc, record=False)

    # -- Ansichten -----------------------------------------------------
    def _view(self, kind):
        if kind not in self.views:
            cls = {"home": HomeView, "thispc": ThisPCView, "folder": FolderView,
                   "gallery": GalleryView}[kind]
            if cls is None:
                w = _placeholder(f"({kind}-Ansicht fehlt noch)")
            else:
                w = cls(self)
            w.show_all()
            self.views[kind] = w
            self.stack.add_named(w, kind)
        return self.views[kind]

    def view_kind(self):
        if self.location == places.HOME:
            return "home"
        if self.location == places.THISPC:
            return "thispc"
        if self.location == places.GALLERY:
            return "gallery"
        return "folder"

    def current_view(self):
        return self.views.get(self.view_kind())

    def folder_view(self):
        v = self.views.get("folder")
        if self.view_kind() == "folder" and v is not None and FolderView is not None \
                and isinstance(v, FolderView):
            return v
        return None

    def command_view(self):
        """Ansicht, auf die Befehlsleiste und Tastenkürzel wirken: die Dateiliste
        oder eine Ansicht mit is_command_target (Katalog)."""
        fv = self.folder_view()
        if fv is not None:
            return fv
        v = self.views.get(self.view_kind())
        return v if getattr(v, "is_command_target", False) else None

    # -- Navigation ----------------------------------------------------
    def navigate(self, loc, record=True):
        if not loc:
            return
        if loc.startswith("volume:"):
            self._mount_volume(loc, record)
            return
        self._end_search()
        if record and self.location is not None and loc != self.location:
            self.back.append(self.location)
            del self.back[:-100]
            self.forward.clear()
        self._show(loc)

    def _end_search(self):
        """Ortswechsel (auch Zurück/Vor) beendet eine Suche wie im Explorer."""
        if self.search_text:
            self.search_text = ""
            fv = self.views.get("folder")
            if fv is not None and hasattr(fv, "stop_search") and fv.is_searching():
                fv.stop_search()
            if self.win.current is self and self.win.navrow is not None:
                self.win.navrow.clear_search()

    def _mount_volume(self, loc, record):
        """Nicht eingehängtes Laufwerk einhängen (fragt ggf. nach dem Passwort)
        und dann öffnen."""
        vol = places.volume_for(loc)
        if vol is None:
            return
        if vol.get_mount() is not None:
            self.navigate(vol.get_mount().get_root().get_uri(), record)
            return

        def done(v, res):
            try:
                v.mount_finish(res)
            except GLib.Error as exc:
                if not exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    from . import fileops
                    fileops.show_errors(self.win, "Das Laufwerk konnte nicht geöffnet werden.",
                                        [(v.get_name(), exc.message)])
                return
            mount = v.get_mount()
            if mount is not None and self in self.win.tabs:     # Tab inzwischen zu?
                self.navigate(mount.get_root().get_uri(), record)
        vol.mount(Gio.MountMountFlags.NONE, Gtk.MountOperation.new(self.win), None, done)

    def _show(self, loc):
        self.location = loc
        kind = self.view_kind()
        view = self._view(kind)
        if kind == "folder" and hasattr(view, "load"):
            view.load(loc)
            folderoptions.record_visit(self.config, loc)
        elif hasattr(view, "refresh"):
            view.refresh()
        self.stack.set_visible_child_name(kind)
        self.widget.update()
        self.win.on_tab_changed(self)

    def go_back(self):
        if self.back:
            self._end_search()
            self.forward.append(self.location)
            self._show(self.back.pop())

    def go_forward(self):
        if self.forward:
            self._end_search()
            self.back.append(self.location)
            self._show(self.forward.pop())

    def go_up(self):
        p = places.parent(self.location)
        if p:
            self.navigate(p)

    def refresh(self):
        v = self.current_view()
        if v is None:
            return
        if hasattr(v, "reload"):
            v.reload()
        elif hasattr(v, "refresh"):
            v.refresh()

    def title(self):
        if self.search_text:
            return f"Suchergebnisse in {places.display_name(self.location)}"
        if self.config.get("full_path_title", False):
            return folderoptions.full_path_text(self.location)
        return places.display_name(self.location)

    # -- host-Schnittstelle ---------------------------------------------
    def open_in_new_tab(self, loc):
        self.win.new_tab(loc, select=False)

    def open_in_new_window(self, loc):
        self.win.app_new_window(loc)

    def state_changed(self):
        if self.win.current is self:
            self.win.update_state()

    def show_properties(self, gfiles):
        self.win.show_properties(gfiles)

    def is_pinned(self, uri):
        return self.win.is_pinned(uri)

    def pin(self, uri):
        self.win.pin(uri)

    def unpin(self, uri):
        self.win.unpin(uri)

    def open_terminal(self, path):
        hostmod.open_terminal(path)

    def launch_file(self, gfile):
        return hostmod.launch_file(gfile, self.win)

    def copy_files(self, gfiles, cut=False):
        self.win.copy_files(gfiles, cut)

    def delete_files(self, gfiles):
        self.win.delete_files(gfiles)

    def start_search(self, query):
        self.win.start_search(query)

    def stop_search(self):
        self.win.stop_search()


# ---------------------------------------------------------------------------
class TabWidget(Gtk.EventBox):
    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.set_visible_window(False)
        self.get_style_context().add_class("tab")
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self._drag = None        # Tab ziehen: Startpunkt, aktiv, ausserhalb der Leiste
        self._ghost = None
        # EventBox ohne eigenes Fenster zeichnet keinen Hintergrund → Box "tab-bg"
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bg.get_style_context().add_class("tab-bg")
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.get_style_context().add_class("tab-sep")
        outer.pack_start(bg, True, True, 0)
        outer.pack_start(sep, False, False, 0)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        inner.get_style_context().add_class("tab-inner")
        bg.pack_start(inner, True, True, 0)
        self.icon = Gtk.Image()
        self.icon.get_style_context().add_class("tab-icon")
        self.label = Gtk.Label(xalign=0)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_width_chars(1)
        self.label.get_style_context().add_class("tab-label")
        self.close_btn = Gtk.Button()
        self.close_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.close_btn.get_style_context().add_class("tab-close")
        self.close_btn.add(icons.fluent("dismiss", 12))
        self.close_btn.set_tooltip_text("Registerkarte schliessen (Strg+W)")
        self.close_btn.set_valign(Gtk.Align.CENTER)
        self.close_btn.connect("clicked", lambda *_: self.tab.win.close_tab(self.tab))
        inner.pack_start(self.icon, False, False, 0)
        inner.pack_start(self.label, True, True, 0)
        inner.pack_end(self.close_btn, False, False, 0)
        self.add(outer)
        self.connect("button-press-event", self._on_press)
        # Linke Taste über eine Geste: die Tab-Leiste ist die Titelleiste, und
        # GTK würde beim Ziehen sonst das ganze Fenster verschieben. Wer die
        # Sequenz beansprucht (CLAIMED), schliesst GTKs Fenster-Ziehen aus.
        self._gesture = Gtk.GestureDrag.new(self)
        self._gesture.set_button(1)
        self._gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._gesture.connect("drag-begin", self._on_drag_begin)
        self._gesture.connect("drag-update", lambda *_: self._on_motion(None, Gtk.get_current_event()))
        self._gesture.connect("drag-end", lambda *_: self._on_release(None, Gtk.get_current_event()))
        self.show_all()
        # Dateien auf den Tab ziehen: Verweilen wechselt den Tab (wie Windows 11)
        dnd.DropTarget(self, self._drop_hit, highlight=self._drop_mark)

    def _drop_hit(self, _x, _y):
        loc = self.tab.location
        dest = Gio.File.new_for_uri(loc) if dnd.droppable(loc) else None
        hover = None if self.tab.win.current is self.tab else (
            lambda: self.tab.win.select_tab(self.tab))
        return dnd.Hit(dest, self.tab.title(), key=f"tab:{loc}", on_hover=hover)

    def _drop_mark(self, hit):
        ctx = self.get_style_context()
        if hit is not None:
            ctx.add_class("drop-hover")
        else:
            ctx.remove_class("drop-hover")

    def update(self):
        loc = self.tab.location
        kind = places.loc_icon_kind(loc) if loc else "folder"
        self.icon.set_from_pixbuf(icons.place_pixbuf(kind, 16))
        title = self.tab.title()
        self.label.set_text(title)
        self.set_tooltip_text(title)

    # -- Tab ziehen (wie Windows 11): umsortieren, herausziehen = neues
    #    Fenster, auf die Leiste eines anderen Fensters = dorthin verschieben
    def _on_drag_begin(self, gesture, x, y):
        bx, by = self.close_btn.translate_coordinates(self, 0, 0)
        if bx <= x < bx + self.close_btn.get_allocated_width() and \
                by <= y < by + self.close_btn.get_allocated_height():
            gesture.set_state(Gtk.EventSequenceState.DENIED)   # Klick gehört dem ✕
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        ev = Gtk.get_current_event()
        self.tab.win.select_tab(self.tab)
        if ev is not None:
            self._drag = {"x": ev.x_root, "y": ev.y_root, "active": False, "out": False}

    def _on_motion(self, _w, ev):
        d = self._drag
        if d is None or ev is None or not hasattr(ev, "x_root"):
            return False
        win = self.tab.win
        if not d["active"]:
            if abs(ev.x_root - d["x"]) < 8 and abs(ev.y_root - d["y"]) < 8:
                return True
            d["active"] = True
            if len(win.tabs) == 1 and abs(ev.y_root - d["y"]) >= abs(ev.x_root - d["x"]):
                # einziger Tab: das Fenster selbst ziehen
                self._drag = None
                win.begin_move_drag(1, int(ev.x_root), int(ev.y_root), ev.time)
                # Loslassen geht an den Fenstermanager: Geste zurücksetzen, sonst
                # schluckt sie den nächsten Klick auf diesen Tab (auch auf sein ✕)
                GLib.idle_add(lambda: (self._gesture.reset(), False)[1])
                return True
        out = not win.over_tab_bar(ev.x_root, ev.y_root, margin=36)
        if out != d["out"]:
            d["out"] = out
            self._show_ghost(out)
        if out:
            self._ghost.move(int(ev.x_root) + 12, int(ev.y_root) + 8)
        else:
            win.move_tab_to_x(self.tab, ev.x_root)
        return True

    def _on_release(self, _w, ev):
        d, self._drag = self._drag, None
        self._show_ghost(False)
        if d is None or ev is None or not hasattr(ev, "x_root") or not d["active"]:
            return False
        win = self.tab.win
        if d["out"]:
            target = win.tab_drop_window(ev.x_root, ev.y_root)
            if target is not None:
                target.adopt_tab(self.tab, ev.x_root)
            elif len(win.tabs) > 1:
                win.detach_tab(self.tab, ev.x_root, ev.y_root)
        return True

    def _show_ghost(self, on):
        if not on:
            if self._ghost is not None:
                self._ghost.destroy()
                self._ghost = None
            return
        g = Gtk.Window(type=Gtk.WindowType.POPUP)
        g.get_style_context().add_class("filefenster")
        g.get_style_context().add_class("tab-ghost")
        box = Gtk.Box(spacing=8)
        box.set_border_width(8)
        box.pack_start(Gtk.Image.new_from_pixbuf(self.icon.get_pixbuf()), False, False, 0)
        box.pack_start(Gtk.Label(label=self.label.get_text()), False, False, 0)
        g.add(box)
        g.set_opacity(0.92)
        g.show_all()
        self._ghost = g

    def _on_press(self, _w, ev):
        win = self.tab.win
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if ev.button == 1:
            return True          # macht die Geste (_on_drag_begin)
        if ev.button == 2:
            win.close_tab(self.tab)
            return True
        if ev.button == 3:
            m = WinMenu()
            m.item("Neue Registerkarte", lambda: win.new_tab(folderoptions.start_location(win.config)),
                   icon="tab_add", accel="Strg+T")
            m.item("Registerkarte duplizieren", lambda: win.new_tab(self.tab.location), icon="copy")
            m.item("In neues Fenster verschieben",
                   lambda: (win.app_new_window(self.tab.location), win.close_tab(self.tab)),
                   icon="window_new", sensitive=len(win.tabs) > 1)
            m.separator()
            m.item("Registerkarte schliessen", lambda: win.close_tab(self.tab), icon="dismiss", accel="Strg+W")
            m.item("Andere Registerkarten schliessen", lambda: win.close_other_tabs(self.tab),
                   sensitive=len(win.tabs) > 1)
            m.item("Registerkarten rechts schliessen", lambda: win.close_tabs_right(self.tab),
                   sensitive=win.tabs.index(self.tab) < len(win.tabs) - 1)
            m.popup_at_pointer(ev, attach_to=self)
            return True
        return False


# ---------------------------------------------------------------------------
def _no_min_width(child):
    """child in einen Rahmen ohne Mindestbreite: bei Platzmangel wird abgeschnitten,
    statt dass GTK das ganze Fenster breiter macht (Breite sonst wie child)."""
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
    sw.set_propagate_natural_width(True)
    sw.set_propagate_natural_height(True)
    sw.set_shadow_type(Gtk.ShadowType.NONE)
    sw.set_valign(child.get_valign())
    sw.get_style_context().add_class("no-min-width")
    sw.add(child)
    vp = sw.get_child()
    if isinstance(vp, Gtk.Viewport):
        vp.set_shadow_type(Gtk.ShadowType.NONE)
    return sw


def _cmd_button(icon=None, label=None, dropdown=False, tooltip=None, toggle=False):
    btn = Gtk.ToggleButton() if toggle else Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_focus_on_click(False)
    ctx = btn.get_style_context()
    ctx.add_class("cmd")
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    box.set_halign(Gtk.Align.CENTER)    # Symbol mittig (Knopf ist breiter: min-width 40px)
    img = None
    if icon:
        img = icons.fluent(icon, 16)
        img.get_style_context().add_class("cmd-icon")
        box.pack_start(img, False, False, 0)
    lab = None
    if label:
        lab = Gtk.Label(label=label)
        lab.get_style_context().add_class("cmd-label")
        box.pack_start(lab, False, False, 0)
        ctx.add_class("with-label")
    else:
        ctx.add_class("icon-only")
    if dropdown:
        arr = icons.fluent("chevron_down", 12)
        arr.get_style_context().add_class("cmd-arrow")
        box.pack_start(arr, False, False, 0)
    btn.add(box)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    btn._img = img
    btn._box = box
    btn._label = lab
    return btn


class ExplorerWindow(Gtk.ApplicationWindow):
    def __init__(self, app, loc=None):
        super().__init__(application=app)
        self.app = app
        self.config = get_config()
        self.tabs = []
        self.current = None
        self.navrow = None
        self._tab_width = 0
        self._pane_lock = False      # Detail-Knopf wird nur nachgeführt
        self._shown_right = None     # sichtbarer rechter Bereich: "details", "preview", None
        self._fix_right_pos = False
        self.get_style_context().add_class("filefenster")
        self.set_icon(icons.folder_pixbuf(64))
        w, h = self.config.get("window_size", [1240, 780])
        self.set_default_size(w, h)
        if self.config.get("window_maximized"):
            self.maximize()

        self._build_titlebar()
        self._build_body()

        self.connect("key-press-event", self._on_key)
        self.connect("button-press-event", self._on_button)
        self.connect("configure-event", self._on_configure)
        self.connect("window-state-event", self._on_state)
        self.connect("delete-event", self._on_delete)
        self.config.connect_changed(self._on_config_changed)
        self.connect("destroy", lambda *_: self.config.disconnect_changed(self._on_config_changed))

        self.new_tab(loc or folderoptions.start_location(self.config))
        self.show_all()
        self._apply_right_pane()
        self._apply_statusbar()
        update_ui.attach_window(self)    # Hinweispunkt am „···“-Knopf

    # ================================================================ Aufbau
    def _build_titlebar(self):
        bar = Gtk.EventBox()
        bar.get_style_context().add_class("tabstrip")
        bar.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        bar.connect("button-press-event", self._on_titlebar_press)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tabs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tabs_box.get_style_context().add_class("tabs")
        self.tabs_box.set_valign(Gtk.Align.END)
        # Viele Tabs dürfen das Fenster nicht aufziehen: bei Platzmangel abschneiden
        box.pack_start(_no_min_width(self.tabs_box), False, False, 0)

        self.newtab_btn = Gtk.Button()
        self.newtab_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.newtab_btn.set_focus_on_click(False)
        self.newtab_btn.get_style_context().add_class("newtab")
        self.newtab_btn.add(icons.fluent("add", 16))
        self.newtab_btn.set_tooltip_text("Neue Registerkarte (Strg+T)")
        self.newtab_btn.set_valign(Gtk.Align.CENTER)
        self.newtab_btn.connect("clicked", lambda *_: self.new_tab(folderoptions.start_location(self.config)))
        box.pack_start(self.newtab_btn, False, False, 0)

        filler = Gtk.Box()
        box.pack_start(filler, True, True, 0)

        caps = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        caps.get_style_context().add_class("caption-buttons")
        caps.set_valign(Gtk.Align.START)
        self.min_btn = self._caption_button("minimize", "Minimieren", self.iconify)
        self.max_btn = self._caption_button("maximize", "Maximieren", self._toggle_maximize)
        self.close_btn = self._caption_button("close", "Schliessen", self.close)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            caps.pack_start(b, False, False, 0)
        box.pack_end(caps, False, False, 0)

        bar.add(box)
        bar.connect("size-allocate", self._on_titlebar_alloc)
        self.titlebar = bar
        self.set_titlebar(bar)

    def _caption_button(self, kind, tooltip, action):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class("caption")
        btn.get_style_context().add_class(kind)
        img = icons.caption_image(kind)
        btn.add(img)
        btn._kind = kind
        btn._img = img
        btn.set_tooltip_text(tooltip)
        btn.connect("clicked", lambda *_: action())
        if kind == "close":
            btn.connect("enter-notify-event",
                        lambda *_: img.set_from_pixbuf(icons.caption_pixbuf("close", "#ffffff")))
            btn.connect("leave-notify-event",
                        lambda *_: img.set_from_pixbuf(icons.caption_pixbuf("close", theme.P["icon"])))
        return btn

    def _build_body(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Werkzeugbereich (gleiche Farbe wie aktiver Tab)
        tool = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        tool.get_style_context().add_class("toolbar-area")
        if NavRow is not None:
            self.navrow = NavRow(self)
            tool.pack_start(self.navrow, False, False, 0)
        else:
            tool.pack_start(_placeholder("(Adresszeile fehlt noch)"), False, False, 8)
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("toolbar-sep")
        tool.pack_start(sep, False, False, 0)
        tool.pack_start(self._build_commandbar(), False, False, 0)
        outer.pack_start(tool, False, False, 0)
        bottom = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.get_style_context().add_class("toolbar-bottom")
        outer.pack_start(bottom, False, False, 0)

        # Navigationsbereich | Inhalt | Detailbereich oder Vorschaufenster
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.get_style_context().add_class("main-paned")
        if NavPane is not None:
            self.navpane = NavPane(self)
        else:
            self.navpane = None
        self.paned.pack1(self.navpane or _placeholder("(Navigationsbereich fehlt noch)"),
                         False, False)
        self.paned.set_position(self.config.get("nav_width", 240))
        self.paned.connect("notify::position", self._on_paned_moved)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.content_stack.get_style_context().add_class("content-area")
        self.content_stack.connect("scroll-event", self._on_content_scroll)

        self.details_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.details_paned.get_style_context().add_class("details-paned")
        self.details_paned.pack1(self.content_stack, True, False)
        self.detailspane = DetailsPane(self) if DetailsPane is not None else None
        self.previewpane = PreviewPane(self) if PreviewPane is not None else None
        # rechts entweder Detailbereich oder Vorschaufenster (wie Windows)
        self.right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        for pane in (self.detailspane, self.previewpane):
            if pane is not None:
                self.right_box.pack_start(pane, True, True, 0)
        self.details_paned.pack2(self.right_box, False, False)
        self.details_paned.connect("size-allocate", self._on_details_paned_alloc)
        self.paned.pack2(self.details_paned, True, False)
        outer.pack_start(self.paned, True, True, 0)

        self.statusbar = self._build_statusbar()
        outer.pack_start(self.statusbar, False, False, 0)
        self.add(outer)

    def _build_commandbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.get_style_context().add_class("commandbar")

        # Im Katalog (wie Windows 11): „Sammlung“ statt „Neu“
        self.gallery_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.btn_collection = _cmd_button("image", "Sammlung", dropdown=True,
                                          tooltip="Ordner der Sammlung auswählen oder hinzufügen")
        self.btn_collection.connect("clicked", self._on_collection_menu)
        self.gallery_box.pack_start(self.btn_collection, False, False, 0)
        self.gallery_box.pack_start(self._cmd_sep(), False, False, 0)
        self.gallery_box.show_all()
        self.gallery_box.set_no_show_all(True)
        self.gallery_box.hide()
        bar.pack_start(self.gallery_box, False, False, 0)
        self.new_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.btn_new = _cmd_button("add_circle", "Neu", dropdown=True,
                                   tooltip="Neues Element im aktuellen Ordner erstellen.")
        self.btn_new.connect("clicked", self._on_new_menu)
        self.new_box.pack_start(self.btn_new, False, False, 0)
        self.new_box.pack_start(self._cmd_sep(), False, False, 0)
        self.new_box.show_all()
        self.new_box.set_no_show_all(True)
        bar.pack_start(self.new_box, False, False, 0)

        self.btn_cut = _cmd_button("cut", tooltip="Ausschneiden (Strg+X)")
        self.btn_copy = _cmd_button("copy", tooltip="Kopieren (Strg+C)")
        self.btn_paste = _cmd_button("clipboard_paste", tooltip="Einfügen (Strg+V)")
        self.btn_rename = _cmd_button("rename", tooltip="Umbenennen (F2)")
        self.btn_share = _cmd_button("share", tooltip="Teilen")
        self.btn_delete = _cmd_button("delete", tooltip="Löschen (Entf)")
        self.btn_cut.connect("clicked", lambda *_: self._fv_call("cut"))
        self.btn_copy.connect("clicked", lambda *_: self._fv_call("copy"))
        self.btn_paste.connect("clicked", lambda *_: self._fv_call("paste"))
        self.btn_rename.connect("clicked", lambda *_: self._fv_call("begin_rename"))
        self.btn_share.connect("clicked", self._on_share)
        self.btn_delete.connect("clicked", lambda *_: self._fv_call("delete"))
        for b in (self.btn_cut, self.btn_copy, self.btn_paste, self.btn_rename,
                  self.btn_share, self.btn_delete):
            bar.pack_start(b, False, False, 0)
        bar.pack_start(self._cmd_sep(), False, False, 0)

        # Nur im Papierkorb (wie Windows 11)
        self.trash_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.btn_empty_trash = _cmd_button("delete", "Papierkorb leeren")
        self.btn_empty_trash.connect("clicked", lambda *_: self._fv_call("empty_trash"))
        self.btn_restore = _cmd_button("arrow_undo", "Alle Elemente wiederherstellen")
        self.btn_restore.connect("clicked", lambda *_: self._fv_call("restore_selected_or_all"))
        self.trash_box.pack_start(self.btn_empty_trash, False, False, 0)
        self.trash_box.pack_start(self.btn_restore, False, False, 0)
        self.trash_box.pack_start(self._cmd_sep(), False, False, 0)
        self.trash_box.show_all()
        self.trash_box.set_no_show_all(True)
        self.trash_box.hide()
        bar.pack_start(self.trash_box, False, False, 0)
        # In einer geöffneten ZIP-Datei (wie Windows): „Alle extrahieren“
        self.archive_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.btn_extract_all = _cmd_button("folder_zip", "Alle extrahieren")
        self.btn_extract_all.connect("clicked", lambda *_: self._extract_current())
        self.archive_box.pack_start(self.btn_extract_all, False, False, 0)
        self.archive_box.pack_start(self._cmd_sep(), False, False, 0)
        self.archive_box.show_all()
        self.archive_box.set_no_show_all(True)
        self.archive_box.hide()
        bar.pack_start(self.archive_box, False, False, 0)
        # Während einer Suche (wie Windows 11): „Suchoptionen“ und „Suche schliessen“
        self._search_content = False
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.btn_search_opts = _cmd_button("filter", "Suchoptionen", dropdown=True)
        self.btn_search_opts.connect("clicked", self._on_search_options)
        btn_close = _cmd_button("dismiss", "Suche schliessen")
        btn_close.connect("clicked", lambda *_: self._close_search())
        self.search_box.pack_start(self.btn_search_opts, False, False, 0)
        self.search_box.pack_start(btn_close, False, False, 0)
        self.search_box.pack_start(self._cmd_sep(), False, False, 0)
        self.search_box.show_all()
        self.search_box.set_no_show_all(True)
        self.search_box.hide()
        bar.pack_start(self.search_box, False, False, 0)

        self.btn_sort = _cmd_button("arrow_sort", "Sortieren", dropdown=True,
                                    tooltip="Elemente sortieren")
        self.btn_sort.connect("clicked", self._on_sort_menu)
        self.btn_view = _cmd_button("apps_list_detail", "Anzeigen", dropdown=True,
                                    tooltip="Layout und Anzeigeoptionen")
        self.btn_view.connect("clicked", self._on_view_menu)
        bar.pack_start(self.btn_sort, False, False, 0)
        bar.pack_start(self.btn_view, False, False, 0)
        bar.pack_start(self._cmd_sep(), False, False, 0)

        # Die Knöpfe links dürfen das Fenster nicht aufziehen (je nach Ort kommen
        # welche dazu: ZIP, Papierkorb, Suche) – bei Platzmangel abschneiden;
        # „···“ und „Details“ bleiben immer sichtbar.
        bar.get_style_context().remove_class("commandbar")
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer.get_style_context().add_class("commandbar")
        outer.pack_start(_no_min_width(bar), False, False, 0)

        self.btn_more = _cmd_button("more_horizontal", tooltip="Weitere Optionen anzeigen")
        self.btn_more.connect("clicked", self._on_more_menu)
        outer.pack_start(self.btn_more, False, False, 0)

        self.btn_details = _cmd_button("panel_right", "Details", toggle=True,
                                       tooltip="Detailbereich anzeigen (Alt+Umschalt+P)")
        self.btn_details.get_style_context().add_class("details-toggle")
        self.btn_details.set_active(bool(self.config.get("details_pane"))
                                    and not self.config.get("preview_pane"))
        self.btn_details.connect("toggled", self._on_details_toggled)
        outer.pack_end(self.btn_details, False, False, 0)
        return outer

    @staticmethod
    def _cmd_sep():
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("cmd-sep")
        return s

    def _build_statusbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.get_style_context().add_class("statusbar")
        self.status_count = Gtk.Label(xalign=0)
        self.status_sel = Gtk.Label(xalign=0)
        self.status_sep1 = Gtk.Label(label="|")
        self.status_sep2 = Gtk.Label(label="|")
        self.status_sel.set_no_show_all(True)
        self.status_sep2.set_no_show_all(True)
        for w in (self.status_sep1, self.status_sep2):
            w.get_style_context().add_class("status-sep")
        bar.pack_start(self.status_count, False, False, 0)
        bar.pack_start(self.status_sep1, False, False, 0)
        bar.pack_start(self.status_sel, False, False, 0)
        bar.pack_start(self.status_sep2, False, False, 0)

        self.sb_details = _cmd_button("apps_list_detail", toggle=True,
                                      tooltip="Zeigt Informationen zu jedem Element im Fenster an.")
        self.sb_large = _cmd_button("grid", toggle=True,
                                    tooltip="Zeigt Elemente mithilfe grosser Miniaturansichten an.")
        for b in (self.sb_large, self.sb_details):
            b.get_style_context().add_class("status-toggle")
        self._sb_lock = False
        self.sb_details.connect("toggled", lambda b: self._sb_toggle(b, "details"))
        self.sb_large.connect("toggled", lambda b: self._sb_toggle(b, "large"))
        bar.pack_end(self.sb_large, False, False, 0)
        bar.pack_end(self.sb_details, False, False, 0)
        return bar

    # ================================================================ Tabs
    def new_tab(self, loc, select=True):
        tab = Tab(self, loc)
        idx = (self.tabs.index(self.current) + 1) if (self.current and not select) else len(self.tabs)
        if not select and self.current is not None:
            # Windows: im Hintergrund geöffnete Tabs rechts vom aktuellen
            idx = self.tabs.index(self.current) + 1
            while idx < len(self.tabs) and getattr(self.tabs[idx], "_bg_opened", False):
                idx += 1
            tab._bg_opened = True
        self.tabs.insert(idx, tab)
        self.tabs_box.pack_start(tab.widget, False, False, 0)
        self.tabs_box.reorder_child(tab.widget, idx)
        self.content_stack.add(tab.stack)
        tab.stack.show_all()
        if select or self.current is None:
            self.select_tab(tab)
        self._relayout_tabs()
        self._update_tab_classes()
        return tab

    def select_tab(self, tab):
        if tab not in self.tabs:
            return
        for t in self.tabs:
            t._bg_opened = False
        self.current = tab
        self.content_stack.set_visible_child(tab.stack)
        if self.navrow is not None:
            self.navrow.set_search_text(tab.search_text)
        self._update_tab_classes()
        self.on_tab_changed(tab)
        v = tab.current_view()
        if v is not None and hasattr(v, "focus_view"):
            GLib.idle_add(lambda: (v.focus_view(), False)[1])
        GLib.timeout_add(60, self._scroll_tab_into_view, tab)

    def _scroll_tab_into_view(self, tab):
        """Bei vielen Tabs (abgeschnitten) den gewählten Tab ins Bild rücken."""
        vp = self.tabs_box.get_parent()
        sw = vp.get_parent() if vp is not None else None
        if not isinstance(sw, Gtk.ScrolledWindow) or tab not in self.tabs:
            return False
        adj = sw.get_hadjustment()
        a = tab.widget.get_allocation()
        v, page = adj.get_value(), adj.get_page_size()
        if a.x < v:
            adj.set_value(a.x)
        elif a.x + a.width > v + page:
            adj.set_value(a.x + a.width - page)
        return False

    # -- Tabs zwischen Fenstern ----------------------------------------------
    def over_tab_bar(self, x_root, y_root, margin=0):
        """Liegt der Punkt (Bildschirmkoordinaten) über der Tab-Leiste?"""
        gw = self.get_window()
        if gw is None:
            return False
        _r, ox, oy = gw.get_origin()
        bx, by = self.tabs_box.translate_coordinates(self, 0, 0)
        w = self.get_allocated_width()
        h = self.tabs_box.get_allocated_height()
        return (ox <= x_root <= ox + w) and (oy + by - margin <= y_root <= oy + by + h + margin)

    def move_tab_to_x(self, tab, x_root):
        """Tab beim Ziehen an die Stelle unter dem Mauszeiger setzen."""
        _r, ox, _oy = self.get_window().get_origin()
        x = x_root - ox
        idx = 0
        for t in self.tabs:
            if t is tab:
                continue
            tx, _ty = t.widget.translate_coordinates(self, 0, 0)
            if x > tx + t.widget.get_allocated_width() / 2:
                idx += 1
        if self.tabs.index(tab) != idx:
            self.tabs.remove(tab)
            self.tabs.insert(idx, tab)
            self.tabs_box.reorder_child(tab.widget, idx)
            self._update_tab_classes()

    def tab_drop_window(self, x_root, y_root):
        """Anderes FileFenster-Fenster, über dessen Tab-Leiste losgelassen wurde."""
        for w in self.app.get_windows():
            if w is not self and isinstance(w, ExplorerWindow) and w.get_visible() \
                    and w.over_tab_bar(x_root, y_root, margin=12):
                return w
        return None

    @staticmethod
    def _copy_history(src, dst):
        dst.back, dst.forward = list(src.back), list(src.forward)

    def adopt_tab(self, tab, x_root):
        """Tab aus einem anderen Fenster übernehmen (Ort und Verlauf)."""
        src = tab.win
        new = self.new_tab(tab.location, select=True)
        self._copy_history(tab, new)
        self.move_tab_to_x(new, x_root)
        src.close_tab(tab)
        self.present()

    def detach_tab(self, tab, x_root, y_root):
        """Tab in ein eigenes Fenster an der Mausposition."""
        nw = ExplorerWindow(self.app, tab.location)
        self._copy_history(tab, nw.current)
        nw.move(max(0, int(x_root) - 120), max(0, int(y_root) - 20))
        nw.present()
        self.close_tab(tab)
        return nw

    def close_tab(self, tab):
        if tab not in self.tabs:
            return
        if len(self.tabs) == 1:
            self.close()
            return
        idx = self.tabs.index(tab)
        self.tabs.remove(tab)
        self.tabs_box.remove(tab.widget)
        self.content_stack.remove(tab.stack)
        for v in tab.views.values():
            if hasattr(v, "stop_search"):
                try:
                    v.stop_search()
                except Exception:
                    pass
        if self.current is tab:
            self.select_tab(self.tabs[min(idx, len(self.tabs) - 1)])
        self._relayout_tabs()
        self._update_tab_classes()

        def destroy():      # erst danach: der Tab kann gerade selbst gezogen werden
            for w in (tab.stack, tab.widget):
                try:
                    w.destroy()     # löst das Aufräumen der Ansichten aus (Überwachung …)
                except Exception:
                    pass
            return False
        GLib.idle_add(destroy)

    def close_other_tabs(self, keep):
        for t in [t for t in self.tabs if t is not keep]:
            self.close_tab(t)

    def close_tabs_right(self, tab):
        idx = self.tabs.index(tab)
        for t in self.tabs[idx + 1:]:
            self.close_tab(t)

    def cycle_tab(self, delta):
        if not self.tabs:
            return
        idx = (self.tabs.index(self.current) + delta) % len(self.tabs)
        self.select_tab(self.tabs[idx])

    def _update_tab_classes(self):
        cur = self.tabs.index(self.current) if self.current in self.tabs else -1
        for i, t in enumerate(self.tabs):
            ctx = t.widget.get_style_context()
            for cls in ("active", "before-active", "last"):
                ctx.remove_class(cls)
            if i == cur:
                ctx.add_class("active")
            elif i == cur - 1:
                ctx.add_class("before-active")
            if i == len(self.tabs) - 1:
                ctx.add_class("last")

    def _on_titlebar_alloc(self, _w, alloc):
        GLib.idle_add(self._relayout_tabs)

    def _relayout_tabs(self):
        if not self.tabs:
            return False
        total = self.titlebar.get_allocated_width()
        if total <= 1:
            return False
        caps = 3 * 46
        avail = total - caps - 44 - 60  # Platz für "+" und Zieh-Fläche
        width = int(max(80, min(240, avail / max(1, len(self.tabs)))))
        if width != self._tab_width:
            self._tab_width = width
            for t in self.tabs:
                t.widget.set_size_request(width, -1)
        else:
            for t in self.tabs:
                if t.widget.get_size_request()[0] != width:
                    t.widget.set_size_request(width, -1)
        return False

    def _on_titlebar_press(self, _w, ev):
        # Linke Taste (Ziehen, Doppelklick = Maximieren) macht GTK selbst – mit
        # Schwelle. Ein eigenes begin_move_drag beim Drücken liess GTKs Zieh-Geste
        # hängen: der nächste Klick (z. B. auf ✕) wurde dann zum Fenster-Ziehen.
        if ev.button == 1:
            return False
        if ev.type == Gdk.EventType.BUTTON_PRESS:
            if ev.button == 3:
                gw = self.get_window()
                if gw is not None:
                    gw.show_window_menu(ev)
                return True
            if ev.button == 2:
                return True
        return False

    def _toggle_maximize(self):
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    # ================================================================ Zustand
    def on_tab_changed(self, tab):
        """Ort eines Tabs hat sich geändert (oder Tab gewechselt)."""
        tab.widget.update()
        if tab is not self.current:
            return
        self.update_state()

    def _extract_current(self):
        arc = places.archive_parts(self.current.location if self.current else None)
        if arc:
            from . import archive
            archive.extract_dialog(arc[0], self, self.current)

    def update_state(self):
        tab = self.current
        if tab is None:
            return
        loc = tab.location
        self.set_title(f"{tab.title()} – FileFenster")
        if self.navrow is not None:
            self.navrow.set_location(loc)
            self.navrow.set_nav_state(bool(tab.back), bool(tab.forward),
                                      places.parent(loc) is not None)
        if self.navpane is not None:
            self.navpane.select_location(loc)

        view = tab.current_view()
        fv = tab.folder_view()
        cv = tab.command_view()
        in_gallery = tab.view_kind() == "gallery"
        sel = self._selected_files()
        has_sel = bool(sel)
        is_folder = fv is not None
        in_trash = is_folder and (tab.location or "").startswith("trash:")
        in_archive = is_folder and (tab.location or "").startswith("archive:")
        # lokale ZIP-Datei: Neu (Ordner), Einfügen, Umbenennen, Löschen wie ein Ordner
        ro_archive = in_archive and self._safe(fv, "archive_read_only", True)
        self.btn_new.set_sensitive(is_folder and not in_trash and not ro_archive)
        self.new_box.set_visible(not in_gallery)
        self.gallery_box.set_visible(in_gallery)
        for b in (self.btn_cut, self.btn_copy, self.btn_delete, self.btn_share):
            b.set_sensitive(has_sel and (is_folder or in_gallery or b is self.btn_copy
                                         or b is self.btn_share)
                            and not (in_archive and b is self.btn_cut)
                            and not (ro_archive and b is self.btn_delete))
        self.btn_rename.set_sensitive(has_sel and is_folder and not in_trash and not ro_archive)
        self.btn_paste.set_sensitive(is_folder and not in_trash and not ro_archive
                                     and self._safe(fv, "can_paste", False))
        self.trash_box.set_visible(in_trash)
        self.archive_box.set_visible(in_archive)
        self.search_box.set_visible(bool(tab and tab.search_text))
        if in_trash:
            self.btn_empty_trash.set_sensitive(self._safe(view, "item_count", 0) > 0)
            self.btn_restore.set_sensitive(self._safe(view, "item_count", 0) > 0)
            self.btn_restore._label.set_text("Ausgewählte Elemente wiederherstellen" if has_sel
                                             else "Alle Elemente wiederherstellen")
        self.btn_sort.set_sensitive(is_folder)
        self.btn_view.set_sensitive(is_folder or in_gallery)
        mode = getattr(fv, "view_mode", "details") if fv else ("large" if in_gallery else "details")
        self._set_view_button_icon(mode)

        # Statusleiste
        count = self._safe(view, "item_count", 0) if view is not None else 0
        self.status_count.set_text("1 Element" if count == 1 else f"{count} Elemente")
        if has_sel:
            n = len(sel)
            txt = "1 Element ausgewählt" if n == 1 else f"{n} Elemente ausgewählt"
            size = self._safe(cv, "selected_size", None) if cv else None
            if size is not None:
                txt += "  " + places.format_size(size)
            self.status_sel.set_text(txt)
            self.status_sel.show()
            self.status_sep2.show()
        else:
            self.status_sel.set_text("")
            self.status_sel.hide()
            self.status_sep2.hide()
        self._sb_lock = True
        self.sb_details.set_active(is_folder and mode == "details")
        self.sb_large.set_active(is_folder and mode == "large")
        self.sb_details.set_sensitive(is_folder)
        self.sb_large.set_sensitive(is_folder)
        self._sb_lock = False

        if self.detailspane is not None and self._shown_right == "details":
            self.detailspane.show_items(loc, sel)
        if self.previewpane is not None and self._shown_right == "preview":
            self.previewpane.show_items(sel)

    @staticmethod
    def _safe(obj, name, default):
        if obj is None or not hasattr(obj, name):
            return default
        try:
            return getattr(obj, name)()
        except Exception as exc:
            print(f"[filefenster] {name}: {exc}")
            return default

    def _selected_files(self):
        if self.current is None:
            return []
        v = self.current.current_view()
        return self._safe(v, "get_selected", []) or []

    def _set_view_button_icon(self, mode):
        img = self.btn_view._img
        if img is not None:
            img.set_from_pixbuf(icons.fluent_pixbuf(VIEW_ICON.get(mode, "apps_list_detail"), 16))

    def _fv_call(self, name, *args):
        fv = self.current.command_view() if self.current else None
        if fv is not None and hasattr(fv, name):
            return getattr(fv, name)(*args)
        # Kopieren aus Start/Dieser PC: Dateien in Zwischenablage
        if name == "copy":
            sel = self._selected_files()
            if sel:
                try:
                    from . import fileops
                    fileops.set_clipboard(sel, cut=False)
                except Exception as exc:
                    print("[filefenster] Kopieren:", exc)
        return None

    def _sb_toggle(self, btn, mode):
        if self._sb_lock or not btn.get_active():
            if not self._sb_lock and not btn.get_active():
                # Aktiven Knopf nicht "abschalten" lassen
                self._sb_lock = True
                fv = self.current.folder_view() if self.current else None
                btn.set_active(fv is not None and getattr(fv, "view_mode", "") == mode)
                self._sb_lock = False
            return
        self.set_view_mode(mode)

    _GALLERY_SIZES = {"xl": 320, "large": 256, "medium": 160, "small": 96}

    def set_view_mode(self, mode):
        fv = self.current.folder_view() if self.current else None
        if fv is not None and hasattr(fv, "set_view_mode"):
            fv.set_view_mode(mode)
        else:
            cv = self.current.command_view() if self.current else None
            if cv is not None and hasattr(cv, "set_size") and mode in self._GALLERY_SIZES:
                cv.set_size(self._GALLERY_SIZES[mode])     # Katalog: Strg+Umschalt+1…4
        self.update_state()

    # ================================================================ Menüs
    def _on_new_menu(self, btn):
        fv = self.current.folder_view()
        if fv is None or not hasattr(fv, "build_new_menu"):
            return
        fv.build_new_menu().popup_below(btn)

    def _on_sort_menu(self, btn):
        fv = self.current.folder_view()
        if fv is None or not hasattr(fv, "build_sort_menu"):
            return
        fv.build_sort_menu().popup_below(btn)

    def _on_collection_menu(self, btn):
        cv = self.current.command_view() if self.current else None
        if cv is not None and hasattr(cv, "build_collection_menu"):
            cv.build_collection_menu().popup_below(btn)

    def _on_view_menu(self, btn):
        fv = self.current.folder_view()
        cv = self.current.command_view()
        gallery_menu = fv is None and cv is not None and hasattr(cv, "build_size_menu")
        m = cv.build_size_menu() if gallery_menu else WinMenu()    # Katalog: Bildgrösse
        mode = getattr(fv, "view_mode", "details") if fv else "details"
        for key, label, icon, n in ([] if gallery_menu else VIEW_MODES):
            m.item(label, lambda k=key: self.set_view_mode(k), icon=icon,
                   accel=f"Strg+Umschalt+{n}", radio=(key == mode))
        m.separator()
        if not gallery_menu:
            m.item("Kompaktansicht", self._toggle_compact, icon="apps_list",
                   check=bool(self.config.get("compact")))
        show = WinMenu()
        show.item("Navigationsbereich", self._toggle_navpane,
                  check=self.paned.get_child1().get_visible())
        show.item("Detailbereich", self.toggle_details_pane,
                  check=self.right_pane() == "details", accel="Alt+Umschalt+P")
        show.item("Vorschaufenster", self.toggle_preview_pane,
                  check=self.right_pane() == "preview", accel="Alt+P",
                  sensitive=self.previewpane is not None)
        show.separator()
        show.item("Dateinamenerweiterungen", self._toggle_extensions,
                  check=bool(self.config.get("show_extensions", True)))
        show.item("Ausgeblendete Elemente", self._toggle_hidden,
                  check=bool(self.config.get("show_hidden")), accel="Strg+H")
        m.item("Einblenden", icon="eye", submenu=show)
        m.popup_below(btn)

    def _on_more_menu(self, btn):
        fv = self.current.command_view()
        is_folder = fv is not None
        m = WinMenu()
        m.item(undo.undo_label(), lambda: undo.undo(self), icon="arrow_undo", accel="Strg+Z",
               sensitive=undo.can_undo())
        m.item(undo.redo_label(), lambda: undo.redo(self), icon="arrow_redo", accel="Strg+Y",
               sensitive=undo.can_redo())
        m.separator()
        m.item("Alles auswählen", lambda: self._fv_call("select_all"), icon="select_all_on",
               accel="Strg+A", sensitive=is_folder)
        m.item("Nichts auswählen", lambda: self._fv_call("select_none"), icon="select_all_off",
               sensitive=is_folder)
        m.item("Auswahl umkehren", lambda: self._fv_call("invert_selection"), icon="arrow_swap",
               sensitive=is_folder)
        m.separator()
        m.item("Als Pfad kopieren", lambda: self._fv_call("copy_path"), icon="document_copy",
               accel="Strg+Umschalt+C", sensitive=is_folder)
        m.item("Eigenschaften", self._properties_of_selection_or_folder, icon="wrench",
               accel="Alt+Eingabe")
        m.separator()
        path = places.local_path(self.current.location) if not places.is_virtual(self.current.location) else None
        m.item("In Terminal öffnen", lambda: hostmod.open_terminal(path), icon="window_console",
               sensitive=bool(path))
        m.item("Papierkorb öffnen", lambda: self.current.navigate("trash:///"), icon="delete")
        loc = self.current.location or ""
        if loc == places.THISPC or loc.startswith("network:"):
            from . import network
            m.separator()
            m.item("Netzlaufwerk verbinden", lambda: network.connect_dialog(self, self.current),
                   icon="link")
        m.separator()
        update_ui.add_menu_item(m, self)
        m.item("Optionen", lambda: folderoptions.show_dialog(self), icon="settings")
        m.popup_below(btn)

    def _on_share(self, btn):
        sel = self._selected_files()
        paths = [g.get_path() for g in sel if g.get_path()]
        m = WinMenu()

        def mail():
            import subprocess
            args = ["xdg-email"]
            for p in paths:
                args += ["--attach", p]
            try:
                subprocess.Popen(args, start_new_session=True)
            except OSError as exc:
                print("[filefenster] xdg-email:", exc)

        m.item("Per E-Mail senden", mail, icon="mail", sensitive=bool(paths))
        m.item("Als Pfad kopieren", lambda: self._fv_call("copy_path"), icon="document_copy",
               sensitive=bool(paths))
        m.popup_below(btn)

    def _properties_of_selection_or_folder(self):
        sel = self._selected_files()
        if not sel and not places.is_virtual(self.current.location):
            sel = [places.to_gfile(self.current.location)]
        if sel:
            self.show_properties(sel)

    # ================================================================ Schalter
    # Die Schalter setzen nur die Einstellung; _on_config_changed wendet sie in
    # allen Fenstern an (auch Änderungen aus den Ordneroptionen).
    def _toggle_compact(self):
        self.config.set("compact", not self.config.get("compact"))

    def _toggle_hidden(self):
        self.config.set("show_hidden", not self.config.get("show_hidden"))

    def _toggle_extensions(self):
        self.config.set("show_extensions", not self.config.get("show_extensions", True))

    def _toggle_navpane(self):
        child = self.paned.get_child1()
        child.set_visible(not child.get_visible())

    # -- rechter Bereich: Detailbereich (Alt+Umschalt+P) oder Vorschaufenster
    # (Alt+P) – wie bei Windows schliessen sie sich gegenseitig aus. Gespeichert
    # in der Einstellung; _on_config_changed führt alle Fenster nach.
    _RIGHT_WIDTH = {"details": ("details_width", 300), "preview": ("preview_width", 360)}

    def right_pane(self):
        """"preview", "details" oder None (laut Einstellung)."""
        if self.config.get("preview_pane") and self.previewpane is not None:
            return "preview"
        if self.config.get("details_pane") and self.detailspane is not None:
            return "details"
        return None

    def set_right_pane(self, which):
        self._remember_right_width()
        # zuerst ausschalten, dann einschalten – nie beide gleichzeitig
        for key, name in (("preview_pane", "preview"), ("details_pane", "details")):
            if name != which and self.config.get(key):
                self.config.set(key, False)
        if which and not self.config.get(f"{which}_pane"):
            self.config.set(f"{which}_pane", True)
        if self._shown_right != self.right_pane():
            self._apply_right_pane()

    def toggle_preview_pane(self):
        self.set_right_pane(None if self.right_pane() == "preview" else "preview")

    def toggle_details_pane(self):
        self.set_right_pane(None if self.right_pane() == "details" else "details")

    def _on_details_toggled(self, btn):
        if not self._pane_lock:
            self.set_right_pane("details" if btn.get_active() else None)

    def _apply_right_pane(self):
        which = self.right_pane()
        self._shown_right = which
        self._pane_lock = True
        self.btn_details.set_active(which == "details")
        self._pane_lock = False
        if self.detailspane is not None:
            self.detailspane.set_visible(which == "details")
        if self.previewpane is not None:
            self.previewpane.set_visible(which == "preview")
            if which != "preview":
                self.previewpane.clear()
        self.right_box.set_visible(which is not None)
        if which:
            alloc = self.details_paned.get_allocated_width()
            if alloc > self._right_width(which) + 200:
                self.details_paned.set_position(alloc - self._right_width(which))
            # GtkPaned verschiebt die Position bei der ersten Zuteilung um die
            # Grössenänderung seit der letzten Zuteilung mit beiden Seiten –
            # danach nochmals setzen (siehe _on_details_paned_alloc)
            self._fix_right_pos = True
        self.update_state()

    def _on_details_paned_alloc(self, _paned, _alloc):
        if self._fix_right_pos:
            self._fix_right_pos = False
            GLib.idle_add(self._position_right)

    def _right_width(self, which):
        key, default = self._RIGHT_WIDTH[which]
        return self.config.get(key, default)

    def _position_right(self):
        alloc = self.details_paned.get_allocated_width()
        if alloc > 1 and self._shown_right:
            pos = alloc - self._right_width(self._shown_right)
            if pos != self.details_paned.get_position():
                self.details_paned.set_position(pos)
        return False

    def _remember_right_width(self):
        """Breite des sichtbaren rechten Bereichs merken (beim Umschalten/Schliessen)."""
        which = self._shown_right
        if not which or not self.right_box.get_visible():
            return
        alloc = self.details_paned.get_allocated_width()
        w = alloc - self.details_paned.get_position()
        if alloc > 1 and 150 < w < (900 if which == "details" else 1400) and w < alloc - 100:
            key = self._RIGHT_WIDTH[which][0]
            if self.config.get(key) != w:
                self.config.set(key, w)

    _FV_SETTERS = {"show_hidden": "set_show_hidden", "show_extensions": "set_show_extensions",
                   "compact": "set_compact", "item_checkboxes": "set_item_checkboxes",
                   "single_click": "set_single_click"}

    def _folder_views(self):
        for t in self.tabs:
            fv = t.views.get("folder")
            if fv is not None:
                yield fv

    def _on_config_changed(self, key, value):
        if key == "pinned" and self.navpane is not None and hasattr(self.navpane, "refresh"):
            self.navpane.refresh()
            for t in self.tabs:
                hv = t.views.get("home")
                if hv is not None and hasattr(hv, "refresh"):
                    hv.refresh()
        elif key in self._FV_SETTERS:
            for fv in self._folder_views():
                if hasattr(fv, self._FV_SETTERS[key]):
                    getattr(fv, self._FV_SETTERS[key])(value)
            self.update_state()
        elif key == "view_mode":
            # Ordneroptionen „Für Ordner übernehmen“ / „Ordner zurücksetzen“
            for fv in self._folder_views():
                if hasattr(fv, "reapply_folder_settings"):
                    fv.reapply_folder_settings()
            self.update_state()
        elif key == "full_path_title":
            for t in self.tabs:
                t.widget.update()
            self.update_state()
        elif key == "status_bar":
            self._apply_statusbar()
        elif key in ("details_pane", "preview_pane"):
            if self._shown_right != self.right_pane():
                self._apply_right_pane()

    def _apply_statusbar(self):
        self.statusbar.set_visible(bool(self.config.get("status_bar", True)))

    # ================================================================ host (Fenster)
    @property
    def location(self):
        return self.current.location if self.current else places.HOME

    def navigate(self, loc):
        if self.current:
            self.current.navigate(loc)

    def open_in_new_tab(self, loc):
        self.new_tab(loc, select=False)

    def open_in_new_window(self, loc):
        self.app_new_window(loc)

    def app_new_window(self, loc):
        win = ExplorerWindow(self.app, loc)
        win.present()

    def go_back(self):
        if self.current:
            self.current.go_back()

    def go_forward(self):
        if self.current:
            self.current.go_forward()

    def go_up(self):
        if self.current:
            self.current.go_up()

    def refresh(self):
        if self.current:
            self.current.refresh()

    def state_changed(self):
        self.update_state()

    def focus_view(self):
        v = self.current.current_view() if self.current else None
        if v is not None and hasattr(v, "focus_view"):
            v.focus_view()
        elif v is not None:
            v.grab_focus()

    def start_search(self, query):
        tab = self.current
        if tab is None:
            return
        query = (query or "").strip()
        if not query:
            self.stop_search()
            return
        loc = tab.location
        if places.is_virtual(loc):
            # Start/Dieser PC: im persönlichen Ordner suchen, Katalog: in der Sammlung
            root = places.uri_for_path(places.home_dir())
            if loc == places.GALLERY:
                from . import gallery
                roots = gallery.active_roots(self.config)
                if roots:
                    root = places.uri_for_path(roots[0])
            tab.navigate(root)
            loc = root
        fv = tab.folder_view()
        if fv is None or not hasattr(fv, "start_search"):
            return
        tab.search_text = query
        fv.start_search(loc, query, content=self._search_content)
        tab.widget.update()
        self.update_state()

    # -- Suchoptionen (Filter wie Windows 11) -------------------------------
    def _on_search_options(self, btn):
        from . import search
        tab = self.current
        query = tab.search_text if tab else ""
        _text, active = search.parse_query(query)
        m = WinMenu()
        for label, title, values in search.FILTER_MENU:
            key = search.FILTER_KEYS[label.lower()]
            sub = WinMenu()
            for v in values:
                sub.item(v, lambda lab=label, val=v: self._set_search_filter(lab, val),
                         radio=active.get(key) == v.casefold())
            m.item(title, submenu=sub)
        m.separator()
        adv = WinMenu()
        adv.item("Dateiinhalte", self._toggle_search_content, check=self._search_content)
        m.item("Erweiterte Optionen", submenu=adv)
        if active:
            m.separator()
            m.item("Filter entfernen", self._clear_search_filters)
        m.popup_below(btn)

    def _set_search_filter(self, label, value):
        from . import search
        tab = self.current
        if tab is None:
            return
        query = search.set_filter(tab.search_text, label, value)
        if self.navrow is not None:
            self.navrow.set_search_text(query)
        self.start_search(query)

    def _clear_search_filters(self):
        from . import search
        tab = self.current
        text, _f = search.parse_query(tab.search_text if tab else "")
        if self.navrow is not None:
            self.navrow.set_search_text(text)
        if text:
            self.start_search(text)
        else:
            self._close_search()

    def _toggle_search_content(self):
        self._search_content = not self._search_content
        tab = self.current
        if tab is not None and tab.search_text:
            self.start_search(tab.search_text)

    def _close_search(self):
        if self.navrow is not None:
            self.navrow.clear_search()
        self.stop_search()

    def stop_search(self):
        tab = self.current
        if tab is None or not tab.search_text:
            return
        tab.search_text = ""
        fv = tab.folder_view()
        if fv is not None and hasattr(fv, "stop_search"):
            fv.stop_search()
        tab.widget.update()
        self.update_state()

    def show_properties(self, gfiles):
        if propsdialog is not None and gfiles:
            propsdialog.show_properties(self, gfiles)

    def is_pinned(self, uri):
        return uri in hostmod.pinned_uris(self.config)

    def pin(self, uri):
        lst = hostmod.pinned_uris(self.config)
        if uri not in lst:
            lst.append(uri)
            self.config.set("pinned", lst)

    def unpin(self, uri):
        self.config.set("pinned", [u for u in hostmod.pinned_uris(self.config) if u != uri])

    def open_terminal(self, path):
        hostmod.open_terminal(path)

    def launch_file(self, gfile):
        return hostmod.launch_file(gfile, self)

    def copy_files(self, gfiles, cut=False):
        from . import fileops
        fileops.set_clipboard(gfiles, cut=cut)
        self.update_state()

    def delete_files(self, gfiles):
        from . import fileops

        def done(*_a):
            self.refresh()
            if self.navpane is not None and hasattr(self.navpane, "refresh"):
                self.navpane.refresh()
        fileops.trash_files(self, gfiles, on_done=done)

    # ================================================================ Eingaben
    def _focus_is_editable(self):
        w = self.get_focus()
        return isinstance(w, Gtk.Editable) or isinstance(w, Gtk.Entry)

    def _on_key(self, _w, ev):
        state = ev.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.MOD1_MASK)
        key = Gdk.keyval_to_lower(ev.keyval)
        # GTK liefert "Delete", "F2", "BackSpace" … – verglichen wird klein
        name = (Gdk.keyval_name(key) or "").lower()

        # Textfelder (Umbenennen, Adresse, Suche) bekommen Tasten zuerst
        if self._focus_is_editable():
            if self.propagate_key_event(ev):
                return True
            # nur Fenster-Kürzel, die nichts mit Textbearbeitung zu tun haben
            if not (ctrl and name in ("t", "w", "n", "tab", "iso_left_tab")) and \
                    name not in ("f5", "f6", "f3") and not (alt and name in ("left", "right", "up")):
                return False
        # Text im Vorschaufenster hat den Fokus: Tasten zuerst dorthin (Strg+C
        # kopiert den markierten Text, Entf/Rücktaste wirken nicht auf Dateien)
        if self.previewpane is not None and self.previewpane.text_has_focus() \
                and not (ctrl and name in ("t", "w", "n", "tab", "iso_left_tab", "page_up", "page_down")) \
                and self.propagate_key_event(ev):
            return True

        tab = self.current
        fv = tab.command_view() if tab else None      # Dateiliste oder Katalog

        def fv_do(method, *a):
            if fv is not None and hasattr(fv, method):
                getattr(fv, method)(*a)
            return True

        if ctrl and not shift and name == "t":
            self.new_tab(folderoptions.start_location(self.config))
            return True
        if ctrl and name == "w":
            self.close_tab(tab)
            return True
        if ctrl and name in ("tab", "iso_left_tab"):
            self.cycle_tab(-1 if (shift or name == "iso_left_tab") else 1)
            return True
        if ctrl and name == "page_down":
            self.cycle_tab(1)
            return True
        if ctrl and name == "page_up":
            self.cycle_tab(-1)
            return True
        if ctrl and not shift and name in [str(i) for i in range(1, 10)]:
            i = int(name) - 1
            if name == "9":
                i = len(self.tabs) - 1
            if 0 <= i < len(self.tabs):
                self.select_tab(self.tabs[i])
            return True
        if ctrl and shift and ev.hardware_keycode in range(10, 18):
            # Strg+Umschalt+1..8 = Ansichten (Tastencode, da Umschalt das Zeichen ändert)
            idx = ev.hardware_keycode - 10
            self.set_view_mode(VIEW_MODES[idx][0])
            return True
        if ctrl and not shift and name == "n":
            self.app_new_window(tab.location if tab else places.HOME)
            return True
        if ctrl and shift and name == "n":
            return fv_do("new_folder")
        if (alt and name == "left") or (name == "backspace" and not ctrl and not alt):
            self.go_back()
            return True
        if alt and name == "right":
            self.go_forward()
            return True
        if alt and name == "up":
            self.go_up()
            return True
        if name == "f5" or (ctrl and name == "r"):
            self.refresh()
            return True
        if (ctrl and name == "l") or (alt and name == "d") or name == "f4":
            if self.navrow is not None:
                self.navrow.focus_address()
            return True
        if (ctrl and name in ("f", "e")) or name == "f3":
            if self.navrow is not None:
                self.navrow.focus_search()
            return True
        if ctrl and name == "z" and not shift:
            undo.undo(self)
            return True
        if ctrl and (name == "y" or (shift and name == "z")):
            undo.redo(self)
            return True
        if ctrl and shift and name == "c":
            return fv_do("copy_path")
        if ctrl and name == "c":
            self._fv_call("copy")
            self.update_state()
            return True
        if ctrl and name == "x":
            fv_do("cut")
            self.update_state()
            return True
        if ctrl and name == "v":
            fv_do("paste")
            return True
        if ctrl and name == "a":
            return fv_do("select_all")
        if ctrl and name == "h":
            self._toggle_hidden()
            return True
        if name == "delete":
            return fv_do("delete", shift)
        if name == "f2":
            return fv_do("begin_rename")
        if alt and name in ("return", "kp_enter"):
            self._properties_of_selection_or_folder()
            return True
        if alt and shift and name == "p":
            self.toggle_details_pane()
            return True
        if alt and name == "p":
            self.toggle_preview_pane()
            return True
        if ctrl and name in ("plus", "equal", "kp_add"):
            return fv_do("zoom", 1)
        if ctrl and name in ("minus", "kp_subtract"):
            return fv_do("zoom", -1)
        if name == "escape" and tab and tab.search_text:
            if self.navrow is not None:
                self.navrow.clear_search()
            self.stop_search()
            return True
        return False

    def _on_button(self, _w, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if ev.button == 8:
            self.go_back()
            return True
        if ev.button == 9:
            self.go_forward()
            return True
        return False

    def _on_content_scroll(self, _w, ev):
        state = ev.state & Gtk.accelerator_get_default_mod_mask()
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        fv = self.current.folder_view() if self.current else None
        if fv is None or not hasattr(fv, "zoom"):
            return False
        ok, dx, dy = ev.get_scroll_deltas()
        if ev.direction == Gdk.ScrollDirection.UP or (ok and dy < 0):
            fv.zoom(1)
        elif ev.direction == Gdk.ScrollDirection.DOWN or (ok and dy > 0):
            fv.zoom(-1)
        self.update_state()
        return True

    # ================================================================ Fenster
    def _on_paned_moved(self, paned, _p):
        pos = paned.get_position()
        if pos > 50:
            self.config.set("nav_width", pos)

    def _on_configure(self, _w, ev):
        if not self.is_maximized():
            w, h = self.get_size()
            if [w, h] != self.config.get("window_size"):
                self.config.set("window_size", [w, h])
        return False

    def _on_state(self, _w, ev):
        maxed = bool(ev.new_window_state & Gdk.WindowState.MAXIMIZED)
        self.max_btn._img.set_from_pixbuf(
            icons.caption_pixbuf("restore" if maxed else "maximize", theme.P["icon"]))
        self.max_btn.set_tooltip_text("Verkleinern" if maxed else "Maximieren")
        if maxed != bool(self.config.get("window_maximized")):
            self.config.set("window_maximized", maxed)
        return False

    def _on_delete(self, *_):
        self._remember_right_width()
        for t in self.tabs:
            for v in t.views.values():
                if hasattr(v, "stop_search"):
                    try:
                        v.stop_search()
                    except Exception:
                        pass
        self.config.save_now()
        return False
