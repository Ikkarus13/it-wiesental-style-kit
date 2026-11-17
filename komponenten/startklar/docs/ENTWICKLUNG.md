# Startklar – Entwicklung

Benutzerdokumentation: [README.md](../README.md)

## Technik

- Python 3 + GTK 3, keine zusätzlichen Pakete
- Dienst `org.startklar.App` (D-Bus) hält das Menü bereit; das Applet ruft die
  Aktion `toggle` auf und übergibt Monitor und Leiste
- Einstellungen: `~/.config/startklar/config.json` (angeheftete Apps, Ansicht,
  Ordner der Fusszeile, Websuche, Design)
- Die Dateisuche liest nur Namen im persönlichen Ordner und lässt
  FUSE-/Netzlaufwerke (z. B. OneDrive) aus
- Schrift Selawik (OFL), Symbole Fluent UI System Icons (MIT)

Aufbau des Repositorys:

- [`startklar/`](../startklar) – das Programm (Dienst und Menüfenster)
- [`applet/`](../applet) – der Startknopf als Cinnamon-Applet
- [`tools/release.sh`](../tools/release.sh) – Release bauen und veröffentlichen
- [`tools/leiste.py`](../tools/leiste.py) – Startknopf in der Cinnamon-Leiste
  einsetzen bzw. das alte Menü zurückholen (von `installieren.sh` benutzt)
- [`dev/`](../dev) – Testwerkzeuge

## Testen

Nur auf einem eigenen Testbildschirm:

```bash
Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp &
DISPLAY=:99 metacity --compositor=xrender &
DISPLAY=:99 STARTKLAR_CONFIG_DIR=/tmp/sk-test STARTKLAR_DRY_RUN=1 dev/harness.py bild.png type:term
```

Die möglichen Schritte von [`dev/harness.py`](../dev/harness.py) stehen im Kopf der
Datei. Die Update-Suche lässt sich mit [`dev/test_update.py`](../dev/test_update.py)
gegen einen lokalen Test-Server prüfen (braucht den Signaturschlüssel).

Umgebungsvariablen für Tests:

| Variable | Wirkung |
|---|---|
| `STARTKLAR_CONFIG_DIR` | Einstellungsordner statt `~/.config/startklar` |
| `STARTKLAR_DRY_RUN=1` | Sitzungsaktionen (Sperren, Abmelden, Ein/Aus) nur protokollieren |
| `STARTKLAR_UPDATE_API` | eigener Server statt `https://api.github.com` |
| `STARTKLAR_NO_UPDATE_CHECK` | keine automatische Update-Suche |
| `STARTKLAR_INDEX_ROOT`, `STARTKLAR_INDEX_DELAY` | Wurzel und Startverzögerung (s) der Dateisuche |
| `STARTKLAR_SIGNING_KEY` | Pfad des privaten Signaturschlüssels für `tools/release.sh` |

## Verwaltung durch ein Style Kit

Ein Style Kit (z. B. IT-Wiesental Style Kit) schreibt
`${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json` mit `kit` (Name),
`komponenten` (Liste von Kennungen) und `verwalten` (Befehl als Liste). Steht dort
`startklar`, liefert `updater.kit_verwaltet()` den Kit-Namen: `check()` meldet kein
Update (ohne GitHub-Anfrage), `install()` verweigert, die tägliche Suche und der Punkt
am Ein/Aus-Knopf entfallen. Die manuelle Suche (Applet, Menü) zeigt eine Meldung mit
Knopf «<Kit> öffnen» (Aktion `app.open-kit`, startet `verwalten` ohne Shell), die
Einstellungen zeigen statt Schalter und «Jetzt suchen» denselben Hinweis. Fehlt die
Datei, ist sie kaputt oder fehlt die Kennung, gilt der eigene Updater. Die Datei wird
bei jedem Aufruf neu gelesen. Test: Abschnitt 4 in `dev/test_update.py`.

## Release

Neues Release: `tools/release.sh X.Y.Z` (Abschnitt in CHANGELOG.md vorher anlegen).
Der private Signaturschlüssel liegt unter `~/.config/startklar-release/` – sichern!
Der dazu passende öffentliche Schlüssel steht in `startklar/release_key.pem`.
