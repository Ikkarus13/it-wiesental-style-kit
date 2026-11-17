"""Installationsablauf: aus einer Auswahl einen Plan machen und ihn ausführen.

Die Oberfläche (gui.py) und der Textmodus (main.py) rufen beide
`Installation(...).ausfuehren(melde)` auf. `melde` bekommt Ereignisse:

    ("schritt", text)          neuer Abschnitt
    ("log", zeile)             Ausgabezeile
    ("fortschritt", 0.0–1.0)
    ("ergebnis", id, ok, text) eine Komponente ist fertig
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from . import katalog, leiste, marke

HIER = os.path.dirname(os.path.abspath(__file__))
PAKET = os.path.dirname(HIER)
KOMP_ORDNER = os.path.join(PAKET, "komponenten")

HOME = os.path.expanduser("~")
DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
CONF = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
ZIEL = os.path.join(DATA, marke.KURZ)
STATUS = os.path.join(ZIEL, "installiert.json")
THEMES = os.path.join(HOME, ".themes")

TROCKEN = os.environ.get("WST_TROCKEN") == "1"   # nichts mit root ausführen


def version():
    try:
        with open(os.path.join(PAKET, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "?"


def manifest():
    """komponenten/manifest.json: {id: {"version": …, "quelle": …, "ref": …}}"""
    try:
        with open(os.path.join(KOMP_ORDNER, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def browser_installiert(bid):
    return shutil.which(katalog.BROWSER_NACH_ID[bid].befehl) is not None


def desktop_sitzung():
    d = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return "cinnamon" in d.lower() or "x-cinnamon" in d.lower()


@dataclass
class Auswahl:
    komponenten: list                         # ids in Katalog-Reihenfolge
    design: str = "dunkel"                    # dunkel | hell
    leiste_mittig: bool = True
    browser: list = field(default_factory=list)     # zu installieren
    webapps: dict = field(default_factory=dict)     # {"outlook": "chrome", …}
    browser_seite: bool = False               # Browser-Teil wurde angeboten
    aktualisieren: bool = False               # Update: Einstellungen des Nutzers nicht anfassen
    erinnern: bool = False                    # Assistent: später einmal «Gefällt es dir?» zeigen

    def hat(self, kid):
        return kid in self.komponenten


class Abbruch(Exception):
    pass


class Installation:
    def __init__(self, auswahl: Auswahl, root_mit="pkexec"):
        self.a = auswahl
        self.root_mit = root_mit              # pkexec | sudo | keiner
        self.melde = lambda *e: None
        self.ergebnisse = {}
        self.hinweise = []                    # Schritte für den Nutzer am Ende

    # ------------------------------------------------------------------ Hilfen
    def log(self, text):
        for zeile in str(text).splitlines() or [""]:
            self.melde("log", zeile)

    def lauf(self, cmd, cwd=None, env=None, pruefen=True, timeout=600):
        self.log("$ " + " ".join(cmd))
        e = dict(os.environ)
        e.update(env or {})
        p = subprocess.Popen(cmd, cwd=cwd, env=e, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace", start_new_session=True)
        ende = time.monotonic() + timeout
        for zeile in p.stdout:
            self.log(zeile.rstrip("\n"))
            if time.monotonic() > ende:
                p.kill()
                raise Abbruch(f"{os.path.basename(cmd[0])} hat zu lange gebraucht.")
        rc = p.wait()
        if pruefen and rc:
            raise Abbruch(f"{os.path.basename(cmd[0])} endete mit Fehler {rc}.")
        return rc

    # ------------------------------------------------------------------ Plan
    def root_auftrag(self):
        pakete = list(katalog.BASIS_PAKETE)
        for kid in self.a.komponenten:
            pakete += katalog.NACH_ID[kid].pakete
        if self.a.hat("onedrive") and shutil.which("nemo"):
            pakete.append("python-nemo")
        browser = [b for b in self.a.browser if not browser_installiert(b)]
        auftrag = {"name": marke.KURZ, "pakete": list(dict.fromkeys(pakete)), "browser": browser,
                   "rclone": self.a.hat("onedrive")}
        if self.a.browser_seite:
            per_browser = {"edge": [], "chrome": []}
            for wid, bid in self.a.webapps.items():
                per_browser[bid].append(wid)
            auftrag["webapps"] = per_browser
        return auftrag

    def braucht_root(self):
        a = self.root_auftrag()
        fehlt = [p for p in a["pakete"] if not _deb_installiert(p)]
        return bool(fehlt or a["browser"] or "webapps" in a or
                    (a["rclone"] and not _rclone_ok()))

    def schritte(self):
        s = []
        if self.braucht_root():
            s.append(("root", "Systempakete und Browser"))
        for kid in self.a.komponenten:
            s.append((kid, katalog.NACH_ID[kid].name))
        s.append(("abschluss", "Abschluss"))
        return s

    # ------------------------------------------------------------------ Ablauf
    def ausfuehren(self, melde):
        self.melde = melde
        schritte = self.schritte()
        fehler_root = None
        if any(k in self.a.komponenten for k in ("design", "startklar", "tray-collapse")):
            leiste.ids_ergaenzen()
        for i, (sid, titel) in enumerate(schritte):
            melde("fortschritt", i / len(schritte))
            melde("schritt", titel)
            try:
                if sid == "root":
                    self._root()
                elif sid == "abschluss":
                    self._abschluss()
                else:
                    self._entpacken(sid)
                    getattr(self, "_inst_" + sid.replace("-", "_"))()
                    self.ergebnisse[sid] = (True, "installiert")
                    melde("ergebnis", sid, True, "installiert")
            except Exception as e:  # noqa: BLE001 – eine Komponente darf scheitern
                text = str(e) or e.__class__.__name__
                self.log(f"FEHLER: {text}")
                if sid == "root":
                    fehler_root = text
                    self.ergebnisse[sid] = (False, text)
                    melde("ergebnis", sid, False, text)
                elif sid != "abschluss":
                    self.ergebnisse[sid] = (False, text)
                    melde("ergebnis", sid, False, text)
        melde("fortschritt", 1.0)
        if fehler_root:
            self.hinweise.insert(0, "Der Systemteil ist nicht vollständig gelaufen: "
                                 f"{fehler_root} – Installer später erneut starten.")
        return all(ok for ok, _ in self.ergebnisse.values())

    # ------------------------------------------------------------------ root
    def _root(self):
        auftrag = self.root_auftrag()
        tmp = tempfile.mkdtemp(prefix="wst-")
        pfad = os.path.join(tmp, "auftrag.json")
        with open(pfad, "w", encoding="utf-8") as f:
            json.dump(auftrag, f, indent=1)
        self.log("Auftrag: " + json.dumps(auftrag, ensure_ascii=False))
        helfer = os.path.join(HIER, "root_helfer.py")
        if TROCKEN or self.root_mit == "keiner":
            self.log("(Trockenlauf – Systemteil wird nicht ausgeführt)")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        if self.root_mit == "sudo":
            cmd = ["sudo", "/usr/bin/python3", "-I", helfer, pfad]
        else:
            cmd = ["pkexec", "/usr/bin/python3", "-I", helfer, pfad]
        fehler = []

        def filter_log(z):
            if z.startswith("@@SCHRITT "):
                self.melde("schritt", z[10:])
            elif z.startswith("@@FEHLER "):
                fehler.append(z[9:])
            else:
                self.melde("log", z)
        alt, self.log = self.log, lambda t: [filter_log(z) for z in str(t).splitlines()]
        try:
            # sudo braucht das Terminal für die Passwortabfrage
            if self.root_mit == "sudo":
                rc = subprocess.call(cmd)
            else:
                rc = self.lauf(cmd, pruefen=False, timeout=3600)
        finally:
            self.log = alt
            shutil.rmtree(tmp, ignore_errors=True)
        if rc in (126, 127) and self.root_mit == "pkexec":
            raise Abbruch("Administrator-Anmeldung abgebrochen.")
        if rc:
            raise Abbruch("; ".join(fehler) or f"Systemteil endete mit Fehler {rc}.")
        self.ergebnisse["root"] = (True, "erledigt")

    # ------------------------------------------------------------------ Entpacken
    def _entpacken(self, kid):
        """komponenten/<kid>/ (fest im Paket) nach ZIEL/<kid> kopieren.
        Erst vollständig daneben kopieren, dann austauschen."""
        quelle = os.path.join(KOMP_ORDNER, kid)
        if not os.path.isdir(quelle):
            raise Abbruch(f"komponenten/{kid} fehlt im Paket.")
        os.makedirs(ZIEL, exist_ok=True)
        ziel = os.path.join(ZIEL, kid)
        if os.path.realpath(quelle) == os.path.realpath(ziel):
            return ziel                       # läuft aus der installierten Kopie
        neu = tempfile.mkdtemp(prefix=f".{kid}-neu-", dir=ZIEL)
        try:
            kopie = os.path.join(neu, kid)
            shutil.copytree(quelle, kopie, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
            alt = None
            if os.path.exists(ziel):
                alt = ziel + f".alt-{os.getpid()}"
                os.rename(ziel, alt)
            os.rename(kopie, ziel)
            if alt:
                shutil.rmtree(alt, ignore_errors=True)
        finally:
            shutil.rmtree(neu, ignore_errors=True)
        self.log(f"{kid} {manifest().get(kid, {}).get('version', '')} nach {ziel} kopiert")
        return ziel

    def _ordner(self, kid):
        return os.path.join(ZIEL, kid)

    def _sh(self, kid, skript, *args, env=None):
        d = self._ordner(kid)
        pfad = os.path.join(d, skript)
        os.chmod(pfad, 0o755)
        return self.lauf(["/bin/bash", pfad, *args], cwd=d, env=env)

    # ------------------------------------------------------------------ Komponenten
    def _inst_design(self):
        d = self._ordner("design")
        os.makedirs(THEMES, exist_ok=True)
        for name in sorted(os.listdir(d)):
            if os.path.isfile(os.path.join(d, name, "index.theme")):
                ziel = os.path.join(THEMES, name)
                if os.path.exists(ziel):
                    shutil.rmtree(ziel)
                shutil.copytree(os.path.join(d, name), ziel, symlinks=True)
                self.log(f"Design {name} → {ziel}")
        if self.a.aktualisieren:
            return                            # nur die Dateien erneuern, Auswahl bleibt
        theme = "Win11-Dark" if self.a.design == "dunkel" else "Win11-Light"
        sicherung = os.path.join(ZIEL, "design-vorher.txt")
        if not os.path.exists(sicherung):
            vorher = []
            for schema, key in _THEME_KEYS:
                vorher.append(f"{schema} {key} {_gs_get(schema, key)}")
            with open(sicherung, "w", encoding="utf-8") as f:
                f.write("\n".join(vorher) + "\n")
        for schema, key in _THEME_KEYS:
            if _gs_set(schema, key, theme):
                self.log(f"{schema} {key} = {theme}")
        _gs_set("org.cinnamon.desktop.interface", "gtk-color-scheme", "", optional=True)
        _gs_set("org.x.apps.portal", "color-scheme",
                "prefer-dark" if self.a.design == "dunkel" else "prefer-light", optional=True)
        if self.a.leiste_mittig:
            self.log(leiste.mittig(os.path.join(ZIEL, "leiste-vorher.txt")))

    def _inst_startklar(self):
        self._sh("startklar", "installieren.sh")
        if self.a.hat("design") and self.a.leiste_mittig and not self.a.aktualisieren:
            # Startklar setzt sich an die Stelle des alten Menüs – wieder mittig
            self.log(leiste.mittig(os.path.join(ZIEL, "leiste-vorher.txt")))

    def _inst_filefenster(self):
        self._sh("filefenster", "installieren.sh")

    def _inst_tray_collapse(self):
        self._sh("tray-collapse", "install.sh")
        self.log(leiste.applet_hinzufuegen_vor_tray())

    def _inst_pwa_notifications(self):
        self._sh("pwa-notifications", "install.sh")

    def _inst_emoji_panel(self):
        self._sh("emoji-panel", "installieren.sh")

    def _browser_konfig_vorbereiten(self):
        """Ordner für Native-Messaging-Hosts anlegen, auch wenn der Browser noch
        nie gestartet wurde – sonst überspringen die Tool-Installer ihn."""
        for b in katalog.BROWSER:
            if browser_installiert(b.id) or b.id in self.a.browser:
                os.makedirs(os.path.join(CONF, b.konfig, "NativeMessagingHosts"), exist_ok=True)

    def _inst_pwa_tray(self):
        self._browser_konfig_vorbereiten()
        konf = os.path.join(CONF, "pwa-tray", "config.json")
        if not os.path.exists(konf):
            bid = self.a.webapps.get("teams", "edge")
            b = katalog.BROWSER_NACH_ID[bid]
            os.makedirs(os.path.dirname(konf), exist_ok=True)
            with open(konf, "w", encoding="utf-8") as f:
                json.dump({"apps": [{
                    "name": "Microsoft Teams",
                    "wm_class": b.wm_prefix + katalog.TEAMS_APP_ID,
                    "desktop": f"{b.desktop_prefix}{katalog.TEAMS_APP_ID}-Default.desktop"}],
                    "button": {"width": 34, "height": 40}, "hover_color": "#c42b1c"},
                    f, indent=2)
            self.log(f"PWA-Tray für Teams in {b.name} eingerichtet.")
        self._sh("pwa-tray", "install.sh")
        ext = os.path.join(self._ordner("pwa-tray"), "edge-extension")
        if os.path.isdir(ext):
            self.hinweise.append(
                "PWA-Tray (Teams-Status): in Edge edge://extensions öffnen, "
                "«Entwicklermodus» einschalten, «Entpackte Erweiterung laden» → "
                f"{ext}")

    def _inst_pwa_link_organizer(self):
        self._browser_konfig_vorbereiten()
        d = self._ordner("pwa-link-organizer")
        local_state = os.path.join(CONF, "google-chrome", "Local State")
        if os.path.exists(local_state):
            self._sh("pwa-link-organizer", "install.sh")
        else:
            # Chrome noch nie gestartet: Host-Eintrag selbst schreiben
            self._edge_links_host(d)
        self.hinweise.append(
            "PWA link organizer: in Chrome chrome://extensions öffnen, «Entwicklermodus» "
            "einschalten, «Entpackte Erweiterung laden» → "
            f"{os.path.join(d, 'extension')}")

    def _edge_links_host(self, d):
        import base64
        with open(os.path.join(d, "extension", "manifest.json"), encoding="utf-8") as f:
            key = json.load(f)["key"]
        ext_id = "".join(chr(ord("a") + int(c, 16))
                         for c in hashlib.sha256(base64.b64decode(key)).hexdigest()[:32])
        host = os.path.join(d, "native-host", "edge_links_host.py")
        os.chmod(host, 0o755)
        ziel = os.path.join(CONF, "google-chrome", "NativeMessagingHosts", "ch.sascha.edge_links.json")
        os.makedirs(os.path.dirname(ziel), exist_ok=True)
        with open(ziel, "w", encoding="utf-8") as f:
            json.dump({"name": "ch.sascha.edge_links",
                       "description": "PWA link organizer: Outlook-Links in Microsoft Edge öffnen",
                       "path": host, "type": "stdio",
                       "allowed_origins": [f"chrome-extension://{ext_id}/"]},
                      f, indent=2, ensure_ascii=False)
        self.log(f"Host eingetragen: {ziel}")

    def _inst_onedrive(self):
        # Systempakete und rclone hat der Systemteil schon erledigt; der eigene
        # Installer fragt dann weder nach sudo noch nach rclone.
        # Branding des Kits für den Client (bleibt bei dessen Updates erhalten)
        eigen = os.path.join(PAKET, "branding", "cloud-sync.json")
        if os.path.exists(eigen):
            ziel = os.path.join(CONF, "onedrive-mint", "branding.json")
            os.makedirs(os.path.dirname(ziel), exist_ok=True)
            shutil.copyfile(eigen, ziel)
            self.log(f"Branding für {katalog.CLOUD_SYNC_NAME} → {ziel}")
        self._sh("onedrive", "install.sh")
        name = katalog.CLOUD_SYNC_NAME
        self.hinweise.append(f"{name}: über das Startmenü «{name}» starten "
                             "und das Microsoft-Konto anmelden.")

    # ------------------------------------------------------------------ Abschluss
    def _abschluss(self):
        os.makedirs(ZIEL, exist_ok=True)
        try:
            with open(STATUS, encoding="utf-8") as f:
                status = json.load(f)
        except (OSError, ValueError):
            status = {}
        m = manifest()
        komp = status.get("komponenten", {})
        for kid, (ok, _) in self.ergebnisse.items():
            if ok and kid in katalog.NACH_ID:
                komp[kid] = m.get(kid, {}).get("version", "")
        status.update({"paket": version(), "komponenten": komp,
                       "design": self.a.design, "zeit": time.strftime("%Y-%m-%d %H:%M")})
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
        # Installer und Entfernen-Werkzeug für später behalten
        werkzeug = os.path.join(ZIEL, "installer")
        if os.path.realpath(HIER) != os.path.realpath(werkzeug):
            if os.path.exists(werkzeug):
                shutil.rmtree(werkzeug)
            shutil.copytree(HIER, werkzeug, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(os.path.join(PAKET, "branding"), os.path.join(ZIEL, "branding"),
                            dirs_exist_ok=True)
            shutil.copy(os.path.join(PAKET, "VERSION"), os.path.join(ZIEL, "VERSION"))
        _entfernen_starter()
        if self.a.erinnern and not self.a.aktualisieren and marke.HAT_DANKE:
            stand_setzen(installiert_am=time.time(), nur_wenn_neu=True)
        verwaltet_schreiben(list(komp))
        if self.a.webapps:
            namen = ", ".join(f"{katalog.WEBAPP_NACH_ID[w].name} ({katalog.BROWSER_NACH_ID[b].name})"
                              for w, b in self.a.webapps.items())
            self.hinweise.append(
                f"Web-Apps {namen}: erscheinen beim nächsten Start des Browsers im "
                "Startmenü. Danach in der App anmelden.")
        if self.a.aktualisieren:
            self.hinweise.append(f"{marke.PRODUKT} ist jetzt auf Version {version()}.")
        if any(k in self.a.komponenten for k in ("design", "tray-collapse", "pwa-notifications",
                                                  "startklar")):
            self.hinweise.append("Einmal ab- und wieder anmelden, damit alles überall wirkt.")


# ---------------------------------------------------------------------- Hilfsfunktionen
_THEME_KEYS = (("org.cinnamon.desktop.interface", "gtk-theme"),
               ("org.cinnamon.desktop.wm.preferences", "theme"),
               ("org.cinnamon.theme", "name"))


def _schema(schema, key):
    from gi.repository import Gio
    src = Gio.SettingsSchemaSource.get_default()
    s = src.lookup(schema, True) if src else None
    if s is None or not s.has_key(key):
        return None
    return Gio.Settings.new(schema)


def _gs_get(schema, key):
    s = _schema(schema, key)
    return s.get_string(key) if s else ""


def _gs_set(schema, key, wert, optional=False):
    from gi.repository import Gio
    s = _schema(schema, key)
    if s is None:
        return False
    try:
        if s.get_value(key).get_type_string() == "s":
            s.set_string(key, wert)
        else:
            return False
    except Exception:  # noqa: BLE001
        if not optional:
            raise
        return False
    Gio.Settings.sync()
    return True


def _deb_installiert(paket):
    p = subprocess.run(["dpkg-query", "-W", "-f=${Status}", paket],
                       capture_output=True, text=True)
    return "install ok installed" in p.stdout


def _rclone_ok():
    exe = shutil.which("rclone")
    if not exe:
        return False
    out = subprocess.run([exe, "version"], capture_output=True, text=True).stdout
    try:
        teile = out.split()[1].lstrip("v").split(".")
        return (int(teile[0]), int(teile[1])) >= (1, 63)
    except (IndexError, ValueError):
        return False


STAND = os.path.join(ZIEL, "update.json")     # Update- und Erinnerungsstand


def stand():
    try:
        with open(STAND, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def stand_setzen(nur_wenn_neu=False, **werte):
    d = stand()
    for k, v in werte.items():
        if not (nur_wenn_neu and k in d):
            d[k] = v
    try:
        os.makedirs(ZIEL, exist_ok=True)
        with open(STAND, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass


VERWALTET = os.path.join(CONF, "style-kit", "verwaltet.json")
UPDATE_AUTOSTART = os.path.join(CONF, "autostart", f"{marke.KURZ}-update.desktop")


def verwaltet_schreiben(komponenten):
    """Den Programmen sagen, dass Updates über dieses Kit kommen (sie suchen dann nicht selbst).
    Ohne Komponenten: Datei und Update-Prüfung beim Anmelden entfernen."""
    if not komponenten:
        for p in (VERWALTET, UPDATE_AUTOSTART):
            try:
                os.unlink(p)
            except OSError:
                pass
        return
    os.makedirs(os.path.dirname(VERWALTET), exist_ok=True)
    main = os.path.join(ZIEL, "installer", "main.py")
    tmp = VERWALTET + ".neu"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"kit": marke.PRODUKT, "komponenten": sorted(komponenten),
                   "verwalten": ["/usr/bin/python3", "-I", main, "--verwalten"]},
                  f, indent=2, ensure_ascii=False)
    os.replace(tmp, VERWALTET)
    if (marke.B.get("update_repo") or "").strip() or marke.HAT_DANKE:
        os.makedirs(os.path.dirname(UPDATE_AUTOSTART), exist_ok=True)
        with open(UPDATE_AUTOSTART, "w", encoding="utf-8") as f:
            f.write(f"""[Desktop Entry]
Type=Application
Name={marke.PRODUKT} – Updates
Comment=Beim Anmelden nach einer neuen Version suchen
Exec=/usr/bin/python3 -I {main} --update-pruefen
Icon={os.path.join(ZIEL, "branding", "symbol.svg")}
NoDisplay=true
X-GNOME-Autostart-Delay=90
""")


def _entfernen_starter():
    apps = os.path.join(DATA, "applications")
    os.makedirs(apps, exist_ok=True)
    logo = os.path.join(ZIEL, "branding", "symbol.svg")
    with open(os.path.join(apps, f"{marke.KURZ}.desktop"), "w", encoding="utf-8") as f:
        f.write(f"""[Desktop Entry]
Type=Application
Name={marke.PRODUKT}
Comment=Komponenten hinzufügen oder entfernen
Exec=/usr/bin/python3 -I {ZIEL}/installer/main.py --verwalten
Icon={logo}
Categories=Settings;
Terminal=false
""")
    # installer/main.py braucht das Paket als Elternordner
    subprocess.run(["update-desktop-database", apps], capture_output=True)


if __name__ == "__main__":
    sys.exit("Bitte installieren.sh verwenden.")
