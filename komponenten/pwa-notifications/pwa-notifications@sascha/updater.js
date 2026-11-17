// Updates über GitHub-Releases: prüfen, herunterladen, an Ort und Stelle einspielen.
//
// Neue Version = Release mit Tag «vX.Y.Z» und dem Paket «<uuid>-X.Y.Z.tar.gz»
// (erzeugt tools/release.sh). Liegt die Erweiterung in einem Git-Checkout
// (Entwicklerinstallation, per Symlink), wird stattdessen «git pull» ausgeführt.
// Alles läuft asynchron über curl/tar, damit Cinnamon nie blockiert.

const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;

function run(argv, input) {
    return new Promise((resolve, reject) => {
        try {
            let flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE;
            if (input)
                flags |= Gio.SubprocessFlags.STDIN_PIPE;
            let proc = Gio.Subprocess.new(argv, flags);
            proc.communicate_utf8_async(input || null, null, (p, res) => {
                try {
                    let [, out, err] = p.communicate_utf8_finish(res);
                    resolve({ status: p.get_exit_status(), out: out || '', err: (err || '').trim() });
                } catch (e) {
                    reject(e);
                }
            });
        } catch (e) {
            reject(e);
        }
    });
}

// curl-Konfiguration über stdin, damit das Token nicht in der Prozessliste steht
function curlConfig(token, accept, version) {
    let lines = ['header = "Accept: ' + accept + '"',
                 'user-agent = "pwa-notifications/' + version + '"'];
    token = (token || '').replace(/[^A-Za-z0-9_]/g, '');
    if (token)
        lines.push('header = "Authorization: Bearer ' + token + '"');
    return lines.join('\n') + '\n';
}

// ---- Style Kit ---------------------------------------------------------------
// Wurde die Erweiterung über ein Style Kit installiert, kommen Updates nur noch über
// das Kit: keine eigene Suche, keine Meldung, keine Installation. Das Kit schreibt
// ${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json mit «kit», «komponenten» und
// «verwalten» (Befehl als Liste). Die Datei wird bei jedem Aufruf neu gelesen.
const KIT_KENNUNG = 'pwa-notifications';
const TOOL_NAME = 'PWA-Benachrichtigungen';

function kitDatei() {
    let base = GLib.getenv('XDG_CONFIG_HOME') || GLib.build_filenamev([GLib.get_home_dir(), '.config']);
    return GLib.build_filenamev([base, 'style-kit', 'verwaltet.json']);
}

// Kit-Fassung: Datei .kit-verwaltet neben metadata.json (Inhalt: Name des Kits). Dann bleibt die
// eigene Update-Funktion auch abgeschaltet, wenn verwaltet.json fehlt oder gelöscht wurde.
function kitMarke() {
    try {
        let [ok, bytes] = GLib.file_get_contents(GLib.build_filenamev(
            [GLib.get_user_data_dir(), 'cinnamon', 'extensions', 'pwa-notifications@sascha', '.kit-verwaltet']));
        if (!ok)
            return null;
        let name = new TextDecoder().decode(bytes).trim().slice(0, 80);
        return { kit: name || 'Style Kit', verwalten: null };
    } catch (e) {
        return null;
    }
}

// { kit, verwalten } wenn ein Style Kit die Erweiterung verwaltet, sonst null
function kitInfo() {
    return kitInfoDatei() || kitMarke();
}

function kitInfoDatei() {
    let data;
    try {
        let [ok, bytes] = GLib.file_get_contents(kitDatei());
        if (!ok)
            return null;
        data = JSON.parse(new TextDecoder().decode(bytes));
    } catch (e) {
        return null;    // fehlt oder kaputt: eigener Updater
    }
    if (!data || typeof data !== 'object' || Array.isArray(data))
        return null;
    let kit = data.kit, komp = data.komponenten, cmd = data.verwalten;
    if (typeof kit !== 'string' || !kit.trim() || !Array.isArray(komp) || !komp.includes(KIT_KENNUNG))
        return null;
    if (!(Array.isArray(cmd) && cmd.length && cmd.every(x => typeof x === 'string' && x)))
        cmd = null;
    return { kit: kit.trim(), verwalten: cmd };
}

// Name des Style Kits, das die Erweiterung verwaltet, sonst null
function kitVerwaltet() {
    let info = kitInfo();
    return info ? info.kit : null;
}

function kitMeldung(kit) {
    return 'Updates für ' + TOOL_NAME + ' kommen über das ' + kit + '.';
}

