// Hängt sich in Cinnamons Benachrichtigungsdienst ein.
//
// Edge (und andere Chromium-Browser) schicken Web-App-Meldungen als «Microsoft Edge» mit
// Browser-Logo; die eigentliche Herkunft (z. B. «teams.cloud.microsoft») steht als erste
// Zeile im Text, das Absenderbild als image-path. NotifyAsync schreibt das um,
// _notifyForSource ergänzt danach Kopfzeile, Stilklasse und die Layout-Korrekturen.
//
// Programm-Regeln (Einstellungen → Verhalten → Programme) gelten für alle Meldungen:
// ausblenden, nur in die Liste (ohne Pop-up) oder nur als Pop-up (nicht in die Liste).

const Clutter = imports.gi.Clutter;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const St = imports.gi.St;
const Main = imports.ui.main;
const MessageTray = imports.ui.messageTray;

const Apps = require('./apps.js');

const ORIGIN = /^([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d+)?(?:\n+|$)/i;
const MUTED_ID_BASE = 0x7f000000;
const FILE_TYPES = ['pdf', 'txt', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'zip', 'png', 'jpg',
                    'jpeg', 'gif', 'msg', 'eml', 'htm', 'html', 'json', 'xml', 'log'];

// Erste Body-Zeile wirklich eine Domain? (nicht «bericht.pdf» oder «v1.2»)
function looksLikeHost(host) {
    let last = host.split('.').pop().toLowerCase();
    return /^[a-z]{2,}$/.test(last) && !FILE_TYPES.includes(last);
}

let daemon = null;
let cfg = null;         // Objekt mit den Einstellungen (Controller in extension.js)
let mutedCount = 0;
let keyboard = null;    // virtuelle Tastatur für die Anruf-Knöpfe

// Teams schickt bei Anrufen nur «X ruft Sie an.» ohne Knöpfe. Die Erweiterung hängt eigene an;
// ein Klick holt das Teams-Fenster nach vorne und drückt das Teams-Tastenkürzel.
const CALL_TEXT = /ruft (Sie|dich) an|is calling you|eingehender anruf|incoming call/i;
const CALL_ACTIONS = ['pwan-call-audio', 'Annehmen', 'pwan-call-video', 'Mit Video', 'pwan-call-decline', 'Ablehnen'];
const CALL_KEYS = { 'pwan-call-audio': 'KEY_s', 'pwan-call-video': 'KEY_a', 'pwan-call-decline': 'KEY_d' };

function log(host, summary) {
    // Nur im Speicher, zum Nachschauen per Looking Glass: global._pwaNotificationLog
    let entries = global._pwaNotificationLog = global._pwaNotificationLog || [];
    entries.push(new Date().toLocaleTimeString() + ' ' + host + ': ' + summary);
    if (entries.length > 20)
        entries.shift();
}

// Absender bekommt eine gültige ID und gleich «geschlossen» zurück; angezeigt wird nichts
function swallow(daemon, invocation) {
    let id = MUTED_ID_BASE + (++mutedCount);
    invocation.return_value(GLib.Variant.new('(u)', [id]));
    GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
        daemon._emitNotificationClosed(id, 2);
        return GLib.SOURCE_REMOVE;
    });
    return undefined;
}

// Regel für ein Programm: Name gleich App-Name (auch der umgeschriebene), .desktop-Name oder Domain
function programRule(names) {
    let wanted = names.filter(Boolean).map(n => String(n).trim().toLowerCase());
    return (cfg.program_rules || []).find(r => r.program && wanted.includes(r.program.trim().toLowerCase())) || null;
}

