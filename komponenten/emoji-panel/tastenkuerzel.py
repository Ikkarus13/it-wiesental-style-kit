#!/usr/bin/env python3
"""Cinnamon-Tastenkürzel des Emoji-Panels anlegen, entfernen oder prüfen.

    tastenkuerzel.py installieren <befehl>   (vorhandene Belegung bleibt erhalten)
    tastenkuerzel.py entfernen

Cinnamon erwartet Einträge der Form «customN» (die Systemeinstellungen rechnen mit der Zahl),
und bemerkt Änderungen nur, wenn sich «custom-list» ändert – dafür wird wie in Cinnamons
eigenem Einstellungsmodul «__dummy__» abwechselnd hinzugefügt und entfernt.
"""
import sys

from gi.repository import Gio

KB = 'org.cinnamon.desktop.keybindings'
CUSTOM = KB + '.custom-keybinding'
PATH = '/org/cinnamon/desktop/keybindings/custom-keybindings/'
DUMMY = '__dummy__'
NAME = 'Emoji-Panel'
LEGACY = 'emoji-panel'          # alte ID bis 1.0.0
DEFAULT = ['<Super>period', '<Super>semicolon']


def entry(cid):
    return Gio.Settings.new_with_path(CUSTOM, PATH + cid + '/')


def toggle_dummy(lst):
    if DUMMY in lst:
        lst.remove(DUMMY)
    else:
        lst.append(DUMMY)


def ours(cid):
    if cid == LEGACY:
        return True
    # nur am Namen erkennen – ein selbst angelegtes Kürzel mit demselben Befehl bleibt unberührt
    return entry(cid).get_string('name') == NAME


def free_id(lst):
    used = {int(c[6:]) for c in lst if c.startswith('custom') and c[6:].isdigit()}
    n = 0
    while n in used:
        n += 1
    return f'custom{n}'


def reset(cid):
    c = entry(cid)
    for key in ('name', 'command', 'binding'):
        c.reset(key)


def install(command):
    gs = Gio.Settings.new(KB)
    lst = list(gs.get_strv('custom-list'))
    mine = [c for c in lst if c != DUMMY and ours(c)]
    binding = None
    for cid in mine:
        if binding is None:
            binding = list(entry(cid).get_strv('binding'))
    new = [c for c in mine if c != LEGACY][:1]
    cid = new[0] if new else free_id(lst)
    for old in mine:
        if old != cid:
            lst.remove(old)
            reset(old)
    c = entry(cid)
    c.set_string('name', NAME)
    c.set_string('command', command)
    c.set_strv('binding', binding if binding is not None else DEFAULT)
    if cid not in lst:
        lst.insert(0, cid)
    toggle_dummy(lst)
    gs.set_strv('custom-list', lst)
    Gio.Settings.sync()
    keys = c.get_strv('binding')
    print('Tastenkürzel:', ', '.join(keys) if keys else 'keines')


def remove():
    gs = Gio.Settings.new(KB)
    lst = list(gs.get_strv('custom-list'))
    for cid in [c for c in lst if c != DUMMY and ours(c)]:
        lst.remove(cid)
        reset(cid)
    toggle_dummy(lst)
    gs.set_strv('custom-list', lst)
    Gio.Settings.sync()


if __name__ == '__main__':
    src = Gio.SettingsSchemaSource.get_default()
    if not src.lookup(KB, True):
        sys.exit('Kein Cinnamon gefunden – Tastenkürzel bitte manuell einrichten.')
    if sys.argv[1:2] == ['installieren'] and len(sys.argv) >= 3:
        install(sys.argv[2])
    elif sys.argv[1:2] == ['entfernen']:
        remove()
    else:
        sys.exit(__doc__)