// Startet die Verwaltung des Kits (Befehl aus verwaltet.json, ohne Shell)
function kitOeffnen() {
    let info = kitInfo();
    if (!info || !info.verwalten)
        return false;
    try {
        Gio.Subprocess.new(info.verwalten, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
        return true;
    } catch (e) {
        return false;
    }
}

function compareVersions(a, b) {
    let pa = a.split('.').map(n => parseInt(n) || 0), pb = b.split('.').map(n => parseInt(n) || 0);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
        let d = (pa[i] || 0) - (pb[i] || 0);
        if (d)
            return d;
    }
    return 0;
}

// Liefert { version, newer, notes, page, assetApi, assetDownload } oder wirft mit verständlicher Meldung.
// Verwaltet ein Style Kit die Erweiterung: ohne Anfrage «kein Update» (newer: false, kit: Name).
async function check(repo, token, uuid, current) {
    let kit = kitVerwaltet();
    if (kit)
        return { version: current, newer: false, notes: '', page: null, assetApi: null,
                 assetDownload: null, kit: kit };
    if (!/^[\w.-]+\/[\w.-]+$/.test(repo || ''))
        throw new Error('Ungültige Update-Quelle «' + repo + '» (erwartet: besitzer/repository)');
    let r = await run(['curl', '-sS', '-L', '--max-time', '20', '-K', '-', '-w', '\n%{http_code}',
                       'https://api.github.com/repos/' + repo + '/releases/latest'],
                      curlConfig(token, 'application/vnd.github+json', current));
    if (r.status !== 0)
        throw new Error('Keine Verbindung zu GitHub (' + r.err + ')');
    let cut = r.out.lastIndexOf('\n');
    let code = parseInt(r.out.slice(cut + 1));
    if (code === 404)
        throw new Error(token
            ? 'Kein Release gefunden – oder das Token hat keinen Zugriff auf ' + repo + '.'
            : 'Kein Release gefunden. Ist das Repository privat? Dann unter «Quelle» ein Zugangstoken eintragen.');
    if (code === 401)
        throw new Error('Das Zugangstoken ist ungültig oder abgelaufen.');
    if (code === 403)
        throw new Error('GitHub verweigert den Zugriff (zu viele Abfragen?). Später nochmals versuchen.');
    if (code !== 200)
        throw new Error('Unerwartete Antwort von GitHub (HTTP ' + code + ').');

    let rel = JSON.parse(r.out.slice(0, cut));
    let version = (rel.tag_name || '').replace(/^v/i, '');
    // landet in der curl-Konfiguration: nur reine Versionsnummern zulassen
    if (!/^\d+(\.\d+)*$/.test(version))
        throw new Error('Ungültige Versionsnummer im Release: «' + rel.tag_name + '»');
    let assets = rel.assets || [];
    let asset = assets.find(a => a.name === uuid + '-' + version + '.tar.gz')
             || assets.find(a => a.name.endsWith('.tar.gz'));
    return {
        version: version,
        newer: !!version && compareVersions(version, current) > 0,
        notes: rel.body || '',
        page: rel.html_url,
        assetApi: asset ? asset.url : null,
        assetDownload: asset ? asset.browser_download_url : null
    };
}

function readMetadata(dir) {
    let [, bytes] = GLib.file_get_contents(GLib.build_filenamev([dir, 'metadata.json']));
    return JSON.parse(new TextDecoder().decode(bytes));
}

// Git-Checkout, aus dem die Erweiterung läuft (Entwicklerinstallation), sonst null
async function gitCheckout(extDir) {
    let r = await run(['realpath', extDir]);
    let real = r.out.trim() || extDir;
    let root = GLib.path_get_dirname(real);
    return GLib.file_test(GLib.build_filenamev([root, '.git']), GLib.FileTest.EXISTS) ? root : null;
}

