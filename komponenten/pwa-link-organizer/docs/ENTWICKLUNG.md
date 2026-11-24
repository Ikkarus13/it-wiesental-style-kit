# PWA link organizer – Entwicklung

## Aufbau

| Pfad | Aufgabe |
|---|---|
| `extension/` | Chrome-Erweiterung (Manifest, `background.js`, Einstellungsseite, Symbole) |
| `native-host/edge_links_host.py` | Native-Messaging-Host: prüft die URL und startet Edge |
| `install.sh` | Host-Eintrag für Chrome und `~/.config/edge-links/config.json` anlegen (`--remove` entfernt den Host-Eintrag) |
| `tests/` | Host-Tests und End-to-End-Test mit echter Chrome-Instanz |
| `tools/release.sh` | Release-Skript |
| `tools/make_icons.py` | erzeugt die Symbole |

Interne Kennungen (`ch.sascha.edge_links`, `~/.config/edge-links`) bleiben für bestehende Installationen gleich.

## Erweiterungs-Schlüssel

Die Erweiterungs-ID ist fest (`pkcdhknlpmepmhnamibdkklkmcplcbhc`, über `key` im
Manifest), deshalb passt der Host-Eintrag immer. Der private Schlüssel dazu liegt
nur lokal in `~/.config/edge-links/ext-key.pem` (nur für ein späteres CRX-Paket nötig).

## Ausnahmen anpassen

Welche Seiten in Chrome bleiben, steht oben in `extension/background.js`
(`OUTLOOK_HOSTS`, `LOGIN_HOSTS`, `KEEP_HOSTS`). Nach einer Änderung in
`chrome://extensions` bei «PWA link organizer» auf ↻ klicken.

## Fehlersuche

- Entscheidungen der Erweiterung (nur Hostnamen, letzte 50): Einstellungsseite
  «Letzte Entscheidungen», gespeichert in `chrome.storage.local` unter `log`
  (`{t, host, why, ms, tries}`; `tries` = wie oft Tab/Fenster nachgefragt wurde)
- Fehler des Hosts (ohne URLs): `~/.cache/edge-links/host.log`
- Erweiterung: `chrome://extensions` → PWA link organizer → «Service Worker» → Konsole
- Spur ohne Erweiterung: Chrome öffnet den Speicher `Default/Local Extension Settings/<ID>`
  (Datei `LOG`) erst beim ersten Speicherzugriff einer Sitzung – also beim ersten
  übergebenen Link oder Protokolleintrag.
- Host von Hand: `printf '\x0e\x00\x00\x00{"ping": true}' | native-host/edge_links_host.py | tail -c +5`

## Tests

```bash
python3 -m unittest tests/test_host.py -v      # Host ohne Chrome
node tests/test_background.js                  # background.js mit nachgebautem chrome-Objekt
Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp &
python3 tests/e2e_chrome.py --mode pwa         # echte Chrome-Instanz auf :99
python3 tests/e2e_chrome.py --mode app
```

Der End-to-End-Test startet Chrome mit eigenem Profil, eigenem HOME und ohne D-Bus
auf dem unsichtbaren Bildschirm `:99`, installiert eine Test-Outlook-PWA (lokale
Seite unter `outlook.cloud.microsoft` per `--host-resolver-rules`), lädt die
Erweiterung über CDP (`Extensions.loadUnpacked`) und klickt 17 Link-Fälle, das
Entscheidungsprotokoll und die Profilwahl in den Einstellungen (mit Schein-`Local State`) durch. Statt
Edge schreibt ein Schein-Programm die URLs in eine Datei (`EDGE_LINKS_BROWSER`).
Das laufende Chrome, Edge und der Desktop bleiben unberührt.

## Veröffentlichen

Version in `extension/manifest.json` **und** `VERSION` in
`native-host/edge_links_host.py` erhöhen, optional `dist/NOTES.md` schreiben,
committen, dann `tools/release.sh`. Das Skript veröffentlicht nur von `master`
und lässt vorher die Host-Tests und `tests/test_background.js` laufen. Bricht es nach dem Tag ab (z. B. `gh`
nicht angemeldet), macht ein zweiter Aufruf beim Release weiter.
