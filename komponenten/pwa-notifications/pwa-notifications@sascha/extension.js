// PWA-Benachrichtigungen – zeigt Meldungen von Web-Apps (Teams, Outlook, 3CX …) mit
// richtigem App-Namen, Icon und Absenderbild statt als «Microsoft Edge», im Stil von Windows 11.
//
// Aufbau:  apps.js           Herkunft → installierte Web-App
//          notifications.js  Einhängen in Cinnamons Benachrichtigungsdienst
//          style.js          Stylesheet aus den Einstellungen
//          updater.js        Updates über GitHub-Releases
//          grouping.js       Benachrichtigungsliste nach App gruppieren

const AppletManager = imports.ui.appletManager;
const Extension = imports.ui.extension;
const Main = imports.ui.main;
const MessageTray = imports.ui.messageTray;
const Settings = imports.ui.settings;
const Util = imports.misc.util;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const St = imports.gi.St;

const Grouping = require('./grouping.js');
const Notifications = require('./notifications.js');
const Style = require('./style.js');
const Updater = require('./updater.js');

const STYLE_KEYS = ['style-scope', 'style-preset', 'custom-background', 'custom-text', 'opacity',
                    'width', 'corner-radius', 'border', 'shadow', 'max-lines'];
const OTHER_KEYS = ['header-mode', 'image-size', 'use-sender-image', 'hide-edge-settings',
                    'auto-dismiss-chats', 'call-buttons', 'app-rules', 'program-rules', 'update-auto-check', 'update-auto-install',
                    'update-repo', 'update-token'];
const CHECK_INTERVAL = 24 * 3600;   // Sekunden zwischen automatischen Prüfungen
const LIST_APPLET = 'notifications@cinnamon.org';   // Benachrichtigungsliste im Panel

let meta = null;
let controller = null;

