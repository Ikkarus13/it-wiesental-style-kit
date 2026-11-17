# Emoji-Panel

Emoji-Panel im Stil von Windows 11 (aufrufbar wie dort mit Win + .) für Linux Mint / Cinnamon unter X11.

## Voraussetzungen

Python + GTK3, keine pip-Pakete (nutzt `python3-xlib`, IBus-Emojidaten, Noto Color Emoji).

## Installation

```bash
./installieren.sh     # Startmenü, Autostart, Tastenkürzel, startet das Panel
./deinstallieren.sh
```

## Bedienung

| Taste | Wirkung |
|---|---|
| **Super + .** oder **Super + ;** | Panel öffnen/schliessen (am Textcursor, sonst am Mauszeiger) |
| einfach lostippen | sucht (deutsche und englische Stichwörter) |
| Pfeiltasten, Bild ↑/↓ | Auswahl bewegen |
| Enter / Klick | einfügen – das Panel bleibt offen wie unter Windows |
| Tab / Shift+Tab | Reiter wechseln |
| Esc, Klick daneben | schliessen |

Reiter: Zuletzt verwendet · Emoji (Kategorien im Stil von Windows, Hautfarbe über ✋) · Kaomoji ·
Symbole · Zwischenablage-Verlauf (nur im Speicher, Passwortmanager-Einträge werden ignoriert).

Auch über die Befehlszeile: `emoji-panel --settings`, `emoji-panel --update`, `emoji-panel --version`.

Nicht enthalten: ein GIF-Reiter (bräuchte Tenor-API-Schlüssel und Internet).

## Hinweise

- Das Panel läuft im Hintergrund (Autostart) und nimmt dem Zielprogramm den Fokus nicht.
- Beim Einfügen wird die Zwischenablage kurz benutzt und danach der vorherige Inhalt
  (Text oder Bild) wiederhergestellt. Formatierter Text (z. B. HTML aus dem Browser)
  kommt dabei als reiner Text zurück.
- Chromium/Edge/Electron melden den Textcursor meist nicht, dann erscheint das Panel
  am Mauszeiger.
- Zuletzt verwendete Emoji und die Hautfarbe werden lokal in
  `~/.local/share/emoji-panel/zustand.json` gespeichert.

## Updates

Das Panel sucht täglich auf GitHub nach einem neueren Release und meldet sich mit einer
Benachrichtigung; ein Klick darauf (oder Zahnrad im Panel → «Jetzt nach Updates suchen»)
spielt die neue Version ein und startet das Panel neu. Ein Git-Checkout wird per `git pull`
aktualisiert, sonst wird das Release-Paket über die Dateien kopiert. Updates kommen über
GitHub-Releases; ist die `gh`-CLI angemeldet oder in den Einstellungen ein Token
eingetragen, wird es für die Abfrage mitverwendet.

## Lizenz und Marken

Siehe [LICENSE](LICENSE).

Unabhängiges Projekt, nicht von Microsoft entwickelt, unterstützt oder mit
Microsoft verbunden. Microsoft und Windows sind Marken der
Microsoft-Unternehmensgruppe.

---

Für Entwickler: [docs/ENTWICKLUNG.md](docs/ENTWICKLUNG.md)
