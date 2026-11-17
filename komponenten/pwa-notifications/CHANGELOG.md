# Änderungen

## 1.5.1

- install.sh --update: Eine abgeschaltete Erweiterung bleibt bei einem Update abgeschaltet.
- Kit-Fassung: Datei .kit-verwaltet schaltet die eigene Update-Suche fest ab (auch ohne verwaltet.json).

## 1.5.0

- Verwaltung durch ein Style Kit: Steht «pwa-notifications» in `~/.config/style-kit/verwaltet.json`, sucht und installiert die Erweiterung keine Updates mehr selbst. «Jetzt nach Updates suchen» und die Einstellungsseite sagen dann «Updates für PWA-Benachrichtigungen kommen über das …» und bieten einen Knopf zum Öffnen des Kits.

## 1.4.0

- Neu: Teams-Anrufe lassen sich direkt aus der Meldung annehmen. Bei «… ruft Sie an» ergänzt die Erweiterung die Knöpfe «Annehmen», «Mit Video» und «Ablehnen» (Teams schickt selbst keine). Ein Klick holt das Teams-Fenster nach vorne und drückt das Teams-Tastenkürzel. Abschaltbar unter Inhalt → Text und Knöpfe.
- Web-Apps merken sich ihre Fensterklasse aus der Verknüpfung (StartupWMClass), damit ihr Fenster gefunden wird.

## 1.3.6

- Doku für Benutzer und Entwickler getrennt (docs/ENTWICKLUNG.md), Lizenzdatei angepasst

## 1.3.5

- Lizenzdatei mit Markenhinweis ergänzt.

## 1.3.4

- Beim Deaktivieren oder Neuladen meldet Cinnamon nicht mehr «Source ID … was not found» im Protokoll. Die Erweiterung versuchte, schon abgelaufene Timer ein zweites Mal zu entfernen.

## 1.3.3

Weitere Korrekturen aus der Code-Prüfung:
- Wird das Cinnamon-Applet «Benachrichtigungen» neu geladen, hängt sich die Erweiterung an die neue Instanz. Vorher blieben Gruppierung und «Neueste Meldungen oben» an der alten hängen und wirkten nicht mehr.
- Updater: Das neue Paket wird neben dem Erweiterungsordner entpackt und per Umbenennen eingetauscht. Scheitert das, kommt die alte Version sicher an ihren Platz zurück, statt in einem Unterordner zu landen.
- Meldungen von unbekannten Web-Apps lesen die installierten Apps höchstens alle 30 Sekunden neu ein, nicht mehr bei jeder Meldung.
- Hell/Dunkel: Die Erweiterung folgt jetzt auch einem Wechsel im GNOME-Schema, wenn das Cinnamon-Schema auf «Standard» steht.

## 1.3.2

Korrekturen aus einer Code-Prüfung:
- Meldungen in der Liste verloren nach einer Stiländerung (Einstellung, Theme, Hell/Dunkel) ihren Kartenstil. Sie werden jetzt auf das neue Stylesheet umgestellt.
- Chrome-Apps ohne Schnellaktionen werden nur noch über den ersten Teil der Domain erraten. Allerweltswörter wie «mail», «app» oder «cloud» führen nicht mehr zu einer falschen App.
- Eine Programm-Regel für «Microsoft Edge» oder «Google Chrome» blendet erkannte Web-Apps (Teams, Outlook …) nicht mehr mit aus.
- Eine erste Textzeile wie «bericht.pdf» oder «v1.2» wird nicht mehr für eine Domain gehalten.
- Updater: Versionsnummern aus GitHub werden geprüft, und der Download fällt auf den zweiten Link zurück, wenn einer fehlt.

## 1.3.1

- Behoben: Pop-ups waren manchmal zu niedrig, der Text hing unter der Karte. Ursache ist ein Fehler in Cinnamon: Lädt ein Icon (z. B. das der Claude-App) erst während der Einblend-Animation fertig, rechnet Cinnamon die Höhe nicht nach. Die Erweiterung richtet die Meldung nach dem Einblenden und nach dem Laden des Icons neu aus. Das wirkt für alle Meldungen.

## 1.3.0

- Neu: Regeln für beliebige Programme (Verhalten → Programme): Meldungen ausblenden, nur in der Liste (ohne Pop-up) oder nur als Pop-up (nicht in der Liste) zeigen. Der Programmname ist der Name über der Gruppe in der Liste, z. B. `blueman`. Er gilt auch für Web-Apps (z. B. «Microsoft Teams»).
- In der gruppierten Liste hat jede App ein Glocken-Symbol: Ein Klick blendet die App ab sofort aus und räumt ihre Meldungen weg. Die Bestätigung bietet «Rückgängig», das die vorherige Regel wiederherstellt.
- Der Abschnitt «Eigene Regeln» heisst jetzt «Regeln für Web-Apps».

## 1.2.0

- Neuer Schalter «Nach App gruppieren (wie unter Windows)» (Verhalten → Benachrichtigungsliste): Die Liste hinter der Glocke fasst Meldungen pro App zusammen. Jede Gruppe hat eine Kopfzeile mit Icon, Name und Anzahl, einen Pfeil zum Ein- und Ausklappen und ein × zum Löschen der ganzen Gruppe. In der Gruppe zeigen die Meldungen nur noch die Uhrzeit.
- Die Gruppen folgen der Reihenfolge der Liste (neueste oben oder unten).

## 1.1.0

- Neuer Schalter «Neueste Meldungen oben» (Verhalten → Benachrichtigungsliste). Er stellt die Reihenfolge in der Liste hinter der Glocke um und bleibt mit der gleichnamigen Einstellung des Cinnamon-Applets synchron, in beide Richtungen.
- Knopf «Weitere Einstellungen der Liste» öffnet die Einstellungen des Cinnamon-Applets «Benachrichtigungen».

## 1.0.1

- Web-Apps aus Google Chrome werden automatisch erkannt (Adresse aus den Schnellaktionen, sonst über den App-Namen), z. B. Outlook auf `outlook.cloud.microsoft`.
- Die Liste «Erkannte Web-Apps» zeigt auch Chrome-Apps und den Browser.
- README: Outlook unter Linux in Chrome statt Edge betreiben (Edge erhält Outlooks Web-Push nicht).

## 1.0.0

- Web-App-Meldungen aus Edge zeigen App-Name, App-Icon und Absenderbild statt «Microsoft Edge»; die Herkunftszeile verschwindet aus dem Text.
- Eigenes Aussehen im Stil von Windows 11 (hell, dunkel, automatisch oder eigene Farben), unabhängig vom Desktop-Theme.
- Einstellungsseite mit Vorschau: Breite, Rundung, Deckkraft, Rahmen, Schatten, Kopfzeile, Bildgrösse, Textlänge.
- Teams-Chats wahlweise automatisch ausblenden; Anrufe mit Knöpfen bleiben stehen.
- Eigene Regeln pro Domain: umbenennen, eigenes Icon, stummschalten.
- Benachrichtigungsliste: Titel und Text überlappen nicht mehr, Zeit steht klein in der Kopfzeile.
- Update-Suche über GitHub-Releases mit Installation an Ort und Stelle oder Download zum manuellen Installieren.
