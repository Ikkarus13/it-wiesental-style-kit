"""Produktname, Farben und Herausgeber aus branding.json (neben diesem Modul).

Darüber legt sich ~/.config/onedrive-mint/branding.json, falls vorhanden: So kann ein
Installationspaket (z. B. ein Style Kit) eigenes Branding mitgeben, und es bleibt bei
Programm-Updates erhalten, weil diese nur den Programmordner ersetzen.

Für eine andere Marke genügt eine andere branding.json – der Code nennt
das Programm nie direkt beim Namen. «OneDrive» steht im Code nur dort, wo der
Dienst von Microsoft gemeint ist.
"""
import json
import os

_STANDARD = {
    "produkt": "Cloud Sync",
    "zusatz": "für Microsoft OneDrive",
    "firma": "",
    "kontakt": "",
    "web": "",
    "farbe": "#0087CB",
    "farbe_hell": "#4FB3E3",
    "update_repo": "",
    "client_id": "",
    "client_secret": "",
    "tenant": "",
}


PAKET_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "branding.json")
EIGENE_DATEI = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "onedrive-mint", "branding.json")


def _laden():
    daten = dict(_STANDARD)
    for pfad in (PAKET_DATEI, EIGENE_DATEI):
        try:
            with open(pfad, encoding="utf-8") as f:
                daten.update({k: v for k, v in json.load(f).items()
                              if k in _STANDARD and isinstance(v, str)})
        except (OSError, ValueError, AttributeError):
            pass
    return daten


B = _laden()
NAME = B["produkt"]                                   # z. B. «Cloud Sync»
VOLLER_NAME = f"{NAME} {B['zusatz']}".strip()         # z. B. «Cloud Sync für Microsoft OneDrive»
FIRMA = B["firma"]
KONTAKT = B["kontakt"]
WEB = B["web"]
FARBE = B["farbe"]
FARBE_HELL = B["farbe_hell"]
UPDATE_REPO = B["update_repo"]
CLIENT_ID = B["client_id"].strip()
CLIENT_SECRET = B["client_secret"].strip()
TENANT = B["tenant"].strip()
# Eigene App-Registrierung des Herausgebers vorgegeben? Sonst meldet rclones App an.
EIGENE_APP = bool(CLIENT_ID)


def app_reg():
    """(client_id, client_secret, tenant) der Herausgeber-App oder None."""
    return (CLIENT_ID, CLIENT_SECRET, TENANT) if EIGENE_APP else None

HINWEIS = (f"{NAME} ist ein unabhängiges Programm"
           + (f" von {FIRMA}" if FIRMA else "")
           + ". Es ist nicht von Microsoft entwickelt, unterstützt oder mit Microsoft verbunden.")
MARKEN = ("Microsoft und OneDrive sind Marken der Microsoft-Unternehmensgruppe. "
          "rclone ist ein Open-Source-Projekt (MIT-Lizenz).")
DATENSCHUTZ = (f"{NAME} verbindet sich direkt mit Microsoft 365 (Microsoft Graph). Anmeldedaten "
               "(Zugriffstoken) und Einstellungen werden nur auf diesem Computer gespeichert "
               "(~/.config/onedrive-mint). Es werden keine Dateien, Kontodaten oder "
               f"Nutzungsdaten an {FIRMA or 'den Herausgeber'} übertragen. "
               "Für Updates fragt das Programm GitHub nach neuen Versionen.")
