"""Startklar – ein Startmenü im Stil von Windows 11 für Linux Mint (Cinnamon).

Läuft als kleiner Hintergrunddienst (Gtk.Application, D-Bus-Name APP_ID);
das Cinnamon-Applet startklar@sascha ruft beim Klick bzw. bei der Super-Taste
die Aktion „toggle“ auf und übergibt dabei Monitor und Panel-Lage.
"""
import os

VERSION = "0.4.0"
APP_ID = "org.startklar.App"
DBUS_PATH = "/org/startklar/App"
SIGNAL_IFACE = "org.startklar.Menu"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
