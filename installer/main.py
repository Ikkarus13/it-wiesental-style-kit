#!/usr/bin/env python3
"""Style Kit (Linux Mint im Stil von Windows 11) – Einstieg. Ohne Optionen startet der grafische Assistent.

Textmodus (z. B. per SSH oder für Verteilungen):
  --text                       ohne Fenster, Auswahl über die folgenden Optionen
  --alle                       alle Komponenten
  --nur ID,ID,…                nur diese Komponenten (siehe --liste)
  --design dunkel|hell         Farbschema (Standard: dunkel)
  --leiste-lassen              Startknopf/Fensterliste nicht in die Mitte setzen
  --browser edge,chrome        diese Browser installieren
  --webapps outlook=chrome,teams=edge
                               Outlook/Teams als Apps anlegen (leer = keine)
  --liste                      Komponenten anzeigen
  --verwalten                  installierte Komponenten entfernen (Fenster)
  --entfernen ID,ID,…          Komponenten entfernen (Textmodus)
  --aktualisieren              installierte Komponenten mit diesem Paket erneuern
                               (Design, Leiste und Browser bleiben, wie sie sind)
  --update-pruefen             nach einer neuen Version des Kits suchen (Autostart)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from installer import ablauf, entfernen, katalog  # noqa: E402


def liste(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def textmodus(args):
    if args.aktualisieren:
        status = {}
        try:
            import json
            with open(ablauf.STATUS, encoding="utf-8") as f:
                status = json.load(f)
        except (OSError, ValueError):
            pass
        komp = [k.id for k in katalog.KOMPONENTEN if k.id in status.get("komponenten", {})]
        if not komp:
            sys.exit("Keine installierten Komponenten gefunden – nichts zu aktualisieren.")
        a = ablauf.Auswahl(komponenten=komp, design=status.get("design", "dunkel"),
                           leiste_mittig=False, aktualisieren=True)
        return _ausfuehren(a)
    if args.alle:
        komp = [k.id for k in katalog.KOMPONENTEN]
    else:
        komp = liste(args.nur)
    unbekannt = [k for k in komp if k not in katalog.NACH_ID]
    if unbekannt or not komp:
        sys.exit(f"Komponenten angeben (--alle oder --nur …). Unbekannt: {', '.join(unbekannt)}"
                 if unbekannt else "Komponenten angeben: --alle oder --nur ID,ID (siehe --liste).")
    komp = [k.id for k in katalog.KOMPONENTEN if k.id in komp]
    a = ablauf.Auswahl(komponenten=komp, design=args.design,
                       leiste_mittig=not args.leiste_lassen)
    a.browser = [b for b in liste(args.browser) if b in katalog.BROWSER_NACH_ID]
    if args.webapps is not None:
        a.browser_seite = True
        for teil in liste(args.webapps):
            wid, _, bid = teil.partition("=")
            if wid not in katalog.WEBAPP_NACH_ID or bid not in katalog.BROWSER_NACH_ID:
                sys.exit(f"--webapps: «{teil}» verstehe ich nicht (z. B. outlook=chrome).")
            a.webapps[wid] = bid
    return _ausfuehren(a)


def _ausfuehren(a):
    inst = ablauf.Installation(a, root_mit="sudo" if os.isatty(0) else "pkexec")

    def melde(art, *d):
        if art == "schritt":
            print(f"\n\033[1;34m==>\033[0m {d[0]}", flush=True)
        elif art == "log":
            print("    " + d[0], flush=True)
        elif art == "ergebnis":
            print(("  \033[32m✓\033[0m " if d[1] else "  \033[31m✗\033[0m ") + d[0]
                  + ("" if d[1] else f": {d[2]}"), flush=True)
    ok = inst.ausfuehren(melde)
    if inst.hinweise:
        print("\nNoch zu tun:")
        for h in inst.hinweise:
            print("  • " + h)
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(prog="installieren.sh", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                usage="%(prog)s [Optionen]")
    p.add_argument("--text", action="store_true")
    p.add_argument("--alle", action="store_true")
    p.add_argument("--nur")
    p.add_argument("--design", choices=("dunkel", "hell"), default="dunkel")
    p.add_argument("--leiste-lassen", action="store_true")
    p.add_argument("--browser", default="")
    p.add_argument("--webapps")
    p.add_argument("--liste", action="store_true")
    p.add_argument("--verwalten", action="store_true")
    p.add_argument("--entfernen")
    p.add_argument("--aktualisieren", action="store_true")
    p.add_argument("--update-pruefen", action="store_true")
    args = p.parse_args()

    if os.geteuid() == 0:
        sys.exit("Bitte als normaler Benutzer starten (nicht mit sudo) – "
                 "das Administrator-Passwort wird bei Bedarf abgefragt.")
    if args.liste:
        inst = entfernen.installiert()
        for k in katalog.KOMPONENTEN:
            v = ablauf.manifest().get(k.id, {}).get("version", "")
            print(f"{k.id:18} {k.name:28} {v:8} {'(installiert)' if k.id in inst else ''}")
        return 0
    if args.entfernen:
        for kid in liste(args.entfernen):
            if kid not in katalog.NACH_ID:
                print(f"Unbekannt: {kid}")
                continue
            print(f"==> {katalog.NACH_ID[kid].name}")
            print(entfernen.entfernen(kid))
        return 0
    if args.update_pruefen:
        from installer import aktualisierung
        return aktualisierung.melden_beim_anmelden()
    if args.text or args.alle or args.nur or args.aktualisieren:
        return textmodus(args)
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        sys.exit("Kein Bildschirm gefunden – Textmodus: installieren.sh --alle (siehe --help).")
    from installer import gui
    return gui.starten(verwalten=args.verwalten)


if __name__ == "__main__":
    sys.exit(main())
