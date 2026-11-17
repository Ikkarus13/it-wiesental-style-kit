"""Branding des Kits aus branding/branding.json (Name, Farben, Schriften, Logo, Kontakt).

Der Installer-Code ist in allen Kits gleich; alles Markenspezifische steht in
branding/. Ein neues Kit braucht nur einen anderen branding-Ordner.
"""
import json
import os
import re

PAKET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDNER = os.path.join(PAKET, "branding")

_STANDARD = {
    "firma": "",
    "produkt": "Style Kit",
    "untertitel": "Linux Mint im Stil von Windows 11",
    "farbe": "#0087cb",
    "kontakt": "",
    "web": "",
    # optional: feste Farben statt der Farben des System-Designs, siehe gui.py
    "stil": {},
    # optional: Text-Logo statt logo.svg/logo.png («●» in logo_punkt-Farbe davor)
    "logo_text": "",
    "logo_punkt": "",
    # optional: kürzerer Titel in der Seitenleiste (z. B. wenn das Logo den Firmennamen zeigt)
    "leiste_titel": "",
    # GitHub-Repo (Besitzer/Name) mit den Releases des Kits; leer = keine Update-Suche
    "update_repo": "",
    # optional: Unterstützen nach der Installation und in der Erinnerung
    #   {"link": "…{betrag}…", "link_frei": "…", "betraege": [5, 10, 20], "waehrung": "€"}
    "unterstuetzen": {},
    # optional: «Bewerten» (z. B. Stern auf GitHub): {"link": "…", "text": "…"}
    "bewerten": {},
    # einmalige Erinnerung «Gefällt es dir?» so viele Tage nach der Installation (0 = nie)
    "erinnerung_tage": 14,
}


def laden():
    b = json.loads(json.dumps(_STANDARD))
    try:
        with open(os.path.join(ORDNER, "branding.json"), encoding="utf-8") as f:
            b.update(json.load(f))
    except (OSError, ValueError):
        pass
    return b


B = laden()
PRODUKT = B["produkt"]
# technischer Name für Ordner, Startmenü-Eintrag und Richtlinien-Datei, z. B. «mein-style-kit»
HAT_DANKE = bool((B.get("unterstuetzen") or {}).get("link_frei") or (B.get("bewerten") or {}).get("link"))
KURZ = re.sub(r"[^a-z0-9]+", "-", PRODUKT.lower()).strip("-") or "style-kit"
