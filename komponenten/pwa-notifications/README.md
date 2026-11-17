# PWA-Benachrichtigungen

Cinnamon-Erweiterung für Linux Mint: Benachrichtigungen von Web-Apps wie **Teams, Outlook oder 3CX**, die als PWA in Microsoft Edge laufen, erscheinen mit dem richtigen App-Namen, App-Icon und Absenderbild. Ohne die Erweiterung kommen sie als «Microsoft Edge» mit Edge-Logo und der Domain als erster Textzeile. Dazu gibt es ein Aussehen im Stil von Windows 11, das sich einstellen lässt.

## Was die Erweiterung macht

- **App statt Browser:** Sie liest die Herkunft (z. B. `teams.cloud.microsoft`) aus der Meldung und sucht die passende installierte Web-App. Daraus kommen Name und Icon, die Domain-Zeile verschwindet aus dem Text.
- **Absenderbild:** Das Profilbild der Person, die schreibt, erscheint links neben dem Text und nicht riesig daneben.
- **Kopfzeile im Stil von Windows 11:** Kleines App-Icon und App-Name über dem Titel. In der Benachrichtigungsliste steht die Uhrzeit rechts daneben.
- **Eigenes Aussehen:** Hell, dunkel, automatisch nach System oder mit eigenen Farben. Es funktioniert mit jedem Desktop-Theme.
- **Benachrichtigungsliste repariert:** Titel und Text überlappen nicht mehr, mehrzeilige Texte werden ganz angezeigt.
- **Gruppierung im Stil von Windows 11:** Wahlweise fasst die Liste Meldungen pro App zusammen, mit Anzahl, Ein- und Ausklappen und «Gruppe löschen». Neueste Meldungen stehen wahlweise oben.
- **Teams-Chats ausblenden lassen:** Wahlweise. Teams schickt Chat-Nachrichten als «dringend», deshalb bleiben sie sonst stehen. Anrufe bleiben immer stehen.
- **Teams-Anrufe direkt annehmen:** Teams schickt bei Anrufen keine Knöpfe mit. Die Erweiterung ergänzt «Annehmen», «Mit Video» und «Ablehnen». Ein Klick holt das Teams-Fenster nach vorne und drückt das Teams-Tastenkürzel (Strg+Umschalt+S, A oder D). Nach dem Ablehnen geht es zurück zum vorherigen Fenster.
- **Regeln pro Domain:** Für Web-Apps, die nicht automatisch erkannt werden, oder um eine App umzubenennen oder stummzuschalten.
- **Programme ausblenden:** Meldungen beliebiger Programme (z. B. `blueman`) ganz ausblenden, nur in der Liste (ohne Pop-up) oder nur als Pop-up (nicht in der Liste) zeigen. Am schnellsten geht es in der gruppierten Liste mit dem Glocken-Symbol der App, danach gibt es «Rückgängig».
- **Updates:** Sucht täglich nach einer neuen Version. Ein Klick installiert sie an Ort und Stelle, alternativ gibt es den Download zum manuellen Installieren.

Erkannt werden automatisch alle Web-Apps aus Edge und Chrome (`~/.local/share/applications/msedge-*.desktop`, `chrome-*.desktop`). Edge schreibt die Adresse der App in die Verknüpfung. Chrome tut das nicht, dort kommt sie aus den Schnellaktionen («Neue Nachricht» …) oder, wenn es keine gibt, aus dem App-Namen (`outlook.cloud.microsoft` → «Outlook»). Bei anderen Chromium-Browsern helfen eigene Regeln.

### Outlook unter Linux: Chrome statt Edge

Outlook schickt Desktop-Meldungen für neue Mails und Termin-Erinnerungen nur im Benachrichtigungsstil **«Browser»** (Outlook → Einstellungen → Allgemein → Benachrichtigungen), und dann per **Web-Push**. Edge unter Linux bekommt diesen Push nicht zugestellt (geprüft November 2026: In den Entwicklertools unter *Application → Background services → Push messaging* kommt nichts an). Mit **Google Chrome** funktioniert es. Deshalb Outlook in Chrome als App installieren (⋮ → *Streamen, speichern und teilen → Seite als App installieren*) und dort den Stil «Browser» wählen. Teams, 3CX und andere Web-Apps laufen in Edge problemlos, sie melden direkt aus der Seite.

Wer Outlook in Edge behält, sollte den Stil auf **«Warnung»** lassen. Mit «Browser» zeigt Outlook dort gar nichts mehr an.

## Installation

Voraussetzung ist Linux Mint bzw. Cinnamon 6.0 oder neuer. `curl` und `tar` (bei Mint vorinstalliert) braucht nur die Update-Funktion.

1. Auf der [Release-Seite](https://github.com/Ikkarus13/pwa-notifications/releases/latest) das Paket `pwa-notifications@sascha-X.Y.Z.tar.gz` herunterladen.
2. Entpacken und installieren:

```bash
tar -xzf pwa-notifications@sascha-*.tar.gz && cd pwa-notifications-*/ && ./install.sh
```

Die Erweiterung ist danach sofort aktiv. Die Einstellungen öffnest du unter **Systemeinstellungen → Erweiterungen → PWA-Benachrichtigungen → Zahnrad**.

## Einstellungen

| Reiter | Inhalt |
|---|---|
| Aussehen | Farbschema, ob alle Meldungen oder nur Web-Apps gestaltet werden, Deckkraft, Breite, Rundung, Rahmen, Schatten, Knopf «Vorschau anzeigen» |
| Inhalt | Kopfzeile (bei Web-Apps / allen / nie), Absenderbild oder App-Icon, Bildgrösse, maximale Textzeilen, Edge-Knopf «Einstellungen» ausblenden |
| Verhalten | Neueste Meldungen oben oder unten in der Liste, Liste nach App gruppieren (ein-/ausklappbar, Gruppe löschen), Programme ausblenden / nur Liste / nur Pop-up, dringende Meldungen ohne Knöpfe ausblenden, erkannte Web-Apps, Regeln für Web-Apps (Domain → Name, Icon, stumm) |
| Updates | Installierte und neueste Version, jetzt suchen, installieren, Release-Seite, automatische Suche/Installation, Quelle und optionaler Zugangstoken |

Position, Anzeigedauer und «Nicht stören» bleiben Einstellungen von Cinnamon. Der Knopf auf der Seite «Aussehen» öffnet sie.

## Updates

Die Update-Suche fragt die GitHub-Releases des Repositorys ab. Unter *Updates → Quelle* lässt sich ein anderes Repository als Quelle eintragen, z. B. ein eigenes mit veröffentlichten Releases. Braucht die Quelle eine Anmeldung, kann dort zusätzlich ein Zugangstoken eingetragen werden (am besten ein *Fine-grained personal access token* mit nur Lesezugriff auf «Contents» genau dieses Repositorys). Er wird unverschlüsselt in den Einstellungen gespeichert.

## Deinstallieren

```bash
./uninstall.sh
```

Mit `--alles` werden auch die Einstellungen gelöscht.

## Marken und Lizenz

Unabhängiges Projekt, nicht von Microsoft entwickelt, unterstützt oder mit Microsoft verbunden. Microsoft, Windows, Edge, Outlook und Teams sind Marken der Microsoft-Unternehmensgruppe; Chrome ist eine Marke von Google LLC. 3CX ist eine Marke der 3CX Ltd. Die Nennung beschreibt nur, womit die Erweiterung zusammenarbeitet. Lizenz: [LICENSE](LICENSE).

Für Entwickler: [docs/ENTWICKLUNG.md](docs/ENTWICKLUNG.md)
