"""D-Bus-Schnittstelle fuer die Dateimanager-Erweiterung und die Kommandozeile."""

import threading

from gi.repository import Gio, GLib

from . import config

XML = f"""
<node>
  <interface name="{config.DBUS_IFACE}">
    <method name="SetPinned">
      <arg type="as" name="paths" direction="in"/>
      <arg type="b" name="pinned" direction="in"/>
    </method>
    <method name="FreeUp">
      <arg type="as" name="paths" direction="in"/>
    </method>
    <method name="MakeAvailable">
      <arg type="as" name="paths" direction="in"/>
    </method>
    <method name="OpenOnline">
      <arg type="s" name="path" direction="in"/>
    </method>
    <method name="Share">
      <arg type="s" name="path" direction="in"/>
    </method>
    <method name="GetState">
      <arg type="s" name="path" direction="in"/>
      <arg type="s" name="state" direction="out"/>
    </method>
    <method name="FreeUpAll">
      <arg type="t" name="freed_bytes" direction="out"/>
    </method>
    <method name="ShowActivity"/>
    <method name="ShowSettings"/>
    <method name="SyncNow"/>
    <signal name="Changed">
      <arg type="as" name="paths"/>
    </signal>
  </interface>
</node>
"""


class DBusService:
    def __init__(self, app, engine):
        self.app = app
        self.engine = engine
        self.connection = None
        self._reg_id = None

    def register(self, connection):
        self.connection = connection
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self._reg_id = connection.register_object(config.DBUS_PATH, node.interfaces[0],
                                                  self._on_call, None, None)

    def unregister(self):
        if self.connection and self._reg_id:
            self.connection.unregister_object(self._reg_id)
            self._reg_id = None

    def emit_changed(self, paths):
        if not self.connection or not paths:
            return
        for i in range(0, len(paths), 500):
            self.connection.emit_signal(None, config.DBUS_PATH, config.DBUS_IFACE, "Changed",
                                        GLib.Variant("(as)", (paths[i:i + 500],)))

    def _on_call(self, conn, sender, path, iface, method, params, invocation):
        args = params.unpack()
        app, eng = self.app, self.engine
        try:
            if method == "SetPinned":
                eng.set_pinned(args[0], args[1])
            elif method == "FreeUp":
                eng.free_up(args[0])
            elif method == "MakeAvailable":
                eng.make_available(args[0])
            elif method == "OpenOnline":
                app.open_online(args[0])
            elif method == "Share":
                app.share(args[0])
            elif method == "GetState":
                # Ordnerstatus liest ggf. den Mount -> nicht im Hauptthread blockieren
                def work(path=args[0]):
                    try:
                        st = eng.state_of(path) or ""
                    except Exception:  # noqa: BLE001
                        st = ""
                    invocation.return_value(GLib.Variant("(s)", (st,)))
                threading.Thread(target=work, daemon=True).start()
                return
            elif method == "FreeUpAll":
                # alles ausser "Immer behalten" und nicht hochgeladenen Dateien freigeben
                def work_all():
                    invocation.return_value(GLib.Variant("(t)", (eng.free_up_all_unused(),)))
                threading.Thread(target=work_all, daemon=True).start()
                return
            elif method == "ShowActivity":
                app.show_activity()
            elif method == "ShowSettings":
                app.show_settings()
            elif method == "SyncNow":
                eng.sync_now()
            invocation.return_value(None)
        except Exception as e:  # noqa: BLE001
            invocation.return_dbus_error(config.DBUS_IFACE + ".Error", str(e))