// Spielt info.version ein. Danach muss die Erweiterung neu geladen werden.
async function install(info, token, uuid, extDir, cacheDir) {
    let kit = kitVerwaltet();
    if (kit)
        throw new Error(kitMeldung(kit));
    let checkout = await gitCheckout(extDir);
    if (checkout) {
        let r = await run(['git', '-C', checkout, 'pull', '--ff-only']);
        if (r.status !== 0)
            throw new Error('git pull fehlgeschlagen: ' + r.err);
        return 'git';
    }
    if (!info.assetApi)
        throw new Error('Das Release enthält kein Installationspaket (' + uuid + '-' + info.version + '.tar.gz).');

    // Arbeitsordner neben der Erweiterung: gleiches Dateisystem, der Tausch unten ist also ein
    // rename und kann nicht mittendrin abbrechen
    let parent = GLib.path_get_dirname(extDir);
    let r = await run(['mktemp', '-d', '-p', parent, '.pwan-update-XXXXXX']);
    let tmp = r.out.trim();
    if (r.status !== 0 || !tmp.startsWith(parent + '/.pwan-update-'))
        throw new Error('Kein Arbeitsordner in ' + parent + ' möglich: ' + r.err);
    let keep = false;   // true: tmp enthält die einzige Kopie der alten Version
    try {
        let pkg = tmp + '/paket.tar.gz';
        // Mit Token über die API (private Repos), sonst über den öffentlichen Download-Link.
        // curl gibt den Authorization-Header bei der Weiterleitung zum Speicher nicht weiter.
        let url = (token ? info.assetApi : info.assetDownload) || info.assetApi || info.assetDownload;
        r = await run(['curl', '-sS', '-fL', '--max-time', '180', '-K', '-', '-o', pkg, url],
                          curlConfig(token, 'application/octet-stream', info.version));
        if (r.status !== 0)
            throw new Error('Download fehlgeschlagen: ' + r.err);

        let out = tmp + '/inhalt';
        GLib.mkdir_with_parents(out, 0o700);
        r = await run(['tar', '-xzf', pkg, '-C', out, '--no-same-owner']);
        if (r.status !== 0)
            throw new Error('Paket lässt sich nicht entpacken: ' + r.err);

        // Paket enthält <uuid>/ direkt oder in einem Unterordner
        let src = [out + '/' + uuid].concat(listDirs(out).map(d => d + '/' + uuid))
            .find(d => GLib.file_test(d + '/metadata.json', GLib.FileTest.EXISTS));
        if (!src)
            throw new Error('Im Paket fehlt der Ordner ' + uuid + '.');
        let meta = readMetadata(src);
        if (meta.uuid !== uuid || meta.version !== info.version)
            throw new Error('Paket passt nicht (' + meta.uuid + ' ' + meta.version + ').');

        // Tauschen: alte Version in den Arbeitsordner, neue an ihren Platz.
        // -T: Ist das Ziel ein Ordner, nie in ihn hinein verschieben.
        let old = tmp + '/alt';
        r = await run(['mv', '-T', extDir, old]);
        if (r.status !== 0)
            throw new Error('Alte Version lässt sich nicht verschieben: ' + r.err);
        r = await run(['mv', '-T', src, extDir]);
        if (r.status !== 0) {
            // Zurück zur alten Version; ein halb angelegter Ordner darf nicht im Weg stehen
            keep = true;
            await run(['rm', '-rf', extDir]);
            let back = await run(['mv', '-T', old, extDir]);
            if (back.status !== 0)
                throw new Error('Neue Version lässt sich nicht einspielen (' + r.err + '), und die alte ' +
                                'nicht zurückholen (' + back.err + '). Sie liegt in ' + old + '.');
            keep = false;
            throw new Error('Neue Version lässt sich nicht einspielen: ' + r.err);
        }

        // Alte Version für den Notfall aufheben; scheitert das, ist sie eben weg
        let backup = cacheDir + '/vorherige-version';
        await run(['rm', '-rf', backup]);
        GLib.mkdir_with_parents(backup, 0o700);
        await run(['mv', '-T', old, backup + '/' + uuid]);
        return 'paket';
    } finally {
        if (!keep)
            run(['rm', '-rf', tmp]).catch(() => {});
    }
}

function listDirs(path) {
    let dirs = [];
    let enumerator = Gio.File.new_for_path(path).enumerate_children('standard::name,standard::type', 0, null);
    let info;
    while ((info = enumerator.next_file(null)))
        if (info.get_file_type() === Gio.FileType.DIRECTORY)
            dirs.push(path + '/' + info.get_name());
    return dirs;
}

// Token der angemeldeten GitHub-CLI, falls vorhanden – so braucht die eigene
// Installation für ein privates Repository kein eingetragenes Token
async function ghToken() {
    if (!GLib.find_program_in_path('gh'))
        return '';
    try {
        let r = await run(['gh', 'auth', 'token']);
        return r.status === 0 ? r.out.trim() : '';
    } catch (e) {
        return '';
    }
}

module.exports = { check, install, compareVersions, gitCheckout, ghToken,
                   kitInfo, kitVerwaltet, kitMeldung, kitOeffnen };
