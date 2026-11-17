# Emoji-Panel – Entwicklung

Benutzerdokumentation: [README.md](../README.md)

## Aufbau

- [`emojipanel.py`](../emojipanel.py) – Panel und Anwendung (GTK3)
- [`emoji-panel`](../emoji-panel) – Startbefehl; läuft das Panel schon, genügt ein D-Bus-Aufruf
- [`daten.py`](../daten.py) – Emoji-, Kaomoji- und Symboldaten
- [`system.py`](../system.py) – X11-Anbindung: Zielfenster, Einfügen per XTest,
  Klicks ausserhalb, Textcursor-Position
- [`zwischenablage.py`](../zwischenablage.py) – Zwischenablage sichern und wiederherstellen
- [`tastenkuerzel.py`](../tastenkuerzel.py) – Cinnamon-Tastenkürzel anlegen, entfernen, prüfen
- [`updater.py`](../updater.py) – Updates über GitHub-Releases
- [`version.py`](../version.py) – Versionsnummer (setzt `tools/release.sh`)

## Funktionsweise

- Läuft im Hintergrund (Autostart `--hidden`); `emoji-panel` ruft die Instanz per D-Bus auf.
- Das Panel ist ein Popup ohne Fokus: Das Zielprogramm behält den Fokus, die Tastatur wird
  nur umgeleitet, solange das Panel offen ist.
- Einfügen: Text in die Zwischenablage, dann Strg+V (in Terminals Strg+Shift+V) per XTest;
  danach wird der vorherige Inhalt (Text oder Bild) wiederhergestellt. Formatierter Text
  (z. B. HTML aus dem Browser) kommt dabei als reiner Text zurück.
- Position am Textcursor über AT-SPI; Chromium/Edge/Electron melden ihn meist nicht,
  dann erscheint das Panel am Mauszeiger.
- Zustand (zuletzt verwendet, Hautfarbe): `~/.local/share/emoji-panel/zustand.json`.
- Updates: Für die Abfrage wird, falls vorhanden, das Token der angemeldeten `gh`-CLI
  oder eins aus den Einstellungen verwendet.

## Verwaltung durch ein Style Kit

Ein Style Kit (z. B. IT-Wiesental Style Kit) schreibt
`${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json` mit `kit` (Name),
`komponenten` (Liste von Kennungen) und `verwalten` (Befehl als Liste). Steht dort
`emoji-panel`, liefert `updater.kit_verwaltet()` den Kit-Namen: `check()` gibt ohne
Anfrage `None` zurück, `install()` verweigert, die tägliche Suche und ihre Meldung
entfallen. `emoji-panel --update` zeigt «Updates für Emoji-Panel kommen über das …»
mit Knopf «<Kit> öffnen» (startet `verwalten` ohne Shell); die Einstellungen zeigen
denselben Hinweis statt Schalter, Token und Suchknopf. Fehlt die Datei, ist sie kaputt
oder fehlt die Kennung, gilt der eigene Updater. Die Datei wird bei jedem Aufruf neu
gelesen.

## Release

Neue Version veröffentlichen: Abschnitt in [`CHANGELOG.md`](../CHANGELOG.md) ergänzen,
committen, dann [`./tools/release.sh X.Y.Z`](../tools/release.sh) (Fehlerbehebung → Patch,
neue Funktion → Minor).
