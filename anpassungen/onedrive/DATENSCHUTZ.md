# Datenschutzhinweis – IT-Wiesental Cloud Sync

IT-Wiesental Cloud Sync verbindet den Computer direkt mit Microsoft 365 (Microsoft Graph und OneDrive-/
SharePoint-Dienste) des angemeldeten Kontos.

- **Was gespeichert wird:** Zugriffstoken der Anmeldung, Kontoname, E-Mail-Adresse, Einstellungen und
  der Datei-Cache – ausschliesslich lokal im Benutzerverzeichnis (`~/.config/onedrive-mint`,
  `~/.cache/onedrive-mint`, `~/.local/state/onedrive-mint`).
- **Was übertragen wird:** Dateien und Metadaten nur zwischen diesem Computer und Microsoft 365.
  IT-Wiesental erhält keine Dateien, Kontodaten, Protokolle oder Nutzungsdaten.
- **Updates:** Das Programm selbst sucht nicht nach Updates. Das IT-Wiesental Style Kit fragt beim
  Anmelden GitHub nach einer neuen Version; dabei werden die üblichen Verbindungsdaten (IP-Adresse)
  an GitHub übermittelt.
- **Entfernen:** `./uninstall.sh` entfernt das Programm; Kontodaten und Cache werden auf Nachfrage
  gelöscht. Die Freigabe der App kann im Microsoft-Konto bzw. in Microsoft Entra widerrufen werden.

Für die Verarbeitung durch Microsoft gelten die Datenschutzbestimmungen von Microsoft.

Kontakt: IT-Wiesental, https://it-wiesental.de
