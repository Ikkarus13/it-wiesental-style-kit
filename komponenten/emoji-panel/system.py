"""X11-Anbindung: Zielfenster, Einfügen per XTest, Klicks ausserhalb, Textcursor-Position."""
import sys

from gi.repository import GLib
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xinput, xtest

try:
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
except Exception:  # Barrierefreiheit nicht verfügbar → Position am Mauszeiger
    Atspi = None

# WM_CLASS-Teile (klein geschrieben) von Terminals – dort fügt Strg+Shift+V ein
TERMINALS = {'gnome-terminal-server', 'gnome-terminal', 'xfce4-terminal', 'mate-terminal', 'lxterminal',
             'qterminal', 'x-terminal-emulator', 'kitty', 'alacritty', 'konsole', 'tilix', 'terminator',
             'wezterm', 'org.wezfurlong.wezterm', 'guake', 'tilda', 'xterm', 'uxterm', 'urxvt', 'rxvt',
             'foot', 'st-256color', 'ptyxis', 'kgx', 'com.mitchellh.ghostty', 'ghostty', 'terminology',
             'cool-retro-term', 'sakura', 'roxterm', 'blackbox', 'com.raggesilver.blackbox', 'deepin-terminal'}


class XHelper:
    def __init__(self, on_click, on_active_change):
        self.d = xdisplay.Display()
        self.root = self.d.screen().root
        self.a_active = self.d.intern_atom('_NET_ACTIVE_WINDOW')
        self.a_pid = self.d.intern_atom('_NET_WM_PID')
        self.on_click = on_click
        self.on_active_change = on_active_change
        # eigene Verbindung für Ereignisse (Rohklicks + Wechsel des aktiven Fensters)
        self.ev = xdisplay.Display()
        er = self.ev.screen().root
        er.change_attributes(event_mask=X.PropertyChangeMask)
        try:
            er.xinput_select_events([(xinput.AllMasterDevices, xinput.RawButtonPressMask)])
        except Exception as e:
            print('XInput2 nicht verfügbar:', e)
        self.ev.flush()
        GLib.io_add_watch(self.ev.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._events)

    def _events(self, *a):
        # Immer True zurückgeben: sonst entfernt GLib die Überwachung für immer
        try:
            while self.ev.pending_events():
                e = self.ev.next_event()
                if e.type == X.PropertyNotify and e.atom == self.a_active:
                    self.on_active_change(self.active_window())
                elif e.type == 35 and getattr(e, 'evtype', None) == xinput.RawButtonPress:  # GenericEvent
                    # XIRawEvent: deviceid(2) time(4) detail(4) – Mausrad (4–7) zählt nicht als Klick
                    raw = e.data if isinstance(e.data, bytes) else b''
                    button = int.from_bytes(raw[6:10], sys.byteorder) if len(raw) >= 10 else 1
                    if button not in (4, 5, 6, 7):
                        self.on_click()
        except Exception as e:
            print('X-Ereignis:', e)
        return True

    def active_window(self):
        try:
            p = self.root.get_full_property(self.a_active, X.AnyPropertyType)
            return p.value[0] if p and len(p.value) else 0
        except Exception:
            return 0

    def geometry(self, wid):
        """(x, y, breite, höhe) des Fensters auf dem Bildschirm oder None."""
        if not wid:
            return None
        try:
            w = self.d.create_resource_object('window', wid)
            g = w.get_geometry()
            t = w.translate_coords(self.root, 0, 0)
            return -t.x, -t.y, g.width, g.height
        except Exception:
            return None

    def window_info(self, wid):
        """(wm_class klein geschrieben, pid)"""
        if not wid:
            return '', 0
        try:
            w = self.d.create_resource_object('window', wid)
            cls = w.get_wm_class() or ()
            p = w.get_full_property(self.a_pid, X.AnyPropertyType)
            return ' '.join(cls).lower(), (p.value[0] if p else 0)
        except Exception:
            return '', 0

    def _kc(self, name):
        return self.d.keysym_to_keycode(XK.string_to_keysym(name))

    def send_paste(self, terminal):
        # Tastaturbelegung aktuell halten (z. B. nach setxkbmap)
        while self.d.pending_events():
            e = self.d.next_event()
            if e.type == X.MappingNotify:
                self.d.refresh_keyboard_mapping(e)
        keys = ['Control_L'] + (['Shift_L'] if terminal else []) + ['v']
        codes = [self._kc(k) for k in keys]
        if 0 in codes:
            raise RuntimeError('Tastaturbelegung ohne ' + keys[codes.index(0)])
        for c in codes:
            xtest.fake_input(self.d, X.KeyPress, c)
        for c in reversed(codes):
            xtest.fake_input(self.d, X.KeyRelease, c)
        self.d.sync()

    @staticmethod
    def is_terminal(wm_class):
        return any(part in TERMINALS for part in wm_class.split())


