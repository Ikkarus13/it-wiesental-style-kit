# IT-Wiesental Cloud Sync – für Microsoft OneDrive

Synchronisierungs-Client für **Microsoft OneDrive** und SharePoint/Teams-Bibliotheken unter Linux,
Teil des **IT-Wiesental Style Kit** ([it-wiesental.de](https://it-wiesental.de)). Er verhält sich
möglichst wie der Windows-Client (Dateien bei Bedarf, Status im Dateimanager, Tray-Symbol).
Unter der Haube läuft **rclone** (`rclone mount --vfs-cache-mode full`).

> **Unabhängiges Produkt.** IT-Wiesental Cloud Sync ist nicht von Microsoft entwickelt, unterstützt oder mit
> Microsoft verbunden. Microsoft und OneDrive sind Marken der Microsoft-Unternehmensgruppe.

## Funktionen

| Windows-Client | Hier |
|---|---|
| Wolkensymbol im Infobereich | Tray-Symbol (XApp.StatusIcon, Fallback AppIndicator) mit Status: aktuell, synchronisiert, angehalten, offline, Fehler |
| Aktivitätscenter (Linksklick) | Flyout mit Uploads/Downloads, Fortschrittsbalken, Speicherplatz pro Konto, Zahnrad-Menü |
| Dateien bei Bedarf | drei Zustände: ☁ nur online · ✓ lokal verfügbar · ● immer behalten (+ Sync/Fehler) |
| Statussymbole im Explorer | Embleme in **Nemo** und Nautilus, dazu die Spalte **„OneDrive-Status“** |
| Rechtsklick „Immer auf diesem Gerät behalten“ / „Speicher freigeben“ | gleich, plus „OneDrive-Link kopieren“ und „Online anzeigen“ |
| Einrichtungsassistent | Willkommen → Kontotyp → Microsoft-Anmeldung im Browser → Ordner → „Dateien bei Bedarf“ |
| Persönlich + Geschäftlich, mehrere Konten | ja, jedes Konto mit eigenem Ordner (`~/OneDrive`, `~/OneDrive - Firma`), eigenem rclone-Prozess und eigenem Cache |
| Teams/SharePoint „Synchronisieren“ (Ordner unter dem Firmennamen) | ja: Link einfügen, `odopen://`-Schaltfläche oder Lesezeichen → `~/Firma/Team - Ordner` |
| Gelöschte Dateien → Papierkorb | „In den Papierkorb“ in Nemo löscht in OneDrive → **OneDrive-Papierkorb (online)**, wiederherstellbar |
| Speicheroptimierung | „Nicht verwendete Dateien nach X Tagen nur online“ (angeheftete ausgenommen) |
| Synchronisierung anhalten | Uploads werden zurückgehalten (rclone-Upload-Queue), Pin-Downloads pausieren |
| Uploads/Downloads begrenzen | Einstellungen → Netzwerk |
| Ordner in der Seitenleiste | Lesezeichen in Nemo/Nautilus + eigenes Ordnersymbol |

## Unterstützte Systeme

| | Linux Mint (Cinnamon) | Ubuntu (GNOME) | Fedora (GNOME) | Arch/Manjaro |
|---|---|---|---|---|
| Installer | apt | apt | dnf | pacman |
| Tray-Symbol | XApp, Linksklick öffnet Aktivitätscenter | AppIndicator (vorinstalliert) | AppIndicator, Installer richtet die GNOME-Erweiterung ein | je nach Desktop |
| Dateimanager | Nemo (+ Nautilus) | Nautilus | Nautilus | Nemo/Nautilus |
| Getestet | ✔ Mint 22.3 | Paketnamen geprüft | Paketnamen geprüft | – |

* Unter GNOME öffnet sich das Aktivitätscenter über das Tray-Menü oder per Mittelklick auf das Symbol
  (AppIndicator kennt keinen Linksklick).
* Unter Wayland (Standard bei Ubuntu/Fedora) erscheint das Aktivitätscenter zentriert statt am Tray-Symbol.
* Auf Fedora mit GNOME muss man sich nach der Installation einmal ab- und wieder anmelden, damit die
  Tray-Erweiterung aktiv wird.
* KDE/Dolphin: Synchronisierung und Tray funktionieren, Symbole und Kontextmenü im Dateimanager fehlen
  (Dolphin bräuchte ein eigenes Plugin).

## Installation

```bash
./install.sh
```

Das Skript erkennt die Paketverwaltung (apt, dnf, pacman) und installiert die nötigen Pakete
(PyGObject, AppIndicator, fuse3, Nemo-/Nautilus-Python-Anbindung, ggf. GNOME-Tray-Erweiterung).
Es bietet an, ein **aktuelles rclone** von rclone.org zu installieren (.deb/.rpm, mindestens v1.63),
kopiert das Programm nach `~/.local/share/onedrive-mint`, installiert Symbole, Starter und die Dateimanager-Erweiterung
und startet danach den Einrichtungsassistenten.

Deinstallieren: `./uninstall.sh`

Kommandozeile: `onedrive-mint --settings | --setup | --activity | --check-update | --quit`

## Updates

Im IT-Wiesental Style Kit sucht das Programm nicht selbst nach Updates. Neue Versionen kommen mit
einer neuen Version des Kits: Das Kit meldet sie beim Anmelden, aktualisiert wird über
Startmenü → **IT-Wiesental Style Kit** → «Jetzt aktualisieren».

## Ordnersicherung (Desktop, Dokumente, Bilder)

Wie „Wichtige PC-Ordner sichern“ unter Windows: Einstellungen → **Sicherung**, dort das OneDrive wählen
und pro Ordner „Sichern …“ klicken. Standardmässig ist nichts aktiv. Ordner, die es im OneDrive schon gibt
(z. B. vom Windows-PC), werden weiterverwendet.

| Ordner | Funktionsweise | Ohne laufenden Client |
|---|---|---|
| **Dokumente, Bilder** | werden ins OneDrive verschoben; das XDG-Benutzerverzeichnis zeigt dorthin, der alte Pfad wird eine Verknüpfung; „Dateien bei Bedarf“ | leer |
| **Desktop** | bleibt ein lokaler Ordner; Dateien werden per `rclone bisync` in beide Richtungen abgeglichen; Ordner liegen im OneDrive und erscheinen als Verknüpfung | Dateien da, Ordnerinhalte nicht |

* Vor dem Verschieben wird geprüft, ob genug Platz im OneDrive ist. Symbolische Links, Sockets und Pipes
  kann OneDrive nicht speichern: Dann wird abgebrochen, bevor etwas verschoben wird.
* Desktop: Werden beide Seiten gleichzeitig geändert, gewinnt die neuere Fassung, die andere bleibt als
  `Name.RECHNER.ext` erhalten. Wird eine Ordner-Verknüpfung vom Desktop gelöscht, landet der Ordner im
  OneDrive-Papierkorb. `desktop.ini` wird nicht abgeglichen, `.lnk`/`.url` werden ausgeblendet.
  Abgleich bei lokalen Änderungen nach wenigen Sekunden, sonst jede Minute. Protokoll: `logs/desktop-bisync.log`.
* Solange eine Sicherung aktiv ist, ist das automatische Zurücksetzen der Benutzerverzeichnisse beim
  Anmelden (`xdg-user-dirs-update`) abgeschaltet. Sonst würde es Dokumente/Bilder auf `~/…` zurückstellen,
  wenn OneDrive noch nicht verbunden ist.
* „Sicherung beenden“ stellt alles zurück, auf Wunsch mit Kopie der Dateien auf den PC.

## Freigabe durch Entra-Administrator

Sperrt eine Organisation die Benutzerzustimmung für Apps (Meldung „Genehmigung durch Administrator erforderlich“,
AADSTS65001/90094), erzeugt das Programm einen **Admin-Consent-Link**:
im Assistenten auf der Anmeldeseite, unter Einstellungen → Erweitert oder automatisch nach einer abgelehnten Anmeldung.
Der Link lässt sich kopieren, per E-Mail an die IT schicken (mit fertigem Text) oder direkt öffnen.

Freigegeben wird die rclone-App (`b15665d9-eda6-4092-8539-0eec376afd59`) bzw. eine eigene Client-ID mit den
delegierten Graph-Berechtigungen `Files.Read(.All)`, `Files.ReadWrite(.All)`, `Sites.Read.All` und `offline_access`.
Nach der Zustimmung leitet Microsoft auf `localhost:53682` weiter. Die Fehlerseite dort ist normal.

## Teams- und SharePoint-Ordner synchronisieren

Wie „Synchronisieren“ unter Windows: Eine Bibliothek oder ein Ordner daraus erscheint als eigener
Sync-Ordner unter dem Firmennamen, z. B. `~/Contoso/Projektteam - Dokumente` oder `~/Contoso/Projektteam - Allgemein`.
In der Nemo-Seitenleiste gibt es den Eintrag „Contoso“ mit Gebäudesymbol. Dateien bei Bedarf, Symbole, Papierkorb usw.
funktionieren dort genauso. Verwendet wird die Anmeldung des Geschäftskontos.

Es gibt drei Wege:

1. **Link einfügen:** Tray-Menü bzw. Aktivitätscenter → *Teams-/SharePoint-Ordner synchronisieren …*
   - In Teams: Kanal → *Dateien* → beim Ordner *…* → *Link kopieren*
   - In SharePoint: den Ordner im Browser öffnen und die Adresse kopieren (Freigabelinks gehen auch)
2. **Schaltfläche „Synchronisieren“ in SharePoint/Teams:** Der Installer registriert das Programm für
   `odopen://`-Links, die diese Schaltfläche aufruft. Der Browser fragt einmal, ob der Link geöffnet werden darf.
3. **Lesezeichen „In OneDrive synchronisieren“:** Falls SharePoint die Schaltfläche unter Linux ausblendet.
   Das Skript liest die Bibliotheks-IDs aus der geöffneten SharePoint-Seite und erzeugt denselben `odopen://`-Link.
   Einrichten lässt es sich im Dialog über den Link *Lesezeichen … einrichten*.

Entfernen geht über Einstellungen → Konto → *Synchronisierung beenden*. Beim Aufheben der Verknüpfung des
Geschäftskontos werden auch dessen Teams-Ordner entfernt.

Technisch ist jede Bibliothek ein eigenes rclone-Remote (`drive_type = documentLibrary`, optional mit Unterordner)
und läuft in einem eigenen rclone-Prozess. Ein paar Dutzend Ordner sind unproblematisch.

## Papierkorb

Nemo legt beim Löschen auf einem FUSE-Laufwerk einen Ordner `.Trash-1000` im OneDrive an. Das Programm leert diesen sofort,
indem es die Elemente in OneDrive löscht – sie landen im **OneDrive-Papierkorb** (Web), Ordner bleiben als Ganzes erhalten.
Der leere Ordner `.Trash-1000` bleibt in OneDrive bestehen (unter Linux versteckt).
Abschaltbar unter Einstellungen → Synchronisierung; dann bleibt der Linux-Papierkorb als Ordner in OneDrive liegen.

## Dateien und Pfade

```
~/.config/onedrive-mint/accounts.json            Konten
~/.config/onedrive-mint/settings.json            Einstellungen
~/.config/onedrive-mint/accounts/<id>/rclone.conf  rclone-Konfiguration inkl. Token (chmod 600)
~/.config/onedrive-mint/accounts/<id>/pins.json    „Immer behalten“/„Nur online“-Entscheidungen
~/.cache/onedrive-mint/<id>/                     rclone-VFS-Cache
~/.local/state/onedrive-mint/logs/<id>.log       rclone-Protokoll
~/.local/state/onedrive-mint/mnt/<id>/           eigentlicher Einhängepunkt (versteckt)
~/OneDrive - …, ~/Firma/<Bibliothek>             Verknüpfung auf den Einhängepunkt
```

Die Konten werden unter einem versteckten Pfad eingehängt, damit sie nicht als Laufwerke auf dem
Desktop und in der Geräteliste von Nemo/Nautilus erscheinen (GIO blendet Einhängepunkte unter
Punkt-Ordnern aus). Sichtbar sind sie als Ordner `~/OneDrive - …` mit Wolkensymbol bzw. als
Firmenordner mit Gebäudesymbol, als Lesezeichen und als Favoriten in der Seitenleiste.
Ein eigenes Symbol direkt in der Laufwerksliste ist nicht möglich: Nemo zeigt dort das symbolische
Laufwerkssymbol, das gvfs nur aus dem Dateisystemtyp ableitet.

Die rclone-Konfiguration ist getrennt von einer eventuell vorhandenen `~/.config/rclone/rclone.conf`.

## Bekannte Einschränkungen

* Ohne laufenden Client ist der OneDrive-Ordner leer (wie bei jedem rclone-Mount). Nicht hochgeladene Änderungen bleiben im Cache und werden beim nächsten Start hochgeladen.
* Ordner-Status ist eine Näherung: ein Ordner gilt als „lokal“, wenn seine direkten Dateien lokal sind.
* Änderungen, die auf anderen Geräten passieren, erscheinen nach spätestens ~1 Minute (rclone `--poll-interval`).
* Der Kontotyp-Check im Assistenten erkennt, wenn man sich mit dem falschen Konto anmeldet.
* Organisationen, die Drittanbieter-Apps sperren, brauchen eine eigene App-Registrierung (Assistent → Kontotyp → Erweitert).
* Kein Versionsverlauf, keine Verknüpfungen „Zu OneDrive hinzufügen“ für fremde Ordner aus „Für mich freigegeben“.

## Datenschutz, Marken und Lizenz

- **Datenschutz:** Anmeldedaten und Einstellungen bleiben auf dem Computer; Dateien gehen nur zwischen
  diesem Computer und Microsoft 365 hin und her. Details: [DATENSCHUTZ.md](DATENSCHUTZ.md) und
  *Einstellungen → Info*.
- **Marken:** Unabhängiges Programm, nicht von Microsoft entwickelt, unterstützt oder mit Microsoft
  verbunden. Microsoft, OneDrive, SharePoint und Teams sind Marken der Microsoft-Unternehmensgruppe.
- **Anmeldung:** Ohne eigene App-Registrierung des Herausgebers meldet sich das Programm über die
  App von rclone an; in Firmen-Tenants muss ein Administrator die App einmal freigeben
  (siehe «Freigabe durch Entra-Administrator»).
- **Lizenz:** Freeware, siehe [LICENSE](LICENSE); Software Dritter: [DRITTANBIETER.md](DRITTANBIETER.md).