function notifyAsync(params, invocation) {
    try {
        let [appName, replacesId, appIcon, summary, body, actions, hints, timeout] = params;
        let originalName = appName, host = null;
        let m = Apps.BROWSERS.includes(appName) && typeof body === 'string' && body.match(ORIGIN);
        if (m && !looksLikeHost(m[1]))
            m = null;
        if (m) {
            let app = Apps.lookup(m[1].toLowerCase(), cfg.app_rules);
            host = app.host;
            log(app.host, summary);

            if (app.mute)
                return swallow(this, invocation);

            let image = hints['image-path'] || hints['image_path'];
            let imagePath = image ? image.deep_unpack() : null;
            if (appIcon)
                hints['x-pwan-browser-icon'] = new GLib.Variant('s', appIcon);
            body = body.slice(m[0].length);
            appName = app.name;
            if (cfg.use_sender_image && imagePath)
                appIcon = imagePath;
            else if (app.icon)
                appIcon = app.icon;
            // Sonst setzt Cinnamon das Bild zusätzlich riesig (125 px) daneben
            delete hints['image-path'];
            delete hints['image_path'];
            if (app.desktopId)
                hints['desktop-entry'] = new GLib.Variant('s', app.desktopId);
            hints['x-pwan-host'] = new GLib.Variant('s', app.host);

            let hasButtons = false;
            let kept = [];
            for (let i = 0; i + 1 < actions.length; i += 2) {
                if (actions[i] === 'settings' && cfg.hide_edge_settings)
                    continue;
                if (actions[i] !== 'default' && actions[i] !== 'settings')
                    hasButtons = true;
                kept.push(actions[i], actions[i + 1]);
            }
            actions = kept;

            if (cfg.call_buttons && !hasButtons && app.wmClass && /teams/.test(app.host)
                && CALL_TEXT.test(body || '')) {
                actions = actions.concat(CALL_ACTIONS);
                hints['x-pwan-window'] = new GLib.Variant('s', app.wmClass);
                hasButtons = true;      // Anrufe bleiben stehen, auch bei «dringende ausblenden»
            }

            // Teams schickt Chat-Nachrichten als «dringend» – die bleiben dann stehen.
            // Anrufe haben Knöpfe (Annehmen/Ablehnen) und bleiben weiterhin stehen.
            let urgency = hints['urgency'] ? hints['urgency'].deep_unpack() : 1;
            if (cfg.auto_dismiss_chats && urgency === 2 && !hasButtons) {
                hints['urgency'] = new GLib.Variant('y', 1);
                if (timeout === 0)
                    timeout = -1;
            }
            params = [appName, replacesId, appIcon, summary, body, actions, hints, timeout];
        }

        let entry = hints['desktop-entry'] ? hints['desktop-entry'].deep_unpack() : null;
        // Bei erkannter Web-App nicht über den Browser-Namen: eine Regel «Microsoft Edge»
        // soll nicht alle Edge-PWAs verschlucken
        let rule = programRule([host ? null : originalName, appName, entry, host]);
        if (rule && rule.mode === 'popup') {
            hints['transient'] = new GLib.Variant('b', true);
        } else if (rule && rule.mode === 'list') {
            hints['x-pwan-mode'] = new GLib.Variant('s', 'list');
        } else if (rule) {
            return swallow(this, invocation);
        }
    } catch (e) {
        global.logError('pwa-notifications: ' + e);
    }
    return Object.getPrototypeOf(this).NotifyAsync.call(this, params, invocation);
}

function makeIcon(icon, size) {
    let gicon = icon;
    if (typeof icon === 'string') {
        if (icon.startsWith('file://'))
            gicon = new Gio.FileIcon({ file: Gio.File.new_for_uri(icon) });
        else if (GLib.path_is_absolute(icon))
            gicon = new Gio.FileIcon({ file: Gio.File.new_for_path(icon) });
        else
            gicon = new Gio.ThemedIcon({ name: icon });
    }
    return new St.Icon({ gicon: gicon, icon_size: size, icon_type: St.IconType.FULLCOLOR,
                         style_class: 'pwan-header-icon' });
}

// In der Benachrichtigungsliste bekommt jeder Eintrag nur seine Mindesthöhe; die des
// umbrechenden Texts ist ein paar Pixel. Sobald die Zeitangabe sichtbar wird (= Meldung
// liegt in der Liste), die volle Höhe als Mindesthöhe setzen.
function fixBodyHeight(notification) {
    let scroll = notification._scrollArea;
    if (!scroll || !notification._timeLabel.visible)
        return;
    // Breite steht erst nach dem Einfügen in die Liste fest
    GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
        try {
            let width = scroll.get_width();
            if (width > 0) {
                let natural = scroll.get_child().get_preferred_height(width)[1];
                scroll.set_style('min-height: ' + Math.ceil(natural) + 'px;');
            }
        } catch (e) {
            // Meldung wurde inzwischen geschlossen
        }
        return GLib.SOURCE_REMOVE;
    });
}

