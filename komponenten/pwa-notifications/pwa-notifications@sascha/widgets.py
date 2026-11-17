#!/usr/bin/python3
"""Eigene Anzeigen für die Einstellungsseite (laden in cinnamon-settings)."""

import configparser
import json
import os
import re
import subprocess
from datetime import datetime

from JsonSettingsWidgets import *  # SettingsWidget, Gtk, GLib …
from gi.repository import GLib, Gtk

HERE = os.path.dirname(os.path.abspath(__file__))
KIT_KENNUNG = 'pwa-notifications'


def kit_info():
    """Wie kitInfo() in updater.js: {'kit', 'verwalten'} wenn ein Style Kit verwaltet."""
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    try:
        with open(os.path.join(base, 'style-kit', 'verwaltet.json'), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    kit, komp, cmd = data.get('kit'), data.get('komponenten'), data.get('verwalten')
    if not isinstance(kit, str) or not kit.strip() or not isinstance(komp, list) or KIT_KENNUNG not in komp:
        return None
    if not (isinstance(cmd, list) and cmd and all(isinstance(x, str) and x for x in cmd)):
        cmd = None
    return {'kit': kit.strip(), 'verwalten': cmd}


def _label(text='', dim=False):
    label = Gtk.Label(label=text, halign=Gtk.Align.START, xalign=0)
    label.set_line_wrap(True)
    label.set_selectable(True)
    if dim:
        label.get_style_context().add_class('dim-label')
    return label


class DetectedApps(SettingsWidget):
    """Liste der installierten Web-Apps, die automatisch erkannt werden (wie apps.js)."""

    def __init__(self, info, key, settings):
        SettingsWidget.__init__(self)
        self.label = _label()
        self.pack_start(self.label, True, True, 0)
        self.refresh()

    def refresh(self):
        folder = os.path.join(GLib.get_user_data_dir(), 'applications')
        rows = []
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            names = []
        browsers = {'msedge': 'Edge', 'chrome': 'Chrome', 'brave': 'Brave', 'vivaldi': 'Vivaldi',
                    'opera': 'Opera', 'chromium': 'Chromium'}
        for name in names:
            prefix = name.split('-')[0]
            if not name.endswith('.desktop') or prefix not in browsers:
                continue
            entry = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                entry.read(os.path.join(folder, name), encoding='utf-8')
                app = re.sub(r'\s*\(PWA\)\s*$', '', entry['Desktop Entry'].get('Name', name))
            except Exception:
                continue
            # Edge: --app-url, Chrome: nur die Adressen der Schnellaktionen
            hosts = []
            for section in entry.sections():
                for url in re.findall(r'--app-(?:url|launch-url-for-shortcuts-menu-item)=([^\s"]+)',
                                      entry[section].get('Exec', '')):
                    host = re.sub(r'^[a-z]+://', '', url, flags=re.I).split('/')[0].split('?')[0].split(':')[0].lower()
                    if host not in hosts:
                        hosts.append(host)
            rows.append(f'{app}  –  {", ".join(hosts) or "Adresse wird über den Namen erkannt"}  ({browsers[prefix]})')
        self.label.set_text('\n'.join(rows) if rows else
                            'Keine Web-Apps aus Edge oder Chrome gefunden. Regeln unten ergänzen.')


class UpdateStatus(SettingsWidget):
    """Installierte und neueste Version, letzte Prüfung, Fehlermeldung."""

    KEYS = ('update-last-check', 'update-latest-version', 'update-message', 'update-available')

    def __init__(self, info, key, settings):
        SettingsWidget.__init__(self)
        self.settings = settings
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.version = _label()
        self.detail = _label(dim=True)
        self.message = _label()
        box.pack_start(self.version, False, False, 0)
        box.pack_start(self.detail, False, False, 0)
        box.pack_start(self.message, False, False, 0)
        # Verwaltung durch ein Style Kit: Hinweis und Knopf zum Öffnen des Kits
        self.kit_button = Gtk.Button(halign=Gtk.Align.START)
        self.kit_button.connect('clicked', self._open_kit)
        self.kit_button.set_no_show_all(True)
        box.pack_start(self.kit_button, False, False, 0)
        self.pack_start(box, True, True, 0)
        for k in self.KEYS:
            settings.listen(k, self.refresh)
        self.refresh()

    def _get(self, key):
        try:
            return self.settings.get_value(key)
        except KeyError:
            return None

    def _open_kit(self, *args):
        kit = kit_info()
        if kit and kit['verwalten']:
            try:
                subprocess.Popen(kit['verwalten'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                pass

    def refresh(self, *args):
        try:
            with open(os.path.join(HERE, 'metadata.json'), encoding='utf-8') as f:
                installed = json.load(f).get('version', '?')
        except (OSError, ValueError):
            installed = '?'
        latest = self._get('update-latest-version') or ''
        checked = self._get('update-last-check') or ''
        message = self._get('update-message') or ''

        if self._get('update-available') and latest:
            text = f'Installiert: <b>{installed}</b>  ·  Verfügbar: <b>{latest}</b>'
        elif latest:
            text = f'Installiert: <b>{installed}</b>  ·  aktuell'
        else:
            text = f'Installiert: <b>{installed}</b>'
        self.version.set_markup(text)

        if checked:
            try:
                when = datetime.fromisoformat(checked.replace('Z', '+00:00')).astimezone()
                self.detail.set_text('Zuletzt geprüft: ' + when.strftime('%d.%m.%Y, %H:%M'))
            except ValueError:
                self.detail.set_text('')
        else:
            self.detail.set_text('Noch nicht geprüft')

        self.message.set_markup(f'<span foreground="#e01b24">{GLib.markup_escape_text(message)}</span>'
                                if message else '')
        self.message.set_visible(bool(message))

        kit = kit_info()
        if kit:
            self.version.set_markup(f'Installiert: <b>{GLib.markup_escape_text(installed)}</b>')
            self.detail.set_text(f'Updates für PWA-Benachrichtigungen kommen über das {kit["kit"]}.')
            self.message.set_visible(False)
            if kit['verwalten']:
                self.kit_button.set_label(f'{kit["kit"]} öffnen')
        self.kit_button.set_visible(bool(kit and kit['verwalten']))