class CaretTracker:
    """Fragt beim Öffnen gezielt das Zielprogramm (über AT-SPI) nach seinem Textcursor.

    Bewusst ohne dauerhafte Ereignis-Listener: deren An-/Abmeldung wird an alle Programme
    verteilt und hat am 2026-11-05 Cinnamon (atk-bridge) zum Absturz gebracht.
    """

    def __init__(self):
        self.ok = False      # Atspi.init() erst, wenn der Bus nachweislich erreichbar ist

    @staticmethod
    def bus_ok():
        """Ist der Barrierefreiheits-Bus erreichbar? libatspi bricht sonst den ganzen Prozess
        ab (g_error «Couldn't connect to accessibility bus») statt einen Fehler zu melden."""
        try:
            from gi.repository import Gio
            r = Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
                'org.a11y.Bus', '/org/a11y/bus', 'org.a11y.Bus', 'GetAddress',
                None, GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NO_AUTO_START, 300, None)
            addr = r.unpack()[0]
            conn = Gio.DBusConnection.new_for_address_sync(
                addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            conn.close_sync(None)
            return True
        except Exception as e:
            print('AT-SPI-Bus nicht erreichbar – Panel öffnet am Mauszeiger:', e)
            return False

    def _ready(self):
        if Atspi is None or not self.bus_ok():
            return False
        if not self.ok:
            try:
                Atspi.init()
                Atspi.set_timeout(300, -1)
                self.ok = True
            except Exception as e:
                print('AT-SPI nicht verfügbar:', e)
        return self.ok

    def _focused(self, pid):
        """Fokussiertes Element im aktiven Fenster des Prozesses pid."""
        desk = Atspi.get_desktop(0)
        for i in range(desk.get_child_count()):
            try:
                app = desk.get_child_at_index(i)
                if app is None or app.get_process_id() != pid:
                    continue
            except Exception:   # hängende oder beendete Anwendung überspringen
                continue
            for j in range(app.get_child_count()):
                frame = app.get_child_at_index(j)
                if frame is None or not frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                    continue
                coll = frame.get_collection_iface()
                if coll is None:
                    continue
                rule = Atspi.MatchRule.new(
                    Atspi.StateSet.new([Atspi.StateType.FOCUSED]), Atspi.CollectionMatchType.ALL,
                    {}, Atspi.CollectionMatchType.NONE, [], Atspi.CollectionMatchType.NONE,
                    [], Atspi.CollectionMatchType.NONE, False)
                found = coll.get_matches(rule, Atspi.CollectionSortOrder.CANONICAL, 1, True)
                if found:
                    return found[0]
        return None

    def caret(self, pid):
        """(x, y, höhe) des Textcursors im Fenster des Prozesses pid oder None."""
        if not pid or not self._ready():
            return None
        try:
            o = self._focused(pid)
            if o is None or o.get_text_iface() is None:
                return None
            off = Atspi.Text.get_caret_offset(o)
            r = Atspi.Text.get_character_extents(o, off, Atspi.CoordType.SCREEN)
            x = r.x
            if (r.height <= 0) and off > 0:
                r = Atspi.Text.get_character_extents(o, off - 1, Atspi.CoordType.SCREEN)
                x = r.x + r.width
            if r.height <= 0 or (r.x <= 0 and r.y <= 0):
                return None
            return x, r.y, r.height
        except Exception:
            return None


class IBusCaret:
    """Liest mit, wo IBus den Textcursor des fokussierten Eingabefelds meldet.

    Programme melden IBus ihre Cursorposition für das Kandidatenfenster – auch Chromium,
    Edge und Electron, die über AT-SPI ohne Screenreader nichts preisgeben. Das Panel hört
    nur zu (eavesdrop auf org.freedesktop.IBus.Panel) und verändert an IBus nichts.
    """

    def __init__(self):
        self.loc = None          # (x, y, w, h) in Bildschirmkoordinaten
        self.focused = False
        self.conn = None
        GLib.idle_add(self._connect)

    def _connect(self):
        try:
            import gi
            gi.require_version('IBus', '1.0')
            from gi.repository import Gio, IBus
            addr = IBus.get_address()
            if not addr:
                raise RuntimeError('IBus läuft nicht')
            self.conn = Gio.DBusConnection.new_for_address_sync(
                addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            # Programme → IBus (InputContext) und IBus → Kandidatenfenster (Panel)
            for iface in ('InputContext', 'Panel'):
                for member in ('SetCursorLocation', 'FocusIn', 'FocusOut'):
                    self.conn.call_sync(
                        'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'AddMatch',
                        GLib.Variant('(s)', (f"type='method_call',interface='org.freedesktop.IBus.{iface}',"
                                             f"member='{member}',eavesdrop=true",)),
                        None, Gio.DBusCallFlags.NONE, 2000, None)
            self.conn.add_filter(self._filter)
            self.conn.connect('closed', self._closed)
        except Exception as e:
            print('IBus-Cursorposition nicht verfügbar:', e)
            self.conn = None
            GLib.timeout_add_seconds(30, self._connect)   # z. B. IBus startet erst später
        return False

    def _closed(self, *a):
        self.conn, self.loc, self.focused = None, None, False
        GLib.timeout_add_seconds(5, self._connect)       # IBus neu gestartet

    def _filter(self, conn, msg, incoming):
        # läuft im D-Bus-Thread: nur Werte merken
        try:
            if incoming and msg.get_interface() in ('org.freedesktop.IBus.Panel',
                                                    'org.freedesktop.IBus.InputContext'):
                m = msg.get_member()
                if m == 'SetCursorLocation':
                    self.loc = tuple(msg.get_body().unpack())[:4]
                elif m == 'FocusIn':
                    self.focused = True
                elif m == 'FocusOut':
                    self.focused = False
        except Exception:
            pass
        return msg

    def caret(self, geom):
        """(x, y, höhe), wenn ein Eingabefeld fokussiert ist und der Cursor im Fenster geom liegt."""
        loc, focused = self.loc, self.focused
        if not (focused and loc) or (loc[0] <= 0 and loc[1] <= 0):
            return None
        x, y, w, h = loc
        if geom:
            gx, gy, gw, gh = geom
            if not (gx <= x <= gx + gw and gy <= y <= gy + gh):
                return None
        return x, y, max(h, 1)
