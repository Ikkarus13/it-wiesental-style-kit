# FileFenster – Entwicklung

Benutzerdokumentation: [README.md](../README.md)

## Aufbau

- [`filefenster.py`](../filefenster.py) – Startskript
- [`filefenster/`](../filefenster) – das Programm (ein Modul je Bereich, z. B.
  `window.py`, `folderview.py`, `navpane.py`, `fileops.py`, `archive.py`,
  `network.py`, `cloudstatus.py`, `updater.py`)
- [`dev/`](../dev) – Tests und Hilfsskripte
- [`tools/`](../tools) – Release und Signaturschlüssel
- [`docs/bilder/`](bilder) – Bilder für die README

## Testen

Tests laufen auf einem unsichtbaren Test-Bildschirm, nie auf dem
Arbeitsbildschirm ([`dev/testbildschirm.py`](../dev/testbildschirm.py)):

```bash
python3 dev/testbildschirm.py :99 &
DISPLAY=:99 python3 dev/test_dnd.py OUTDIR
```

Nimmt Xvfb, falls installiert (`sudo apt install xvfb`), sonst Xephyr.
Screenshots: `import -display :99 -window root`. Die einzelnen Tests
(`dev/test_*.py`) beschreiben ihren Aufruf im Dateikopf.

Die Bilder der README erzeugt [`dev/readme_bilder.py`](../dev/readme_bilder.py)
mit einem Demo-Benutzer und erfundenen Dateien:

```bash
python3 dev/testbildschirm.py :99 &
DISPLAY=:99 python3 dev/readme_bilder.py docs/bilder
```

### Umgebungsvariablen

| Variable | Wirkung |
|---|---|
| `FILEFENSTER_CONFIG_DIR` | Einstellungsordner umbiegen (für Tests immer setzen) |
| `FILEFENSTER_NO_UNIQUE=1` | eigene Instanz statt Weitergabe an ein laufendes FileFenster |
| `FILEFENSTER_START` | Startordner |
| `FILEFENSTER_NO_UPDATE_CHECK=1` | keine automatische Update-Suche |
| `FILEFENSTER_SNAPSHOT=/pfad/bild.png` | Fenster-Schnappschuss nach `FILEFENSTER_SNAPSHOT_DELAY` ms |
| `FILEFENSTER_SNAPSHOT_QUIT=1` | nach dem Schnappschuss beenden |
| `FILEFENSTER_UPDATE_API` | statt `https://api.github.com/repos/<Besitzer>/filefenster` |
| `FILEFENSTER_GITHUB_TOKEN` | Zugangsdaten (sonst `GITHUB_TOKEN`, `GH_TOKEN`, `gh auth token`) |
| `FILEFENSTER_SIGNING_KEY` | Pfad des privaten Signaturschlüssels |

GitHub wird zuerst ohne Anmeldung gefragt; Zugangsdaten werden nur verwendet,
wenn das Repository ohne sie nicht sichtbar ist, und nie an andere Rechner als
GitHub gesendet.

## Verwaltung durch ein Style Kit

Ein Style Kit (z. B. IT-Wiesental Style Kit) schreibt
`${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json` mit `kit` (Name),
`komponenten` (Liste von Kennungen) und `verwalten` (Befehl als Liste). Steht dort
`filefenster`, liefert `updater.kit_verwaltet()` den Kit-Namen: `check()` gibt ohne
Anfrage `(None, False)` zurück, `download()`, `stage()` und `activate()` verweigern,
die automatische Suche und der Punkt am „···“-Knopf entfallen. „Nach Updates suchen“
zeigt im Dialog «Updates für FileFenster kommen über das …» und den Knopf
«<Kit> öffnen» (startet `verwalten` ohne Shell). Fehlt die Datei, ist sie kaputt
oder fehlt die Kennung, gilt der eigene Updater. Die Datei wird bei jedem Aufruf neu
gelesen. Test: `DISPLAY=:99 python3 dev/test_style_kit.py`.

## Release

Neue Versionen veröffentlicht [`tools/make_release.sh`](../tools/make_release.sh)
(Ablauf und Optionen im Kopf des Skripts). Ein Release besteht aus dem Tag
`vX.Y.Z`, dem Paket `filefenster-X.Y.Z.tar.gz`, dessen Prüfsumme (`.sha256`) und
der Ed25519-Signatur (`.sig`).

Signaturschlüssel: einmalig mit
[`tools/make_signing_key.sh`](../tools/make_signing_key.sh) erzeugen (`--pin`
trägt den öffentlichen Schlüssel in `filefenster/release_key.pem` ein). Der
private Schlüssel liegt ausserhalb des Repositorys
(`~/.config/filefenster-release/signing-key.pem`) und muss gesichert werden:
Wer ihn verliert, kann keine Updates mehr ausliefern, die bestehende
Installationen annehmen.
