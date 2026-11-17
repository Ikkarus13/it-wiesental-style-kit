# Entwicklung – Style Kit

Der Installer ist in allen Style Kits gleich (`installer/`, `installieren.sh`, `tools/`, `dev/`).
Alles Markenspezifische liegt in `branding/`, `anpassungen/`, `komponenten/`, `README.md`, `LICENSE`,
`CHANGELOG.md`, `docs/`.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `installieren.sh` | Einstieg, prüft Python-GTK, startet `installer/main.py` |
| `installer/main.py` | Optionen, Textmodus, `--verwalten`, `--entfernen` |
| `installer/gui.py` | Assistent (GTK 3); Farben, Schriften, Logo aus `branding/` |
| `installer/ablauf.py` | Plan und Ausführung, Installation jeder Komponente |
| `installer/root_helfer.py` | Systemteil (pkexec/sudo): apt-Pakete, Edge/Chrome-Quellen, rclone, Web-App-Richtlinien |
| `installer/katalog.py` | Komponenten, Browser, Web-Apps |
| `installer/leiste.py` | Cinnamon-Leiste (Applets, mittig) |
| `installer/entfernen.py` | Komponenten entfernen |
| `installer/marke.py` | liest `branding/branding.json` |
| `installer/aktualisierung.py` | Updates des Kits über dessen GitHub-Releases |
| `komponenten/<id>/` | vollständiger Quelltext jeder Komponente in der übernommenen Version |
| `komponenten/manifest.json` | Version, Quelle und Commit jeder Komponente |
| `komponenten/quellen.json` | aus welchem Repo jede Komponente übernommen wird |
| `anpassungen/` | Dateien, die beim Übernehmen über die Komponenten gelegt werden |

## Komponenten übernehmen

Die Werkzeuge werden in ihren eigenen Repos weiterentwickelt. Ins Kit kommt eine neue Version
nur, wenn man sie bewusst übernimmt:

```bash
./tools/komponente_uebernehmen.sh --liste              # Stand im Kit
./tools/komponente_uebernehmen.sh startklar            # neuestes Release
./tools/komponente_uebernehmen.sh startklar v0.3.1     # bestimmte Version (Tag oder Commit)
./tools/komponente_uebernehmen.sh design               # Designs aus ~/.themes
./tools/komponente_uebernehmen.sh --anpassen          # nur anpassungen/ erneut anwenden
git diff --stat komponenten/                           # prüfen, dann CHANGELOG + Commit
```

Nach jedem Übernehmen legt das Skript `anpassungen/` darüber:

- `anpassungen/alle/<datei>` – in jede Komponente ausser `design` (z. B. `LICENSE` des Kits),
- `anpassungen/<id>/<datei>` – nur in diese Komponente (z. B. Branding, README, Datenschutzhinweis),
- `anpassungen/<id>/ENTFERNEN` – Pfade (eine Zeile je Pfad), die im Kit nicht mitkommen.

So bleiben die Tool-Repos unverändert, und jedes Kit liefert seine eigenen Lizenz- und Markentexte.

## Updates

Die Programme suchen nicht selbst nach Updates, solange sie über ein Kit installiert sind:
`ablauf._abschluss` schreibt `~/.config/style-kit/verwaltet.json`
(`{"kit": …, "komponenten": […], "verwalten": [Befehl]}`), und die Updater der Tools halten sich daran.
Neue Versionen kommen nur mit einem neuen Kit-Release:

- `branding.json` → `update_repo` (z. B. `Besitzer/mein-style-kit`) muss ein **öffentliches**
  Repository sein; ein Token wird nie verwendet. Leer = keine Update-Suche.
- Beim Anmelden prüft `main.py --update-pruefen` (Autostart `<name>-update.desktop`) einmal und meldet eine
  neue Version per Benachrichtigung; «Aktualisieren» öffnet die Verwaltung.
