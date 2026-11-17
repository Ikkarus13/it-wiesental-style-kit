# Änderungen

## 1.1.0

- Verwaltung durch ein Style Kit: Steht «emoji-panel» in `~/.config/style-kit/verwaltet.json`, sucht und installiert das Emoji-Panel keine Updates mehr selbst. `emoji-panel --update` und die Einstellungen sagen dann «Updates für Emoji-Panel kommen über das …» und bieten einen Knopf zum Öffnen des Kits.

## 1.0.6

- Doku für Benutzer und Entwickler getrennt (docs/ENTWICKLUNG.md), Lizenzdatei angepasst

## 1.0.5

- Lizenzdatei mit Markenhinweis ergänzt.

## 1.0.4

- Panel öffnet jetzt auch in Edge, Web-Apps (Outlook, Teams) und Electron-Programmen am Textcursor statt am Mauszeiger: Die Cursorposition kommt von der Eingabemethode IBus, an die diese Programme sie ohnehin melden.

## 1.0.3

- Absturz behoben: Ist der Barrierefreiheits-Dienst (AT-SPI) nicht erreichbar, beendete die Systembibliothek das ganze Panel beim Öffnen. Jetzt wird das vorher geprüft und das Panel öffnet am Mauszeiger.

## 1.0.2

- Eigene Tastenkürzel, die das Emoji-Panel aufrufen, werden bei Installation/Update nicht mehr als Panel-Eintrag behandelt und bleiben unverändert.

## 1.0.1

- Zwischenablage wird nach dem Einfügen mit allen Formaten wiederhergestellt – kopierte Dateien, formatierter Text und Bilder bleiben erhalten (vorher nur reiner Text oder Bild).
- Tastenkürzel-Eintrag im Cinnamon-Format («customN»): Eigene Tastenkürzel in den Systemeinstellungen lassen sich wieder anlegen.
- Installation/Update bricht nicht mehr ab, wenn Cinnamons Kürzelliste gerade kein «__dummy__» enthält; Updates behalten geänderte Tastenkürzel und Autostart-Einstellung.
- Update-Download schickt das GitHub-Token nicht mehr an den Download-Server weiter; Zustandsdatei mit Token nur noch für den eigenen Benutzer lesbar.
- Super + . schliesst das offene Panel wieder (wie unter Windows).
- Sehr schnelles Klicken direkt nach dem Öffnen fügt nicht mehr ins eigene Suchfeld ein.
- Enter fügt nur noch ein, wenn eine Auswahl sichtbar ist.
- Robuster bei kaputter Zustandsdatei, hängenden Programmen und vollem Speicher; Panel blitzt beim Start nicht mehr auf.
- Terminal-Erkennung genauer (Strg+Shift+V nur in echten Terminals).

## 1.0.0

- Emoji-Panel nach Vorbild von Windows (Super + . / Super + ;): Zuletzt verwendet, Emoji mit Kategorien und Hautfarbe, Kaomoji, Symbole, Zwischenablage-Verlauf.
- Suche mit deutschen und englischen Stichwörtern, Bedienung per Pfeiltasten, Enter und Tab.
- Panel erscheint am Textcursor (wo das Programm ihn meldet), sonst am Mauszeiger.
- Schnelles Mehrfachklicken fügt jedes Emoji zuverlässig ein; die alte Zwischenablage kommt danach zurück.
- Einstellungsfenster (Zahnrad im Panel oder Startmenü) mit täglicher Update-Suche über GitHub-Releases und Installation per Knopfdruck.
