# Änderungen

Ältere Versionen: siehe die GitHub-Releases.

## 1.1.1

- Diagnose aus Teams (Seitentitel, Beschriftungen) wird nur noch gespeichert, wenn ~/.cache/pwa-tray/diagnose-an existiert, und dann nur für den Benutzer lesbar. Eine alte diag.json wird gelöscht.
- Kit-Fassung: Datei .kit-verwaltet schaltet die eigene Update-Suche fest ab.

## 1.1.0

- Verwaltung durch ein Style Kit: Steht «pwa-tray» in `~/.config/style-kit/verwaltet.json`, sucht und installiert PWA-Tray keine Updates mehr selbst. «Nach Updates suchen» meldet dann «Updates für PWA-Tray kommen über das …» und bietet einen Knopf zum Öffnen des Kits.
