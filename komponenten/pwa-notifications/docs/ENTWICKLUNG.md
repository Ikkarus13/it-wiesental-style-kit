# PWA-Benachrichtigungen – Entwicklung

## Entwicklungsinstallation

```bash
./install.sh --entwicklung
```

Damit zeigt die installierte Erweiterung per Symlink auf diesen Ordner. Nach Änderungen die Erweiterung neu laden:

```bash
gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.Cinnamon.ReloadXlet pwa-notifications@sascha EXTENSION
```

Fehler und Meldungen stehen in `~/.xsession-errors` bzw. in Looking Glass (`Alt+F2`, `lg`). Die letzten 20 umgeschriebenen Web-App-Meldungen liegen im Speicher unter `global._pwaNotificationLog`.

## Aufbau

Die Dateien liegen in `pwa-notifications@sascha/`.

| Datei | Aufgabe |
|---|---|
| `extension.js` | Einstieg, Einstellungen, Updates, Vorschau |
| `notifications.js` | Einhängen in Cinnamons Benachrichtigungsdienst, Umschreiben, Kopfzeile |
| `apps.js` | Domain → installierte Web-App, eigene Regeln |
| `style.js` | Stylesheet aus den Einstellungen erzeugen und laden |
| `updater.js` | GitHub-Releases prüfen, Paket laden, einspielen |
| `grouping.js` | Benachrichtigungsliste des Cinnamon-Applets nach App gruppieren |
| `settings-schema.json`, `widgets.py` | Einstellungsseite |

## Neue Version veröffentlichen

1. In `CHANGELOG.md` einen Abschnitt `## X.Y.Z` mit den Änderungen anlegen und alles committen.
2. Das Release-Skript ausführen:

```bash
./tools/release.sh X.Y.Z
```

Das Skript setzt die Version, baut das Paket, taggt, pusht und legt das GitHub-Release an.

## Updates aus einem Git-Checkout

Läuft die Erweiterung aus einem Git-Checkout (`./install.sh --entwicklung`), holt «Update installieren» den neuesten Stand per `git pull`.

## Verwaltung durch ein Style Kit

Ein Style Kit (z. B. IT-Wiesental Style Kit) schreibt
`${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json` mit `kit` (Name),
`komponenten` (Liste von Kennungen) und `verwalten` (Befehl als Liste). Steht dort
`pwa-notifications`, liefert `kitVerwaltet()` in `updater.js` den Kit-Namen (Datei per
`GLib.file_get_contents`, bei jedem Aufruf neu gelesen): `check()` meldet ohne Anfrage
«kein Update», `install()` verweigert, die tägliche Suche entfällt. «Jetzt nach Updates
suchen» und «Update installieren» melden «Updates für PWA-Benachrichtigungen kommen über
das …» mit Knopf «<Kit> öffnen» (startet `verwalten` ohne Shell). Die Einstellungsseite
(`widgets.py`, `UpdateStatus`) zeigt denselben Hinweis und Knopf. Fehlt die Datei, ist
sie kaputt oder fehlt die Kennung, gilt der eigene Updater.
