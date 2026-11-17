// Erzeugt das Stylesheet für die Benachrichtigungen aus den Einstellungen und
// hängt es ins laufende Cinnamon-Theme ein – unabhängig vom installierten Theme.
//
// Gestaltet werden nur Meldungen mit der Klasse StyleManager.styleClass (setzt notifications.js).
// Jedes neu erzeugte Stylesheet bekommt eine eigene Klasse («pwan-g3», «pwan-g4» …): St merkt
// sich berechnete Stile pro Klassenkombination und würde neue Meldungen sonst im alten Stil zeigen.
// Die Selektoren sind bewusst spezifischer als die üblichen Theme-Regeln
// (#notification, .menu #notification, …), damit sie unabhängig von der Ladereihenfolge gelten.

const Main = imports.ui.main;
const Clutter = imports.gi.Clutter;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const St = imports.gi.St;

const PRESETS = {
    dark: { bg: [43, 43, 43], fg: [255, 255, 255], card: [50, 50, 50], shadow: 0.5 },
    light: { bg: [249, 249, 249], fg: [26, 26, 26], card: [253, 253, 253], shadow: 0.22 }
};

function parseColor(str, fallback) {
    try {
        let [ok, c] = Clutter.Color.from_string(str);
        if (ok)
            return [c.red, c.green, c.blue];
    } catch (e) {
        // ungültige Farbe
    }
    return fallback;
}

function rgba(c, a) {
    return 'rgba(' + c[0] + ', ' + c[1] + ', ' + c[2] + ', ' + (+a).toFixed(2) + ')';
}

function mix(a, b, t) {
    return a.map((v, i) => Math.round(v * (1 - t) + b[i] * t));
}

function luminance(c) {
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255;
}

// Hell oder dunkel wie das System (Mint: org.x.apps.portal, sonst GNOME), sonst nach Themename
// Hell/Dunkel-Wunsch des Systems, in dieser Reihenfolge; «default» fällt auf den nächsten durch.
// Die Erweiterung beobachtet alle, die es gibt (extension.js _watchColorScheme).
const COLOR_SCHEMES = [['org.x.apps.portal', 'color-scheme'],
                       ['org.gnome.desktop.interface', 'color-scheme']];

function systemPrefersDark() {
    let source = Gio.SettingsSchemaSource.get_default();
    for (let [schema, key] of COLOR_SCHEMES) {
        let found = source.lookup(schema, true);
        if (found && found.has_key(key)) {
            let value = new Gio.Settings({ schema_id: schema }).get_string(key);
            if (value === 'prefer-dark')
                return true;
            if (value === 'prefer-light')
                return false;
        }
    }
    let theme = new Gio.Settings({ schema_id: 'org.cinnamon.theme' }).get_string('name');
    return /dark/i.test(theme);
}