// App einer Meldung (Name, Icon) – für die Kopfzeile und die Gruppierung der Liste
function appInfo(ndata, source, host) {
    if (host) {
        let app = Apps.lookup(host, cfg.app_rules);
        return { key: app.name, name: app.name, icon: app.icon || ndata.hints['x-pwan-browser-icon'] || null };
    }
    let info = source.app && source.app.get_app_info();
    let icon = info && info.get_icon();
    if (!icon && ndata.appIcon && !/^(\/|file:)/.test(ndata.appIcon))
        icon = ndata.appIcon;
    let name = source.app ? source.app.get_name() : (ndata.appName || source.title || '');
    return { key: name, name: name, icon: icon || null };
}

function decorate(notification, ndata, source, host, styled) {
    if (notification._pwanDone)
        return;
    notification._pwanDone = true;

    notification._titleLabel.add_style_class_name('pwan-title');
    notification._timeLabel.add_style_class_name('pwan-time');
    notification._timeLabel.connect('notify::visible', () => fixBodyHeight(notification));
    relayoutWhenIconLoads(notification);
    if (styled)
        notification._table.add_style_class_name(cfg.style.styleClass);

    let info = appInfo(ndata, source, host);
    info.program = ndata.appName || info.name;      // für «Diese App ausblenden» in der Liste
    notification._pwanApp = info;
    if (cfg.header_mode === 'none' || !(host || (cfg.header_mode === 'all' && ndata.appName)))
        return;
    let header = new St.BoxLayout({ style_class: 'pwan-header' });
    // In der gruppierten Liste steht die App schon über der Gruppe: diese Teile ausblenden
    notification._pwanHeaderApp = [];
    if (info.icon)
        notification._pwanHeaderApp.push(makeIcon(info.icon, 16));
    notification._pwanHeaderApp.push(new St.Label({ text: info.name, style_class: 'pwan-header-label',
                                                    y_align: Clutter.ActorAlign.CENTER }));
    notification._pwanHeaderApp.forEach(a => header.add_actor(a));
    // Zeitangabe (nur in der Liste sichtbar) rechts in die Kopfzeile
    let time = notification._timeLabel;
    time.get_parent().remove_child(time);
    time.x_expand = true;
    time.x_align = Clutter.ActorAlign.END;
    time.y_align = Clutter.ActorAlign.CENTER;
    header.add_actor(time);
    notification._bannerBox.insert_child_at_index(header, 0);
}

function notifyForSource(source, ndata) {
    let host = (ndata.hints && ndata.hints['x-pwan-host']) || null;
    let styled = !!host || cfg.style_scope === 'all';
    if (styled)
        source.ICON_SIZE = cfg.image_size;

    // «Nur in der Liste»: statt des Pop-ups direkt an die Liste geben (das macht Cinnamon sonst
    // nach dem Pop-up). Ohne Listen-Applet würde die Meldung verschwinden – dann normal zeigen.
    // Erst nach decorate() übergeben, sonst kennt die Gruppierung die App noch nicht.
    let listOnly = ndata.hints && ndata.hints['x-pwan-mode'] === 'list'
        && MessageTray.extensionsHandlingNotifications > 0;
    let forList = null;
    if (listOnly)
        source.notify = n => {
            source.pushNotification(n);     // wie Source.notify, nur ohne Signal ans Pop-up
            forList = n;
        };
    try {
        Object.getPrototypeOf(this)._notifyForSource.call(this, source, ndata);
    } finally {
        if (listOnly)
            delete source.notify;
    }

    if (ndata.notification) {
        try {
            decorate(ndata.notification, ndata, source, host, styled);
        } catch (e) {
            global.logError('pwa-notifications: ' + e);
        }
    }
    if (forList)
        Main.messageTray.emit('notify-applet-update', forList);
}