var Controller = class Controller {
    constructor(metadata) {
        this.meta = metadata;
        this.uuid = metadata.uuid;
        this.cacheDir = GLib.build_filenamev([GLib.get_user_cache_dir(), this.uuid]);
        this._timers = [];
        this._busy = false;
        this._pending = null;   // gefundenes Update { version, page, … }

        this.settings = new Settings.ExtensionSettings(this, this.uuid);
        for (let key of STYLE_KEYS)
            this.settings.bind(key, key.replace(/-/g, '_'), () => this._applyStyle());
        for (let key of OTHER_KEYS)
            this.settings.bind(key, key.replace(/-/g, '_'));
        this.settings.bind('list-newest-first', 'list_newest_first', () => this._applyListOrder());
        this.settings.bind('list-group-by-app', 'list_group_by_app', () => {
            this._watchListApplet();    // Applet evtl. inzwischen neu geladen
            Grouping.refresh();
        });

        this.style = new Style.StyleManager(this.uuid, (old, cls) => this._swapStyleClass(old, cls));
        this._applyStyle();
        this._watchColorScheme();
        Notifications.install(Main.notificationDaemon, this);

        this._announceUpdate();
        // Das Applet startet evtl. erst nach der Erweiterung oder wird später neu geladen
        this._appletSignals = ['extension-loaded', 'extension-unloaded'].map(signal =>
            Extension.Type.APPLET.connect(signal, (type, uuid) => {
                if (uuid === LIST_APPLET)
                    this._watchListApplet();
            }));
        if (!this._watchListApplet())
            this._addTimer(15, () => { this._watchListApplet(); return GLib.SOURCE_REMOVE; });
        // Erste Prüfung kurz nach dem Start, danach regelmässig
        this._addTimer(90, () => { this._autoCheck(); return GLib.SOURCE_REMOVE; });
        this._addTimer(6 * 3600, () => { this._autoCheck(); return GLib.SOURCE_CONTINUE; });
    }

    destroy() {
        this._timers.forEach(id => GLib.source_remove(id));
        this._timers = [];
        (this._schemeWatches || []).forEach(([settings, id]) => settings.disconnect(id));
        this._schemeWatches = [];
        (this._appletSignals || []).forEach(id => Extension.Type.APPLET.disconnect(id));
        this._appletSignals = [];
        this._unwatchListApplet();
        Notifications.uninstall();
        this.style.destroy();
        this.settings.finalize();
    }

    // Abgelaufene Einmal-Timer fallen aus der Liste, sonst meldet source_remove in destroy()
    // «Source ID … was not found»
    _addTimer(seconds, fn) {
        let id = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => {
            let again = fn();
            if (again !== GLib.SOURCE_CONTINUE)
                this._timers = this._timers.filter(t => t !== id);
            return again;
        });
        this._timers.push(id);
    }

    // ---- Aussehen ----------------------------------------------------------

    _applyStyle() {
        if (!this.style)
            return;
        try {
            this.style.apply(cls => Style.build(this, cls));
        } catch (e) {
            global.logError('pwa-notifications: ' + e);
        }
    }

    // Meldungen in der Liste und im Pop-up tragen noch die Klasse des alten Stylesheets
    _swapStyleClass(old, cls) {
        let all = Object.values(Main.notificationDaemon._notifications || {}).map(nd => nd.notification);
        let applet = this._listApplet();
        if (applet)
            all = all.concat(applet.notifications);
        all.push(Main.messageTray._notification);
        for (let n of new Set(all)) {
            if (n && n._table && !n._destroyed && n._table.has_style_class_name(old)) {
                n._table.remove_style_class_name(old);
                n._table.add_style_class_name(cls);
            }
        }
    }

    // Alle Schemas beobachten, die style.js liest: steht das erste auf «default», gilt das zweite
    _watchColorScheme() {
        let source = Gio.SettingsSchemaSource.get_default();
        this._schemeWatches = [];
        for (let [schema, key] of Style.COLOR_SCHEMES) {
            let found = source.lookup(schema, true);
            if (!found || !found.has_key(key))
                continue;
            let settings = new Gio.Settings({ schema_id: schema });
            this._schemeWatches.push([settings, settings.connect('changed::' + key, () => this._applyStyle())]);
        }
    }

    // ---- Knöpfe in den Einstellungen ---------------------------------------

    // Schickt eine Meldung genau so, wie Edge sie für Teams schickt – sie läuft also
    // durch dieselbe Umschreibung. Eine noch sichtbare Vorschau wird vorher geschlossen,
    // sonst stellt Cinnamon die neue hinten an.
    showPreview() {
        let ndata = this._previewId && Main.notificationDaemon._notifications[this._previewId];
        if (ndata && ndata.notification)
            ndata.notification.destroy();

        let avatar = GLib.build_filenamev([this.meta.path, 'icons', 'vorschau-absender.png']);
        let hints = {
            'image-path': new GLib.Variant('s', avatar),
            'urgency': new GLib.Variant('y', 1),
            'desktop-entry': new GLib.Variant('s', 'microsoft-edge')
        };
        Gio.DBus.session.call(
            'org.freedesktop.Notifications', '/org/freedesktop/Notifications',
            'org.freedesktop.Notifications', 'Notify',
            new GLib.Variant('(susssasa{sv}i)', [
                'Microsoft Edge', 0, '', 'Max Muster',
                'teams.cloud.microsoft\n\nSo sehen Benachrichtigungen mit den aktuellen Einstellungen aus. ' +
                'Längere Nachrichten werden auf mehrere Zeilen umbrochen.',
                ['default', 'Activate', 'settings', 'Einstellungen'], hints, -1]),
            null, Gio.DBusCallFlags.NONE, -1, null, (conn, res) => {
                try {
                    [this._previewId] = conn.call_finish(res).deep_unpack();
                } catch (e) {
                    global.logError('pwa-notifications: Vorschau: ' + e);
                }
            });
    }

    // Aus der gruppierten Liste (Glocken-Knopf): Programm ab jetzt ausblenden, mit «Rückgängig»
    hideProgram(program) {
        let same = r => (r.program || '').trim().toLowerCase() === program.trim().toLowerCase();
        let before = (this.program_rules || []).slice();
        this._setProgramRules(before.filter(r => !same(r)).concat([{ program: program, mode: 'hide' }]));
        let n = this._notify('Meldungen von «' + program + '» ausgeblendet',
                             'Ändern unter Einstellungen → Verhalten → Programme.', [['undo', 'Rückgängig']]);
        n.setTransient(true);       // Bestätigung nicht in der Liste aufheben
        n.connect('action-invoked', () => this._setProgramRules(before));
    }

    _setProgramRules(rules) {
        this.program_rules = rules;
        this.settings.setValue('program-rules', rules);    // löst keinen Rückruf aus
    }

    openListSettings() {
        let applet = this._listApplet();
        Util.spawn(['xlet-settings', 'applet', LIST_APPLET].concat(applet ? ['-i', String(applet.instance_id)] : []));
    }

    openNotificationSettings() {
        Util.spawn(['cinnamon-settings', 'notifications']);
    }

    reloadApps() {
        require('./apps.js').scan();
        this._notify('Web-Apps neu eingelesen', 'Neu installierte Web-Apps werden jetzt erkannt.');
    }

    checkUpdatesNow() {
        this._check(true);
    }

    installUpdate() {
        let kit = Updater.kitVerwaltet();
        if (kit)
            this._notifyKit(kit);
        else if (this._pending)
            this._install(this._pending);
        else
            this._check(true, true);
    }

    openReleasePage() {
        let url = this.settings.getValue('update-page-url') || this.meta.url;
        if (!url)
            return;
        try {
            Gio.AppInfo.launch_default_for_uri(url, null);
        } catch (e) {
            this._notify('Seite lässt sich nicht öffnen', url);
        }
    }

    // ---- Benachrichtigungsliste ------------------------------------------------
    // Die Reihenfolge ist eine Einstellung des Cinnamon-Applets. Unser Schalter spiegelt sie
    // nur, damit man sie hier findet; Änderungen im Applet-Dialog kommen per Signal zurück.

    _listApplet() {
        return AppletManager.getRunningInstancesForUuid(LIST_APPLET)[0] || null;
    }

    // Hängt sich an die laufende Applet-Instanz. Wurde das Applet neu geladen, ist das eine neue
    // Instanz: dann die alte loslassen und Gruppierung und Signal neu einrichten.
    _watchListApplet() {
        let applet = this._listApplet();
        if (applet === this._watchedApplet)
            return !!applet;
        this._unwatchListApplet();
        if (!applet || !applet.settings)
            return false;
        this._watchedApplet = applet;
        this._takeListOrder(applet.showNewestFirst);
        this._listSettings = applet.settings;
        this._listSignal = applet.settings.connect('changed::showNewestFirst',
                                                   (s, key, oldValue, value) => this._takeListOrder(value));
        Grouping.install(applet, this);
        return true;
    }

    _unwatchListApplet() {
        let old = this._watchedApplet;
        if (!old)
            return;
        // Läuft die alte Instanz nicht mehr, hat ihr finalize() die Signale schon gelöst
        // und ihre Actors sind zerstört
        let alive = AppletManager.getRunningInstancesForUuid(LIST_APPLET).includes(old);
        try {
            this._listSettings.disconnect(this._listSignal);
        } catch (e) {
            if (alive)
                global.logError('pwa-notifications: ' + e);
        }
        Grouping.uninstall(alive);
        this._watchedApplet = this._listSettings = this._listSignal = null;
    }

    _takeListOrder(value) {
        value = !!value;
        if (this.list_newest_first !== value) {
            this.list_newest_first = value;
            this.settings.setValue('list-newest-first', value);    // löst keinen Rückruf aus
        }
    }

    _applyListOrder() {
        this._watchListApplet();    // Applet evtl. inzwischen neu geladen
        let applet = this._watchedApplet;
        if (!applet || applet.showNewestFirst === this.list_newest_first)
            return;
        // setValue schreibt nur die Datei; Eigenschaft und Liste selbst nachziehen
        applet.settings.setValue('showNewestFirst', this.list_newest_first);
        applet.showNewestFirst = this.list_newest_first;
        applet.update_list();
    }

    // ---- Updates -------------------------------------------------------------

    _repo() {
        return (this.update_repo || '').trim() || this.meta['update-repo'];
    }

    async _token() {
        return (this.update_token || '').trim() || await Updater.ghToken();
    }

    _autoCheck() {
        if (!this.update_auto_check || Updater.kitVerwaltet())    // Kit: Updates kommen von dort
            return;
        let last = Date.parse(this.settings.getValue('update-last-check') || '') || 0;
        if (Date.now() - last >= CHECK_INTERVAL * 1000)
            this._check(false, this.update_auto_install);
    }

    async _check(manual, installIfNewer = false) {
        if (this._busy)
            return;
        this._busy = true;
        try {
            let kit = Updater.kitVerwaltet();
            if (kit) {
                this._pending = null;
                this._busy = false;
                this.settings.setValue('update-available', false);
                this.settings.setValue('update-message', '');
                if (manual)
                    this._notifyKit(kit);
                return;
            }
            let info = await Updater.check(this._repo(), await this._token(), this.uuid, this.meta.version);
            this.settings.setValue('update-last-check', new Date().toISOString());
            this.settings.setValue('update-latest-version', info.version);
            this.settings.setValue('update-page-url', info.page || '');
            this.settings.setValue('update-available', info.newer);
            this.settings.setValue('update-message', '');
            this._pending = info.newer ? info : null;
            this._busy = false;
            if (info.newer && installIfNewer)
                this._install(info);
            else if (info.newer)
                this._offer(info);
            else if (manual)
                this._notify('Keine Updates', 'Version ' + this.meta.version + ' ist aktuell.');
        } catch (e) {
            this._busy = false;
            this.settings.setValue('update-last-check', new Date().toISOString());
            this.settings.setValue('update-message', e.message);
            if (manual)
                this._notify('Update-Suche fehlgeschlagen', e.message);
        }
    }

    _notifyKit(kit) {
        let info = Updater.kitInfo();
        let buttons = info && info.verwalten ? [['kit', kit + ' öffnen']] : null;
        let n = this._notify(Updater.kitMeldung(kit), 'Installiert ist Version ' + this.meta.version + '.', buttons);
        if (buttons)
            n.connect('action-invoked', (n, id) => {
                if (id === 'kit')
                    Updater.kitOeffnen();
            });
    }

    _offer(info) {
        let notes = info.notes.trim().split('\n').slice(0, 6).join('\n');
        let n = this._notify('Update verfügbar: Version ' + info.version,
                             'Installiert ist ' + this.meta.version + '.' + (notes ? '\n\n' + notes : ''),
                             [['install', 'Jetzt installieren'], ['download', 'Herunterladen']]);
        n.connect('action-invoked', (n, id) => {
            if (id === 'install')
                this._install(info);
            else if (id === 'download')
                this.openReleasePage();
        });
    }

    async _install(info) {
        let kit = Updater.kitVerwaltet();
        if (kit) {
            this._pending = null;
            this.settings.setValue('update-available', false);
            this._notifyKit(kit);
            return;
        }
        if (this._busy)
            return;
        this._busy = true;
        try {
            let how = await Updater.install(info, await this._token(), this.uuid, this.meta.path, this.cacheDir);
            this.settings.setValue('update-available', false);
            this.settings.setValue('update-message', '');
            if (how === 'git')
                this._notify('Entwicklerinstallation aktualisiert', 'Neuester Stand per git pull geholt.');
            // Neu laden, sobald dieser Aufruf zu Ende ist; die neue Version meldet sich selbst
            GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                Extension.reloadExtension(this.uuid, Extension.Type.EXTENSION);
                return GLib.SOURCE_REMOVE;
            });
        } catch (e) {
            this.settings.setValue('update-message', e.message);
            this._notify('Update fehlgeschlagen', e.message, [['download', 'Manuell herunterladen']])
                .connect('action-invoked', () => this.openReleasePage());
        } finally {
            this._busy = false;
        }
    }

    // Nach einem Update (oder der Erstinstallation) einmal Bescheid geben
    _announceUpdate() {
        let before = this.settings.getValue('installed-version');
        if (before === this.meta.version)
            return;
        this.settings.setValue('installed-version', this.meta.version);
        this.settings.setValue('update-available', false);
        if (before)
            this._notify('PWA-Benachrichtigungen aktualisiert',
                         'Version ' + before + ' → ' + this.meta.version + '.',
                         [['notes', 'Was ist neu?']])
                .connect('action-invoked', () => this.openReleasePage());
    }

    _notify(title, body, buttons) {
        let source = new MessageTray.SystemNotificationSource();
        Main.messageTray.add(source);
        let icon = new St.Icon({ gicon: Gio.icon_new_for_string(GLib.build_filenamev([this.meta.path, 'icon.png'])),
                                 icon_size: 40, icon_type: St.IconType.FULLCOLOR });
        let n = new MessageTray.Notification(source, title, body, { icon: icon });
        n._table.add_style_class_name(this.style.styleClass);
        n._titleLabel.add_style_class_name('pwan-title');
        (buttons || []).forEach(([id, label]) => n.addButton(id, label));
        n.setTransient(!buttons);
        source.notify(n);
        return n;
    }
};

function init(metadata) {
    meta = metadata;
}

// Das zurückgegebene Objekt bekommt die Knopf-Aufrufe aus den Einstellungen
function enable() {
    controller = new Controller(meta);
    return controller;
}

function disable() {
    if (controller)
        controller.destroy();
    controller = null;
}