- Die Verwaltung (`--verwalten`) zeigt den Stand und den Knopf «Jetzt aktualisieren»: Paket und
  `.sha256` laden, prüfen, entpacken, dann `installer/main.py --aktualisieren` des neuen Pakets.
- `--aktualisieren` erneuert alle installierten Komponenten; Design-Wahl, Leiste, Browser und
  Web-Apps bleiben, wie sie sind. Braucht die neue Version zusätzliche Systempakete, fragt pkexec.

## Branding

`branding/branding.json`:

| Schlüssel | Bedeutung |
|---|---|
| `produkt`, `untertitel`, `firma`, `kontakt`, `web` | Texte im Assistenten; aus `produkt` entsteht der technische Name (Ordner `~/.local/share/<name>`, Startmenü-Eintrag, Richtlinien-Datei `<name>.json`) |
| `farbe` | Akzentfarbe (Knöpfe, Fortschritt, aktiver Schritt) |
| `logo_text`, `logo_punkt` | Wortmarke als Text mit farbigem Punkt statt `logo.svg`/`logo.png` |
| `leiste_titel` | kürzerer Titel unter dem Logo in der Seitenleiste (sonst `produkt`) |
| `update_repo` | GitHub-Repo (`Besitzer/Name`) mit den Releases des Kits; leer = keine Update-Suche (siehe «Updates») |
| `unterstuetzen` | optional: `{"link": "…{betrag}…", "link_frei": "…", "betraege": [5, 10, 20], "waehrung": "€"}` – Karte «Gefällt dir …?» auf der Fertig-Seite (nur Assistent, nicht beim Aktualisieren) |
| `bewerten` | optional: `{"link": "…", "knopf": "★ Stern auf GitHub"}` |
| `erinnerung_tage` | einmalige Meldung «Gefällt dir …?» so viele Tage nach einer Installation über den Assistenten (Standard 14, 0 = nie; nur wenn `unterstuetzen` oder `bewerten` gesetzt) |
| `stil` | optional: `leiste_bg`, `leiste_text`, `fuss_bg`, `fuss_text`, `akzent_text`, `radius`, `schrift_text`, `schrift_groesse`, `schrift_titel`, `schrift_mono` |

Weitere Dateien: `logo.svg`/`logo.png`, `symbol.svg` (Fenstersymbol), `schriften/*.ttf`
(werden nur für den Assistenten geladen), `cloud-sync.json` (optional: Branding für den
OneDrive-Client; wird nach `~/.config/onedrive-mint/branding.json` geschrieben und bleibt bei
dessen Updates erhalten – immer alle Schlüssel setzen, auch leere `client_id`/`client_secret`).

## Paket und Release

```bash
./tools/paket_bauen.sh          # dist/<name>-<VERSION>.tar.gz aus dem committeten Stand
./tools/release.sh 1.3.0        # VERSION, Paket, Tag, GitHub-Release (CHANGELOG-Abschnitt nötig)
```

Das Release enthält das Paket zweimal: `<name>-X.Y.Z.tar.gz` (für die Update-Funktion) und
`<name>.tar.gz` (fester Name). Bei einem öffentlichen Repo zeigt
`https://github.com/<Besitzer>/<repo>/releases/latest/download/<name>.tar.gz` immer auf die neueste
Version – dieser Link eignet sich für die Homepage.

## Testen

Nur auf einem Testbildschirm mit Test-HOME und eigenem D-Bus, z. B.
`WST_TROCKEN=1 DISPLAY=:99 dev/gui_bilder.py AUSGABE --installieren`
(`WST_TROCKEN=1` überspringt den Systemteil). `dev/gui_bilder.py` klickt den Assistenten
durch und macht Bildschirmfotos (auch für die README).

## Installer-Code zwischen Kits abgleichen

```bash
./tools/installer_abgleichen.sh ../anderes-kit     # übernimmt installer/, dev/, tools/, installieren.sh
```
