# PWA-Tray – Entwicklung

## Aufbau

| Datei | Aufgabe |
|---|---|
| `pwa_tray.py` | der Helfer: ✕ abfangen, Fenster verstecken, Tray-Symbol, D-Bus-Aktionen |
| `badge.py` | zeichnet Status-Punkt und Zähler mit cairo |
| `native_host.py` | Native-Messaging-Host: nimmt Status und Zähler von der Erweiterung entgegen und gibt sie per D-Bus weiter |
| `edge-extension/` | Erweiterung, die Status und Zähler in der Teams-Seite abliest |
| `updater.py` | GitHub-Releases prüfen, neue Version einspielen (bei Git-Checkout `git pull`) |
| `version.py` | `VERSION` und Repository |
| `install.sh` | Autostart, Native-Messaging-Host für Edge/Chrome, Start des Helfers |
| `tools/release.sh` | Release-Skript |
| `dev/cs-test.html` | Schein-Teams-Seite zum Testen des Content-Scripts der Erweiterung |

## Wie es funktioniert

- **✕ abfangen:** Edge zeichnet die Titelleiste der PWA selbst, der Fenstermanager sieht den
  Klick also nie. PWA-Tray legt deshalb ein unsichtbares X11-Kindfenster (InputOnly) genau
  über das ✕ im Teams-Fenster und fängt den Klick dort ab. Ein zweites Kindfenster zeichnet
  beim Darüberfahren das rote ✕.
- **Verstecken:** `_NET_WM_STATE_SKIP_TASKBAR/PAGER` plus Minimieren. Das Fenster und damit
  die Teams-Seite bleiben bestehen.
- **Tray-Symbol:** XApp-StatusIcon. Status-Punkt und Zähler zeichnet `badge.py` mit cairo.
- **Cinnamon-Neustart:** Startet Cinnamon neu, während Teams im Tray liegt, vergisst es die
  Zuordnung des Fensters zu Teams (es erscheint dann unter „Edge“). PWA-Tray erkennt das beim
  Zurückholen und meldet das Fenster einmal neu an. Von Hand:
  `gdbus call --session --dest ch.sascha.PwaTray --object-path /ch/sascha/PwaTray --method org.gtk.Actions.Activate remanage [] {}`

## Fehlersuche

- Debug-Ausgabe: `PWA_TRAY_DEBUG=1 ./pwa_tray.py`
- Was die Teams-Seite zuletzt gemeldet hat: `~/.cache/pwa-tray/last-status.json`
- Welche Beschriftungen die Seite liefert (für die Status-Erkennung): `touch ~/.cache/pwa-tray/diagnose-an`, dann steht es nach spätestens 10 Minuten in `~/.cache/pwa-tray/diag.json` (nur zur Fehlersuche, danach `diagnose-an` wieder löschen)
- Meldet die Seite 3 Minuten lang nichts, zeigt das Symbol wieder das normale Teams-Logo.

## Verwaltung durch ein Style Kit

Ein Style Kit (z. B. IT-Wiesental Style Kit) schreibt
`${XDG_CONFIG_HOME:-~/.config}/style-kit/verwaltet.json` mit `kit` (Name),
`komponenten` (Liste von Kennungen) und `verwalten` (Befehl als Liste). Steht dort
`pwa-tray`, liefert `updater.kit_verwaltet()` den Kit-Namen: `check()` gibt ohne
Anfrage `None` zurück, `install()` und `run_installer()` verweigern, die tägliche Suche
und ihre Meldung entfallen. «Nach Updates suchen» im Tray-Menü meldet «Updates für
PWA-Tray kommen über das …» mit Knopf «<Kit> öffnen» (Aktion `app.open-kit`, startet
`verwalten` ohne Shell). Fehlt die Datei, ist sie kaputt oder fehlt die Kennung, gilt
der eigene Updater. Die Datei wird bei jedem Aufruf neu gelesen.

## Neue Version veröffentlichen

1. `VERSION` in `version.py` erhöhen (Fehlerbehebung → `1.0.x`, neue Funktion → `1.x.0`).
2. Abschnitt in `CHANGELOG.md` ergänzen und als `dist/NOTES.md` (deutsche Stichpunkte) ablegen.
3. Committen, dann `tools/release.sh` (pusht, setzt den Tag und legt das GitHub-Release mit Paket an).
