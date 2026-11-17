# Startklar

Ein Startmenü im Stil von Windows 11 für Linux Mint (Cinnamon).

- **Angeheftet** – deine Apps als Kacheln: per Ziehen umsortieren oder aus „Alle“
  hineinziehen; Rechtsklick: lösen, nach vorne verschieben, an Taskleiste anheften
- **Empfohlen** – zuletzt geöffnete Dateien und neu installierte Apps
- **Alle** – Ansicht *Kategorie*, *Raster* oder *Liste* (mit Buchstabensprung)
- **Suche** – einfach lostippen: Apps, Einstellungen, Dokumente, Ordner, Web;
  links die Treffer, rechts Details und Aktionen
- **Fusszeile** – Konto (Sperren, Abmelden), Ordner-Schnellzugriffe, Ein/Aus
- Acryl-Hintergrund, Einblenden von unten, hell/dunkel nach System-Design
- **Startknopf** (Cinnamon-Applet) mit Super-Taste und Rechtsklick-Menü im Stil von **Win+X**

## Voraussetzungen

- Linux Mint mit Cinnamon
- Python 3 + GTK 3, keine zusätzlichen Pakete

## Installieren

```bash
./installieren.sh
```

Setzt den Startknopf an die Stelle des bisherigen Menüs in der Leiste
(Cinnamenu oder Cinnamon-Menü). Zurück zum alten Menü:

```bash
./installieren.sh --altes-menue
```

Entfernen: `./deinstallieren.sh` (Einstellungen bleiben, ausser mit `--alles`).

## Einstellungen

Rechtsklick auf den Startknopf → „Startklar-Einstellungen“ (oder Rechtsklick auf eine
freie Stelle im Menü, oder `startklar --settings`): Layout, Symbolgrösse, Menübreite,
sichtbare Bereiche, Ordner neben Ein/Aus, Design, Suche, Updates. Gespeichert wird in
`~/.config/startklar/config.json` (angeheftete Apps, Ansicht, Ordner der Fusszeile,
Websuche, Design); Änderungen gelten sofort.

## Updates

Startklar sucht täglich nach einem neuen Release (blauer Punkt am Ein/Aus-Knopf,
Eintrag „Startklar aktualisieren“). Von Hand: Rechtsklick auf den Startknopf →
„Startklar: Nach Updates suchen“. Updates kommen über GitHub-Releases. Installiert
wird nur ein Paket mit gültiger Prüfsumme und Ed25519-Signatur. Ist eine
GitHub-Anmeldung vorhanden (`gh auth login`) oder `github_token` in der
Einstellungsdatei eingetragen, wird sie für die Abfrage mitverwendet.

## Datenschutz

Die Dateisuche liest nur Namen im persönlichen Ordner und lässt
FUSE-/Netzlaufwerke (z. B. OneDrive) aus.

## Lizenz und Marken

Siehe [LICENSE](LICENSE). Mitgeliefert: Schrift Selawik (OFL,
[assets/fonts/LICENSE-Selawik.txt](assets/fonts/LICENSE-Selawik.txt)), Symbole
Fluent UI System Icons (MIT,
[assets/icons/LICENSE-FluentIcons.txt](assets/icons/LICENSE-FluentIcons.txt)).

Unabhängiges Projekt, nicht von Microsoft entwickelt, unterstützt oder mit
Microsoft verbunden. Microsoft und Windows sind Marken der
Microsoft-Unternehmensgruppe.

---

Für Entwickler: [docs/ENTWICKLUNG.md](docs/ENTWICKLUNG.md)
