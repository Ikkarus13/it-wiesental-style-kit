"""Was das Paket enthält: Komponenten, Browser und Web-Apps.

Jede Komponente liegt vollständig im Paket unter komponenten/<id>/ (übernommen mit
tools/komponente_uebernehmen.sh) und wird nach ~/.local/share/<paket>/<id> kopiert.
Von dort läuft ihr eigener Installer.
"""
import json
import os
from dataclasses import dataclass, field

_PAKET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cloud_sync_name():
    """Produktname des OneDrive-Clients: Branding des Kits (branding/cloud-sync.json),
    sonst das Branding, das die Komponente selbst mitbringt."""
    for pfad in (os.path.join(_PAKET, "branding", "cloud-sync.json"),
                 os.path.join(_PAKET, "komponenten", "onedrive", "onedrive_mint", "branding.json")):
        try:
            with open(pfad, encoding="utf-8") as f:
                name = json.load(f).get("produkt")
            if name:
                return name
        except (OSError, ValueError):
            pass
    return "Cloud Sync"


CLOUD_SYNC_NAME = _cloud_sync_name()


@dataclass(frozen=True)
class Komponente:
    id: str
    name: str
    beschreibung: str
    pakete: tuple = ()                   # apt-Pakete, die vorher da sein müssen
    braucht_browser: bool = False        # Browser-Seite im Assistenten anbieten
    hinweis: str = ""                    # kurzer Zusatz in der Auswahl
    standard: bool = True                # bei «Auswahl» vorab angehakt


BASIS_PAKETE = ("python3", "python3-gi", "gir1.2-gtk-3.0", "curl", "tar")

KOMPONENTEN = (
    Komponente(
        "design", "Design im Stil von Windows 11",
        "Fenster, Leiste und Menüs im Stil von Windows 11 (hell oder dunkel). "
        "Freies Design Win11-gtk-theme (GPL-3).",
        pakete=("gtk2-engines-murrine", "gtk2-engines-pixbuf")),
    Komponente(
        "startklar", "Startklar – Startmenü",
        "Startmenü wie Windows 11 mit Suche, Angeheftet und Empfohlen. "
        "Ersetzt das bisherige Menü, die Windows-Taste öffnet es.", pakete=("openssl",)),
    Komponente(
        "filefenster", "FileFenster – Explorer",
        "Datei-Explorer im Stil von Windows 11 mit Tabs, Vorschau und ZIP. "
        "Wird zum Standard-Dateimanager.",
        pakete=("openssl", "p7zip-full", "poppler-utils", "ffmpegthumbnailer",
                "bubblewrap", "gvfs-backends")),
    Komponente(
        "tray-collapse", "Infobereich-Überlauf",
        "Pfeil in der Leiste, der Tray-Symbole wie unter Windows in ein "
        "Überlauffenster einklappt."),
    Komponente(
        "pwa-notifications", "PWA-Benachrichtigungen",
        "Meldungen von Outlook, Teams & Co. als Windows-11-Karten, "
        "gruppierte Mitteilungsliste.", braucht_browser=True),
    Komponente(
        "pwa-tray", "PWA-Tray",
        "Schliessen von Teams minimiert ins Tray, Symbol mit Status und "
        "Zähler.", pakete=("python3-xlib", "gir1.2-xapp-1.0"),
        braucht_browser=True, hinweis="für Teams in Edge"),
    Komponente(
        "emoji-panel", "Emoji-Panel",
        "Windows + . öffnet Emojis, Symbole und den Zwischenablage-Verlauf.",
        pakete=("python3-xlib", "ibus-data", "gir1.2-ibus-1.0", "gir1.2-atspi-2.0")),
    Komponente(
        "pwa-link-organizer", "PWA link organizer",
        "Links aus Outlook (Chrome-App) öffnen sich in Microsoft Edge.", braucht_browser=True,
        hinweis="braucht Chrome und Edge"),
    Komponente(
        "onedrive", CLOUD_SYNC_NAME,
        "Synchronisierung mit Microsoft OneDrive und SharePoint-Bibliotheken: "
        "Dateien bei Bedarf, Status im Dateimanager.", hinweis="für Microsoft OneDrive",
        pakete=("python3-gi", "gir1.2-gtk-3.0", "gir1.2-ayatanaappindicator3-0.1",
                "fuse3", "libnotify-bin", "xdg-utils", "curl", "gir1.2-xapp-1.0")),
)

NACH_ID = {k.id: k for k in KOMPONENTEN}


@dataclass(frozen=True)
class Browser:
    id: str
    name: str
    paket: str
    befehl: str               # Programm, an dem man «schon installiert» erkennt
    konfig: str               # Ordner unter ~/.config
    wm_prefix: str            # Fensterklasse der Web-Apps: <prefix><app-id>
    desktop_prefix: str       # Startmenü-Einträge der Web-Apps


BROWSER = (
    Browser("edge", "Microsoft Edge", "microsoft-edge-stable", "microsoft-edge",
            "microsoft-edge", "crx__", "msedge-"),
    Browser("chrome", "Google Chrome", "google-chrome-stable", "google-chrome",
            "google-chrome", "crx_", "chrome-"),
)
BROWSER_NACH_ID = {b.id: b for b in BROWSER}


@dataclass(frozen=True)
class WebApp:
    id: str
    name: str
    url: str
    browser_empfohlen: str
    warum: str = ""
    weitere_urls: tuple = field(default=())


WEBAPPS = (
    WebApp("outlook", "Outlook", "https://outlook.cloud.microsoft/mail/", "chrome",
           "In Chrome kommen die Mail-Meldungen zuverlässig an."),
    WebApp("teams", "Microsoft Teams", "https://teams.cloud.microsoft/", "edge",
           "PWA-Tray zeigt den Teams-Status für Edge."),
)
WEBAPP_NACH_ID = {w.id: w for w in WEBAPPS}

# Bekannte App-IDs (aus dem Web-App-Manifest; in Edge und Chrome gleich)
TEAMS_APP_ID = "ompifgpmddkgmclendfeacglnodjjndh"
