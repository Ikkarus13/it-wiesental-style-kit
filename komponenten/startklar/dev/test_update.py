#!/usr/bin/env python3
"""Update-Suche gegen einen lokalen Test-Server (kein GitHub, nichts wird installiert).

    STARTKLAR_CONFIG_DIR=<leer> dev/test_update.py
Braucht den echten Signaturschlüssel (~/.config/startklar-release/signing-key.pem).
"""
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
assert os.environ.get("STARTKLAR_CONFIG_DIR"), "STARTKLAR_CONFIG_DIR setzen"
KEY = os.path.expanduser("~/.config/startklar-release/signing-key.pem")
WORK = tempfile.mkdtemp(prefix="sk-update-")
os.environ["XDG_CACHE_HOME"] = os.path.join(WORK, "cache")
os.environ["XDG_CONFIG_HOME"] = os.path.join(WORK, "config")     # für verwaltet.json
FILES = {}
SEEN = []
NEED_AUTH = {"on": False}
fails = 0


def check(name, cond):
    global fails
    print(("ok   " if cond else "FEHL ") + name)
    fails += 0 if cond else 1


def build(version, evil=False):
    src = os.path.join(WORK, "src")
    shutil.rmtree(src, ignore_errors=True)
    top = os.path.join(src, f"startklar-{version}")
    shutil.copytree(ROOT, top, ignore=shutil.ignore_patterns(".git", "__pycache__", "dist"))
    init = os.path.join(top, "startklar", "__init__.py")
    txt = open(init).read()
    import re
    open(init, "w").write(re.sub(r'VERSION = "[^"]+"', f'VERSION = "{version}"', txt))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(top, arcname=f"startklar-{version}")
        if evil:
            info = tarfile.TarInfo("../ausbruch.txt")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"boom"))
    return buf.getvalue()


def publish(version, data, sig=None, sha=None):
    name = f"startklar-{version}.tar.gz"
    pkg = os.path.join(WORK, name)
    open(pkg, "wb").write(data)
    if sig is None:
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", KEY, "-rawin", "-in", pkg,
                        "-out", pkg + ".sig"], check=True)
        sig = open(pkg + ".sig", "rb").read()
    import hashlib
    sha = sha or (hashlib.sha256(data).hexdigest() + "  " + name + "\n").encode()
    FILES.clear()
    FILES[name], FILES[name + ".sig"], FILES[name + ".sha256"] = data, sig, sha
    FILES["latest"] = json.dumps({
        "tag_name": "v" + version, "body": "Test",
        "assets": [{"name": n, "url": f"http://127.0.0.1:{PORT}/asset/{n}"}
                   for n in (name, name + ".sig", name + ".sha256")]}).encode()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append((self.headers.get("Host"), self.path, self.headers.get("Authorization")))
        if self.path.endswith("/releases/latest"):
            if NEED_AUTH["on"] and not self.headers.get("Authorization"):
                self.send_response(404)
                self.end_headers()
                return
            body = FILES["latest"]
        elif self.path.startswith("/asset/"):
            # wie GitHub: Umleitung zum Speicher-Host (andere Adresse)
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.2:{PORT2}/s3/{self.path[7:]}")
            self.end_headers()
            return
        elif self.path.startswith("/s3/"):
            body = FILES.get(self.path[4:], b"")
        else:
            body = b""
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
srv2 = http.server.ThreadingHTTPServer(("127.0.0.2", 0), H)
PORT, PORT2 = srv.server_address[1], srv2.server_address[1]
for s in (srv, srv2):
    threading.Thread(target=s.serve_forever, daemon=True).start()
os.environ["STARTKLAR_UPDATE_API"] = f"http://127.0.0.1:{PORT}"

from startklar import updater  # noqa: E402
from startklar.config import get_config  # noqa: E402

started = []
updater._spawn = lambda args, **kw: started.append(args)

# 1. gültiges Update
publish("9.9.9", build("9.9.9"))
rel = updater.check()
check("neue Version erkannt", rel is not None and rel["version"] == "9.9.9")
check("installiert", updater.install(rel) == "9.9.9")
check("Installer gestartet", started and started[-1][-1] == "--update"
      and started[-1][1].endswith("startklar-9.9.9/installieren.sh"))
check("Asset-Umleitung ohne Token", all(a is None for h, p, a in SEEN if p.startswith("/s3/")))

# 2. gleiche Version → kein Update
publish("0.0.1", build("0.0.1"))
check("ältere Version ignoriert", updater.check() is None)


def fails_with(text, version, data, **kw):
    publish(version, data, **kw)
    rel = updater.check()
    try:
        updater.install(rel)
        return False
    except updater.UpdateError as e:
        return text in str(e)


data = build("9.9.9")
check("falsche Signatur abgewiesen", fails_with("Signatur", "9.9.9", data, sig=b"x" * 64))
check("falsche Prüfsumme abgewiesen", fails_with("Prüfsumme", "9.9.9", data, sha=b"0" * 64 + b"\n"))
check("Pfad-Ausbruch abgewiesen", fails_with("unsicher", "9.9.9", build("9.9.9", evil=True)))
check("nichts ausserhalb entpackt", not any(f == "ausbruch.txt" for _r, _d, fs in
                                            os.walk(os.path.join(WORK, "cache")) for f in fs))

# 3. privates Repo: erst ohne, dann mit Token
NEED_AUTH["on"] = True
get_config().set("github_token", "test-token")
publish("9.9.9", build("9.9.9"))
SEEN.clear()
rel = updater.check()
check("privates Repo mit Token", rel is not None)
check("Token nur an API-Host", any(a == "Bearer test-token" for h, p, a in SEEN
                                   if p.endswith("/latest")))
updater.install(rel)
check("kein Token am Speicher-Host", all(a is None for h, p, a in SEEN if p.startswith("/s3/")))

# 4. Verwaltung durch ein Style Kit
KIT = os.path.join(WORK, "config", "style-kit", "verwaltet.json")
os.makedirs(os.path.dirname(KIT), exist_ok=True)


def kit(text):
    open(KIT, "w").write(text)


kit("{kaputt")
check("kaputte Datei → eigener Updater", updater.kit_verwaltet() is None
      and updater.check() is not None)
kit(json.dumps({"kit": "Test Kit", "komponenten": ["filefenster"], "verwalten": ["true"]}))
check("fremde Kennung → eigener Updater", updater.kit_verwaltet() is None)
kit(json.dumps({"kit": "Test Kit", "komponenten": ["startklar"],
                "verwalten": ["/bin/true", "--verwalten"]}))
check("eigene Kennung → verwaltet", updater.kit_verwaltet() == "Test Kit")
SEEN.clear()
check("check() meldet kein Update", updater.check() is None)
check("keine Anfrage an GitHub", not SEEN)
try:
    updater.install(rel)
    check("install() verweigert", False)
except updater.UpdateError as e:
    check("install() verweigert", "kommen über das Test Kit" in str(e))
check("Kit öffnen startet Befehl", updater.kit_oeffnen())
kit(json.dumps({"kit": "Test Kit", "komponenten": ["startklar"], "verwalten": "rm -rf /"}))
check("Shell-String wird nicht ausgeführt", updater.kit_verwaltet() == "Test Kit"
      and not updater.kit_oeffnen())
os.remove(KIT)
check("Datei weg → eigener Updater", updater.check() is not None)

shutil.rmtree(WORK, ignore_errors=True)
print("Fehler:", fails)
sys.exit(1 if fails else 0)
