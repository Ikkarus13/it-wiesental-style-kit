// Ordnet die Herkunft einer Browser-Benachrichtigung (z. B. «teams.cloud.microsoft»)
// einer installierten Web-App zu: Name, Icon, .desktop-Datei.
// Quelle sind die .desktop-Dateien, die Edge/Chrome beim Installieren einer PWA anlegen,
// ergänzt um die eigenen Regeln aus den Einstellungen.
//
// Edge schreibt die Adresse als --app-url hinein. Chrome nicht – dort stehen Adressen nur in
// den Schnellaktionen («Neue Nachricht» …). Fehlen auch die, wird über den Namen geraten:
// ein Teil der Domain ist ein Wort im App-Namen (outlook.cloud.microsoft → «Outlook»).

const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;

// Browser, die Web-App-Meldungen mit der Herkunft als erster Textzeile schicken
const BROWSERS = ['Microsoft Edge', 'Google Chrome', 'Chromium', 'Brave', 'Vivaldi', 'Opera'];

let installed = {};     // Host -> { name, icon, desktopId }
let apps = [];          // alle Web-Apps, auch die ohne bekannte Adresse
let scannedAt = 0;      // Date.now() des letzten Einlesens

// Unbekannte Hosts lösen höchstens so oft ein neues Einlesen aus (es läuft synchron)
const RESCAN_MS = 30 * 1000;

function hostOf(url) {
    return url.replace(/^[a-z]+:\/\//i, '').split(/[\/?:#]/)[0].toLowerCase();
}

// Allerweltswörter am Domain-Anfang sagen nichts über die App (mail.google.com, app.slack.com)
const GENERIC = ['www', 'app', 'apps', 'web', 'mail', 'cloud', 'login', 'portal', 'my', 'online', 'office', 'go'];

// Nur das erste Label zählt (outlook.cloud.microsoft → «outlook»), sonst trifft z. B. «cloud»
// oder «google» die falsche App
function guess(host) {
    let label = host.split('.')[0];
    if (label.length < 3 || GENERIC.includes(label))
        return null;
    return apps.find(a => a.name.toLowerCase().split(/[^a-z0-9]+/).includes(label)) || null;
}

// Fensterklasse der App (Edge: crx__<id>, Chrome: crx_<id>) – zum Finden ihres Fensters
function startupWMClass(kf) {
    try {
        return kf.get_string('Desktop Entry', 'StartupWMClass');
    } catch (e) {
        return null;
    }
}

function scan() {
    installed = {};
    apps = [];
    scannedAt = Date.now();
    let dir = GLib.build_filenamev([GLib.get_user_data_dir(), 'applications']);
    let enumerator;
    try {
        enumerator = Gio.File.new_for_path(dir).enumerate_children('standard::name', 0, null);
    } catch (e) {
        return;
    }
    let info;
    while ((info = enumerator.next_file(null))) {
        let file = info.get_name();
        if (!file.endsWith('.desktop') || !/^(msedge|chrome|brave|vivaldi|opera|chromium)-/.test(file))
            continue;
        try {
            let kf = new GLib.KeyFile();
            kf.load_from_file(GLib.build_filenamev([dir, file]), GLib.KeyFileFlags.NONE);
            let app = {
                name: kf.get_locale_string('Desktop Entry', 'Name', null).replace(/\s*\(PWA\)\s*$/, ''),
                icon: kf.get_string('Desktop Entry', 'Icon'),
                desktopId: file.slice(0, -'.desktop'.length),
                wmClass: startupWMClass(kf)
            };
            apps.push(app);
            // [Desktop Entry] zuerst, danach die Schnellaktionen
            for (let group of kf.get_groups()[0]) {
                let exec;
                try {
                    exec = kf.get_string(group, 'Exec');
                } catch (e) {
                    continue;   // Gruppe ohne Exec (has_key gibt es in GJS nicht)
                }
                for (let m of exec.matchAll(/--app-(?:url|launch-url-for-shortcuts-menu-item)=([^\s"]+)/g)) {
                    let host = hostOf(m[1]);
                    if (!installed[host])
                        installed[host] = app;
                }
            }
        } catch (e) {
            // Unvollständiger Eintrag, überspringen
        }
    }
}

function matches(host, domain) {
    domain = (domain || '').trim().toLowerCase();
    return domain && (host === domain || host.endsWith('.' + domain));
}

// Liefert immer ein Objekt: { host, name, icon, desktopId, known, mute }
// rules: Liste aus den Einstellungen [{ domain, name, icon, mute }]
function lookup(host, rules) {
    let age = Date.now() - scannedAt;
    if (!installed[host] && (age < 0 || age >= RESCAN_MS))
        scan();         // evtl. neu installierte PWA (age < 0: Uhr wurde zurückgestellt)
    let app = installed[host] || guess(host);
    let rule = (rules || []).find(r => matches(host, r.domain)) || null;
    return {
        host: host,
        name: (rule && rule.name) || (app && app.name) || host,
        icon: (rule && rule.icon) || (app && app.icon) || null,
        desktopId: app ? app.desktopId : null,
        wmClass: app ? app.wmClass : null,
        known: !!(app || (rule && rule.name)),
        mute: !!(rule && rule.mute)
    };
}

module.exports = { BROWSERS, scan, lookup };
