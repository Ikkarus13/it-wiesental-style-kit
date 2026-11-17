// Gruppiert die Benachrichtigungsliste (Cinnamon-Applet «Benachrichtigungen») nach App, wie im
// Windows-Infocenter: pro App eine Kopfzeile mit Anzahl, Ein-/Ausklappen und «Gruppe löschen».
//
// Das Applet sortiert seine Liste in _reorderNotifications neu (bei jeder Änderung über
// update_list). Beides wird an der Applet-Instanz überschrieben; die Reihenfolge
// (neueste oben/unten) bestimmt weiterhin das Applet.

const Clutter = imports.gi.Clutter;
const St = imports.gi.St;
const MessageTray = imports.ui.messageTray;

const { makeIcon } = require('./notifications.js');

let applet = null;
let cfg = null;             // Controller mit list_group_by_app
let headers = new Map();    // Gruppenschlüssel -> { actor, count, toggle, toggleIcon }
let collapsed = new Set();  // eingeklappte Gruppen

function groupOf(notification) {
    let app = notification._pwanApp;
    if (app && app.name)
        return app;
    // Meldungen von Cinnamon selbst laufen nicht durch den Benachrichtigungsdienst
    let name = (notification.source && notification.source.title) || 'System';
    return { key: name, name: name, icon: null };
}

function iconButton(iconName, onClick) {
    let icon = new St.Icon({ icon_name: iconName, icon_type: St.IconType.SYMBOLIC, icon_size: 14 });
    let button = new St.Button({ style_class: 'pwan-group-button', child: icon, y_align: St.Align.MIDDLE });
    button.connect('clicked', onClick);
    return [button, icon];
}

function makeHeader(group) {
    let key = group.key;
    let actor = new St.BoxLayout({ style_class: 'pwan-group-header' });
    if (group.icon)
        actor.add_actor(makeIcon(group.icon, 16));
    actor.add_actor(new St.Label({ text: group.name, style_class: 'pwan-group-title',
                                   y_align: Clutter.ActorAlign.CENTER }));
    let count = new St.Label({ style_class: 'pwan-group-count', y_align: Clutter.ActorAlign.CENTER });
    actor.add_actor(count);
    actor.add_actor(new St.Bin({ x_expand: true }));

    let [toggle, toggleIcon] = iconButton('xsi-pan-down-symbolic', () => {
        if (collapsed.has(key))
            collapsed.delete(key);
        else
            collapsed.add(key);
        applet.update_list();
    });
    let clearGroup = () => applet.notifications.filter(n => groupOf(n).key === key)
        .forEach(n => n.destroy(MessageTray.NotificationDestroyedReason.DISMISSED));
    let [clear] = iconButton('xsi-window-close-symbolic', clearGroup);
    // Programm ab jetzt ausblenden (legt eine Regel an, mit «Rückgängig»)
    if (group.program) {
        let [mute] = iconButton('xsi-notifications-disabled-symbolic', () => {
            cfg.hideProgram(group.program);
            clearGroup();
        });
        actor.add_actor(mute);
    }
    actor.add_actor(toggle);
    actor.add_actor(clear);
    return { actor, count, toggle, toggleIcon };
}

function showAppParts(notification, visible) {
    (notification._pwanHeaderApp || []).forEach(a => a.visible = visible);
}

function removeHeaders() {
    for (let header of headers.values())
        header.actor.destroy();
    headers.clear();
}

function restoreNotifications(target) {
    for (let n of target.notifications) {
        n.actor.visible = true;
        showAppParts(n, true);
    }
}

// Ersetzt Applet._reorderNotifications (this = Applet)
function reorder() {
    if (!cfg || !cfg.list_group_by_app) {
        removeHeaders();
        restoreNotifications(this);
        return Object.getPrototypeOf(this)._reorderNotifications.call(this);
    }

    let ordered = this.notifications.slice();
    if (this.showNewestFirst)
        ordered.reverse();
    // Gruppen in der Reihenfolge ihrer ersten Meldung
    let groups = new Map();
    for (let n of ordered) {
        let group = groupOf(n);
        if (!groups.has(group.key))
            groups.set(group.key, { info: group, items: [] });
        groups.get(group.key).items.push(n);
    }

    let bin = this._notificationbin;
    for (let child of bin.get_children())
        bin.remove_child(child);
    for (let [key, header] of headers) {
        if (!groups.has(key)) {
            header.actor.destroy();
            headers.delete(key);
            collapsed.delete(key);
        }
    }

    for (let [key, group] of groups) {
        let header = headers.get(key);
        if (!header) {
            header = makeHeader(group.info);
            headers.set(key, header);
        }
        let several = group.items.length > 1;
        let folded = several && collapsed.has(key);
        header.count.text = several ? String(group.items.length) : '';
        header.count.visible = several;
        header.toggle.visible = several;
        header.toggleIcon.icon_name = folded ? 'xsi-pan-end-symbolic' : 'xsi-pan-down-symbolic';
        bin.add_child(header.actor);
        group.items.forEach((n, i) => {
            bin.add_child(n.actor);
            n.actor.visible = !folded || i === 0;
            showAppParts(n, false);
        });
    }
}

// Ersetzt Applet.update_list: bei leerer Liste ruft das Applet _reorderNotifications nicht auf
function updateList() {
    Object.getPrototypeOf(this).update_list.call(this);
    if (this.notifications.length === 0)
        removeHeaders();
}

function install(listApplet, controller) {
    if (applet && applet !== listApplet)
        uninstall(false);
    applet = listApplet;
    cfg = controller;
    // update_list und _reorderNotifications werden über this.… aufgerufen: Instanz-Eigenschaften greifen
    applet._reorderNotifications = reorder;
    applet.update_list = updateList;
    applet.update_list();
}

function refresh() {
    if (applet)
        applet.update_list();
}

// alive = false: Die Applet-Instanz läuft nicht mehr (neu geladen oder entfernt), ihre Actors
// sind zerstört. Dann nur die Verweise lösen, nichts mehr an der Liste anfassen.
function uninstall(alive = true) {
    if (!applet)
        return;
    delete applet._reorderNotifications;
    delete applet.update_list;
    if (alive) {
        removeHeaders();
        restoreNotifications(applet);
        applet.update_list();
    } else {
        headers.clear();
    }
    applet = null;
    cfg = null;
    collapsed.clear();
}

module.exports = { install, refresh, uninstall };
