# Änderungen

## 1.1.3

- Aktualisieren lässt Einstellungen in Ruhe: Startklar, FileFenster, PWA-Benachrichtigungen und Emoji-Panel laufen im Update-Modus (Leiste, Standard-Dateimanager, Autostart und abgeschaltete Erweiterungen bleiben), der Überlauf-Pfeil wird nicht wieder eingefügt, keine alten «Noch zu tun»-Hinweise mehr.
- Cloud Sync wird beim Aktualisieren ohne Rückfragen erneuert und danach neu gestartet; Nemo-Fenster bleiben offen.
- Systemteil: rclone in fester Version mit Prüfsumme (vorher scheiterte der Download an einer Sperre des Servers), Signaturschlüssel von Edge und Chrome werden am Fingerabdruck geprüft, Downloads nur über https.
- verwaltet.json für mehrere Kits; nur Kits mit Update-Quelle übernehmen die Updates der Programme.
- Updates des Kits sind signiert (Ed25519); ein Paket ohne gültige Signatur wird abgelehnt.
- Die Programme im Kit tragen die Datei .kit-verwaltet: Ihre eigene Update-Suche bleibt fest abgeschaltet.
- Enthalten: Emoji-Panel 1.1.1, PWA-Tray 1.1.1 (Teams-Diagnose nur noch auf Wunsch), PWA-Benachrichtigungen 1.5.1, Cloud Sync 2.4.2.

## 1.1.2

- Nach der Installation: «Gefällt dir das Style Kit?» mit Stern auf GitHub und Unterstützen per PayPal (5 / 10 / 20 € oder anderer Betrag). Zwei Wochen später einmalig eine Erinnerung, danach nie wieder.
- GitHub: Sponsor-Knopf (FUNDING.yml) und Abschnitt «Unterstützen» in der README.

## 1.1.1

- Cloud Sync 2.4.1: Quelle heisst jetzt Ikkarus13/cloud-sync (Repository umbenannt).

## 1.1.0

- Lizenz: Freeware – kostenlos nutzbar, privat und geschäftlich, aber nicht Open Source (LICENSE, auch in jeder Komponente).
- Cloud Sync durchgehend als «IT-Wiesental Cloud Sync» (Branding, README, Datenschutzhinweis), ohne Entwickler-Dateien.
- Updates nur noch über das Kit: Die Programme suchen nicht mehr selbst. Das Kit prüft beim Anmelden auf eine neue Version und meldet sie; Startmenü → IT-Wiesental Style Kit → «Jetzt aktualisieren» (Design, Leiste und Browser bleiben).
- Release enthält das Paket zusätzlich unter festem Namen (Download-Link «latest» für die Homepage).
- Enthalten: Startklar 0.4.0, FileFenster 0.16.0, Emoji-Panel 1.1.0, PWA-Tray 1.1.0, PWA-Benachrichtigungen 1.5.0, Cloud Sync 2.4.0.

## 1.0.7

- README: Leistenbilder neu, mit dem aktuellen Cloud-Sync-Symbol.

## 1.0.6

- README: neuer Abschnitt «So sieht es aus» mit Bildern aller Werkzeuge.

## 1.0.5

- PWA-Benachrichtigungen 1.4.0: Teams-Anrufe lassen sich direkt aus der Meldung annehmen, mit Video annehmen oder ablehnen (Teams selbst schickt keine Knöpfe mit).

## 1.0.4

- PWA-Tray 1.0.3: Das rote ✕ sitzt auch bei nicht maximiertem Teams-Fenster genau auf dem Schliessen-Knopf (vorher erschien es versetzt neben dem echten Knopf).

## 1.0.3

- Cloud Sync 2.3.1: Leistensymbol in voller Leistengrösse.

## 1.0.2

- Cloud Sync 2.3.0: neues Leistensymbol (Wolke mit Haken im Ruhezustand, passt sich heller und dunkler Leiste an).

## 1.0.1

- Seitenleiste zeigt unter der Wortmarke nur noch «Style Kit» statt den Namen doppelt.

## 1.0.0

- Erste Version des IT-Wiesental Style Kit: Linux Mint im Stil von Windows 11, gleicher Installer
  wie die anderen Style Kits, Gestaltung von it-wiesental.de (Farben, Schriften, Wortmarke).
- Enthält Design, Startklar, FileFenster, Infobereich-Überlauf, PWA-Benachrichtigungen, PWA-Tray,
  Emoji-Panel, PWA link organizer und IT-Wiesental Cloud Sync (für Microsoft OneDrive).
