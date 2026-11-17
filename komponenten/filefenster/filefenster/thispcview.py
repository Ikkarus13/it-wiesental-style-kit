"""Ansicht "Dieser PC" wie im Windows-11-Explorer.

Abschnitte:
    Geräte und Laufwerke   Kacheln je Laufwerk mit Belegungsbalken
    Netzwerkadressen       Netzlaufwerke (Grösse wird asynchron nachgeladen)
"""
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import folderoptions, icons, places, theme
from .homeview import _Section, fit_flowboxes, make_flowbox
from .navpane import location_menu, rgba

TILE_W = 260
TILE_H = 64
BAR_H = 12


class _CapacityBar(Gtk.DrawingArea):
    """Belegungsbalken: Hintergrund @bar_bg, Füllung @bar_fill, ab 90 % @bar_full."""

    def __init__(self, fraction=0.0):
        super().__init__()
        self.fraction = max(0.0, min(1.0, fraction))
        self.set_size_request(-1, BAR_H)
        self.get_style_context().add_class("capacity-bar")
        self.connect("draw", self._on_draw)

    def set_fraction(self, fraction):
        self.fraction = max(0.0, min(1.0, fraction))
        self.queue_draw()

    def _on_draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        P = theme.P
        Gdk.cairo_set_source_rgba(cr, rgba(P["bar_bg"]))
        cr.rectangle(0, 0, w, h)
        cr.fill()
        fill = P["bar_full"] if self.fraction >= 0.9 else P["bar_fill"]
        fw = round(w * self.fraction)
        if fw > 0:
            Gdk.cairo_set_source_rgba(cr, rgba(fill))
            cr.rectangle(0, 0, fw, h)
            cr.fill()
        if not theme.DARK_MODE:
            # feiner Rahmen wie im hellen Windows-Stil
            Gdk.cairo_set_source_rgba(cr, rgba("rgba(0,0,0,0.12)"))
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, w - 1, h - 1)
            cr.stroke()
        return False


def _free_text(free, total):
    return f"{places.format_size(free)} frei von {places.format_size(total)}"


