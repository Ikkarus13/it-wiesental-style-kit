# Tray einklappen – Entwicklung

## Aufbau

| Datei | Aufgabe |
|---|---|
| `tray-collapse@sascha/applet.js` | das Applet (Pfeil, Fenster über dem Panel, Einhängen in die Tray-Applets) |
| `tray-collapse@sascha/settings-schema.json` | Einstellungen (Darstellung, Symbole pro Zeile, Applet-Liste …) |
| `tray-collapse@sascha/metadata.json` | UUID, Name, unterstützte Cinnamon-Versionen |
| `install.sh` | kopiert das Applet nach `~/.local/share/cinnamon/applets/tray-collapse@sascha` |

## Technik

Das Applet ersetzt die Tray-Applets von Cinnamon nicht, sondern hängt sich zur Laufzeit in die
laufenden Applets `xapp-status@cinnamon.org` (StatusNotifier-/AppIndicator-Symbole) und
`systray@cinnamon.org` (alte XEmbed-Symbole) ein. Zusätzlich werden die in den Einstellungen
aufgeführten Cinnamon-Applets (Liste mit Applet-UUIDs) eingehängt. Wird das Applet entfernt,
ist alles wie vorher.

## Änderungen ausprobieren

Nach `./install.sh` läuft ein bereits geladenes Applet noch in der alten Fassung: Rechtsklick auf
den Pfeil → Entfernen und neu hinzufügen, oder Cinnamon neu starten.
