#!/usr/bin/env python3
"""PWA-Tray: Schliessen-Knopf einer Edge/Chrome-PWA minimiert ins Tray statt zu beenden.

Funktionsweise (X11):
  * Ueber den von Chromium selbst gezeichneten Schliessen-Knopf wird ein unsichtbares
    InputOnly-Kindfenster ins PWA-Fenster gelegt. Es faengt den Klick ab, bevor Edge ihn sieht.
  * Klick -> Fenster bekommt _NET_WM_STATE_SKIP_TASKBAR/PAGER und wird minimiert.
    Die Seite laeuft weiter (Verbindung, Anrufe, Meldungen bleiben aktiv).
  * Tray-Symbol (XApp.StatusIcon): Linksklick zeigt/versteckt, Rechtsklick-Menue.
  * Ctrl+W im PWA-Fenster versteckt ebenfalls. Alt+F4 beendet weiterhin wirklich.
"""
import json
import os
import signal
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("XApp", "1.0")
from gi.repository import Gio, GLib, Gtk, XApp  # noqa: E402

from Xlib import X, XK, display, error  # noqa: E402
from Xlib.protocol import event as xevent  # noqa: E402

import badge  # noqa: E402
import updater  # noqa: E402
from version import VERSION  # noqa: E402

APP_ID = "ch.sascha.PwaTray"
DEBUG = bool(os.environ.get("PWA_TRAY_DEBUG"))
CONFIG_PATH = os.path.expanduser("~/.config/pwa-tray/config.json")
DEFAULT_CONFIG = {
    "apps": [
        {
            "name": "Microsoft Teams",
            "wm_class": "crx__ompifgpmddkgmclendfeacglnodjjndh",
            "desktop": "msedge-ompifgpmddkgmclendfeacglnodjjndh-Default.desktop",
        }
    ],
    # Bereich oben rechts im Fenster, der als Schliessen-Knopf gilt (Pixel)
    "button": {"width": 34, "height": 40},
    "hover_color": "#c42b1c",
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        cfg.update(user)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    except Exception as e:  # kaputte Datei -> Standard
        print(f"pwa-tray: Konfiguration nicht lesbar ({e}), nutze Standard", file=sys.stderr)
    return cfg


class X11:
    def __init__(self):
        self.d = display.Display()
        self.d.set_error_handler(lambda *a, **k: None)  # Fenster verschwinden jederzeit
        self.root = self.d.screen().root
        self.root.change_attributes(event_mask=X.PropertyChangeMask)
        self._atoms = {}

    def atom(self, name):
        if name not in self._atoms:
            self._atoms[name] = self.d.intern_atom(name)
        return self._atoms[name]

    def prop(self, win, name):
        try:
            p = win.get_full_property(self.atom(name), X.AnyPropertyType)
            return list(p.value) if p else []
        except error.XError:
            return []

    def client_msg(self, win, type_name, data):
        data = (list(data) + [0] * 5)[:5]
        ev = xevent.ClientMessage(window=win, client_type=self.atom(type_name), data=(32, data))
        self.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.d.flush()

    def set_skip(self, win, on):
        self.client_msg(win, "_NET_WM_STATE", [1 if on else 0,
                                               self.atom("_NET_WM_STATE_SKIP_TASKBAR"),
                                               self.atom("_NET_WM_STATE_SKIP_PAGER"), 2])

    def minimize(self, win):
        self.client_msg(win, "WM_CHANGE_STATE", [3])  # IconicState

    def activate(self, win, timestamp):
        self.client_msg(win, "_NET_ACTIVE_WINDOW", [2, timestamp, 0])

    def server_time(self):
        # Zeitstempel des aktuellen GTK-Ereignisses (z. B. Menüklick); 0 nur als Notlösung
        return Gtk.get_current_event_time()

    def close(self, win):
        self.client_msg(win, "_NET_CLOSE_WINDOW", [X.CurrentTime, 2])

    def withdraw(self, win):
        """Fenster nach ICCCM freigeben (WM vergisst es; erneutes map -> neu verwaltet)."""
        win.unmap()
        ev = xevent.UnmapNotify(window=win, event=self.root, from_configure=False)
        self.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.d.flush()

    def is_minimized(self, win):
        st = self.prop(win, "WM_STATE")
        if st and st[0] == 3:
            return True
        return self.atom("_NET_WM_STATE_HIDDEN") in self.prop(win, "_NET_WM_STATE")

    def active_window(self):
        v = self.prop(self.root, "_NET_ACTIVE_WINDOW")
        return v[0] if v else 0


def parse_color(hexstr, depth):
    h = hexstr.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    px = (r << 16) | (g << 8) | b
    if depth == 32:
        px |= 0xFF000000
    return px


class TrackedApp:
    """Ein PWA-Fenster (Hauptfenster einer App) samt Klick-Faenger und Tray-Symbol."""

    def __init__(self, owner, cfg):
        self.owner = owner
        self.x = owner.x
        self.cfg = cfg
        self.bw = owner.cfg["button"]["width"]
        self.bh = owner.cfg["button"]["height"]
        self.win = None
        self.catcher = None
        self.hover = None
        self.hover_gc = None
        self.white_gc = None
        self.pressed = False
        self.hidden = False
        self.seen_min = False
        self.remanaging = False
        self.width = 0

        # Status aus der Edge-Erweiterung (siehe edge-extension/, native_host.py)
        self.web_app = cfg.get("web_app", "teams" if "ompifgpmddkgmclendfeacglnodjjndh"
                               in cfg["wm_class"] else None)
        self.unread = 0
        self.presence = None
        self.presence_label = ""
        self.last_web = 0
        self._icon_gen = 0
        self._shown_icon = None

        self.icon = XApp.StatusIcon()
        self.icon.set_name("pwa-tray-" + cfg["wm_class"])
        self.icon.set_tooltip_text(cfg["name"])
        self.icon.set_icon_name(self._icon_path())
        self.icon.connect("activate", self._on_activate)
        self.icon.set_secondary_menu(self._build_menu())
        self.icon.set_visible(False)

    # ---------- Tray ----------
    def _app_info(self):
        try:
            return Gio.DesktopAppInfo.new(self.cfg["desktop"])
        except TypeError:
            return None

    def _icon_path(self):
        info = self._app_info()
        if info and info.get_icon():
            ic = info.get_icon()
            if isinstance(ic, Gio.FileIcon):
                return ic.get_file().get_path()
            if isinstance(ic, Gio.ThemedIcon):
                return ic.get_names()[0]
        return "application-x-executable"

    def set_web_status(self, unread, presence, label):
        self.last_web = GLib.get_monotonic_time()
        self.unread = max(0, int(unread or 0))
        self.presence = presence if presence in badge.PRESENCE else None
        self.presence_label = label or badge.presence_text(self.presence)
        self.update_icon()

    def check_stale(self):
        """Meldet die Seite > 3 min nichts (Teams zu, Erweiterung aus), Symbol zuruecksetzen."""
        if self.last_web and GLib.get_monotonic_time() - self.last_web > 180 * 1_000_000:
            self.last_web = 0
            self.unread, self.presence, self.presence_label = 0, None, ""
            self.update_icon()

    def update_icon(self):
        key = (self.presence, self.unread)
        if key == self._shown_icon:
            return
        self._shown_icon = key
        base = self._icon_path()
        path = base
        if self.presence or self.unread:
            # Wechselnde Dateinamen, sonst zeigt Cinnamon das zwischengespeicherte alte Bild
            self._icon_gen = (self._icon_gen + 1) % 2
            name = f"{self.cfg['wm_class']}-{self._icon_gen}.png"
            path = badge.render(base, self.presence, self.unread,
                                os.path.join(badge.CACHE, name)) or base
        self.icon.set_icon_name(path)
        extras = []
        if self.presence:
            extras.append(badge.presence_text(self.presence))
        if self.unread:
            extras.append(f"{self.unread} ungelesen")
        tip = self.cfg["name"] + (" – " + " · ".join(extras) if extras else "")
        self.icon.set_tooltip_text(tip)

    def _build_menu(self):
        m = Gtk.Menu()
        show = Gtk.MenuItem(label=f"{self.cfg['name']} anzeigen")
        show.connect("activate", lambda *_: self.show())
        quit_app = Gtk.MenuItem(label=f"{self.cfg['name']} beenden")
        quit_app.connect("activate", lambda *_: self.quit_app())
        quit_self = Gtk.MenuItem(label="Tray-Helfer beenden")
        quit_self.connect("activate", lambda *_: self.owner.quit())
        self.update_item = Gtk.MenuItem(label="Nach Updates suchen")
        self.update_item.connect("activate", lambda *_: self.owner.update_clicked())
        for it in (show, Gtk.SeparatorMenuItem(), quit_app, Gtk.SeparatorMenuItem(),
                   self.update_item, quit_self):
            m.append(it)
        m.show_all()
        return m

    def _on_activate(self, _icon, button, time):
        self.owner.last_time = time or 0
        if button != 1:
            return
        if self.win is None:
            info = self._app_info()
            if info:
                info.launch([], None)
            return
        if self.hidden or self.x.is_minimized(self.win) or self.x.active_window() != self.win.id:
            self.show()
        else:
            self.hide()

    # ---------- Fenster ----------
    def attach(self, win):
        if self.win is not None and self.win.id == win.id:
            return
        self.detach()
        self.win = win
        try:
            win.change_attributes(event_mask=X.StructureNotifyMask | X.SubstructureNotifyMask
                                  | X.PropertyChangeMask)
            geo = win.get_geometry()
        except error.XError:
            self.win = None
            return
        self.width = geo.width
        depth = geo.depth
        x0, y0 = self._button_origin()
        # Rote Hover-Flaeche (nur waehrend der Mauszeiger drueber ist sichtbar)
        self.hover = win.create_window(x0, y0, self.bw, self.bh, 0, X.CopyFromParent,
                                       X.InputOutput, X.CopyFromParent,
                                       background_pixel=parse_color(self.owner.cfg["hover_color"], depth),
                                       event_mask=X.ExposureMask)
        self.white_gc = self.hover.create_gc(foreground=parse_color("#ffffff", depth), line_width=1)
        # Unsichtbarer Klick-Faenger obendrauf
        self.catcher = win.create_window(x0, y0, self.bw, self.bh, 0, 0, X.InputOnly, X.CopyFromParent,
                                         event_mask=X.ButtonPressMask | X.ButtonReleaseMask
                                         | X.EnterWindowMask | X.LeaveWindowMask)
        self.catcher.map()
        self._grab_ctrl_w()
        self.hidden = self.x.atom("_NET_WM_STATE_SKIP_TASKBAR") in self.x.prop(win, "_NET_WM_STATE")
        self.seen_min = self.hidden and self.x.is_minimized(win)
        self.x.d.flush()
        self.icon.set_visible(True)

    def _grab_ctrl_w(self):
        kc = self.x.d.keysym_to_keycode(XK.string_to_keysym("w"))
        for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            try:
                self.win.grab_key(kc, X.ControlMask | extra, True, X.GrabModeAsync, X.GrabModeAsync)
            except error.XError:
                pass
        self.keycode_w = kc

    def detach(self):
        for w in (self.catcher, self.hover):
            if w is not None:
                try:
                    w.destroy()
                except error.XError:
                    pass
        self.catcher = self.hover = None
        self.win = None
        self.hidden = False
        self.pressed = False
        self.icon.set_visible(False)

    def _button_origin(self):
        """Oben rechts im sichtbaren Fenster. Nicht maximiert zeichnet Edge einen unsichtbaren
        Schattenrand ums Fenster (_GTK_FRAME_EXTENTS = links, rechts, oben, unten) – der echte
        ✕-Knopf sitzt dann um diesen Rand nach innen versetzt."""
        ext = self.x.prop(self.win, "_GTK_FRAME_EXTENTS") if self.win is not None else []
        right, top = (ext[1], ext[2]) if len(ext) >= 4 else (0, 0)
        return max(0, self.width - right - self.bw), top

    def reposition(self, width=None, force=False):
        if width is not None:
            if width == self.width and not force:
                return
            self.width = width
        x0, y0 = self._button_origin()
        for w in (self.hover, self.catcher):
            if w is not None:
                w.configure(x=x0, y=y0)
        self.x.d.flush()

    def raise_ours(self):
        # Erst den Faenger nach oben, dann die Hover-Flaeche direkt darunter -
        # so liegt nie kurz etwas ueber dem Faenger (sonst Leave/Enter-Schleife).
        if self.catcher is not None:
            self.catcher.raise_window()
            if self.hover is not None:
                self.hover.configure(sibling=self.catcher, stack_mode=X.Below)
        self.x.d.flush()

    def draw_hover(self):
        if self.hover is None:
            return
        s = 10
        cx, cy = self.bw // 2, self.bh // 2 - 3  # Symbol sitzt bei Chromium etwas ueber der Mitte
        x1, y1, x2, y2 = cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2
        self.hover.poly_segment(self.white_gc, [(x1, y1, x2, y2), (x1, y2, x2, y1)])
        self.x.d.flush()

    def hide(self):
        if self.win is None:
            return
        if self.hover is not None:
            self.hover.unmap()
        self.hidden = True
        self.seen_min = False  # erst nach echtem Minimieren auf "hervorgeholt" achten
        self.x.set_skip(self.win, True)
        self.x.minimize(self.win)

    def show(self):
        if self.win is None:
            return
        self.x.set_skip(self.win, False)
        self.hidden = False
        self.x.activate(self.win, self.owner.last_time or self.x.server_time())
        self.owner.last_time = 0
        GLib.timeout_add(700, self._check_tracking)

    # ---------- Cinnamon-Zuordnung reparieren ----------
    # Wird Cinnamon neu gestartet (oder man meldet sich an), waehrend das Fenster versteckt
    # (skip-taskbar) ist, nimmt der WindowTracker es nicht auf und holt das auch spaeter nicht
    # nach. Das Fenster landet dann in der Taskleiste unter "Edge" statt unter Teams.
    # Abhilfe: Fenster einmal freigeben und neu anzeigen -> Cinnamon erfasst es als neu.
    def _cinnamon_app(self):
        js = ('var t=imports.gi.Cinnamon.WindowTracker.get_default(); var r="none"; '
              'global.get_window_actors().forEach(function(a){var w=a.get_meta_window(); '
              f'if(w.get_xwindow && w.get_xwindow()=={self.win.id}){{var x=t.get_window_app(w); '
              'r=x?x.get_id():"null";}}); r')
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = bus.call_sync("org.Cinnamon", "/org/Cinnamon", "org.Cinnamon", "Eval",
                                GLib.Variant("(s)", (js,)), GLib.VariantType("(bs)"),
                                Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
            ok, out = res.unpack()
            return json.loads(out) if ok else None
        except Exception:  # noqa: BLE001  kein Cinnamon / Eval gesperrt
            return None

    def _check_tracking(self):
        if self.win is None or self.hidden:
            return False
        if self._cinnamon_app() == "null":
            self._remanage()
        return False

    def _remanage(self):
        win = self.win
        state = self.x.prop(win, "_NET_WM_STATE")
        maxed = [a for a in ("_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
                 if self.x.atom(a) in state]
        self.remanaging = True
        self.x.withdraw(win)

        def remap():
            try:
                win.map()
                self.x.d.flush()
            except error.XError:
                self.remanaging = False
                return False
            GLib.timeout_add(500, restore)
            return False

        def restore():
            self.remanaging = False
            if maxed:
                self.x.client_msg(win, "_NET_WM_STATE",
                                  [1] + [self.x.atom(a) for a in maxed] + [2])
            self.x.activate(win, self.x.server_time())
            self.owner.rescan()
            return False

        GLib.timeout_add(400, remap)

    def quit_app(self):
        if self.win is not None:
            self.x.close(self.win)

    # ---------- X-Ereignisse ----------
    def handle(self, ev):
        """Liefert True, wenn das Ereignis zu dieser App gehoerte."""
        if self.win is None:
            return False
        t = ev.type
        wid = getattr(ev, "window", None)
        wid = wid.id if wid is not None else None
        if self.catcher is not None and wid == self.catcher.id:
            if t == X.EnterNotify:
                self.hover.map()
                self.x.d.flush()
            elif t == X.LeaveNotify:
                self.hover.unmap()
                self.pressed = False
                self.x.d.flush()
            elif t == X.ButtonPress and ev.detail == 1:
                self.pressed = True
            elif t == X.ButtonRelease and ev.detail == 1:
                inside = 0 <= ev.event_x < self.bw and 0 <= ev.event_y < self.bh
                if self.pressed and inside:
                    self.hide()
                self.pressed = False
            return True
        if self.hover is not None and wid == self.hover.id and t == X.Expose:
            self.draw_hover()
            return True
        if t == X.KeyPress and ev.detail == getattr(self, "keycode_w", -1) and wid == self.win.id:
            self.hide()
            return True
        if t in (X.CreateNotify, X.MapNotify):
            parent = ev.parent if t == X.CreateNotify else ev.event
            if parent.id != self.win.id:
                return False
            if wid not in (self.catcher.id if self.catcher else 0, self.hover.id if self.hover else 0):
                # Chromium hat ein eigenes Kindfenster angelegt -> unseres wieder nach oben
                self.raise_ours()
            return True
        if wid != self.win.id:
            return False
        if t == X.ConfigureNotify:
            self.reposition(ev.width)
        elif t == X.PropertyNotify:
            name = self.x.d.get_atom_name(ev.atom)
            if name == "_GTK_FRAME_EXTENTS":
                # Maximieren/Verkleinern: Schattenrand kommt oder geht
                self.reposition(force=True)
            if name in ("WM_STATE", "_NET_WM_STATE") and self.hidden:
                if self.x.is_minimized(self.win):
                    self.seen_min = True
                elif self.seen_min:
                    # Von aussen wieder hervorgeholt (Meldung angeklickt, Startmenue ...)
                    self.x.set_skip(self.win, False)
                    self.hidden = False
                    GLib.timeout_add(700, self._check_tracking)
        elif t == X.DestroyNotify:
            self.win = None
            self.catcher = self.hover = None
            self.detach()
        return True


class PwaTray(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.apps = []
        self.last_time = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # Von native_host.py per D-Bus aufgerufen (org.gtk.Actions.Activate "web-status")
        act = Gio.SimpleAction.new("web-status", GLib.VariantType("s"))
        act.connect("activate", self._on_web_status)
        self.add_action(act)
        # Manuelle Reparatur der Cinnamon-Zuordnung (auch zum Testen):
        # gdbus call --session --dest ch.sascha.PwaTray --object-path /ch/sascha/PwaTray \
        #   --method org.gtk.Actions.Activate remanage [] {}
        act = Gio.SimpleAction.new("open-kit", None)
        act.connect("activate", lambda *_: updater.kit_oeffnen())
        self.add_action(act)
        act = Gio.SimpleAction.new("remanage", None)
        act.connect("activate", lambda *_: [a._remanage() for a in self.apps
                                             if a.win is not None and not a.hidden])
        self.add_action(act)

    def _on_web_status(self, _act, param):
        try:
            msg = json.loads(param.get_string())
        except (ValueError, TypeError):
            return
        if msg.get("type") != "status":
            return
        for a in self.apps:
            if a.web_app and a.web_app == msg.get("app"):
                a.set_web_status(msg.get("unread"), msg.get("presence"), msg.get("presenceText"))

    def _check_stale(self):
        for a in self.apps:
            a.check_stale()
        return True

    def do_activate(self):
        if self.apps:
            return  # zweiter Start -> nichts tun
        self.hold()
        self.cfg = load_config()
        self.x = X11()
        self.apps = [TrackedApp(self, a) for a in self.cfg["apps"]]
        GLib.io_add_watch(self.x.d.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_x)
        GLib.timeout_add(500, self._on_x)  # gepufferte Ereignisse sicher abarbeiten
        GLib.timeout_add_seconds(30, self._check_stale)
        # SIGTERM = Neustart (install.sh, Update, Abmelden): versteckte Fenster bleiben versteckt,
        # die neue Instanz uebernimmt sie. Menue "Tray-Helfer beenden" holt sie dagegen zurueck.
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._quit_keep_hidden)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.quit)
        self.rescan()
        # Update-Suche: kurz nach dem Start, danach taeglich
        self.update_info = None
        self._last_update_check = 0
        GLib.timeout_add_seconds(90, self._auto_update_check)
        GLib.timeout_add_seconds(3600, self._auto_update_check)

    def _quit_keep_hidden(self):
        self.keep_hidden = True
        self.quit()
        return GLib.SOURCE_REMOVE

    # ---------- Updates ----------
    def _auto_update_check(self):
        if updater.kit_verwaltet():     # Updates kommen ueber das Style Kit
            return GLib.SOURCE_CONTINUE
        if time.time() - self._last_update_check > 20 * 3600 and \
                self.cfg.get("auto_update_check", True):
            self._check_update(manual=False)
        return GLib.SOURCE_CONTINUE

    def _set_update_label(self, text, sensitive=True):
        for a in self.apps:
            a.update_item.set_label(text)
            a.update_item.set_sensitive(sensitive)

    def _check_update(self, manual):
        kit = updater.kit_verwaltet()
        if kit:
            self.update_info = None
            self._set_update_label("Nach Updates suchen")
            if manual:
                self._notify_kit(kit)
            return
        self._last_update_check = time.time()
        if manual:
            self._set_update_label("Suche nach Updates …", False)

        def work():
            try:
                info, err = updater.check(tok=updater.token(self.cfg.get("update_token"))), None
            except Exception as e:  # noqa: BLE001
                info, err = None, str(e)
            GLib.idle_add(self._update_checked, info, err, manual)

        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, info, err, manual):
        self.update_info = info
        if info:
            self._set_update_label(f"Update auf {info['version']} installieren")
            self._notify("PWA-Tray: Update verfügbar",
                         f"Version {info['version']} ist bereit (installiert: {VERSION}). "
                         "Im Tray-Menü installieren.")
        else:
            self._set_update_label("Nach Updates suchen")
            if manual:
                self._notify("PWA-Tray", f"Fehler bei der Update-Suche: {err}" if err
                             else f"Version {VERSION} ist aktuell.")
        return False

    def update_clicked(self):
        if updater.kit_verwaltet():
            self._check_update(manual=True)
            return
        if not self.update_info:
            self._check_update(manual=True)
            return
        info = self.update_info
        self._set_update_label(f"Installiere {info['version']} …", False)

        def work():
            try:
                updater.install(info, updater.token(self.cfg.get("update_token")))
                err = None
            except Exception as e:  # noqa: BLE001
                err = str(e)
            GLib.idle_add(self._update_installed, info, err)

        threading.Thread(target=work, daemon=True).start()

    def _update_installed(self, info, err):
        if err:
            self._set_update_label(f"Update auf {info['version']} installieren")
            self._notify("PWA-Tray: Update fehlgeschlagen", err)
            return False
        self._notify("PWA-Tray aktualisiert",
                     f"Version {info['version']} wird gestartet. Falls sich die Edge-Erweiterung "
                     "geändert hat: in edge://extensions einmal neu laden (↻).")
        updater.run_installer()   # beendet diese Instanz (SIGTERM) und startet die neue
        return False

    def _notify_kit(self, kit):
        has_cmd = bool((updater.kit_info() or {}).get("verwalten"))
        self._notify("PWA-Tray", updater.kit_meldung(kit), f"{kit} öffnen" if has_cmd else None,
                     "app.open-kit")

    def _notify(self, title, body, button=None, action=None):
        n = Gio.Notification.new(title)
        n.set_body(body)
        if button and action:
            n.add_button(button, action)
        self.send_notification(None, n)

    def do_shutdown(self):
        for a in self.apps:
            if a.win is not None and a.hidden and not getattr(self, "keep_hidden", False):
                a.show()  # nichts unsichtbar zuruecklassen
            a.detach()
        if getattr(self, "x", None):
            self.x.d.flush()
        Gtk.Application.do_shutdown(self)

    def rescan(self):
        clients = self.x.prop(self.x.root, "_NET_CLIENT_LIST")
        found = {}
        for wid in clients:
            w = self.x.d.create_resource_object("window", wid)
            try:
                wc = w.get_wm_class()
            except error.XError:
                continue
            if not wc:
                continue
            for a in self.apps:
                if wc[0] != a.cfg["wm_class"]:
                    continue
                # erstes = Hauptfenster; ein bereits verfolgtes Fenster behaelt aber Vorrang
                # (nach dem Neu-Verwalten steht es hinten in der Liste)
                if a.cfg["wm_class"] not in found or (a.win is not None and a.win.id == wid):
                    found[a.cfg["wm_class"]] = w
        for a in self.apps:
            w = found.get(a.cfg["wm_class"])
            if w is None:
                if a.win is not None and not a.remanaging:
                    a.detach()
            else:
                a.attach(w)

    def _on_x(self, *_):
        try:
            while self.x.d.pending_events():
                ev = self.x.d.next_event()
                if DEBUG:
                    print("ev", ev.__class__.__name__, hex(getattr(getattr(ev, "window", None), "id", 0)),
                          file=sys.stderr, flush=True)
                if ev.type == X.PropertyNotify and ev.window.id == self.x.root.id:
                    if ev.atom == self.x.atom("_NET_CLIENT_LIST"):
                        self.rescan()
                    continue
                for a in self.apps:
                    if a.handle(ev):
                        break
        except Exception as e:  # nie wegen eines Ereignisses abstuerzen
            print(f"pwa-tray: {e!r}", file=sys.stderr)
        return True


if __name__ == "__main__":
    sys.exit(PwaTray().run(sys.argv))