class _DriveTile(Gtk.FlowBoxChild):
    def __init__(self, drive):
        super().__init__()
        self.drive = drive
        self.uri = drive["uri"]
        self.get_style_context().add_class("tile")
        self.get_style_context().add_class("drive-tile")
        self.set_size_request(TILE_W, TILE_H)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        img = Gtk.Image.new_from_pixbuf(icons.place_pixbuf(drive["kind"], 48))
        img.get_style_context().add_class("tile-icon")
        img.set_valign(Gtk.Align.CENTER)
        box.pack_start(img, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        text.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=drive["label"], xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.get_style_context().add_class("tile-name")
        text.pack_start(name, False, False, 0)
        self.bar = _CapacityBar()
        self.bar.set_no_show_all(True)
        text.pack_start(self.bar, False, False, 0)
        self.sub = Gtk.Label(xalign=0)
        self.sub.set_ellipsize(Pango.EllipsizeMode.END)
        self.sub.get_style_context().add_class("tile-sub")
        self.sub.set_no_show_all(True)
        text.pack_start(self.sub, False, False, 0)
        box.pack_start(text, True, True, 0)
        self.add(box)

        total, free = drive.get("total") or 0, drive.get("free") or 0
        if total > 0:
            self.set_capacity(total, free)
        elif drive["kind"] == "network":
            root = Gio.File.new_for_uri(self.uri)
            self.sub.set_text(root.get_parse_name())
            self.sub.show()
        tip = (drive.get("tip") or drive.get("path")
               or Gio.File.new_for_uri(self.uri).get_parse_name())
        self.set_tooltip_text(f"{drive['label']}\n{tip}" if tip != drive["label"] else tip)

    def set_capacity(self, total, free):
        self.bar.set_fraction((total - free) / total if total else 0)
        self.bar.show()
        self.sub.set_text(_free_text(free, total))
        self.sub.show()


class ThisPCView(Gtk.ScrolledWindow):
    """Dieser PC: Geräte und Laufwerke, Netzwerkadressen."""

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.config = host.config
        ctx = self.get_style_context()
        ctx.add_class("content-area")
        ctx.add_class("home-view")
        ctx.add_class("thispc-view")
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self._destroyed = False
        self._tile_press = False
        self._busy = False
        self._timer = 0
        self._cancel = Gio.Cancellable()

        events = Gtk.EventBox()
        events.get_style_context().add_class("home-bg")
        events.connect("button-press-event", self._on_bg_press)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.get_style_context().add_class("home-body")
        events.add(body)
        self.add(events)

        self.flow_drives = self._make_flow()
        self.sec_drives = _Section("drives", "Geräte und Laufwerke", self.flow_drives, self.config)
        body.pack_start(self.sec_drives, False, False, 0)
        self.flow_net = self._make_flow()
        self.sec_net = _Section("netlocs", "Netzwerkadressen", self.flow_net, self.config)
        self.sec_net.set_no_show_all(True)
        body.pack_start(self.sec_net, False, False, 0)
        fit_flowboxes(self, [self.flow_drives, self.flow_net], TILE_W)

        self._vm = Gio.VolumeMonitor.get()
        self._vm_ids = [self._vm.connect(sig, lambda *_: self._schedule_refresh())
                        for sig in ("mount-added", "mount-removed", "mount-changed",
                                    "volume-added", "volume-removed", "volume-changed")]
        self.connect("destroy", self._on_destroy)
        self.config.connect_changed(self._on_config_changed)
        self._fill()

    def _on_config_changed(self, key, value):
        if key == "single_click" and not self._destroyed:
            for flow in self._flows():
                flow.set_activate_on_single_click(bool(value))

    def _make_flow(self):
        flow = make_flowbox()
        # Ordneroptionen: Öffnen durch einfachen Klick, jeder Ordner im eigenen Fenster
        flow.set_activate_on_single_click(bool(self.config.get("single_click", False)))
        flow.connect("child-activated", lambda _f, child: folderoptions.open_folder(self.host, child.uri))
        flow.connect("selected-children-changed", self._on_selection)
        flow.connect("button-press-event", self._on_flow_press)
        return flow

    def _flows(self):
        return (self.flow_drives, self.flow_net)

    # -- Aufbau --------------------------------------------------------------
    def _fill(self):
        keep = [c.uri for f in self._flows() for c in f.get_selected_children()]
        self._cancel.cancel()
        self._cancel = Gio.Cancellable()
        self._busy = True
        for flow in self._flows():
            for child in flow.get_children():
                flow.remove(child)
        has_net = False
        for drive in places.drives():
            tile = _DriveTile(drive)
            if drive["kind"] == "network" and drive.get("path") is None:
                self.flow_net.add(tile)
                has_net = True
                self._query_size(tile)
            else:
                self.flow_drives.add(tile)
            if drive["uri"] in keep:
                tile.get_parent().select_child(tile)
        for flow in self._flows():
            flow.show_all()
        if has_net:
            # show_all wirkt nicht bei no_show_all -> kurz aufheben
            self.sec_net.set_no_show_all(False)
            self.sec_net.show_all()
            self.sec_net.set_no_show_all(True)
        else:
            self.sec_net.hide()
        self._busy = False

    def _query_size(self, tile):
        root = Gio.File.new_for_uri(tile.uri)

        def done(src, res, *_):
            try:
                info = src.query_filesystem_info_finish(res)
            except GLib.Error:
                return
            total = info.get_attribute_uint64("filesystem::size")
            free = info.get_attribute_uint64("filesystem::free")
            if total:
                tile.set_capacity(total, free)

        root.query_filesystem_info_async("filesystem::size,filesystem::free",
                                         GLib.PRIORITY_LOW, self._cancel, done)

    def _schedule_refresh(self, delay=400):
        if self._destroyed:
            return
        if self._timer:
            GLib.source_remove(self._timer)

        def run():
            self._timer = 0
            if not self._destroyed:
                self._fill()
                self.host.state_changed()
            return False

        self._timer = GLib.timeout_add(delay, run)

    # -- Auswahl & Maus ------------------------------------------------------
    def _on_selection(self, flow):
        if self._busy:
            return
        if flow.get_selected_children():
            self._busy = True
            for other in self._flows():
                if other is not flow:
                    other.unselect_all()
            self._busy = False
        self.host.state_changed()

    def _on_flow_press(self, flow, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        child = flow.get_child_at_pos(int(ev.x), int(ev.y))
        if child is None:
            if ev.button in (1, 3):
                self.clear_selection()
            return ev.button == 3
        # Hintergrund-Klick (EventBox) soll diese Auswahl nicht wieder aufheben
        self._tile_press = True
        GLib.idle_add(self._reset_tile_press)
        if ev.button == 1:
            # wie Windows schon beim Drücken auswählen (FlowBox wählt erst beim Loslassen)
            if not child.is_selected():
                flow.select_child(child)
            return False
        if ev.button == 2:
            self.host.open_in_new_tab(child.uri)
            return True
        if ev.button == 3:
            flow.select_child(child)
            self.tile_menu(child).popup_at_pointer(ev, attach_to=flow)
            return True
        return False

    def tile_menu(self, child):
        return location_menu(self.host, child.uri, open_default=True, deletable=False,
                             mount=child.drive.get("mount"), icon_row=False,
                             on_changed=lambda: self._schedule_refresh(200))

    def _reset_tile_press(self):
        self._tile_press = False
        return False

    def _on_bg_press(self, _w, ev):
        if self._tile_press:
            return False
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button in (1, 3):
            self.clear_selection()
        return False

    def clear_selection(self):
        for flow in self._flows():
            flow.unselect_all()

    # -- API -----------------------------------------------------------------
    def refresh(self):
        self._fill()

    def get_selected(self):
        return [Gio.File.new_for_uri(c.uri)
                for f in self._flows() for c in f.get_selected_children()]

    def item_count(self):
        return sum(len(f.get_children()) for f in self._flows())

    def _on_destroy(self, *_):
        self._destroyed = True
        self.config.disconnect_changed(self._on_config_changed)
        self._cancel.cancel()
        for hid in self._vm_ids:
            self._vm.disconnect(hid)
        self._vm_ids = []
