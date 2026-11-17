"""Komponenten wieder entfernen (über die eigenen Deinstallations-Skripte,
wo es sie gibt). Systempakete, Browser und Web-App-Richtlinien bleiben –
die Richtlinien entfernt ein erneuter Installer-Lauf ohne Web-Apps."""
import json
import os
import shutil
import subprocess

from . import ablauf, leiste

Z = ablauf.ZIEL


def _sh(kid, skript, *args):
    pfad = os.path.join(Z, kid, skript)
    if not os.path.exists(pfad):
        return f"{skript} fehlt"
    p = subprocess.run(["/bin/bash", pfad, *args], cwd=os.path.join(Z, kid),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return (p.stdout + p.stderr).strip()


def _design():
    sicherung = os.path.join(Z, "design-vorher.txt")
    try:
        with open(sicherung, encoding="utf-8") as f:
            for zeile in f:
                teile = zeile.split()
                if len(teile) == 3:
                    ablauf._gs_set(teile[0], teile[1], teile[2], optional=True)
        os.unlink(sicherung)
    except OSError:
        pass
    leiste.wiederherstellen(os.path.join(Z, "leiste-vorher.txt"))
    try:
        os.unlink(os.path.join(Z, "leiste-vorher.txt"))
    except OSError:
        pass
    return "Design und Leiste wie vorher (Designs bleiben in ~/.themes)."


def _tray_collapse():
    leiste.applet_entfernen(leiste.TRAY_COLLAPSE)
    shutil.rmtree(os.path.join(ablauf.DATA, "cinnamon", "applets", leiste.TRAY_COLLAPSE),
                  ignore_errors=True)
    return "Applet entfernt."


def _pwa_tray():
    d = os.path.join(Z, "pwa-tray")
    subprocess.run(["pkill", "-u", str(os.getuid()), "-f", f"^python3 {d}/pwa_tray.py"])
    for p in (os.path.join(ablauf.CONF, "autostart", "pwa-tray.desktop"),
              os.path.join(ablauf.CONF, "microsoft-edge", "NativeMessagingHosts", "ch.sascha.pwa_tray.json"),
              os.path.join(ablauf.CONF, "google-chrome", "NativeMessagingHosts", "ch.sascha.pwa_tray.json")):
        try:
            os.unlink(p)
        except OSError:
            pass
    return "PWA-Tray beendet und Autostart entfernt."


AKTIONEN = {
    "design": _design,
    "startklar": lambda: _sh("startklar", "deinstallieren.sh"),
    "filefenster": lambda: _sh("filefenster", "deinstallieren.sh"),
    "tray-collapse": _tray_collapse,
    "pwa-notifications": lambda: _sh("pwa-notifications", "uninstall.sh"),
    "pwa-tray": _pwa_tray,
    "emoji-panel": lambda: _sh("emoji-panel", "deinstallieren.sh"),
    "pwa-link-organizer": lambda: _sh("pwa-link-organizer", "install.sh", "--remove"),
    "onedrive": lambda: _sh("onedrive", "uninstall.sh"),
}


def entfernen(kid):
    text = AKTIONEN[kid]()
    if kid != "design":
        shutil.rmtree(os.path.join(Z, kid), ignore_errors=True)
    try:
        with open(ablauf.STATUS, encoding="utf-8") as f:
            status = json.load(f)
        status.get("komponenten", {}).pop(kid, None)
        with open(ablauf.STATUS, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
        ablauf.verwaltet_schreiben(list(status.get("komponenten", {})))
    except (OSError, ValueError):
        pass
    return text


def installiert():
    try:
        with open(ablauf.STATUS, encoding="utf-8") as f:
            return json.load(f).get("komponenten", {})
    except (OSError, ValueError):
        return {}