// cfg: Objekt mit den Einstellungswerten (Schlüssel mit _ statt -), cls: Klasse der Meldungen
function build(cfg, cls) {
    let maxBody = Math.max(1, cfg.max_lines) * 1.3;
    let D = sel => ['#notification.' + cls + ' ', '.menu #notification.' + cls + ' ',
                    '.popup-menu #notification.' + cls + ' '].map(p => p + sel).join(',\n');

    // Immer: behebt das Überlappen in der Benachrichtigungsliste, Kopfzeile, Textlänge
    let css = [
        '.pwan-title { min-height: 1.35em; }',
        '.pwan-header { spacing: 6px; padding-bottom: 2px; }',
        '.pwan-header-label { font-size: 0.9em; }',
        '.pwan-time { font-size: 0.85em; }',
        // Gruppen-Kopfzeilen liegen direkt auf dem Menü: neutrale Farben, passen zu hellen und dunklen Themes
        '.pwan-group-header { spacing: 8px; padding: 10px 4px 4px 6px; }',
        '.pwan-group-title { font-weight: bold; }',
        '.pwan-group-count { font-size: 0.85em; padding: 0 7px; border-radius: 9px; background-color: rgba(128, 128, 128, 0.28); }',
        '.pwan-group-button { padding: 4px; border-radius: 4px; }',
        '.pwan-group-button:hover { background-color: rgba(128, 128, 128, 0.25); }',
        D('#notification-scrollview') + ' { max-height: ' + maxBody + 'em; }'
    ];
    if (cfg.style_preset === 'theme')
        return css.join('\n');

    let p;
    if (cfg.style_preset === 'custom') {
        let bg = parseColor(cfg.custom_background, PRESETS.dark.bg);
        let fg = parseColor(cfg.custom_text, PRESETS.dark.fg);
        p = { bg: bg, fg: fg, card: mix(bg, fg, 0.03), shadow: luminance(bg) < 0.5 ? 0.5 : 0.22 };
    } else {
        let dark = cfg.style_preset === 'dark' || (cfg.style_preset !== 'light' && systemPrefersDark());
        p = PRESETS[dark ? 'dark' : 'light'];
    }

    let W = cfg.width, R = cfg.corner_radius, fg = p.fg;
    let border = cfg.border ? '1px solid ' + rgba(fg, 0.09) : '0';
    let shadow = cfg.shadow ? '0 8px 22px ' + rgba([0, 0, 0], p.shadow) : 'none';

    css.push(
        '#notification.' + cls + ',\n#notification.' + cls + '.multi-line-notification,\n' +
        '#notification.' + cls + '.notification-with-image {\n' +
        '  width: ' + W + 'px;\n  padding: 14px 16px;\n  spacing-rows: 4px;\n  spacing-columns: 12px;\n' +
        '  border-radius: ' + R + 'px;\n  border: ' + border + ';\n' +
        '  color: ' + rgba(fg, 1) + ';\n  background-color: ' + rgba(p.bg, cfg.opacity / 100) + ';\n' +
        '  box-shadow: ' + shadow + ';\n  margin: 6px 12px 12px 12px;\n}',

        // In der Benachrichtigungsliste: flache Karten ohne Schatten
        ['.menu', '.popup-menu'].map(m => m + ' #notification.' + cls + ',\n' +
                                          m + ' #notification.' + cls + '.multi-line-notification').join(',\n') + ' {\n' +
        '  margin: 0;\n  box-shadow: none;\n  border: 1px solid ' + rgba(fg, 0.08) + ';\n' +
        '  border-radius: ' + Math.min(R, 8) + 'px;\n  background-color: ' + rgba(p.card, 1) + ';\n}',
        '.menu #notification.' + cls + ':hover, .popup-menu #notification.' + cls + ':hover {\n' +
        '  background-color: ' + rgba(mix(p.card, fg, 0.05), 1) + ';\n}',

        D('.url-highlighter') + ' { color: ' + rgba(fg, 0.78) + '; }',
        D('.pwan-header-label') + ' { color: ' + rgba(fg, 0.65) + '; }',
        D('.pwan-time') + ' { color: ' + rgba(fg, 0.55) + '; }',
        D('#notification-body') + ' { spacing: 4px; }',
        D('#notification-actions') + ' { spacing: 8px; padding-top: 8px; }',
        D('.notification-button') + ' {\n  padding: 6px 12px;\n  border-radius: 4px;\n' +
        '  border: 1px solid ' + rgba(fg, 0.08) + ';\n  color: ' + rgba(fg, 1) + ';\n' +
        '  background-color: ' + rgba(fg, 0.06) + ';\n}',
        D('.notification-button:hover') + ' { background-color: ' + rgba(fg, 0.10) + '; }',
        D('.notification-button:active') + ' { color: ' + rgba(fg, 0.78) + '; background-color: ' + rgba(fg, 0.03) + '; }'
    );
    return css.join('\n\n');
}

// Lädt/ersetzt das erzeugte Stylesheet und hält es über Theme-Wechsel hinweg aktiv
var StyleManager = class StyleManager {
    // onClassChange(alt, neu): vorhandene Meldungen auf die neue Klasse umstellen
    constructor(uuid, onClassChange) {
        this._onClassChange = onClassChange || null;
        this._dir = GLib.build_filenamev([GLib.get_user_cache_dir(), uuid]);
        GLib.mkdir_with_parents(this._dir, 0o700);
        this._builder = null;
        this._css = null;
        this._path = null;
        this._theme = null;
        this._counter = 0;
        this.styleClass = 'pwan-g0';
        this._themeSignal = Main.themeManager.connect('theme-set', () => this._load());
    }

    // builder: cls => CSS-Text für Meldungen mit der Klasse cls
    apply(builder) {
        this._builder = builder;
        this._load();
    }

    _load() {
        this._unload();
        if (!this._builder)
            return;
        let cls = 'pwan-g' + (++this._counter);
        let path = GLib.build_filenamev([this._dir, 'style-' + this._counter + '.css']);
        this._css = this._builder(cls);
        GLib.file_set_contents(path, this._css);
        let previous = this.styleClass;
        this.styleClass = cls;
        if (this._onClassChange && previous !== cls)
            this._onClassChange(previous, cls);
        try {
            this._theme = St.ThemeContext.get_for_stage(global.stage).get_theme();
            this._theme.load_stylesheet(path);
            this._path = path;
        } catch (e) {
            global.logError('pwa-notifications: Stylesheet: ' + e);
            GLib.unlink(path);
        }
    }

    _unload() {
        if (this._theme && this._path) {
            try {
                this._theme.unload_stylesheet(this._path);
            } catch (e) {
                // Theme wurde inzwischen ersetzt
            }
        }
        if (this._path)
            GLib.unlink(this._path);
        this._path = null;
        this._theme = null;
    }

    destroy() {
        Main.themeManager.disconnect(this._themeSignal);
        this._unload();
    }
};

module.exports = { build, StyleManager, systemPrefersDark, COLOR_SCHEMES };
