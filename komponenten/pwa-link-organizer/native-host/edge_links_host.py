#!/usr/bin/env python3
"""Native-Messaging-Host von PWA link organizer: öffnet eine URL in Microsoft Edge.

Chrome startet das Skript für jede Nachricht neu (chrome.runtime.sendNativeMessage)
und schickt genau eine Nachricht:

    {"url": "https://…"}                  → Edge öffnet die URL, Antwort {"ok": true}
    {"url": "https://…", "profile": "X"}  → im Edge-Profilordner X ("" = zuletzt benutztes)
    {"profiles": true}                    → Edge-Profile für die Auswahl in der Erweiterung
    {"ping": true}                        → Antwort {"ok": true, "version": …} (Diagnose)

Bei Fehlern lautet die Antwort {"ok": false, "error": "…"}; die Erweiterung öffnet
die URL dann doch in Chrome, damit kein Link verloren geht.

Ohne "profile" in der Nachricht gilt ~/.config/edge-links/config.json:
    {"profile": "Default"}   Edge-Profilordner (--profile-directory)
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit

VERSION = "1.2.1"
MAX_MESSAGE = 1024 * 1024
MAX_URL = 32 * 1024
EDGE_NAMES = ("microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta", "microsoft-edge-dev")

_config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
_cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
CONFIG_FILE = os.path.join(_config_home, "edge-links", "config.json")
EDGE_STATE = os.path.join(_config_home, "microsoft-edge", "Local State")
LOG_FILE = os.path.join(_cache_home, "edge-links", "host.log")


def log(text):
    """Nur Fehler protokollieren (keine besuchten URLs), Datei bleibt klein."""
    # Steuerzeichen (Zeilenumbrüche) ersetzen: eine Zeile pro Eintrag, nichts unterschiebbar
    text = "".join(c if c >= " " and c != "\x7f" else "?" for c in str(text))
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 100_000:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        raise ValueError("Nachricht abgeschnitten")
    (length,) = struct.unpack("=I", header)
    if length > MAX_MESSAGE:
        raise ValueError("Nachricht zu gross")
    data = stream.read(length)
    if len(data) < length:
        raise ValueError("Nachricht abgeschnitten")
    return json.loads(data.decode("utf-8"))


def send_message(stream, obj):
    data = json.dumps(obj).encode("utf-8")
    stream.write(struct.pack("=I", len(data)))
    stream.write(data)
    stream.flush()


def check_url(url):
    """Nur vollständige http(s)-URLs ohne Leer- und Steuerzeichen."""
    if not isinstance(url, str) or not url:
        raise ValueError("keine URL")
    if len(url) > MAX_URL:
        raise ValueError("URL zu lang")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        raise ValueError("URL enthält Leer- oder Steuerzeichen")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("nur http- und https-Links")
    if not parts.hostname:
        raise ValueError("URL ohne Host")
    return url


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log(f"config.json unlesbar: {e}")
        return {}


def edge_profiles():
    """Profile aus Edges «Local State»: (Liste [{dir, name, user}], zuletzt benutztes)."""
    try:
        with open(EDGE_STATE, encoding="utf-8") as f:
            profile = json.load(f).get("profile", {})
    except (OSError, ValueError, AttributeError):
        return None, None
    cache = profile.get("info_cache") or {}
    order = [d for d in profile.get("profiles_order") or [] if d in cache]
    order += sorted(d for d in cache if d not in order)
    items = [{"dir": d, "name": cache[d].get("name") or d, "user": cache[d].get("user_name") or ""}
             for d in order if isinstance(cache.get(d), dict)]
    return items, profile.get("last_used")


def valid_profile_name(profile):
    return (isinstance(profile, str) and 0 < len(profile) < 200 and not profile.startswith("-")
            and "/" not in profile and profile not in (".", "..")
            and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in profile))


def resolve_profile(msg, cfg):
    """Profil aus der Nachricht (Wahl in der Erweiterung), sonst aus config.json.
    Ein Profil, das es in Edge nicht (mehr) gibt, wird ignoriert → zuletzt benutztes."""
    if "profile" in msg:
        profile = msg["profile"]
        if profile and not valid_profile_name(profile):
            raise ValueError("ungültiger Profilname")
    else:
        profile = cfg.get("profile")
        if profile and not valid_profile_name(profile):
            # Ein Tippfehler in config.json soll nicht jeden Link nach Chrome zurückschicken
            log("config.json: ungültiger Profilname – Edge wählt selbst")
            return None
    if not profile:
        return None
    known, _ = edge_profiles()
    if known is not None and profile not in {p["dir"] for p in known}:
        log(f"Edge-Profil «{profile}» nicht gefunden – Edge wählt selbst")
        return None
    return profile


def edge_command(profile):
    # EDGE_LINKS_BROWSER ersetzt Edge (nur für Tests: Chrome reicht seine Umgebung durch).
    browser = os.environ.get("EDGE_LINKS_BROWSER")
    if not browser:
        for name in EDGE_NAMES:
            browser = shutil.which(name) or (f"/usr/bin/{name}" if os.path.exists(f"/usr/bin/{name}") else None)
            if browser:
                break
    if not browser:
        raise RuntimeError("Microsoft Edge nicht gefunden")
    cmd = [browser]
    if profile:
        cmd.append(f"--profile-directory={profile}")
    return cmd


def clean_env():
    """Variablen von Chrome nicht an Edge vererben (Wrapper/Desktop-Datei)."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("CHROME_", "GOOGLE_")) and k != "GNOME_DISABLE_CRASH_DIALOG"}


def open_in_edge(url, profile):
    cmd = edge_command(profile) + [url]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,  # überlebt das Beenden des Hosts durch Chrome
        close_fds=True, env=clean_env(), cwd=os.path.expanduser("~"),
    )
    # Läuft Edge schon, gibt der Aufruf die URL weiter und endet gleich (Code 0).
    # Startet Edge neu, läuft der Prozess weiter – dann nicht warten.
    try:
        code = proc.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        return
    if code != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} endete mit Code {code}")


def main():
    out = sys.stdout.buffer
    try:
        msg = read_message(sys.stdin.buffer)
        if msg is None:
            return 0
        if not isinstance(msg, dict):
            raise ValueError("ungültige Nachricht")
        if msg.get("ping"):
            send_message(out, {"ok": True, "version": VERSION})
            return 0
        if msg.get("profiles"):
            profiles, last_used = edge_profiles()
            if profiles is None:
                raise RuntimeError("Edge-Profile nicht lesbar (Edge noch nie gestartet?)")
            default = load_config().get("profile")
            send_message(out, {"ok": True, "version": VERSION, "profiles": profiles,
                               "last_used": last_used, "default": default if isinstance(default, str) else ""})
            return 0
        url = check_url(msg.get("url"))
        open_in_edge(url, resolve_profile(msg, load_config()))
        send_message(out, {"ok": True})
    except Exception as e:  # noqa: BLE001 – jede Störung an die Erweiterung melden
        log(f"Fehler: {e}")
        try:
            send_message(out, {"ok": False, "error": str(e)})
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
