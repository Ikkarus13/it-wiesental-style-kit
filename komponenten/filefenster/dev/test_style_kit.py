#!/usr/bin/env python3
"""Verwaltung durch ein Style Kit testen (style-kit/verwaltet.json):

  * Datei fehlt, kaputt oder ohne «filefenster» → eigener Updater wie bisher
  * mit «filefenster»: check() ohne Anfrage «kein Update», download/stage/activate
    verweigern, kein Hinweispunkt, keine automatische Suche, der Dialog zeigt
    «Updates für FileFenster kommen über das …» und den Knopf «… öffnen»

Eigenes HOME/XDG_CONFIG_HOME in einem Temp-Ordner, kein Netz:

    DISPLAY=:99 python3 dev/test_style_kit.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

if os.environ.get("DISPLAY") in (None, "", ":0", ":0.0"):
    sys.exit("Nur auf dem Test-Bildschirm starten (z. B. DISPLAY=:99)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="ff-kit-")
os.environ.update(HOME=TMP, XDG_CONFIG_HOME=os.path.join(TMP, "config"),
                  XDG_CACHE_HOME=os.path.join(TMP, "cache"),
                  XDG_DATA_HOME=os.path.join(TMP, "data"),
                  FILEFENSTER_CONFIG_DIR=os.path.join(TMP, "cfg"),
                  FILEFENSTER_UPDATE_API="http://127.0.0.1:9", NO_PROXY="*")
for k in ("GITHUB_TOKEN", "GH_TOKEN", "FILEFENSTER_GITHUB_TOKEN"):
    os.environ.pop(k, None)
sys.path.insert(0, ROOT)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from filefenster import update_ui, updater  # noqa: E402

KIT = os.path.join(TMP, "config", "style-kit", "verwaltet.json")
os.makedirs(os.path.dirname(KIT))
fails = 0


def check(name, cond):
    global fails
    print(("  ok    " if cond else "  FEHLER ") + name)
    fails += 0 if cond else 1


def kit(data):
    with open(KIT, "w") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))


def refuses(fn, *args):
    try:
        fn(*args)
    except updater.UpdateError as e:
        return "kommen über das Test Kit" in str(e)
    return False


# 1. ohne Verwaltung: eigener Updater (Fake-API nicht erreichbar → Fehler statt «kein Update»)
check("ohne Datei nicht verwaltet", updater.kit_verwaltet() is None)
kit("{kaputt")
check("kaputte Datei nicht verwaltet", updater.kit_verwaltet() is None)
kit({"kit": "Test Kit", "komponenten": ["startklar"], "verwalten": ["true"]})
check("fremde Kennung nicht verwaltet", updater.kit_verwaltet() is None)
try:
    updater.check()
    check("eigene Suche fragt GitHub", False)
except updater.UpdateError:
    check("eigene Suche fragt GitHub", True)

# 2. verwaltet
MARK = os.path.join(TMP, "kit-gestartet")
kit({"kit": "Test Kit", "komponenten": ["filefenster"],
     "verwalten": ["/usr/bin/touch", MARK]})
check("verwaltet", updater.kit_verwaltet() == "Test Kit")
check("check() ohne Anfrage: kein Update", updater.check() == (None, False))
check("download verweigert", refuses(updater.download, None))
check("stage verweigert", refuses(updater.stage, "/nicht/da.tar.gz"))
check("activate verweigert", refuses(updater.activate, None))
check("install verweigert", refuses(updater.install, "/nicht/da.tar.gz"))
check("keine automatische Suche", not update_ui.UpdateManager.auto_possible())


class FakeApp:
    def get_windows(self):
        return []


class FakeMgr(update_ui.UpdateManager):
    def __init__(self):            # ohne Timer
        self.app, self.config, self.release, self.checking = FakeApp(), update_ui.get_config(), None, False
        self.dialog, self._callbacks = None, []

    def check(self, callback=None, manual=False):
        raise AssertionError("darf nicht suchen")


mgr = FakeMgr()
mgr.config.set("update_known", "99.0.0")
check("kein Hinweispunkt", mgr.badge_version() == "")

dlg = update_ui.UpdateDialog(None, mgr)
dlg.mode = "installed"
dlg.start_check()
while Gtk.events_pending():
    Gtk.main_iteration()
check("Dialog-Zustand managed", dlg.state == "managed")
check("Meldung im Dialog", dlg.info.get_text() == "Updates für FileFenster kommen über das Test Kit.")
check("Knopf «Test Kit öffnen»", dlg.btn_kit.get_visible()
      and dlg.btn_kit.get_label() == "Test Kit öffnen")
check("kein «Jetzt aktualisieren»", not dlg.btn_update.get_visible())
check("kein Schalter «automatisch suchen»", not dlg.auto_check.get_visible())
dlg.btn_kit.clicked()
for _ in range(50):
    if os.path.exists(MARK):
        break
    time.sleep(0.05)
check("Knopf startet «verwalten»", os.path.exists(MARK))

kit({"kit": "Test Kit", "komponenten": ["filefenster"], "verwalten": "touch " + MARK + "2"})
dlg = update_ui.UpdateDialog(None, mgr)
dlg.start_check()
check("Shell-String: kein Knopf", dlg.state == "managed" and not dlg.btn_kit.get_visible())
dlg.destroy()

os.remove(KIT)
check("Datei weg: Hinweispunkt wieder möglich", updater.kit_verwaltet() is None)

shutil.rmtree(TMP, ignore_errors=True)
print("Fehler:", fails)
sys.exit(1 if fails else 0)