// Cinnamon-Fehler: Ändert sich die Grösse einer Meldung während des Einblendens (typisch: ein
// Datei-Icon wird nach ~100 ms fertig geladen und ist dann 40 statt 0 px hoch), rechnet das Pop-up
// nicht nach und bleibt zu niedrig – der Text hängt unter der Karte. Ein queue_relayout() auf die
// Karte nach dem Einblenden behebt es.
function relayout(notification) {
    try {
        if (notification && notification._table && !notification._destroyed)
            notification._table.queue_relayout();
    } catch (e) {
        // Meldung inzwischen zerstört
    }
}

function showNotificationCompleted() {
    Object.getPrototypeOf(this)._showNotificationCompleted.call(this);
    relayout(this._notification);
}

// Icon wird erst nachgeladen: sobald es da ist, noch einmal neu ausrichten
function relayoutWhenIconLoads(notification) {
    let icon = notification._icon;
    if (!icon || icon.height > 0)
        return;
    let id = icon.connect('notify::height', () => {
        if (icon.height <= 0)
            return;
        icon.disconnect(id);
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            relayout(notification);
            return GLib.SOURCE_REMOVE;
        });
    });
}

function pressShortcut(key) {
    if (!keyboard)
        keyboard = Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    let send = (keyval, state) => keyboard.notify_keyval(Clutter.get_current_event_time(), keyval, state);
    let combo = [Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter[key]];
    combo.forEach(k => send(k, Clutter.KeyState.PRESSED));
    combo.slice().reverse().forEach(k => send(k, Clutter.KeyState.RELEASED));
}

function handleCallAction(ndata, action) {
    let wmClass = ndata && ndata.hints && ndata.hints['x-pwan-window'];
    let windows = global.get_window_actors().map(a => a.meta_window)
        // Edge/Chrome: Klasse «Microsoft-edge», die App-Kennung steht in der Instanz (crx__<id>)
        .filter(w => w && (w.get_wm_class_instance() === wmClass || w.get_wm_class() === wmClass))
        .sort((a, b) => b.get_user_time() - a.get_user_time());
    if (!windows.length)
        return;
    let previous = global.display.focus_window;
    Main.activateWindow(windows[0]);
    // Teams braucht den Fokus, bevor das Kürzel ankommt
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 350, () => {
        pressShortcut(CALL_KEYS[action]);
        // Nach dem Ablehnen zurück zum Fenster, in dem man gerade war
        if (action === 'pwan-call-decline' && previous && previous !== windows[0])
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
                Main.activateWindow(previous);
                return GLib.SOURCE_REMOVE;
            });
        return GLib.SOURCE_REMOVE;
    });
}

// Eigene Knöpfe nicht an den Browser weitergeben, sondern selbst ausführen
function emitActionInvoked(id, action) {
    if (typeof action === 'string' && action.startsWith('pwan-call-')) {
        try {
            handleCallAction(this._notifications[id], action);
        } catch (e) {
            global.logError('pwa-notifications: Anruf-Knopf: ' + e);
        }
        return;
    }
    Object.getPrototypeOf(this)._emitActionInvoked.call(this, id, action);
}

function install(notificationDaemon, settings) {
    daemon = notificationDaemon;
    cfg = settings;
    Apps.scan();
    // Als Eigenschaften der Instanz: D-Bus ruft die Methoden dynamisch über das Objekt auf
    daemon.NotifyAsync = notifyAsync;
    daemon._notifyForSource = notifyForSource;
    daemon._emitActionInvoked = emitActionInvoked;
    // wird als this._showNotificationCompleted() aufgerufen
    Main.messageTray._showNotificationCompleted = showNotificationCompleted;
}

function uninstall() {
    if (daemon) {
        delete daemon.NotifyAsync;
        delete daemon._notifyForSource;
        delete daemon._emitActionInvoked;
    }
    keyboard = null;
    delete Main.messageTray._showNotificationCompleted;
    daemon = null;
    cfg = null;
}

module.exports = { install, uninstall, makeIcon };
