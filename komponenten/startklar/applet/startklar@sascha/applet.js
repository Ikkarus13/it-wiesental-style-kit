// Startklar – Startknopf für die Cinnamon-Leiste.
//
// Klick bzw. Super-Taste: ruft im Dienst org.startklar.App die Aktion
// „toggle“ auf und übergibt Monitor, Leiste und Lage des Knopfs (JSON).
// Rechtsklick: Schnellzugriff wie das Windows-Menü Win+X.
// Läuft der Dienst nicht, startet D-Bus ihn über die .service-Datei;
// sonst wird ~/.local/bin/startklar direkt gestartet.

const Applet = imports.ui.applet;
const Main = imports.ui.main;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const Util = imports.misc.util;
const Clutter = imports.gi.Clutter;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const St = imports.gi.St;

const BUS = 'org.startklar.App';
const PATH = '/org/startklar/App';
const SIGNAL_IFACE = 'org.startklar.Menu';

function which(prog) {
    return GLib.find_program_in_path(prog) !== null;
}

class StartklarApplet extends Applet.IconApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        this.metadata = metadata;
        this.setAllowedLayout(Applet.AllowedLayout.BOTH);
        this.set_applet_icon_path(metadata.path + '/start.svg');
        this.set_applet_tooltip('Start');

        this.settings = new Settings.AppletSettings(this, metadata.uuid, instanceId);
        this.settings.bind('overlay-key', 'overlayKey', () => this._bindKeys());
        this.settings.bind('winx-key', 'winxKey', () => this._bindKeys());
        this._bindKeys();

        this._buildWinX();

        this._signalId = Gio.DBus.session.signal_subscribe(
            BUS, SIGNAL_IFACE, 'VisibilityChanged', PATH, null,
            Gio.DBusSignalFlags.NONE,
            (conn, sender, path, iface, signal, params) => {
                let [visible] = params.deep_unpack();
                this._menuVisible = visible;
                if (visible)
                    this.actor.add_style_pseudo_class('checked');
                else
                    this.actor.remove_style_pseudo_class('checked');
            });
        // Das Kontextmenü setzt/löscht „checked“ am selben Knopf – danach wiederherstellen
        this._applet_context_menu.connect('open-state-changed', (m, isOpen) => {
            if (!isOpen && this._menuVisible)
                GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                    if (this._menuVisible)
                        this.actor.add_style_pseudo_class('checked');
                    return GLib.SOURCE_REMOVE;
                });
        });
    }

    _bindKeys() {
        Main.keybindingManager.addXletHotKey(this, 'overlay-key', this.overlayKey,
                                             () => this._call('toggle', 'key'));
        Main.keybindingManager.addXletHotKey(this, 'winx-key', this.winxKey,
                                             () => this._openWinX());
    }

    on_applet_clicked(event) {
        this._call('toggle', 'click');
        return true;
    }

    on_applet_removed_from_panel() {
        if (this._side)
            this._side.close();
        if (this._restoreManager)
            this._restoreManager();
        if (this._sidePopup) {
            this._sidePopup.destroy();
            this._sidePopup = null;
        }
        Main.keybindingManager.removeXletHotKey(this, 'overlay-key');
        Main.keybindingManager.removeXletHotKey(this, 'winx-key');
        if (this._signalId) {
            Gio.DBus.session.signal_unsubscribe(this._signalId);
            this._signalId = 0;
        }
        this.settings.finalize();
    }

    _geometry(source) {
        let mi = this.panel ? this.panel.monitorIndex : Main.layoutManager.primaryIndex;
        let m = Main.layoutManager.monitors[mi] || Main.layoutManager.primaryMonitor;
        let [bx, by] = this.actor.get_transformed_position();
        let panel = [m.x, m.y + m.height - 40, m.width, 40];
        if (this.panel && this.panel.actor) {
            let pa = this.panel.actor;
            let [px, py] = pa.get_transformed_position();
            panel = [Math.round(px), Math.round(py), Math.round(pa.width), Math.round(pa.height)];
        }
        let sides = {};
        sides[St.Side.TOP] = 'top';
        sides[St.Side.BOTTOM] = 'bottom';
        sides[St.Side.LEFT] = 'left';
        sides[St.Side.RIGHT] = 'right';
        return {
            monitor: [m.x, m.y, m.width, m.height],
            panel: panel,
            side: sides[this._orientation] || 'bottom',
            zone: this.locationLabel || 'left',
            button: [Math.round(bx), Math.round(by), Math.round(this.actor.width),
                     Math.round(this.actor.height)],
            source: source,
        };
    }

    _call(action, source, extra) {
        let data = this._geometry(source);
        if (extra)
            Object.assign(data, extra);
        let json = JSON.stringify(data);
        Gio.DBus.session.call(
            BUS, PATH, 'org.gtk.Actions', 'Activate',
            new GLib.Variant('(sava{sv})', [action, [new GLib.Variant('s', json)], {}]),
            null, Gio.DBusCallFlags.NONE, 15000, null,
            (conn, res) => {
                try {
                    conn.call_finish(res);
                } catch (e) {
                    global.logError('Startklar: ' + e.message);
                    // Nur wenn der Dienst nicht da ist bzw. nicht startet – bei einem
                    // Zeitüberlauf liegt der Aufruf noch an, ein zweiter würde das
                    // Menü gleich wieder schliessen
                    let missing = [Gio.DBusError.SERVICE_UNKNOWN, Gio.DBusError.NAME_HAS_NO_OWNER,
                                   Gio.DBusError.SPAWN_EXEC_FAILED, Gio.DBusError.SPAWN_FAILED,
                                   Gio.DBusError.SPAWN_CHILD_EXITED, Gio.DBusError.SPAWN_SERVICE_NOT_FOUND]
                        .some(code => e.matches && e.matches(Gio.DBusError, code));
                    let bin = GLib.get_home_dir() + '/.local/bin/startklar';
                    if (missing && GLib.file_test(bin, GLib.FileTest.IS_EXECUTABLE))
                        Util.spawn([bin, '--' + action, json]);
                }
            });
    }

    _activate(action) {
        Gio.DBus.session.call(
            BUS, PATH, 'org.gtk.Actions', 'Activate',
            new GLib.Variant('(sava{sv})', [action, [], {}]),
            null, Gio.DBusCallFlags.NONE, 4000, null,
            (conn, res) => {
                try {
                    conn.call_finish(res);
                } catch (e) {
                    global.logError('Startklar: ' + e.message);
                }
            });
    }

    // ------------------------------------------------------------ Win+X
    _run(argv) {
        if (which(argv[0]))
            Util.spawn(argv);
    }

    _buildWinX() {
        let menu = this._applet_context_menu;
        let pos = 0;
        let add = (label, icon, cb, prog) => {
            if (prog && !which(prog))
                return;
            let item = new PopupMenu.PopupIconMenuItem(label, icon, St.IconType.SYMBOLIC);
            item.connect('activate', cb);
            menu.addMenuItem(item, pos++);
        };
        let sep = () => menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem(), pos++);

        add('Installierte Apps', 'view-app-grid-symbolic', () => this._run(['mintinstall']), 'mintinstall');
        add('Energieoptionen', 'battery-good-symbolic', () => this._run(['cinnamon-settings', 'power']), 'cinnamon-settings');
        add('Ereignisanzeige', 'utilities-system-monitor-symbolic', () => this._run(['gnome-logs']), 'gnome-logs');
        add('System', 'computer-symbolic', () => this._run(['cinnamon-settings', 'info']), 'cinnamon-settings');
        add('Netzwerkverbindungen', 'network-wired-symbolic', () => this._run(['nm-connection-editor']), 'nm-connection-editor');
        add('Datenträgerverwaltung', 'drive-harddisk-symbolic', () => this._run(['gnome-disks']), 'gnome-disks');
        sep();
        add('Terminal', 'utilities-terminal-symbolic', () => this._terminal(), null);
        add('Task-Manager', 'utilities-system-monitor-symbolic', () => this._run(['gnome-system-monitor']), 'gnome-system-monitor');
        add('Einstellungen', 'preferences-system-symbolic', () => this._run(['cinnamon-settings']), 'cinnamon-settings');
        add('Explorer', 'folder-symbolic', () => this._run(['xdg-open', GLib.get_home_dir()]), null);
        add('Suchen', 'edit-find-symbolic', () => this._call('show', 'winx'), null);
        add('Ausführen', 'system-run-symbolic', () => Main.getRunDialog().open(), null);
        sep();
        // „Herunterfahren oder abmelden“ öffnet sich wie bei Windows rechts
        // daneben – schon beim Darüberfahren, nicht erst beim Klicken.
        let entries = [
            ['Abmelden', 'system-log-out-symbolic', ['cinnamon-session-quit', '--logout', '--no-prompt']],
            ['Energie sparen', 'weather-clear-night-symbolic', ['systemctl', 'suspend']],
            ['Herunterfahren', 'system-shutdown-symbolic', ['cinnamon-session-quit', '--power-off', '--no-prompt']],
            ['Neu starten', 'system-reboot-symbolic', ['cinnamon-session-quit', '--reboot', '--no-prompt']],
        ];
        let canSide = this._menuManager
            && typeof this._menuManager._activeMenuContains === 'function'
            && typeof this._menuManager._shouldBlockEvent === 'function';
        if (canSide) {
            let power = new PopupMenu.PopupIconMenuItem('Herunterfahren oder abmelden',
                                                        'system-shutdown-symbolic',
                                                        St.IconType.SYMBOLIC, {activate: false});
            let arrowBin = new St.Bin({x_align: St.Align.END});
            arrowBin.child = PopupMenu.arrowIcon(St.Side.RIGHT);
            power.addActor(arrowBin, {expand: true, span: -1, align: St.Align.END});
            menu.addMenuItem(power, pos++);
            this._side = this._sideMenu(menu, power, entries);
        } else {
            // Rückfall (andere Cinnamon-Version): klassisch nach unten aufklappen
            let power = new PopupMenu.PopupSubMenuMenuItem('Herunterfahren oder abmelden');
            for (let [label, icon, argv] of entries) {
                let it = new PopupMenu.PopupIconMenuItem(label, icon, St.IconType.SYMBOLIC);
                it.connect('activate', () => this._run(argv));
                power.menu.addMenuItem(it);
            }
            menu.addMenuItem(power, pos++);
        }
        add('Desktop', 'user-desktop-symbolic', () => {
            global.workspace_manager.toggle_desktop(global.get_current_time());
        }, null);
        sep();
        add('Startklar-Einstellungen', 'preferences-desktop-symbolic',
            () => this._activate('settings'), null);
        add('Startklar: Nach Updates suchen', 'software-update-available-symbolic',
            () => this._activate('check-update'), null);
        sep();
    }

    // Untermenü rechts neben einem Eintrag. Bewusst KEIN addChildMenu: Cinnamons
    // Menüverwaltung stapelt Kind-Menüs modal (Hauptmenü dann gesperrt, ein Klick
    // daneben schliesst nur das Untermenü, der nächste wird geschluckt). Stattdessen
    // gilt das Untermenü für die Verwaltung dieses Applets als Teil des Hauptmenüs:
    // Ein Klick daneben schliesst beides, Klicks im Untermenü kommen an.
    _sideMenu(parent, item, entries) {
        let side = new PopupMenu.PopupMenu(item.actor, St.Side.LEFT);   // öffnet rechts
        side.actor.add_style_class_name('startklar-side');
        Main.uiGroup.add_actor(side.actor);
        side.actor.hide();
        this._sidePopup = side;

        let mgr = this._menuManager;
        let inSide = (actor) => side.isOpen && actor && !actor.is_finalized()
                                && side.actor.contains(actor);
        let origContains = mgr._activeMenuContains;
        let origBlock = mgr._shouldBlockEvent;
        mgr._activeMenuContains = function (actor) {
            return origContains.call(this, actor) || inSide(actor);
        };
        mgr._shouldBlockEvent = function (event) {
            return inSide(event.get_source()) ? false : origBlock.call(this, event);
        };
        this._restoreManager = () => {
            mgr._activeMenuContains = origContains;
            mgr._shouldBlockEvent = origBlock;
        };

        for (let [label, icon, argv] of entries) {
            let it = new PopupMenu.PopupIconMenuItem(label, icon, St.IconType.SYMBOLIC);
            it.connect('activate', () => {
                // schliesst über open-state-changed auch das Untermenü (nach dem Grab-Ende)
                parent.close(false);
                this._run(argv);
            });
            side.addMenuItem(it);
        }

        // has_pointer gilt nur für das Element direkt unter der Maus → selbst rechnen
        let pointerIn = (actor) => {
            if (!side.isOpen)
                return false;
            let [px, py] = global.get_pointer();
            let [x, y] = actor.get_transformed_position();
            let [w, h] = actor.get_transformed_size();
            return px >= x && px < x + w && py >= y && py < y + h;
        };
        let timer = 0;
        let clear = () => {
            if (timer) {
                GLib.source_remove(timer);
                timer = 0;
            }
        };
        let later = (ms, fn) => {
            clear();
            timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
                timer = 0;
                fn();
                return GLib.SOURCE_REMOVE;
            });
        };
        let close = () => {
            clear();
            if (side.isOpen)
                side.close(false);
        };
        let closeSoon = () => later(300, () => {
            if (!pointerIn(side.actor))
                close();
        });
        let open = (focus) => {
            clear();
            if (!parent.isOpen)
                return;
            if (!side.isOpen)
                side.open(false);
            if (focus) {
                let first = side._getMenuItems().find(i => i instanceof PopupMenu.PopupBaseMenuItem);
                if (first)
                    first.actor.grab_key_focus();
            }
        };
        item.connect('active-changed', (it, active) => {
            if (active)
                later(250, () => open(false));
            else if (!side.isOpen)
                clear();
        });
        item.actor.connect('button-release-event', () => {
            open(false);
            return Clutter.EVENT_STOP;
        });
        item.actor.connect('key-press-event', (a, ev) => {
            let k = ev.get_key_symbol();
            if (k === Clutter.KEY_Right || k === Clutter.KEY_Return
                || k === Clutter.KEY_KP_Enter || k === Clutter.KEY_space) {
                open(true);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        // Esc/Pfeil links im Untermenü: nur das Untermenü zu. Abfangen in der
        // Capture-Phase (vor PopupMenus eigenem Esc-Handler) und ZUERST den Fokus
        // zurückgeben – sonst verliert Clutter beim Verstecken den Fokus, und die
        // Verwaltung schliesst das ganze Menü.
        side.actor.connect('captured-event', (a, ev) => {
            if (ev.type() !== Clutter.EventType.KEY_PRESS)
                return Clutter.EVENT_PROPAGATE;
            let k = ev.get_key_symbol();
            if (k === Clutter.KEY_Left || k === Clutter.KEY_Escape) {
                item.actor.grab_key_focus();
                close();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        // Maus auf einem anderen Eintrag des Hauptmenüs (auch Cinnamons „Über …“ usw.)
        parent.connect('active-changed', (m, active) => {
            if (active && active !== item && side.isOpen)
                closeSoon();
        });
        parent.connect('open-state-changed', (m, isOpen) => {
            if (!isOpen)
                close();
        });
        return {close: close};
    }

    _terminal() {
        let exec = 'x-terminal-emulator';
        try {
            let s = new Gio.Settings({schema_id: 'org.cinnamon.desktop.default-applications.terminal'});
            exec = s.get_string('exec') || exec;
        } catch (e) {}
        Util.spawnCommandLine(exec);
    }

    _openWinX() {
        if (this._applet_context_menu.isOpen)
            this._applet_context_menu.close();
        else
            this._applet_context_menu.open(true);
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new StartklarApplet(metadata, orientation, panelHeight, instanceId);
}
